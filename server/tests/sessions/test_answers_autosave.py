"""自动保存（AC-14 / AC-15 / C-16）：client_seq 单调、到期 409、越权与审计脱敏。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.exam import Exam
from app.models.exam_session import ExamSession
from app.models.question import Question
from app.models.user import Role, User

API = "/api"


def test_client_seq_is_monotonic(
    client: TestClient,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    make_exam: Callable[..., tuple[Exam, list[Question]]],
    start_session: Callable[..., dict[str, Any]],
    save_answers: Callable[..., dict[str, Any]],
) -> None:
    teacher = make_user("teacher-save-seq")
    student = make_user("student-save-seq", role=Role.STUDENT.value)
    exam, questions = make_exam(teacher)
    question_id = questions[0].id

    login(student.username)
    body = start_session(exam.id)
    session_id = body["id"]

    first = save_answers(session_id, 5, [(question_id, ["A"])])
    assert set(first) == {"saved_seq", "saved_at", "applied"}
    assert first["applied"] is True
    assert first["saved_seq"] == 5

    # 旧 seq（乱序到达）→ 200 + applied=false + 当前 saved_seq，不覆盖较新答案（AC-15）
    stale = save_answers(session_id, 3, [(question_id, ["B"])])
    assert stale["applied"] is False
    assert stale["saved_seq"] == 5

    state = client.get(f"{API}/sessions/{session_id}").json()
    assert state["answers"] == [{"question_id": question_id, "selected": ["A"]}]
    assert state["last_client_seq"] == 5

    # 同 seq 重放同样被忽略
    same = save_answers(session_id, 5, [(question_id, ["B"])])
    assert same["applied"] is False
    assert same["saved_seq"] == 5

    # 更大 seq 才落库
    newer = save_answers(session_id, 6, [(question_id, ["B"])])
    assert newer["applied"] is True
    assert newer["saved_seq"] == 6
    state = client.get(f"{API}/sessions/{session_id}").json()
    assert state["answers"] == [{"question_id": question_id, "selected": ["B"]}]
    assert state["last_client_seq"] == 6


def test_save_after_submit_returns_409(
    client: TestClient,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
    make_exam: Callable[..., tuple[Exam, list[Question]]],
    start_session: Callable[..., dict[str, Any]],
    save_answers: Callable[..., dict[str, Any]],
) -> None:
    teacher = make_user("teacher-save-submitted")
    student = make_user("student-save-submitted", role=Role.STUDENT.value)
    exam, questions = make_exam(teacher)
    question_id = questions[0].id

    login(student.username)
    body = start_session(exam.id)
    session_id = body["id"]
    save_answers(session_id, 1, [(question_id, ["A"])])
    submitted = client.post(
        f"{API}/sessions/{session_id}/submit",
        headers=csrf_headers(),
    )
    assert submitted.status_code == 200, submitted.text

    response = client.put(
        f"{API}/sessions/{session_id}/answers",
        json={"client_seq": 2, "answers": [{"question_id": question_id, "selected": ["B"]}]},
        headers=csrf_headers(),
    )
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "EXAM_EXPIRED"


def test_save_after_deadline_auto_submits_and_grades(
    client: TestClient,
    db_session: Session,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
    make_exam: Callable[..., tuple[Exam, list[Question]]],
    start_session: Callable[..., dict[str, Any]],
    save_answers: Callable[..., dict[str, Any]],
    force_deadline: Callable[..., ExamSession],
) -> None:
    teacher = make_user("teacher-save-expired")
    student = make_user("student-save-expired", role=Role.STUDENT.value)
    exam, questions = make_exam(
        teacher,
        specs=[{"type": "single", "points": 10, "answer": ["A"]}],
    )
    question_id = questions[0].id

    login(student.username)
    body = start_session(exam.id)
    session_id = body["id"]
    save_answers(session_id, 1, [(question_id, ["A"])])
    force_deadline(session_id)

    # AC-14：到期后继续保存 → 409 EXAM_EXPIRED，且服务端已自动交卷判分
    response = client.put(
        f"{API}/sessions/{session_id}/answers",
        json={"client_seq": 2, "answers": [{"question_id": question_id, "selected": ["B"]}]},
        headers=csrf_headers(),
    )
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "EXAM_EXPIRED"

    db_session.expire_all()
    stored = db_session.get(ExamSession, session_id)
    assert stored is not None
    assert stored.status == "expired"
    assert float(stored.score or 0) == 10.0
    assert stored.submitted_at is not None

    # 随后的读接口：200 + status=expired + score（不返回 409，C-13）
    state = client.get(f"{API}/sessions/{session_id}")
    assert state.status_code == 200
    payload = state.json()
    assert payload["status"] == "expired"
    assert payload["score"] == 10.0
    assert payload["submitted_at"] is not None
    assert payload["answers"] == [{"question_id": question_id, "selected": ["A"]}]

    # 幂等：再读一次不会改动成绩/提交时间
    submitted_at = payload["submitted_at"]
    again = client.get(f"{API}/sessions/{session_id}").json()
    assert again["status"] == "expired"
    assert again["score"] == 10.0
    assert again["submitted_at"] == submitted_at


def test_save_permissions_and_validation(
    client: TestClient,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
    make_exam: Callable[..., tuple[Exam, list[Question]]],
    start_session: Callable[..., dict[str, Any]],
) -> None:
    teacher = make_user("teacher-save-auth")
    student_a = make_user("student-save-a", role=Role.STUDENT.value)
    student_b = make_user("student-save-b", role=Role.STUDENT.value)
    exam, questions = make_exam(teacher)
    question_id = questions[0].id

    login(student_a.username)
    body = start_session(exam.id)
    session_id = body["id"]

    # 跨学生越权 → 403（AC-18），响应体无业务数据
    login(student_b.username)
    for method, url, payload in (
        ("get", f"{API}/sessions/{session_id}", None),
        (
            "put",
            f"{API}/sessions/{session_id}/answers",
            {"client_seq": 2, "answers": [{"question_id": question_id, "selected": ["A"]}]},
        ),
        ("post", f"{API}/sessions/{session_id}/submit", None),
    ):
        if method == "get":
            response = client.get(url)
        elif method == "put":
            response = client.put(url, json=payload, headers=csrf_headers())
        else:
            response = client.post(url, headers=csrf_headers())
        assert response.status_code == 403, (method, response.text)
        assert response.json()["code"] == "FORBIDDEN"
        assert set(response.json()) == {"code", "message", "request_id"}

    login(student_a.username)
    # 不存在的会话 → 404
    assert client.get(f"{API}/sessions/999999").status_code == 404
    missing = client.put(
        f"{API}/sessions/999999/answers",
        json={"client_seq": 2, "answers": [{"question_id": question_id, "selected": ["A"]}]},
        headers=csrf_headers(),
    )
    assert missing.status_code == 404
    assert missing.json()["code"] == "NOT_FOUND"

    # 题目不属于本场考试 → 422
    _, other_questions = make_exam(teacher, title="另一场考试")
    foreign = client.put(
        f"{API}/sessions/{session_id}/answers",
        json={
            "client_seq": 2,
            "answers": [{"question_id": other_questions[0].id, "selected": ["A"]}],
        },
        headers=csrf_headers(),
    )
    assert foreign.status_code == 422, foreign.text
    assert foreign.json()["code"] == "VALIDATION_ERROR"

    # client_seq 必须 >= 1；answers 至少 1 条（契约 schema）
    for payload in (
        {"client_seq": 0, "answers": [{"question_id": question_id, "selected": ["A"]}]},
        {"client_seq": 2, "answers": []},
        {"client_seq": 2},
    ):
        response = client.put(
            f"{API}/sessions/{session_id}/answers",
            json=payload,
            headers=csrf_headers(),
        )
        assert response.status_code == 422, payload
        assert response.json()["code"] == "VALIDATION_ERROR"


def test_save_audit_has_no_answer_plaintext(
    client: TestClient,
    db_session: Session,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    make_exam: Callable[..., tuple[Exam, list[Question]]],
    start_session: Callable[..., dict[str, Any]],
    save_answers: Callable[..., dict[str, Any]],
) -> None:
    teacher = make_user("teacher-save-audit")
    student = make_user("student-save-audit", role=Role.STUDENT.value)
    exam, questions = make_exam(teacher)

    login(student.username)
    body = start_session(exam.id)
    save_answers(body["id"], 3, [(questions[0].id, ["A", "B"])])

    audit_row = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "session.save")
    )
    assert audit_row is not None
    assert audit_row.actor_id == student.id
    assert audit_row.target_type == "exam_session"
    assert audit_row.target_id == str(body["id"])
    assert audit_row.ip
    assert audit_row.detail is not None
    # detail 只记元信息，不含 selected/答案明文
    assert set(audit_row.detail) == {"client_seq", "question_count", "applied"}
    assert audit_row.detail["client_seq"] == 3
    assert audit_row.detail["question_count"] == 1
    assert audit_row.detail["applied"] is True
    assert "selected" not in str(audit_row.detail)
