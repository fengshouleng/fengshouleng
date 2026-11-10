"""交卷幂等（AC-13 / ADR-005 / C-15）：首次判分、重复提交 200、并发只产生一条成绩。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.models.answer import Answer
from app.models.audit_log import AuditLog
from app.models.exam import Exam
from app.models.exam_session import ExamSession
from app.models.question import Question
from app.models.user import Role, User
from app.sessions import service as sessions_service

API = "/api"


def test_first_submit_grades_and_repeat_returns_same_score(
    client: TestClient,
    db_session: Session,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
    make_exam: Callable[..., tuple[Exam, list[Question]]],
    start_session: Callable[..., dict[str, Any]],
    save_answers: Callable[..., dict[str, Any]],
) -> None:
    teacher = make_user("teacher-submit-first")
    student = make_user("student-submit-first", role=Role.STUDENT.value)
    exam, questions = make_exam(
        teacher,
        specs=[
            {"type": "single", "points": 10, "answer": ["A"]},
            {"type": "single", "points": 20, "answer": ["B"]},
        ],
    )
    q1, q2 = questions

    login(student.username)
    body = start_session(exam.id)
    session_id = body["id"]
    save_answers(session_id, 1, [(q1.id, ["A"]), (q2.id, ["A"])])

    first = client.post(
        f"{API}/sessions/{session_id}/submit",
        headers=csrf_headers(),
    )
    assert first.status_code == 200, first.text
    payload = first.json()
    assert payload["session_id"] == session_id
    assert payload["status"] == "submitted"
    assert payload["score"] == 10.0
    assert payload["replayed"] is False
    submitted_at = payload["submitted_at"]
    assert datetime.fromisoformat(submitted_at) <= datetime.now(UTC) + timedelta(seconds=5)

    # 判分落库：逐题 is_correct / earned_points + 会话总分
    db_session.expire_all()
    stored = db_session.get(ExamSession, session_id)
    assert stored is not None
    assert stored.status == "submitted"
    assert float(stored.score or 0) == 10.0
    assert stored.submitted_at is not None

    rows = {
        row.question_id: row
        for row in db_session.scalars(
            select(Answer).where(Answer.session_id == session_id)
        ).all()
    }
    assert rows[q1.id].is_correct is True
    assert float(rows[q1.id].earned_points or 0) == 10.0
    assert rows[q2.id].is_correct is False
    assert float(rows[q2.id].earned_points or 0) == 0.0

    # 重复提交：200 + replayed=true + 同一成绩/提交时间（AC-13，无 409 分支）
    second = client.post(
        f"{API}/sessions/{session_id}/submit",
        headers=csrf_headers(),
    )
    assert second.status_code == 200, second.text
    replay = second.json()
    assert replay["replayed"] is True
    assert replay["session_id"] == session_id
    assert replay["score"] == payload["score"]
    assert replay["submitted_at"] == submitted_at
    assert replay["status"] == "submitted"

    # 只有一条成绩行（唯一约束 + 状态迁移，不重复计分）
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(ExamSession)
            .where(ExamSession.exam_id == exam.id)
        )
        == 1
    )
    audits = db_session.scalars(
        select(AuditLog)
        .where(AuditLog.action == "session.submit")
        .order_by(AuditLog.id)
    ).all()
    assert [row.detail["replayed"] for row in audits if row.detail] == [False, True]


def test_submit_after_deadline_marks_expired(
    client: TestClient,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
    make_exam: Callable[..., tuple[Exam, list[Question]]],
    start_session: Callable[..., dict[str, Any]],
    save_answers: Callable[..., dict[str, Any]],
    force_deadline: Callable[..., ExamSession],
) -> None:
    teacher = make_user("teacher-submit-expired")
    student = make_user("student-submit-expired", role=Role.STUDENT.value)
    exam, questions = make_exam(
        teacher,
        specs=[{"type": "single", "points": 10, "answer": ["A"]}],
    )

    login(student.username)
    body = start_session(exam.id)
    session_id = body["id"]
    save_answers(session_id, 1, [(questions[0].id, ["A"])])
    force_deadline(session_id)

    first = client.post(
        f"{API}/sessions/{session_id}/submit",
        headers=csrf_headers(),
    )
    assert first.status_code == 200, first.text
    payload = first.json()
    assert payload["status"] == "expired"
    assert payload["replayed"] is False
    assert payload["score"] == 10.0
    assert payload["submitted_at"] is not None

    # 自动到期后再提交：200 + status=expired + 既有成绩 + replayed=true
    second = client.post(
        f"{API}/sessions/{session_id}/submit",
        headers=csrf_headers(),
    )
    assert second.status_code == 200, second.text
    replay = second.json()
    assert replay["status"] == "expired"
    assert replay["replayed"] is True
    assert replay["score"] == 10.0
    assert replay["submitted_at"] == payload["submitted_at"]


def test_submit_permissions(
    client: TestClient,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
    make_exam: Callable[..., tuple[Exam, list[Question]]],
    start_session: Callable[..., dict[str, Any]],
) -> None:
    teacher = make_user("teacher-submit-auth")
    student_a = make_user("student-submit-a", role=Role.STUDENT.value)
    student_b = make_user("student-submit-b", role=Role.STUDENT.value)
    exam, _ = make_exam(teacher)

    login(student_a.username)
    body = start_session(exam.id)
    session_id = body["id"]

    # 他人会话 → 403；教师（含自己考试）→ 403（docs/12 §1）
    login(student_b.username)
    assert (
        client.post(f"{API}/sessions/{session_id}/submit", headers=csrf_headers()).status_code
        == 403
    )

    login(teacher.username)
    assert (
        client.post(f"{API}/sessions/{session_id}/submit", headers=csrf_headers()).status_code
        == 403
    )

    # 不存在 → 404
    login(student_a.username)
    missing = client.post(f"{API}/sessions/999999/submit", headers=csrf_headers())
    assert missing.status_code == 404
    assert missing.json()["code"] == "NOT_FOUND"


def test_concurrent_double_submit_yields_single_score(
    client: TestClient,
    db_session: Session,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    make_exam: Callable[..., tuple[Exam, list[Question]]],
    start_session: Callable[..., dict[str, Any]],
    save_answers: Callable[..., dict[str, Any]],
) -> None:
    """并发双提交：唯一约束 + 状态迁移保证只产生一条成绩，后到者只返回既有成绩。"""
    teacher = make_user("teacher-submit-concurrent")
    student = make_user("student-submit-concurrent", role=Role.STUDENT.value)
    exam, questions = make_exam(
        teacher,
        specs=[{"type": "single", "points": 10, "answer": ["A"]}],
    )

    login(student.username)
    body = start_session(exam.id)
    session_id = body["id"]
    save_answers(session_id, 1, [(questions[0].id, ["A"])])

    first_session, _, replayed_first = sessions_service.submit_session(
        db_session,
        session_id,
        student=student,
    )
    assert replayed_first is False
    score = float(first_session.score or 0)
    assert score == 10.0
    submitted_at = first_session.submitted_at
    assert submitted_at is not None

    # 第二个独立连接（模拟并发请求）：状态迁移已生效，只读既有成绩
    factory = sessionmaker(
        bind=db_session.get_bind(),
        autoflush=False,
        expire_on_commit=False,
    )
    with factory() as other:
        second_session, _, replayed_second = sessions_service.submit_session(
            other,
            session_id,
            student=student,
        )
        assert replayed_second is True
        assert second_session.status == "submitted"
        assert float(second_session.score or 0) == score
        assert second_session.submitted_at is not None

    db_session.expire_all()
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(ExamSession)
            .where(ExamSession.exam_id == exam.id)
        )
        == 1
    )
    answer_row = db_session.scalar(
        select(Answer).where(Answer.session_id == session_id)
    )
    assert answer_row is not None
    assert float(answer_row.earned_points or 0) == 10.0
