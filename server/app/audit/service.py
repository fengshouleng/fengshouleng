"""审计写入、查询与脱敏的统一入口（TASK-BE-006）。

约定：
- 与业务写操作在**同一事务**提交（docs/11-data-model.md §4.7），
  由调用方在写入后 `commit()`，审计失败则整体回滚；
- `detail` 落库前脱敏：键名含 password/token/secret/authorization/cookie/answer
  的字段直接丢弃，字符串截断到 255；脱敏在写入入口统一执行，调用方不得绕过；
- 明文密码、服务端 session token、正确答案永不出现在审计中；
- 查询入口 `list_audit_logs`：默认只返回最近 `DEFAULT_RETENTION_DAYS` 天，
  `from`/`to` 为闭区间，按 `created_at` 降序、`id` 降序稳定分页；
- 过期记录由 `python -m app.audit.cleanup` 删除（保留 180 天）。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog

# 动作名（契约与 docs/12 约定的稳定字符串）
AUTH_LOGIN = "auth.login"
AUTH_LOGIN_FAILED = "auth.login_failed"
AUTH_LOGOUT = "auth.logout"
USER_CREATE = "user.create"
USER_UPDATE = "user.update"
#: 越权尝试（RBAC/归属 403）留痕，字段见 docs/12 §4；2026-11-11 起必做
PERMISSION_DENIED = "permission.denied"

#: 审计保留天数（docs/11-data-model.md#audit_logs）
DEFAULT_RETENTION_DAYS = 180

#: 键名命中任一子串即整键丢弃（大小写不敏感）；`answer` 防止正确答案明文落库
_SENSITIVE_KEY_PARTS: tuple[str, ...] = (
    "password",
    "token",
    "secret",
    "authorization",
    "cookie",
    "answer",
)
_MAX_DETAIL_DEPTH = 4
_MAX_STRING_LENGTH = 255


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def _sanitize_value(value: Any, depth: int) -> Any:
    if depth > _MAX_DETAIL_DEPTH:
        return "[truncated]"
    if isinstance(value, Mapping):
        return _sanitize_mapping(value, depth)
    if isinstance(value, list | tuple):
        return [_sanitize_value(item, depth + 1) for item in value]
    if isinstance(value, str):
        return value[:_MAX_STRING_LENGTH]
    return value


def _sanitize_mapping(mapping: Mapping[Any, Any], depth: int) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in mapping.items():
        text_key = str(key)
        if _is_sensitive_key(text_key):
            continue
        cleaned[text_key] = _sanitize_value(value, depth + 1)
    return cleaned


def sanitize_detail(detail: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """对审计 detail 做递归脱敏（敏感键名整键丢弃，其余字符串截断）。"""
    if detail is None:
        return None
    return _sanitize_mapping(detail, 0)


def record(
    db: Session,
    *,
    action: str,
    actor_id: int | None = None,
    target_type: str | None = None,
    target_id: str | int | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    detail: Mapping[str, Any] | None = None,
) -> AuditLog:
    """把一条审计记录加入当前事务（调用方负责 commit）。"""
    entry = AuditLog(
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=None if target_id is None else str(target_id)[:64],
        ip=(ip or "-")[:45],
        user_agent=None if user_agent is None else user_agent[:255],
        detail=sanitize_detail(detail),
        created_at=datetime.now(UTC),
    )
    db.add(entry)
    return entry


def _as_utc(value: datetime) -> datetime:
    """查询参数可能是无时区的本地时间；统一按 UTC 比较（SQLite 不保存时区）。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def list_audit_logs(
    db: Session,
    *,
    action: str | None = None,
    actor_id: int | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    page: int = 1,
    page_size: int = 20,
    now: datetime | None = None,
) -> tuple[list[AuditLog], int]:
    """分页查询审计记录：默认最近 180 天，`start`/`end` 闭区间，时间倒序。

    返回 `(items, total)`；`page`/`page_size` 由路由层先做 FastAPI 校验。
    `start` 缺省为 `end - 180 天`，`end` 缺省为当前时间，因此两者都缺省时
    恰好是「最近 180 天」。显式传入 `start` 可查询更早的历史（契约允许）。
    """
    moment = _as_utc(now) if now is not None else datetime.now(UTC)
    end_at = _as_utc(end) if end is not None else moment
    start_at = (
        _as_utc(start)
        if start is not None
        else end_at - timedelta(days=DEFAULT_RETENTION_DAYS)
    )

    conditions = [
        AuditLog.created_at >= start_at,
        AuditLog.created_at <= end_at,
    ]
    if action is not None:
        conditions.append(AuditLog.action == action)
    if actor_id is not None:
        conditions.append(AuditLog.actor_id == actor_id)

    total = int(
        db.scalar(select(func.count()).select_from(AuditLog).where(*conditions)) or 0
    )
    statement = (
        select(AuditLog)
        .where(*conditions)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(db.scalars(statement).all()), total


def request_ip(request: Request) -> str | None:
    """请求来源 IP（反向代理场景由 Nginx 注入 X-Forwarded-For，后续任务再解析）。"""
    client = request.client
    return None if client is None else client.host


def request_user_agent(request: Request) -> str | None:
    agent = request.headers.get("user-agent")
    return None if agent is None else agent[:255]


__all__ = [
    "AUTH_LOGIN",
    "AUTH_LOGIN_FAILED",
    "AUTH_LOGOUT",
    "DEFAULT_RETENTION_DAYS",
    "PERMISSION_DENIED",
    "USER_CREATE",
    "USER_UPDATE",
    "list_audit_logs",
    "record",
    "request_ip",
    "request_user_agent",
    "sanitize_detail",
]
