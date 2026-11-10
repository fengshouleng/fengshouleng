"""TASK-BE-008 验收测试：结构化访问日志中间件。

覆盖：
- 成功请求 / 4xx / 5xx 各写**一行**（单行可 `json.loads`），5xx 用临时路由触发
- 字段完整、`latency_ms` 为数值、`ts` 可解析、user_agent 截断到 120
- 未登录 `actor_id=null`，登录后 `actor_id=用户 id`
- `path` 不含 query；密码 / Cookie / Authorization / 请求体 / 会话 token 不落日志
- request_id 与响应头一致（复用 request_id 中间件生成的值）
- 纯 ASGI 行为：不缓冲流式响应、非 http scope 不写日志
"""

from __future__ import annotations

import asyncio
import io
import json
import re
from collections.abc import Callable
from datetime import datetime
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.types import Message, Receive, Scope, Send

from app.middleware.access_log import AccessLogMiddleware

API = "/api"
PASSWORD = "correct-horse-123"

#: 规格要求的字段（缺一不可；actor_id/user_agent 缺失时也要输出 null）
REQUIRED_FIELDS = {
    "ts",
    "level",
    "event",
    "request_id",
    "method",
    "path",
    "status",
    "latency_ms",
    "actor_id",
    "user_agent",
}


def single_line(lines: Callable[[], list[dict[str, Any]]]) -> dict[str, Any]:
    """断言恰好一条访问日志并返回它。"""
    entries = lines()
    assert len(entries) == 1, entries
    return entries[0]


async def _drive(middleware: AccessLogMiddleware, scope: Scope) -> list[Message]:
    """直接以 ASGI 方式驱动中间件，收集它向下游 `send` 的消息。"""
    messages: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        messages.append(message)

    await middleware(scope, receive, send)
    return messages


def test_success_request_writes_one_json_line(
    client: TestClient,
    access_lines: Callable[[], list[dict[str, Any]]],
    access_text: Callable[[], str],
) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200

    raw_lines = [line for line in access_text().splitlines() if line.strip()]
    assert len(raw_lines) == 1  # 每个请求恰好一行
    entry = json.loads(raw_lines[0])  # 单行即可解析（不是多行 JSON）

    assert set(entry) >= REQUIRED_FIELDS
    assert entry["event"] == "access"
    assert entry["logger"] == "app.access"
    assert entry["level"] == "info"
    assert entry["method"] == "GET"
    assert entry["path"] == "/healthz"
    assert entry["status"] == 200


def test_latency_is_numeric_and_user_agent_truncated(
    client: TestClient,
    access_lines: Callable[[], list[dict[str, Any]]],
) -> None:
    client.get("/healthz", headers={"User-Agent": "U" * 200})
    entry = single_line(access_lines)

    assert isinstance(entry["latency_ms"], float)
    assert entry["latency_ms"] >= 0.0
    assert entry["user_agent"] == "U" * 120

    parsed = datetime.fromisoformat(entry["ts"])  # 3.11+ 支持结尾的 Z
    assert parsed.tzinfo is not None


def test_4xx_is_logged_as_warning(
    client: TestClient,
    access_lines: Callable[[], list[dict[str, Any]]],
) -> None:
    response = client.get("/no-such-route")
    assert response.status_code == 404

    entry = single_line(access_lines)
    assert entry["level"] == "warning"
    assert entry["status"] == 404
    assert entry["path"] == "/no-such-route"


def test_unauthenticated_request_is_logged_with_null_actor(
    client: TestClient,
    access_lines: Callable[[], list[dict[str, Any]]],
) -> None:
    response = client.get(f"{API}/auth/me")
    assert response.status_code == 401

    entry = single_line(access_lines)
    assert entry["level"] == "warning"
    assert entry["status"] == 401
    assert entry["path"] == f"{API}/auth/me"
    assert entry["actor_id"] is None
    assert "actor_id" in entry  # 字段存在，不是被省略


def test_5xx_is_logged_as_error(
    app: FastAPI,
    access_lines: Callable[[], list[dict[str, Any]]],
) -> None:
    @app.get("/_test/boom")
    def _boom() -> None:
        raise RuntimeError("boom")

    # raise_server_exceptions=False：让 Starlette 的 500 处理器返回响应
    with TestClient(app, raise_server_exceptions=False) as failing_client:
        response = failing_client.get("/_test/boom")

    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"

    entry = single_line(access_lines)
    assert entry["level"] == "error"
    assert entry["status"] == 500
    assert entry["path"] == "/_test/boom"


def test_request_id_matches_response_header(
    client: TestClient,
    access_lines: Callable[[], list[dict[str, Any]]],
) -> None:
    response = client.get("/healthz")

    entry = single_line(access_lines)
    assert entry["request_id"] == response.headers["X-Request-ID"]


def test_inbound_request_id_is_reused(
    client: TestClient,
    access_lines: Callable[[], list[dict[str, Any]]],
) -> None:
    headers = {"X-Request-ID": "req-be008-abc"}
    response = client.get("/healthz", headers=headers)

    entry = single_line(access_lines)
    assert entry["request_id"] == "req-be008-abc"
    assert entry["request_id"] == response.headers["X-Request-ID"]


