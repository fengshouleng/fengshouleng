"""`GET /audit-logs`：AC-21 权限、AC-22 字段完整、分页 / 过滤 / 默认 180 天窗口。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.user import Role, User

API = "/api"
_ERROR_KEYS = {"code", "message", "request_id"}


def _seed(
    db: Session,
    *,
    action: str,
    actor_id: int | None = None,
    created_at: datetime | None = None,
    target_type: str | None = "test",
    target_id: str | None = "1",
    ip: str | None = "127.0.0.1",
    detail: dict[str, Any] | None = None,
) -> AuditLog:
    row = AuditLog(
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        ip=ip,
        user_agent="pytest",
        detail=detail,
        created_at=created_at or datetime.now(UTC),
    )
    db.add(row)
    db.commit()
    return row


def _resolve_ref(schema: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    name = str(node["$ref"]).rsplit("/", 1)[-1]
    return schema["components"]["schemas"][name]


def test_openapi_contract(app: FastAPI) -> None:
    """GET /audit-logs 的 OpenAPI 参数与响应结构对齐契约。"""
    schema = app.openapi()
    operation = schema["paths"]["/api/audit-logs"]["get"]
    assert operation["operationId"] == "listAuditLogs"

    parameters = {item["name"]: item for item in operation["parameters"]}
    assert {"action", "actor_id", "from", "to", "page", "page_size"} <= set(parameters)
    assert parameters["page"]["schema"]["minimum"] == 1
    assert parameters["page_size"]["schema"]["maximum"] == 100

    response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    page = _resolve_ref(schema, response_schema)
    assert set(page["required"]) == {"items", "total", "page", "page_size"}
    assert set(page["properties"]) == {"items", "total", "page", "page_size"}

    audit_log = _resolve_ref(schema, page["properties"]["items"]["items"])
    assert set(audit_log["properties"]) == {
        "id",
        "actor_id",
        "action",
        "target_type",
        "target_id",
        "ip",
        "user_agent",
        "detail",
        "created_at",
    }


def test_audit_logs_admin_only_ac21(
    client: TestClient,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
) -> None:
    teacher = make_user("teacher-audit-perm")
    student = make_user("student-audit-perm", role=Role.STUDENT.value)
    admin = make_user("admin-audit-perm", role=Role.ADMIN.value)

    # 未登录 → 401
    anonymous = client.get(f"{API}/audit-logs")
    assert anonymous.status_code == 401, anonymous.text
    assert anonymous.json()["code"] == "AUTH_REQUIRED"

    # 教师 → 403，错误体不泄漏任何审计字段
    login(teacher.username)
    teacher_response = client.get(f"{API}/audit-logs")
    assert teacher_response.status_code == 403, teacher_response.text
    assert teacher_response.json()["code"] == "FORBIDDEN"
    assert set(teacher_response.json()) == _ERROR_KEYS

    # 学生 → 403
    login(student.username)
    student_response = client.get(f"{API}/audit-logs")
    assert student_response.status_code == 403, student_response.text
    assert student_response.json()["code"] == "FORBIDDEN"

    # admin → 200，分页结构符合契约
    login(admin.username)
    admin_response = client.get(f"{API}/audit-logs")
    assert admin_response.status_code == 200, admin_response.text
    page = admin_response.json()
    assert set(page) == {"items", "total", "page", "page_size"}
    assert page["page"] == 1
    assert page["page_size"] == 20
    # 登录动作已写审计，至少能查到 admin 自己的 auth.login
    assert any(
        item["action"] == "auth.login" and item["actor_id"] == admin.id
        for item in page["items"]
    )


def test_write_operation_is_audited_ac22(
    client: TestClient,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
) -> None:
    admin = make_user("admin-audit-ac22", role=Role.ADMIN.value)
    login(admin.username)

    created = client.post(
        f"{API}/users",
        json={
            "username": "audited-new-user",
            "display_name": "被审计的新账号",
            "role": "teacher",
            "password": "plain-text-password-123",
        },
        headers=csrf_headers(),
    )
    assert created.status_code == 201, created.text
    new_user_id = created.json()["id"]

    response = client.get(
        f"{API}/audit-logs",
        params={"action": "user.create", "actor_id": admin.id},
    )
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["total"] >= 1
    item = page["items"][0]

    # 字段完整（AC-22 / 契约 required）
    assert item["action"] == "user.create"
    assert item["actor_id"] == admin.id
    assert item["target_type"] == "user"
    assert item["target_id"] == str(new_user_id)
    assert item["ip"]  # C-05：来源地址必填（TestClient 下为 testclient）
    assert datetime.fromisoformat(item["created_at"]) is not None
    assert item["detail"] is not None
    assert item["detail"]["username"] == "audited-new-user"

    # 明文密码永不落 detail
    assert "plain-text-password-123" not in str(item["detail"])
    assert "password" not in str(item["detail"]).lower()


def test_pagination_and_filters(
    client: TestClient,
    db_session: Session,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
) -> None:
    admin = make_user("admin-audit-page", role=Role.ADMIN.value)
    other = make_user("other-audit-page")
    now = datetime.now(UTC)

    seeded = [
        _seed(
            db_session,
            action="test.page",
            actor_id=admin.id,
            created_at=now - timedelta(minutes=index),
            target_id=str(index),
        )
        for index in range(5)
    ]
    _seed(
        db_session,
        action="test.other",
        actor_id=other.id,
        created_at=now - timedelta(minutes=10),
    )
    login(admin.username)

    # 第 2 页、每页 2 条：created_at 降序（最新 index=0 在最前）
    page2 = client.get(
        f"{API}/audit-logs",
        params={"action": "test.page", "page": 2, "page_size": 2},
    )
    assert page2.status_code == 200, page2.text
    body = page2.json()
    assert body["total"] == 5
    assert body["page"] == 2
    assert body["page_size"] == 2
    assert [item["id"] for item in body["items"]] == [seeded[2].id, seeded[3].id]

    page3 = client.get(
        f"{API}/audit-logs",
        params={"action": "test.page", "page": 3, "page_size": 2},
    )
    assert [item["id"] for item in page3.json()["items"]] == [seeded[4].id]

    # actor_id 过滤 + 无命中
    by_actor = client.get(
        f"{API}/audit-logs",
        params={"action": "test.other", "actor_id": other.id},
    )
    assert by_actor.json()["total"] == 1
    no_hit = client.get(f"{API}/audit-logs", params={"actor_id": 999_999})
    assert no_hit.json()["total"] == 0
    assert no_hit.json()["items"] == []

    # from/to 闭区间：now-2.5min ~ now-0.5min 命中 index=1、2
    window = client.get(
        f"{API}/audit-logs",
        params={
            "action": "test.page",
            "from": (now - timedelta(minutes=2.5)).isoformat(),
            "to": (now - timedelta(minutes=0.5)).isoformat(),
            "page_size": 100,
        },
    )
    assert window.status_code == 200, window.text
    assert {item["id"] for item in window.json()["items"]} == {
        seeded[1].id,
        seeded[2].id,
    }

    # from > to → 空集，不是 500
    reversed_window = client.get(
        f"{API}/audit-logs",
        params={
            "from": now.isoformat(),
            "to": (now - timedelta(days=1)).isoformat(),
        },
    )
    assert reversed_window.status_code == 200
    assert reversed_window.json()["total"] == 0

    # 分页参数越界 → 422
    assert client.get(f"{API}/audit-logs", params={"page": 0}).status_code == 422
    assert (
        client.get(f"{API}/audit-logs", params={"page_size": 101}).status_code == 422
    )


def test_default_window_is_recent_180_days(
    client: TestClient,
    db_session: Session,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
) -> None:
    admin = make_user("admin-audit-window", role=Role.ADMIN.value)
    now = datetime.now(UTC)
    old = _seed(
        db_session,
        action="test.retention.old",
        actor_id=admin.id,
        created_at=now - timedelta(days=200),
    )
    recent = _seed(
        db_session,
        action="test.retention.recent",
        actor_id=admin.id,
        created_at=now - timedelta(days=10),
    )
    login(admin.username)

    # 不传 from/to：只返回最近 180 天，200 天前的记录被排除
    default_page = client.get(
        f"{API}/audit-logs",
        params={"action": "test.retention.old"},
    )
    assert default_page.status_code == 200, default_page.text
    assert default_page.json()["total"] == 0
    assert old.id not in {item["id"] for item in default_page.json()["items"]}

    recent_page = client.get(
        f"{API}/audit-logs",
        params={"action": "test.retention.recent"},
    )
    assert recent_page.json()["total"] == 1
    assert recent_page.json()["items"][0]["id"] == recent.id

    # 显式 from 可查询更早历史（契约允许）
    explicit = client.get(
        f"{API}/audit-logs",
        params={
            "action": "test.retention.old",
            "from": (now - timedelta(days=210)).isoformat(),
        },
    )
    assert explicit.json()["total"] == 1
    assert explicit.json()["items"][0]["id"] == old.id
