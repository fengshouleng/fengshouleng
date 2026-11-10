"""CSRF 双提交校验（docs/12-permission-matrix.md §3）。

规则：非 GET/HEAD/OPTIONS 请求必须携带 `X-CSRF-Token`，且与**当前会话**的
`csrf_token` 一致；缺失或不匹配 → 403 `CSRF_INVALID`。

- 登录接口（`POST /auth/login`）本身豁免：此时尚无会话；
- 未携带任何会话的写请求先返回 401 `AUTH_REQUIRED`（认证先于 CSRF）；
- 本依赖挂在 `app/api/router.py` 的聚合路由器上，后续任务卡新增的写接口自动受保护。
"""

from __future__ import annotations

import hmac

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app import errors
from app.auth import dependencies, service
from app.db import get_db
from app.errors import AppError

SAFE_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})
_LOGIN_PATH_SUFFIX = "/auth/login"
CSRF_FAILED_MESSAGE = "CSRF 校验失败"


def validate_csrf(header_value: str | None, expected_token: str) -> bool:
    """常量时间比较请求头与会话绑定的 CSRF token。"""
    if not header_value:
        return False
    return hmac.compare_digest(header_value, expected_token)


def is_csrf_exempt(request: Request) -> bool:
    """安全方法或登录接口豁免 CSRF。"""
    if request.method.upper() in SAFE_METHODS:
        return True
    return request.url.path.rstrip("/").endswith(_LOGIN_PATH_SUFFIX)


def enforce_csrf(request: Request, db: Session = Depends(get_db)) -> None:
    """FastAPI 依赖：校验写请求的 X-CSRF-Token。"""
    if is_csrf_exempt(request):
        return
    context = dependencies.get_auth_context(request, db)
    if context is None:
        raise errors.auth_required()
    header_value = request.headers.get(service.CSRF_HEADER_NAME)
    if not validate_csrf(header_value, context.session.csrf_token):
        raise AppError(errors.CSRF_INVALID, CSRF_FAILED_MESSAGE, 403)


__all__ = [
    "SAFE_METHODS",
    "enforce_csrf",
    "is_csrf_exempt",
    "validate_csrf",
]
