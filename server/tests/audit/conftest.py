"""audit 测试 fixture：账号、登录、CSRF。

与 `tests/reports/conftest.py` 同构的最小集合；审计接口只读，无需构造业务
实体（AC-22 的写操作直接调用 `POST /users`）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth.service import hash_password
from app.models.user import Role, User

API = "/api"
PASSWORD = "correct-horse-123"
CSRF_HEADER = "X-CSRF-Token"


@pytest.fixture()
def make_user(db_session: Session) -> Callable[..., User]:
    """创建并提交一个账号（默认教师）。"""

    def factory(
        username: str,
        *,
        role: str = Role.TEACHER.value,
        display_name: str | None = None,
        is_active: bool = True,
    ) -> User:
        user = User(
            username=username,
            display_name=display_name or username,
            role=role,
            password_hash=hash_password(PASSWORD),
            is_active=is_active,
        )
        db_session.add(user)
        db_session.commit()
        return user

    return factory


@pytest.fixture()
def login(client: TestClient) -> Callable[..., dict[str, Any]]:
    """登录并返回响应体（含 csrf_token）；Cookie 由 TestClient 保持。"""

    def factory(username: str, password: str = PASSWORD) -> dict[str, Any]:
        response = client.post(
            f"{API}/auth/login",
            json={"username": username, "password": password},
        )
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        return body

    return factory


@pytest.fixture()
def csrf_headers(client: TestClient) -> Callable[[], dict[str, str]]:
    """读取当前会话的 CSRF token 并构造请求头。"""

    def factory() -> dict[str, str]:
        response = client.get(f"{API}/auth/me")
        assert response.status_code == 200, response.text
        token: str = response.json()["csrf_token"]
        return {CSRF_HEADER: token}

    return factory


__all__ = ["API", "CSRF_HEADER", "PASSWORD"]