def test_path_excludes_query_string(
    client: TestClient,
    access_lines: Callable[[], list[dict[str, Any]]],
    access_text: Callable[[], str],
) -> None:
    response = client.get("/healthz?token=query-secret-token&password=query-password")
    assert response.status_code == 200

    entry = single_line(access_lines)
    assert entry["path"] == "/healthz"
    assert "?" not in entry["path"]

    raw = access_text()
    assert "query-secret-token" not in raw
    assert "query-password" not in raw


def test_line_matches_slow_requests_awk_expectations(
    client: TestClient,
    access_text: Callable[[], str],
) -> None:
    """deploy/monitoring/slow_requests.awk 用正则抓字段：这里固化同样的约束。

    awk 侧要求：`"latency_ms"` 后跟**数字**（不是字符串）、`"status"` 为数字、
    `"method"` / `"path"` 为不含内部引号的字符串，且整行不含 tab/换行。
    """
    response = client.get("/healthz?token=should-not-be-logged")
    assert response.status_code == 200

    line = access_text().rstrip("\n")
    assert "\n" not in line
    assert "\t" not in line
    assert "?" not in line
    assert re.search(r'"latency_ms"[ \t]*:[ \t]*[0-9]+(\.[0-9]+)?', line)
    assert re.search(r'"status"[ \t]*:[ \t]*[0-9]+', line)
    assert re.search(r'"method"[ \t]*:[ \t]*"[^"]*"', line)
    assert re.search(r'"path"[ \t]*:[ \t]*"[^"]*"', line)


def test_logged_in_request_reports_actor_id(
    client: TestClient,
    access_lines: Callable[[], list[dict[str, Any]]],
    make_user: Callable[..., Any],
    login: Callable[..., dict[str, Any]],
) -> None:
    user = make_user("access-log-user")
    login("access-log-user")

    response = client.get(f"{API}/auth/me")
    assert response.status_code == 200

    entries = access_lines()
    assert [entry["path"] for entry in entries] == [
        f"{API}/auth/login",
        f"{API}/auth/me",
    ]
    assert entries[0]["actor_id"] is None  # 登录请求本身还没带会话
    assert entries[1]["actor_id"] == str(user.id)


def test_sensitive_values_are_never_logged(
    client: TestClient,
    access_lines: Callable[[], list[dict[str, Any]]],
    access_text: Callable[[], str],
    make_user: Callable[..., Any],
) -> None:
    make_user("secret-user")
    response = client.post(
        f"{API}/auth/login",
        json={"username": "secret-user", "password": PASSWORD},
        headers={
            "Authorization": "Bearer authorization-secret-token",
            "User-Agent": "ua-probe/1.0",
        },
    )
    assert response.status_code == 200

    session_token = client.cookies.get("session_id")
    assert session_token

    raw = access_text()
    assert PASSWORD not in raw  # 请求体里的密码
    assert session_token not in raw  # 会话 token（响应 Set-Cookie）
    assert "authorization-secret-token" not in raw
    assert "authorization" not in raw.lower()
    assert "cookie" not in raw.lower()
    assert "password" not in raw.lower()
    assert "secret-user" not in raw  # 请求体里的用户名

    entry = single_line(access_lines)
    # 只有规格字段 + logger；没有 body / cookies / headers 之类的容器字段
    assert set(entry) == REQUIRED_FIELDS | {"logger"}
    assert entry["path"] == f"{API}/auth/login"
    assert entry["method"] == "POST"
    assert entry["user_agent"] == "ua-probe/1.0"


def test_pure_asgi_does_not_buffer_streaming_response() -> None:
    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 503, "headers": []})
        await send({"type": "http.response.body", "body": b"a", "more_body": True})
        await send({"type": "http.response.body", "body": b"b", "more_body": False})

    buffer = io.StringIO()
    middleware = AccessLogMiddleware(inner, stream=buffer)
    scope: Scope = {
        "type": "http",
        "method": "get",
        "path": "/api/stream",
        "headers": [(b"user-agent", b"agent-" + b"x" * 200)],
    }

    messages = asyncio.run(_drive(middleware, scope))

    # 三个消息原样、按序通过（中间件不聚合响应体）
    assert [message["type"] for message in messages] == [
        "http.response.start",
        "http.response.body",
        "http.response.body",
    ]

    record = json.loads(buffer.getvalue())
    assert record["status"] == 503
    assert record["level"] == "error"
    assert record["method"] == "GET"
    assert record["path"] == "/api/stream"
    assert record["actor_id"] is None
    assert len(record["user_agent"]) == 120
    assert isinstance(record["request_id"], str)
    assert record["request_id"]


def test_non_http_scope_is_not_logged() -> None:
    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "lifespan.startup.complete"})

    buffer = io.StringIO()
    middleware = AccessLogMiddleware(inner, stream=buffer)

    messages = asyncio.run(_drive(middleware, {"type": "lifespan"}))

    assert messages == [{"type": "lifespan.startup.complete"}]
    assert buffer.getvalue() == ""
