"""结构化访问日志中间件（TASK-BE-008；REL-003 慢请求统计的前置）。

每个 HTTP 请求结束（含未捕获异常）向 stdout 写一行 JSON，字段与
`docs/72-monitoring.md` §5.1 对齐：

    {"ts":"2026-11-09T00:00:00.123Z","level":"info","logger":"app.access",
     "event":"access","request_id":"6f1c0f3e-...","method":"GET",
     "path":"/api/questions","status":200,"latency_ms":12.34,
     "actor_id":"42","user_agent":"curl/8.5.0"}

设计要点：

1. **纯 ASGI 实现**：不继承 Starlette 的 `BaseHTTPMiddleware`（它会缓冲响应体，
   并在异常/流式场景下引入额外语义）。本中间件只旁路 `send` 读取
   `http.response.start` 的状态码，不缓冲、不改写响应体，SSE / 文件下载等
   流式响应不受影响；非 http scope（lifespan / websocket）原样透传、不写日志。
2. **只记安全字段**：`path` 取自 `scope["path"]`，query 在
   `scope["query_string"]`、本模块从不读取；不读请求体，不读 Cookie /
   Authorization 头。因此密码、用户答案、会话与 CSRF token 都不会落盘。
3. **复用同一个 request_id**：读取 `request.state.request_id`（由 `app/main.py`
   的 request_id 中间件写入）。本中间件在 `create_app()` 中注册在它之后，
   Starlette 中后注册者更靠外层，故本中间件的读取发生在响应阶段——此时 id
   必已注入，与响应头 / 错误体里的值一致；只有取不到时才退化为入站
   `X-Request-ID` 头，仍没有则生成 uuid4（保证字段不为空）。
4. **级别**：5xx → `error`，4xx → `warning`，2xx/3xx → `info`。注意
   docs/72 §5.1 的枚举写作 `warn`；本卡按任务要求输出 `warning`
   （语义相同，只在 4xx 上写法不同，按 `error` / `info` 过滤的查询无差异）。
5. **线程安全**：`write_record()` 用模块级锁串行化“整行写入 + flush”，
   并发请求不会写出交错的半行；写日志本身失败（stdout 关闭、取字段异常等）
   一律静默丢弃，不影响业务请求与响应。

`latency_ms` 由 `time.perf_counter()` 度量，保留 2 位小数。
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.settings import Settings

#: 固定标识：便于在日志系统里按 logger / event 过滤访问行
ACCESS_LOGGER = "app.access"
ACCESS_EVENT = "access"

LEVEL_INFO = "info"
LEVEL_WARNING = "warning"
LEVEL_ERROR = "error"

#: user_agent 落日志前的最大长度（超长 UA 只保留前缀）
MAX_USER_AGENT_LENGTH = 120

DEFAULT_REQUEST_ID_HEADER = "X-Request-ID"

#: 兜底状态码：未产生响应就抛异常记 500；客户端提前断开记 499（nginx 约定）
_SERVER_ERROR_STATUS = 500
_CLIENT_CLOSED_STATUS = 499

#: 访问日志写入锁：保证“一行 JSON + 换行”不会被并发请求切成半行
_WRITE_LOCK = threading.Lock()


def level_for_status(status: int) -> str:
    """状态码 → 日志级别（5xx=error，4xx=warning，其余 info）。"""
    if status >= 500:
        return LEVEL_ERROR
    if status >= 400:
        return LEVEL_WARNING
    return LEVEL_INFO


def _iso8601_utc(moment: datetime) -> str:
    """RFC3339 UTC、毫秒精度、以 `Z` 结尾（如 `2026-11-09T00:00:00.123Z`）。"""
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _state_value(scope: Scope, key: str) -> Any:
    """读 `request.state` 里的值（Starlette 把它存在 `scope["state"]` 的 dict 里）。"""
    state = scope.get("state")
    if isinstance(state, dict):
        return state.get(key)
    if state is None:
        return None
    return getattr(state, key, None)


def _header_value(scope: Scope, name: str) -> str | None:
    """按名字取请求头（大小写不敏感；ASGI 里是 bytes，用 latin-1 还原）。"""
    target = name.lower().encode("latin-1")
    for item in scope.get("headers") or ():
        raw_name, raw_value = item
        if not isinstance(raw_name, bytes) or not isinstance(raw_value, bytes):
            continue
        if raw_name.lower() == target:
            return raw_value.decode("latin-1")
    return None


def _request_id_header_name(scope: Scope) -> str:
    """请求追踪头名：优先用应用配置，取不到时用默认值。"""
    application = scope.get("app")
    settings = getattr(getattr(application, "state", None), "settings", None)
    if isinstance(settings, Settings):
        return settings.request_id_header
    return DEFAULT_REQUEST_ID_HEADER


def _resolve_request_id(scope: Scope) -> str:
    """复用 request_id 中间件注入的值；退化路径保证字段非空。"""
    existing = _state_value(scope, "request_id")
    if isinstance(existing, str) and existing:
        return existing
    inbound = _header_value(scope, _request_id_header_name(scope))
    if inbound:
        return inbound
    return str(uuid.uuid4())


def _coerce_actor_id(value: Any) -> str | None:
    """把用户 id 统一成字符串（契约里 actor_id 为 string|null；布尔不算 id）。"""
    if isinstance(value, bool):
        return None
    if not isinstance(value, int | str):
        return None
    text = str(value).strip()
    return text or None


def _resolve_actor_id(scope: Scope) -> str | None:
    """当前登录用户 id；未登录（或无认证依赖运行过）为 None。"""
    direct = _coerce_actor_id(_state_value(scope, "actor_id"))
    if direct is not None:
        return direct
    user = _state_value(scope, "user")
    if user is None:
        # app/auth/dependencies.py 同时注入 auth_context；兼容只读它的场景
        user = getattr(_state_value(scope, "auth_context"), "user", None)
    return _coerce_actor_id(getattr(user, "id", None))


def _resolve_user_agent(scope: Scope) -> str | None:
    """User-Agent（截断到 120 字符）；缺失时保持 null 而不是省略字段。"""
    raw = _header_value(scope, "user-agent")
    if raw is None:
        return None
    text = raw.strip()
    return text[:MAX_USER_AGENT_LENGTH] or None


def build_record(
    *,
    scope: Scope,
    status: int,
    latency_ms: float,
    now: datetime | None = None,
) -> dict[str, Any]:
    """构造一行访问日志的内容（字段顺序固定，便于人工排查）。"""
    moment = now if now is not None else datetime.now(UTC)
    return {
        "ts": _iso8601_utc(moment),
        "level": level_for_status(status),
        "logger": ACCESS_LOGGER,
        "event": ACCESS_EVENT,
        "request_id": _resolve_request_id(scope),
        "method": str(scope.get("method") or "").upper(),
        "path": str(scope.get("path") or ""),
        "status": int(status),
        "latency_ms": round(float(latency_ms), 2),
        "actor_id": _resolve_actor_id(scope),
        "user_agent": _resolve_user_agent(scope),
    }


def write_record(record: dict[str, Any], stream: io.TextIOBase | None = None) -> str:
    """把一条访问日志写成单行 JSON 并 flush；返回实际写入的行（便于测试）。

    `stream` 缺省时在**调用时**解析 `sys.stdout`，这样 pytest 等对
    `sys.stdout` 的替换对中间件同样生效，也便于单测注入 `io.StringIO`。
    """
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    # 类型标注用 TextIOBase，但 typeshed 里 sys.stdout 可能是 TextIOWrapper/TextIO，
    # 这里按文本流处理以兼容两种标注（行为一致）
    target: Any = sys.stdout if stream is None else stream
    # 日志失败绝不能影响业务请求：stdout 关闭/被替换成非文本流时静默丢弃
    with contextlib.suppress(AttributeError, OSError, ValueError), _WRITE_LOCK:
        target.write(line + "\n")
        target.flush()
    return line


class AccessLogMiddleware:
    """纯 ASGI 访问日志中间件：只观察 `http.response.start` 的状态码。"""

    def __init__(self, app: ASGIApp, *, stream: io.TextIOBase | None = None) -> None:
        self.app = app
        self.stream = stream

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status: int | None = None
        failed = False

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            # 未捕获异常：先记 500 再原样抛出，交给 ServerErrorMiddleware 出响应
            failed = True
            raise
        finally:
            latency_ms = (time.perf_counter() - started) * 1000.0
            code = status if status is not None else (
                _SERVER_ERROR_STATUS if failed else _CLIENT_CLOSED_STATUS
            )
            # 兜底：即便取字段/序列化意外失败，也不能把业务请求带崩
            with contextlib.suppress(Exception):
                write_record(
                    build_record(scope=scope, status=code, latency_ms=latency_ms),
                    self.stream,
                )


__all__ = [
    "ACCESS_EVENT",
    "ACCESS_LOGGER",
    "DEFAULT_REQUEST_ID_HEADER",
    "LEVEL_ERROR",
    "LEVEL_INFO",
    "LEVEL_WARNING",
    "MAX_USER_AGENT_LENGTH",
    "AccessLogMiddleware",
    "build_record",
    "level_for_status",
    "write_record",
]
