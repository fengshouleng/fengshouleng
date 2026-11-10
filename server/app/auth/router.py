"""认证路由：POST /auth/login、POST /auth/logout、GET /auth/me。

契约：contracts/openapi.yaml#/paths/~1auth~1login 等。
- 登录成功下发 HttpOnly Cookie `session_id`（8h，Secure 由配置决定）
- 登出吊销会话并清 Cookie，返回 204
- `/auth/me` 返回当前用户与会话绑定的 CSRF token
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.auth import service
from app.auth.csrf import enforce_csrf
from app.auth.dependencies import AuthContext, get_current_auth
from app.auth.schemas import LoginRequest, LoginResponse, MeResponse
from app.db import get_db
from app.settings import Settings, get_settings
from app.users.schemas import UserOut

router = APIRouter(
    prefix="/auth",
    tags=["auth"],
    dependencies=[Depends(enforce_csrf)],
)


def _settings(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    return settings if isinstance(settings, Settings) else get_settings()


@router.post(
    "/login",
    response_model=LoginResponse,
    operation_id="login",
    summary="登录",
)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> LoginResponse:
    settings = _settings(request)
    user, session, token = service.login(
        db,
        username=payload.username,
        password=payload.password,
        settings=settings,
        ip=audit.request_ip(request),
        user_agent=audit.request_user_agent(request),
    )
    max_age = int(service.session_ttl(settings).total_seconds())
    response.set_cookie(
        key=service.SESSION_COOKIE_NAME,
        value=token,
        max_age=max_age,
        path="/",
        httponly=True,
        secure=service.cookie_secure(settings),
        samesite="lax",
    )
    return LoginResponse(user=UserOut.model_validate(user), csrf_token=session.csrf_token)


@router.post(
    "/logout",
    status_code=204,
    response_class=Response,
    operation_id="logout",
    summary="登出并吊销会话",
)
def logout(
    request: Request,
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> Response:
    service.logout(
        db,
        context.session,
        actor_id=context.user.id,
        ip=audit.request_ip(request),
        user_agent=audit.request_user_agent(request),
    )
    result = Response(status_code=204)
    result.delete_cookie(service.SESSION_COOKIE_NAME, path="/")
    return result


@router.get(
    "/me",
    response_model=MeResponse,
    operation_id="me",
    summary="当前登录用户与 CSRF token",
)
def me(context: AuthContext = Depends(get_current_auth)) -> MeResponse:
    return MeResponse(
        user=UserOut.model_validate(context.user),
        csrf_token=context.session.csrf_token,
    )


__all__ = ["router"]
