"""认证依赖：解析会话、暴露当前用户、角色校验。

TASK-BE-001 的会话解析用 FastAPI 依赖实现（`app/main.py` 属 TASK-BE-000 产物，
本卡不修改它）：依赖在进入路由前执行，等价于会话中间件，并把
`request.state.user` / `request.state.auth_session` / `request.state.auth_context`
注入给后续代码复用。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app import errors
from app.auth import service
from app.db import get_db
from app.models.auth_session import AuthSession
from app.models.user import User

_AUTH_CONTEXT_ATTR = "auth_context"


@dataclass(frozen=True, slots=True)
class AuthContext:
    """已通过校验的会话主体。"""

    user: User
    session: AuthSession


def get_auth_context(request: Request, db: Session = Depends(get_db)) -> AuthContext | None:
    """从 Cookie 解析会话并校验过期/吊销/停用；未登录返回 None（不抛错）。"""
    cached = getattr(request.state, _AUTH_CONTEXT_ATTR, None)
    if isinstance(cached, AuthContext):
        return cached
    token = request.cookies.get(service.SESSION_COOKIE_NAME)
    if not token:
        return None
    resolved = service.get_active_session(db, token)
    if resolved is None:
        return None
    session, user = resolved
    context = AuthContext(user=user, session=session)
    state: Any = request.state
    state.auth_context = context
    state.user = user
    state.auth_session = session
    return context


def get_current_auth(context: AuthContext | None = Depends(get_auth_context)) -> AuthContext:
    """要求已登录；否则 401 AUTH_REQUIRED。"""
    if context is None:
        raise errors.auth_required()
    return context


def get_current_user(context: AuthContext = Depends(get_current_auth)) -> User:
    """当前登录用户（停用账号再次兜底拒绝）。"""
    if not context.user.is_active:
        raise errors.auth_required()
    return context.user


def get_current_session(context: AuthContext = Depends(get_current_auth)) -> AuthSession:
    """当前会话记录（登出等场景需要 csrf_token / id）。"""
    return context.session


def require_role(*roles: str) -> Callable[..., User]:
    """生成角色依赖，例如 `Depends(require_role("admin"))`：命中返回用户，否则 403。"""
    allowed = frozenset(roles)

    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise errors.forbidden()
        return user

    return dependency


__all__ = [
    "AuthContext",
    "get_auth_context",
    "get_current_auth",
    "get_current_session",
    "get_current_user",
    "require_role",
]
