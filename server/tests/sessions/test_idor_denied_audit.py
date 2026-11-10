"""归属越权（IDOR）也要留痕：学生 A 读学生 B 的会话 → 403 + `permission.denied`。

与 `server/tests/audit/test_permission_denied_audit.py`（角色越权）互补：
docs/12 §4 要求「被拒绝的越权尝试写审计」；本用例证明归属判定路径同样落审计，
且审计里带真实的 actor / target；403 响应体不泄露归属信息（仍是统一 `Error`）。
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


def test_idor_denial_is_audited(
    client: TestClient,
    db_session: Session,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    make_exam: Callable[..., tuple[Any, list[Any]]],
    start_session: Callable[..., dict[str, Any]],
) -> None:
    teacher = make_user("idor-audit-teacher")
    student_a = make_user("idor-audit-a", role=Role.STUDENT.value)
    student_b = make_user("idor-audit-b", role=Role.STUDENT.value)
    exam, _ = make_exam(teacher)

    login(student_b.username)
    session_b = start_session(exam.id)

    login(student_a.username)
    response = client.get(f"{API}/sessions/{session_b['id']}")

    assert response.status_code == 403, response.text
    body = response.json()
    assert body["code"] == "FORBIDDEN"
    # 不泄露归属信息：错误体里不得出现 B 的用户名或会话数据
    assert "idor-audit-b" not in response.text

    db_session.expire_all()
    rows = list(
        db_session.scalars(
            select(AuditLog).where(
                AuditLog.action == "permission.denied",
                AuditLog.actor_id == student_a.id,
            )
        ).all()
    )
    assert len(rows) == 1, rows
    row = rows[0]
    assert row.target_id == f"GET /api/sessions/{session_b['id']}"
    assert row.detail is not None
    assert row.detail["path"] == f"/api/sessions/{session_b['id']}"
    assert row.ip == "testclient"


def test_idor_denial_does_not_leak_into_b_audit(
    client: TestClient,
    db_session: Session,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    make_exam: Callable[..., tuple[Any, list[Any]]],
    start_session: Callable[..., dict[str, Any]],
) -> None:
    """审计归属正确：记录挂在越权方 A 上，不写到被越权方 B 名下。"""
    teacher = make_user("idor-audit-teacher-2")
    student_a = make_user("idor-audit-a2", role=Role.STUDENT.value)
    student_b = make_user("idor-audit-b2", role=Role.STUDENT.value)
    exam, _ = make_exam(teacher)

    login(student_b.username)
    session_b = start_session(exam.id)
    login(student_a.username)
    assert client.get(f"{API}/sessions/{session_b['id']}").status_code == 403

    db_session.expire_all()
    rows_b = list(
        db_session.scalars(
            select(AuditLog).where(
                AuditLog.action == "permission.denied",
                AuditLog.actor_id == student_b.id,
            )
        ).all()
    )
    assert rows_b == []
