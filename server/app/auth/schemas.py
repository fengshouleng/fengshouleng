"""认证相关 Pydantic 模型（与 contracts/openapi.yaml 一一对应）。

- `LoginRequest`：POST /auth/login 请求体
- `LoginResponse` / `MeResponse`：`{user, csrf_token}`
- `UserOut` 复用 users 模块的 User schema，保证 /auth/* 与 /users 的 User 结构一致
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.users.schemas import UserOut


class LoginRequest(BaseModel):
    """登录请求体（LoginRequest）。"""

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class LoginResponse(BaseModel):
    """登录成功响应（LoginResponse）。"""

    user: UserOut
    csrf_token: str


class MeResponse(BaseModel):
    """当前登录用户响应（MeResponse，结构与 LoginResponse 一致）。"""

    user: UserOut
    csrf_token: str


__all__ = ["LoginRequest", "LoginResponse", "MeResponse"]
