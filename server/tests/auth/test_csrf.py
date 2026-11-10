"""CSRF 双提交校验测试（docs/12-permission-matrix.md §3）。

覆盖：写请求缺失/错误 X-CSRF-Token → 403 CSRF_INVALID；正确 token 放行；
token 与会话绑定（A 的 token 不能用于 B）；登出同样受 CSRF 保护；GET 不需要。
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.service import hash_password
from app.models.auth_session import AuthSession
from app.models.user import Role, User

API = "/api"
PASSWORD = "correct-horse-123"
CSRF_HEADER = "X-CSRF-Token"
NEW_USER: dict[str, Any] = {
    "username": "created-by-admin",
    "display_name": "Created By Admin",
    "role": "student",
    "password": "created-pass-123",
}


def make_user(db: Session, username: str, *, role: str = Role.ADMIN.value) -> User:
    user = User(
        username=username,
        display_name=username,
        role=role,
        password_hash=hash_password(PASSWORD),
        is_active=True,
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


def test_write_without_csrf_is_403(client: TestClient, db_session: Session) -> None:
    make_user(db_session, "admin-csrf")
    login(client, "admin-csrf")

    response = client.post(f"{API}/users", json=NEW_USER)
    assert response.status_code == 403
    payload = response.json()
    assert payload["code"] == "CSRF_INVALID"
    assert set(payload) == {"code", "message", "request_id"}


def test_write_with_wrong_csrf_is_403(client: TestClient, db_session: Session) -> None:
    make_user(db_session, "admin-wrong-csrf")
    login(client, "admin-wrong-csrf")

    response = client.post(
        f"{API}/users",
        json=NEW_USER,
        headers={CSRF_HEADER: "not-the-real-token"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_INVALID"


def test_write_with_valid_csrf_succeeds(client: TestClient, db_session: Session) -> None:
    make_user(db_session, "admin-valid-csrf")
    login(client, "admin-valid-csrf")

    response = client.post(
        f"{API}/users",
        json=NEW_USER,
        headers={CSRF_HEADER: csrf_token(client)},
    )
    assert response.status_code == 201, response.text
    assert response.json()["username"] == NEW_USER["username"]


def test_csrf_token_is_bound_to_session(app: FastAPI, db_session: Session) -> None:
    make_user(db_session, "admin-bound")
    first = TestClient(app)
    second = TestClient(app)
    try:
        login(first, "admin-bound")
        login(second, "admin-bound")
        first_token = csrf_token(first)

        crossing = second.post(
            f"{API}/users",
            json=NEW_USER,
            headers={CSRF_HEADER: first_token},
        )
        assert crossing.status_code == 403
        assert crossing.json()["code"] == "CSRF_INVALID"

        own = second.post(
            f"{API}/users",
            json=NEW_USER,
            headers={CSRF_HEADER: csrf_token(second)},
        )
        assert own.status_code == 201, own.text
    finally:
        first.close()
        second.close()


def test_logout_requires_csrf(client: TestClient, db_session: Session) -> None:
    make_user(db_session, "logout-csrf", role=Role.TEACHER.value)
    login(client, "logout-csrf")

    missing = client.post(f"{API}/auth/logout")
    assert missing.status_code == 403
    assert missing.json()["code"] == "CSRF_INVALID"

    wrong = client.post(f"{API}/auth/logout", headers={CSRF_HEADER: "bad-token"})
    assert wrong.status_code == 403
    assert wrong.json()["code"] == "CSRF_INVALID"

    # 会话仍然有效
    assert client.get(f"{API}/auth/me").status_code == 200

    valid = client.post(f"{API}/auth/logout", headers={CSRF_HEADER: csrf_token(client)})
    assert valid.status_code == 204

    db_session.expire_all()
    stored = db_session.scalar(select(AuthSession))
    assert stored is not None
    assert stored.revoked_at is not None


def test_safe_methods_do_not_require_csrf(client: TestClient, db_session: Session) -> None:
    make_user(db_session, "admin-safe")
    login(client, "admin-safe")

    assert client.get(f"{API}/users").status_code == 200
    assert client.get(f"{API}/auth/me").status_code == 200


def test_login_does_not_require_csrf(client: TestClient, db_session: Session) -> None:
    make_user(db_session, "login-exempt", role=Role.TEACHER.value)
    body = login(client, "login-exempt")
    assert body["csrf_token"]
