"""会话生命周期测试（AC-01/AC-04）。

覆盖：GET /auth/me、登出吊销 + 清 Cookie、过期会话 401、吊销会话 401、
管理员停用账号后该账号现有会话立即 401（AC-04）、TTL 来自 settings。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.service import hash_password
from app.models.auth_session import AuthSession
from app.models.user import Role, User
from app.settings import Settings

API = "/api"
PASSWORD = "correct-horse-123"
CSRF_HEADER = "X-CSRF-Token"


def make_user(
    db: Session,
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
    db.add(user)
    db.commit()
    return user


def login(client: TestClient, username: str) -> dict[str, Any]:
    response = client.post(
        f"{API}/auth/login",
        json={"username": username, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def csrf_token(client: TestClient) -> str:
    response = client.get(f"{API}/auth/me")
    assert response.status_code == 200, response.text
    token: str = response.json()["csrf_token"]
    return token


def test_me_requires_login(client: TestClient) -> None:
    response = client.get(f"{API}/auth/me")
    assert response.status_code == 401
    payload = response.json()
    assert payload["code"] == "AUTH_REQUIRED"
    assert set(payload) == {"code", "message", "request_id"}


def test_me_returns_user_and_csrf_after_login(client: TestClient, db_session: Session) -> None:
    user = make_user(db_session, "me-user")
    login(client, "me-user")

    response = client.get(f"{API}/auth/me")
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    assert body["user"]["id"] == user.id
    assert body["user"]["username"] == "me-user"
    assert body["user"]["role"] == "teacher"
    assert body["csrf_token"] == csrf_token(client)


def test_logout_revokes_session_and_clears_cookie(
    client: TestClient, db_session: Session
) -> None:
    make_user(db_session, "logout-user")
    login(client, "logout-user")
    token = csrf_token(client)

    response = client.post(f"{API}/auth/logout", headers={CSRF_HEADER: token})
    assert response.status_code == 204
    assert response.content == b""
    assert "session_id=" in (response.headers.get("set-cookie") or "").lower()

    db_session.expire_all()
    stored = db_session.scalar(select(AuthSession))
    assert stored is not None
    assert stored.revoked_at is not None

    assert client.get(f"{API}/auth/me").status_code == 401


def test_logout_without_session_is_401(client: TestClient) -> None:
    response = client.post(f"{API}/auth/logout", headers={CSRF_HEADER: "whatever"})
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_REQUIRED"


def test_expired_session_returns_401(client: TestClient, db_session: Session) -> None:
    make_user(db_session, "expired-user")
    login(client, "expired-user")

    db_session.expire_all()
    stored = db_session.scalar(select(AuthSession))
    assert stored is not None
    stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()

    response = client.get(f"{API}/auth/me")
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_REQUIRED"


def test_revoked_session_returns_401(client: TestClient, db_session: Session) -> None:
    make_user(db_session, "revoked-user")
    login(client, "revoked-user")

    db_session.expire_all()
    stored = db_session.scalar(select(AuthSession))
    assert stored is not None
    stored.revoked_at = datetime.now(UTC)
    db_session.commit()

    response = client.get(f"{API}/auth/me")
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_REQUIRED"


def test_session_ttl_comes_from_settings(
    client: TestClient, db_session: Session, settings: Settings
) -> None:
    make_user(db_session, "ttl-user")
    login(client, "ttl-user")

    db_session.expire_all()
    stored = db_session.scalar(select(AuthSession))
    assert stored is not None
    ttl_seconds = (stored.expires_at - stored.created_at).total_seconds()
    expected = settings.session_ttl_hours * 3600
    assert abs(ttl_seconds - expected) < 5


def test_deactivated_user_sessions_invalidated_immediately(
    app: FastAPI, db_session: Session
) -> None:
    """AC-04：管理员停用账号 → 该账号现有会话立即 401，且会话被吊销。"""
    teacher = make_user(db_session, "teacher-deact", role=Role.TEACHER.value)
    make_user(db_session, "admin-deact", role=Role.ADMIN.value)

    teacher_client = TestClient(app)
    admin_client = TestClient(app)
    try:
        login(teacher_client, "teacher-deact")
        login(admin_client, "admin-deact")
        admin_csrf = csrf_token(admin_client)

        response = admin_client.patch(
            f"{API}/users/{teacher.id}",
            json={"is_active": False},
            headers={CSRF_HEADER: admin_csrf},
        )
        assert response.status_code == 200, response.text
        assert response.json()["is_active"] is False

        # 现有会话立即失效
        assert teacher_client.get(f"{API}/auth/me").status_code == 401

        # 停用账号无法再次登录
        relogin = teacher_client.post(
            f"{API}/auth/login",
            json={"username": "teacher-deact", "password": PASSWORD},
        )
        assert relogin.status_code == 401
        assert relogin.json()["code"] == "AUTH_INVALID_CREDENTIALS"

        db_session.expire_all()
        sessions = list(
            db_session.scalars(
                select(AuthSession).where(AuthSession.user_id == teacher.id)
            ).all()
        )
        assert sessions
        assert all(item.revoked_at is not None for item in sessions)
    finally:
        teacher_client.close()
        admin_client.close()
