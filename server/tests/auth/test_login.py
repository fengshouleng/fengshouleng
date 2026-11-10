"""登录接口测试（AC-01/AC-02/AC-22）。

覆盖：
- 登录成功：200 + {user, csrf_token} + HttpOnly/SameSite=Lax/Path=/ 的 session_id
- 数据库只存 sha256(session_id)，密码只存 argon2id 哈希
- 账号不存在与密码错误返回同一个 401 AUTH_INVALID_CREDENTIALS
- 停用账号不可登录；审计记录 auth.login / auth.login_failed 且不含明文密码
- Cookie Secure 由配置决定（Secure 逻辑单测）
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.service import cookie_secure, hash_password
from app.models.audit_log import AuditLog
from app.models.auth_session import AuthSession
from app.models.user import Role, User
from app.settings import Settings

API = "/api"
PASSWORD = "correct-horse-123"
CSRF_HEADER = "X-CSRF-Token"
USER_FIELDS = {"id", "username", "display_name", "role", "is_active", "created_at"}


def make_user(
    db: Session,
    username: str,
    *,
    role: str = Role.TEACHER.value,
    password: str = PASSWORD,
    is_active: bool = True,
) -> User:
    user = User(
        username=username,
        display_name=username,
        role=role,
        password_hash=hash_password(password),
        is_active=is_active,
    )
    db.add(user)
    db.commit()
    return user


def test_login_success_payload_cookie_and_storage(client: TestClient, db_session: Session) -> None:
    user = make_user(db_session, "teacher-login")

    response = client.post(
        f"{API}/auth/login",
        json={"username": "teacher-login", "password": PASSWORD},
    )
    assert response.status_code == 200

    body: dict[str, Any] = response.json()
    assert set(body) == {"user", "csrf_token"}
    assert set(body["user"]) == USER_FIELDS
    assert body["user"]["id"] == user.id
    assert body["user"]["username"] == "teacher-login"
    assert body["user"]["display_name"] == "teacher-login"
    assert body["user"]["role"] == "teacher"
    assert body["user"]["is_active"] is True
    assert isinstance(body["user"]["created_at"], str)
    assert isinstance(body["csrf_token"], str) and body["csrf_token"]

    set_cookie = (response.headers.get("set-cookie") or "").lower()
    assert "session_id=" in set_cookie
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "path=/" in set_cookie

    token = client.cookies.get("session_id")
    assert token

    db_session.expire_all()
    stored = db_session.scalar(select(AuthSession))
    assert stored is not None
    assert stored.token_hash != token
    assert len(stored.token_hash) == 64
    assert stored.csrf_token == body["csrf_token"]
    assert stored.revoked_at is None
    assert stored.expires_at is not None

    audit_row = db_session.scalar(select(AuditLog).where(AuditLog.action == "auth.login"))
    assert audit_row is not None
    assert audit_row.actor_id == user.id
    assert audit_row.target_type == "user"
    assert audit_row.target_id == str(user.id)
    assert PASSWORD not in str(audit_row.detail)


def test_password_is_stored_as_argon2id_hash(client: TestClient, db_session: Session) -> None:
    make_user(db_session, "hash-user")
    db_session.expire_all()
    stored = db_session.scalar(select(User).where(User.username == "hash-user"))
    assert stored is not None
    assert stored.password_hash.startswith("$argon2id$")
    assert PASSWORD not in stored.password_hash


def test_wrong_password_and_unknown_user_are_indistinguishable(
    client: TestClient, db_session: Session
) -> None:
    make_user(db_session, "known-user")

    wrong = client.post(
        f"{API}/auth/login",
        json={"username": "known-user", "password": "definitely-wrong"},
    )
    unknown = client.post(
        f"{API}/auth/login",
        json={"username": "ghost-user", "password": "definitely-wrong"},
    )

    assert wrong.status_code == 401
    assert unknown.status_code == 401
    assert wrong.json()["code"] == unknown.json()["code"] == "AUTH_INVALID_CREDENTIALS"
    assert wrong.json()["message"] == unknown.json()["message"]
    assert "user" not in wrong.json()

    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(AuthSession)) == 0

    failures = list(
        db_session.scalars(
            select(AuditLog).where(AuditLog.action == "auth.login_failed")
        ).all()
    )
    assert len(failures) == 2
    for entry in failures:
        text = str(entry.detail)
        assert "definitely-wrong" not in text
        assert "password" not in text.lower()
        assert "token" not in text.lower()


def test_inactive_user_cannot_login(client: TestClient, db_session: Session) -> None:
    make_user(db_session, "inactive-user", is_active=False)

    response = client.post(
        f"{API}/auth/login",
        json={"username": "inactive-user", "password": PASSWORD},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_INVALID_CREDENTIALS"

    db_session.expire_all()
    audit_row = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "auth.login_failed")
    )
    assert audit_row is not None
    assert audit_row.detail is not None
    assert audit_row.detail.get("reason") == "inactive"
    assert audit_row.detail.get("username") == "inactive-user"


def test_login_payload_validation(client: TestClient) -> None:
    missing_password = client.post(f"{API}/auth/login", json={"username": "only-username"})
    assert missing_password.status_code == 422
    assert missing_password.json()["code"] == "VALIDATION_ERROR"

    too_long = client.post(
        f"{API}/auth/login",
        json={"username": "x" * 65, "password": PASSWORD},
    )
    assert too_long.status_code == 422


def test_password_change_after_reset_still_verifies() -> None:
    """hash/verify 往返（argon2id 参数变化后 verify 仍按哈希内参数校验）。"""
    from app.auth.service import verify_password

    hashed = hash_password(PASSWORD)
    assert verify_password(hashed, PASSWORD) is True
    assert verify_password(hashed, "another-password") is False
    assert verify_password("not-a-hash", PASSWORD) is False


def test_cookie_secure_flag_follows_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SESSION_COOKIE_SECURE", raising=False)
    assert cookie_secure(Settings(app_env="test", database_url="sqlite://")) is False
    assert cookie_secure(Settings(app_env="prod", database_url="sqlite://")) is True
    assert cookie_secure(Settings(app_env="staging", database_url="sqlite://")) is True

    monkeypatch.setenv("SESSION_COOKIE_SECURE", "true")
    assert cookie_secure(Settings(app_env="local", database_url="sqlite://")) is True
    # test 环境始终关闭 Secure（测试走 http）
    assert cookie_secure(Settings(app_env="test", database_url="sqlite://")) is False

    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    assert cookie_secure(Settings(app_env="prod", database_url="sqlite://")) is False
