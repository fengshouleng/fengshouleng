"""middleware 测试 fixture：stdout 捕获、账号与登录辅助。

访问日志中间件在**写日志时**解析 `sys.stdout`，因此这里用 pytest 自带的
`capsys` 捕获 stdout（测试过程中 pytest 的全局捕获会接管 `sys.stdout`，
用 monkeypatch 直接替换会在捕获初始化时被覆盖）。`_StdoutCapture` 会累积
多次读取的内容，保证 `access_text()` 与 `access_lines()` 先后调用都看到全文。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth.service import hash_password
from app.models.user import Role, User

API = "/api"
PASSWORD = "correct-horse-123"


class _StdoutCapture:
    """累积 pytest 捕获到的 stdout，供 `access_text()` / `access_lines()` 复用。"""

    def __init__(self, capsys: pytest.CaptureFixture[str]) -> None:
        self._capsys = capsys
        self._buffer = ""

    def getvalue(self) -> str:
        self._buffer += self._capsys.readouterr().out
        return self._buffer


@pytest.fixture()
def stdout_buffer(capsys: pytest.CaptureFixture[str]) -> _StdoutCapture:
    """返回写日志后可变现的 stdout 累积器。"""
    return _StdoutCapture(capsys)


@pytest.fixture()
def access_lines(
    stdout_buffer: _StdoutCapture,
) -> Callable[[], list[dict[str, Any]]]:
    """返回“解析缓冲里所有访问日志行”的函数（非 JSON / 非 access 行忽略）。"""

    def read() -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for raw in stdout_buffer.getvalue().splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and parsed.get("event") == "access":
                entries.append(parsed)
        return entries

    return read


@pytest.fixture()
def access_text(stdout_buffer: _StdoutCapture) -> Callable[[], str]:
    """返回“缓冲的原始文本”，用于断言敏感值没有出现。"""

    def read() -> str:
        return stdout_buffer.getvalue()

    return read


@pytest.fixture()
def make_user(db_session: Session) -> Callable[..., User]:
    """创建并提交一个账号（默认教师）。"""

    def factory(
        username: str,
        *,
        role: str = Role.TEACHER.value,
        is_active: bool = True,
    ) -> User:
        user = User(
            username=username,
            display_name=username,
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


__all__ = ["API", "PASSWORD"]
