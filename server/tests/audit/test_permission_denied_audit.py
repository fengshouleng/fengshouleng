"""越权尝试写审计（AC-22 / docs/12 §4；2026-11-11 起由「可选」改为必做）。

本文件覆盖「角色越权」与两条「不该记」的路径，防止审计口径漂移：

1. **角色越权**（学生访问教师/管理员接口）→ 记 `permission.denied`；
2. **401 未登录** 不记（不是越权尝试，是未认证）；
3. **CSRF 失败** 不记（语义是请求伪造防护，不是角色/归属越权）。

归属越权（IDOR）的留痕用例在 `server/tests/sessions/test_permission_denied_audit.py`
（那边才有会话/考试工厂）。审计用本请求的数据库会话写入并独立提交，
因此 403 响应不会因为审计失败而改变；`detail` 只放 method/path/reason/request_id。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.user import Role, User

API = "/api"


def _denied_rows(db: Session, actor_id: int | None = None) -> list[AuditLog]:
    statement = select(AuditLog).where(AuditLog.action == "permission.denied")
    if actor_id is not None:
        statement = statement.where(AuditLog.actor_id == actor_id)
    return list(db.scalars(statement.order_by(AuditLog.id)).all())


def test_role_denial_is_audited(
    client: TestClient,
    db_session: Session,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
) -> None:
    """学生创建题目（RBAC 403）→ 审计里有 actor=学生、target=端点的记录。"""
    student = make_user("audit-denied-student", role=Role.STUDENT.value)
    login(student.username)

    response = client.post(
        f"{API}/questions",
        json={"type": "single", "stem": "越权建题尝试"},
        headers=csrf_headers(),
    )
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "FORBIDDEN"

    db_session.expire_all()
    rows = _denied_rows(db_session, actor_id=student.id)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row.target_type == "endpoint"
    assert row.target_id == "POST /api/questions"
    assert row.ip == "testclient"
    assert row.detail is not None
    assert row.detail["method"] == "POST"
    assert row.detail["path"] == "/api/questions"
    assert row.detail["reason"]
    # request_id 三元组（响应体 / 响应头 / 审计 detail）必须一致
    assert row.detail["request_id"] == response.json()["request_id"]
    assert response.headers.get("X-Request-ID") == row.detail["request_id"]
    # 审计 detail 里不能出现请求体或敏感字段
    assert "越权建题尝试" not in str(row.detail)


def test_unauthenticated_401_is_not_audited(
    client: TestClient,
    db_session: Session,
) -> None:
    """401 不是「越权尝试」：不写 permission.denied（避免未登录噪声污染审计）。"""
    response = client.post(f"{API}/questions", json={"type": "single", "stem": "x"})
    assert response.status_code == 401, response.text

    db_session.expire_all()
    assert _denied_rows(db_session) == []


def test_csrf_failure_is_not_audited(
    client: TestClient,
    db_session: Session,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
) -> None:
    """CSRF 失败是「请求伪造防护」，语义不是角色/归属越权，不写 permission.denied。"""
    teacher = make_user("audit-denied-csrf")
    login(teacher.username)

    response = client.post(
        f"{API}/questions",
        json={"type": "single", "stem": "无 CSRF 头"},
    )
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "CSRF_INVALID"

    db_session.expire_all()
    assert _denied_rows(db_session, actor_id=teacher.id) == []
