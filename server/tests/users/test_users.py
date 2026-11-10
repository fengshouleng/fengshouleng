"""用户管理接口测试（AC-03/AC-04/AC-22）。

覆盖：
- GET /users 分页结构 items/total/page/page_size、角色过滤、默认分页参数
- 仅 admin：学生/教师访问一律 403 且不泄露用户数据
- POST /users 创建（201、密码 8-128、用户名唯一 409、响应不含密码）
- PATCH /users/{id} 修改角色/显示名/密码/启停用（停用吊销会话）
- 审计：user.create / user.update 记录 actor 且不泄露密码/token
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.service import hash_password
from app.models.audit_log import AuditLog
from app.models.auth_session import AuthSession
from app.models.user import Role, User

API = "/api"
PASSWORD = "correct-horse-123"
CSRF_HEADER = "X-CSRF-Token"
USER_FIELDS = {"id", "username", "display_name", "role", "is_active", "created_at"}

NEW_USER: dict[str, Any] = {
    "username": "created-user",
    "display_name": "Created User",
    "role": "student",
    "password": "created-pass-123",
}


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


def login(client: TestClient, username: str, password: str = PASSWORD) -> dict[str, Any]:
    response = client.post(
        f"{API}/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def csrf_token(client: TestClient) -> str:
    response = client.get(f"{API}/auth/me")
    assert response.status_code == 200, response.text
    token: str = response.json()["csrf_token"]
    return token


def csrf_headers(client: TestClient) -> dict[str, str]:
    return {CSRF_HEADER: csrf_token(client)}


def test_list_users_page_schema(client: TestClient, db_session: Session) -> None:
    admin = make_user(db_session, "admin-list", role=Role.ADMIN.value)
    make_user(db_session, "teacher-a", role=Role.TEACHER.value)
    make_user(db_session, "teacher-b", role=Role.TEACHER.value)
    login(client, admin.username)

    response = client.get(f"{API}/users")
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    assert set(body) == {"items", "total", "page", "page_size"}
    assert body["page"] == 1
    assert body["page_size"] == 20
    assert body["total"] == 3
    assert len(body["items"]) == 3
    for item in body["items"]:
        assert set(item) == USER_FIELDS
    usernames = {item["username"] for item in body["items"]}
    assert usernames == {"admin-list", "teacher-a", "teacher-b"}
    assert "password_hash" not in response.text
    assert PASSWORD not in response.text


def test_list_users_pagination_and_role_filter(client: TestClient, db_session: Session) -> None:
    make_user(db_session, "admin-page", role=Role.ADMIN.value)
    for name in ("t1", "t2", "t3"):
        make_user(db_session, name, role=Role.TEACHER.value)
    for name in ("s1", "s2"):
        make_user(db_session, name, role=Role.STUDENT.value)
    login(client, "admin-page")

    response = client.get(
        f"{API}/users",
        params={"role": "teacher", "page": 2, "page_size": 2},
    )
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    assert body["total"] == 3
    assert body["page"] == 2
    assert body["page_size"] == 2
    assert [item["username"] for item in body["items"]] == ["t3"]
    assert all(item["role"] == "teacher" for item in body["items"])


def test_student_cannot_list_users(client: TestClient, db_session: Session) -> None:
    make_user(db_session, "student-list", role=Role.STUDENT.value)
    login(client, "student-list")

    response = client.get(f"{API}/users")
    assert response.status_code == 403
    payload = response.json()
    assert payload["code"] == "FORBIDDEN"
    assert set(payload) == {"code", "message", "request_id"}
    assert "items" not in payload
    assert "password_hash" not in response.text


def test_teacher_cannot_list_users(client: TestClient, db_session: Session) -> None:
    make_user(db_session, "teacher-list", role=Role.TEACHER.value)
    login(client, "teacher-list")

    response = client.get(f"{API}/users")
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


def test_admin_creates_user(client: TestClient, db_session: Session) -> None:
    admin = make_user(db_session, "admin-create", role=Role.ADMIN.value)
    login(client, admin.username)

    response = client.post(
        f"{API}/users",
        json=NEW_USER,
        headers=csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    assert set(body) == USER_FIELDS
    assert body["username"] == NEW_USER["username"]
    assert body["display_name"] == NEW_USER["display_name"]
    assert body["role"] == "student"
    assert body["is_active"] is True
    assert "password" not in response.text.lower()

    db_session.expire_all()
    created = db_session.scalar(select(User).where(User.username == NEW_USER["username"]))
    assert created is not None
    assert created.password_hash.startswith("$argon2id$")
    assert NEW_USER["password"] not in created.password_hash

    audit_row = db_session.scalar(select(AuditLog).where(AuditLog.action == "user.create"))
    assert audit_row is not None
    assert audit_row.actor_id == admin.id
    assert audit_row.target_type == "user"
    assert audit_row.target_id == str(created.id)
    assert NEW_USER["password"] not in str(audit_row.detail)


def test_create_user_duplicate_username_conflict(
    client: TestClient, db_session: Session
) -> None:
    make_user(db_session, "admin-dup", role=Role.ADMIN.value)
    make_user(db_session, "taken-name", role=Role.TEACHER.value)
    login(client, "admin-dup")

    response = client.post(
        f"{API}/users",
        json={**NEW_USER, "username": "taken-name"},
        headers=csrf_headers(client),
    )
    assert response.status_code == 409
    assert response.json()["code"] == "CONFLICT"


def test_create_user_validation(client: TestClient, db_session: Session) -> None:
    make_user(db_session, "admin-valid", role=Role.ADMIN.value)
    login(client, "admin-valid")

    short_password = client.post(
        f"{API}/users",
        json={**NEW_USER, "password": "short"},
        headers=csrf_headers(client),
    )
    assert short_password.status_code == 422
    assert short_password.json()["code"] == "VALIDATION_ERROR"

    bad_role = client.post(
        f"{API}/users",
        json={**NEW_USER, "role": "superuser"},
        headers=csrf_headers(client),
    )
    assert bad_role.status_code == 422


def test_student_cannot_create_user(client: TestClient, db_session: Session) -> None:
    make_user(db_session, "student-create", role=Role.STUDENT.value)
    login(client, "student-create")

    response = client.post(
        f"{API}/users",
        json=NEW_USER,
        headers=csrf_headers(client),
    )
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


def test_admin_updates_user_role_and_display_name(
    client: TestClient, db_session: Session
) -> None:
    admin = make_user(db_session, "admin-update", role=Role.ADMIN.value)
    target = make_user(db_session, "target-update", role=Role.STUDENT.value)
    login(client, admin.username)

    response = client.patch(
        f"{API}/users/{target.id}",
        json={"role": "teacher", "display_name": "Updated Name"},
        headers=csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    assert body["id"] == target.id
    assert body["role"] == "teacher"
    assert body["display_name"] == "Updated Name"

    db_session.expire_all()
    refreshed = db_session.get(User, target.id)
    assert refreshed is not None
    assert refreshed.role == "teacher"
    assert refreshed.display_name == "Updated Name"

    audit_row = db_session.scalar(select(AuditLog).where(AuditLog.action == "user.update"))
    assert audit_row is not None
    assert audit_row.actor_id == admin.id
    assert audit_row.target_id == str(target.id)


def test_update_user_not_found(client: TestClient, db_session: Session) -> None:
    make_user(db_session, "admin-404", role=Role.ADMIN.value)
    login(client, "admin-404")

    response = client.patch(
        f"{API}/users/999999",
        json={"display_name": "Nobody"},
        headers=csrf_headers(client),
    )
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


def test_student_cannot_patch_self_role(client: TestClient, db_session: Session) -> None:
    student = make_user(db_session, "student-patch", role=Role.STUDENT.value)
    login(client, student.username)

    response = client.patch(
        f"{API}/users/{student.id}",
        json={"role": "admin"},
        headers=csrf_headers(client),
    )
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"

    db_session.expire_all()
    refreshed = db_session.get(User, student.id)
    assert refreshed is not None
    assert refreshed.role == "student"


def test_password_reset_revokes_sessions_and_changes_login(
    app: FastAPI, db_session: Session
) -> None:
    admin = make_user(db_session, "admin-reset", role=Role.ADMIN.value)
    target = make_user(db_session, "target-reset", role=Role.STUDENT.value)

    admin_client = TestClient(app)
    target_client = TestClient(app)
    try:
        login(admin_client, admin.username)
        login(target_client, target.username)
        assert target_client.get(f"{API}/auth/me").status_code == 200

        response = admin_client.patch(
            f"{API}/users/{target.id}",
            json={"password": "brand-new-pass-456"},
            headers={CSRF_HEADER: csrf_token(admin_client)},
        )
        assert response.status_code == 200, response.text

        # 旧会话被吊销，旧密码失效
        assert target_client.get(f"{API}/auth/me").status_code == 401
        old = target_client.post(
            f"{API}/auth/login",
            json={"username": target.username, "password": PASSWORD},
        )
        assert old.status_code == 401

        # 新密码可登录
        new = target_client.post(
            f"{API}/auth/login",
            json={"username": target.username, "password": "brand-new-pass-456"},
        )
        assert new.status_code == 200

        db_session.expire_all()
        sessions = list(
            db_session.scalars(
                select(AuthSession).where(AuthSession.user_id == target.id)
            ).all()
        )
        assert sessions
        assert any(item.revoked_at is not None for item in sessions)

        audit_row = db_session.scalar(
            select(AuditLog).where(AuditLog.action == "user.update")
        )
        assert audit_row is not None
        detail = str(audit_row.detail)
        assert "brand-new-pass-456" not in detail
        assert "password" not in detail.lower()
        assert "token" not in detail.lower()
    finally:
        admin_client.close()
        target_client.close()


def test_disable_user_via_patch_revokes_sessions(
    app: FastAPI, db_session: Session
) -> None:
    admin = make_user(db_session, "admin-disable", role=Role.ADMIN.value)
    target = make_user(db_session, "target-disable", role=Role.TEACHER.value)

    admin_client = TestClient(app)
    target_client = TestClient(app)
    try:
        login(admin_client, admin.username)
        login(target_client, target.username)

        response = admin_client.patch(
            f"{API}/users/{target.id}",
            json={"is_active": False},
            headers={CSRF_HEADER: csrf_token(admin_client)},
        )
        assert response.status_code == 200
        assert response.json()["is_active"] is False
        assert target_client.get(f"{API}/auth/me").status_code == 401

        db_session.expire_all()
        sessions = list(
            db_session.scalars(
                select(AuthSession).where(AuthSession.user_id == target.id)
            ).all()
        )
        assert sessions
        assert all(item.revoked_at is not None for item in sessions)
    finally:
        admin_client.close()
        target_client.close()
