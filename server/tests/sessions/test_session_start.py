"""会话开始 / 恢复（AC-11 / AC-12 / C-22 状态码语义）。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.service import as_utc
from app.models.exam import Exam
from app.models.exam_session import ExamSession
from app.models.question import Question
from app.models.user import Role, User

API = "/api"


def test_start_creates_session_with_deadline(
    client: TestClient,
    db_session: Session,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    make_exam: Callable[..., tuple[Exam, list[Question]]],
    start_session: Callable[..., dict[str, Any]],
) -> None:
    teacher = make_user("teacher-session-start")
    student = make_user("student-session-start", role=Role.STUDENT.value)
    exam, questions = make_exam(
        teacher,
        specs=[{"type": "single", "points": 10, "answer": ["A"]}],
        duration_min=30,
    )

    login(student.username)
    body = start_session(exam.id)

    assert body["exam_id"] == exam.id
    assert body["status"] == "in_progress"
    assert body["last_client_seq"] == 0
    assert body["answers"] == []
    assert body["submitted_at"] is None
    assert body["score"] is None

    # 作答视图：只有契约字段，绝不含正确答案
    state_question = body["questions"][0]
    assert set(state_question) == {
        "question_id",
        "type",
        "stem",
        "options",
        "order_no",
        "points",
    }
    assert state_question["question_id"] == questions[0].id
    assert state_question["type"] == "single"
    assert state_question["points"] == 10.0
    assert state_question["order_no"] == 1
    assert [option["key"] for option in state_question["options"]] == ["A", "B"]

    started = datetime.fromisoformat(body["started_at"])
    deadline = datetime.fromisoformat(body["deadline_at"])
    assert deadline - started == timedelta(minutes=30)
    assert abs(
        (datetime.fromisoformat(body["server_time"]) - datetime.now(UTC)).total_seconds()
    ) < 60

    # 落库：唯一一条会话，deadline 与响应一致
    db_session.expire_all()
    rows = db_session.scalars(
        select(ExamSession).where(ExamSession.exam_id == exam.id)
    ).all()
    assert len(rows) == 1
    assert rows[0].id == body["id"]
    assert rows[0].student_id == student.id
    assert as_utc(rows[0].deadline_at) == deadline


def test_resume_returns_saved_answers_without_recomputing_deadline(
    client: TestClient,
    db_session: Session,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    make_exam: Callable[..., tuple[Exam, list[Question]]],
    start_session: Callable[..., dict[str, Any]],
    save_answers: Callable[..., dict[str, Any]],
) -> None:
    teacher = make_user("teacher-session-resume")
    student = make_user("student-session-resume", role=Role.STUDENT.value)
    exam, questions = make_exam(teacher)
    question_id = questions[0].id

    login(student.username)
    first = start_session(exam.id)
    assert first["status"] == "in_progress"

    saved = save_answers(first["id"], 1, [(question_id, ["A"])])
    assert saved["applied"] is True
    assert saved["saved_seq"] == 1

    # 第二次进入：200 恢复（不是 201），deadline 不重算，答案回读（C-01/C-22）
    second = start_session(exam.id, expect=200)
    assert second["id"] == first["id"]
    assert second["deadline_at"] == first["deadline_at"]
    assert second["started_at"] == first["started_at"]
    assert second["last_client_seq"] == 1
    assert second["answers"] == [{"question_id": question_id, "selected": ["A"]}]

    # GET /sessions/{id} 同样是唯一恢复入口
    state = client.get(f"{API}/sessions/{first['id']}")
    assert state.status_code == 200
    assert state.json()["answers"] == [{"question_id": question_id, "selected": ["A"]}]
    assert state.json()["last_client_seq"] == 1

    db_session.expire_all()
    rows = db_session.scalars(
        select(ExamSession).where(ExamSession.exam_id == exam.id)
    ).all()
    assert len(rows) == 1


def test_start_rejects_out_of_window_invisible_and_wrong_role(
    client: TestClient,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
    make_exam: Callable[..., tuple[Exam, list[Question]]],
) -> None:
    teacher = make_user("teacher-session-window")
    student = make_user("student-session-window", role=Role.STUDENT.value)
    now = datetime.now(UTC)

    future, _ = make_exam(
        teacher,
        start_at=now + timedelta(minutes=60),
        end_at=now + timedelta(minutes=180),
        duration_min=30,
    )
    past, _ = make_exam(
        teacher,
        start_at=now - timedelta(minutes=180),
        end_at=now - timedelta(minutes=60),
        duration_min=30,
    )
    closed, _ = make_exam(teacher, status="closed")
    draft, _ = make_exam(teacher, status="draft")

    login(student.username)

    def start(exam_id: int) -> Any:
        return client.post(f"{API}/exams/{exam_id}/sessions", headers=csrf_headers())

    # AC-11：未到开始时间 → 409 EXAM_NOT_STARTED
    response = start(future.id)
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "EXAM_NOT_STARTED"

    # 已截止 / 已关闭（无既有会话）→ 409 EXAM_EXPIRED
    for exam_id in (past.id, closed.id):
        response = start(exam_id)
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "EXAM_EXPIRED"

    # 不存在 / draft（不可见）→ 404（防枚举，C-17 / C-22）
    for exam_id in (draft.id, 999999):
        response = start(exam_id)
        assert response.status_code == 404, response.text
        assert response.json()["code"] == "NOT_FOUND"
        assert set(response.json()) == {"code", "message", "request_id"}

    # 角色：教师不能创建会话 → 403
    login(teacher.username)
    response = start(future.id)
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


def test_deadline_is_capped_by_exam_end_at(
    client: TestClient,
    db_session: Session,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    make_exam: Callable[..., tuple[Exam, list[Question]]],
    start_session: Callable[..., dict[str, Any]],
) -> None:
    teacher = make_user("teacher-session-cap")
    student = make_user("student-session-cap", role=Role.STUDENT.value)
    now = datetime.now(UTC)
    # 时间窗只剩 5 分钟，但时长为 30 分钟 → deadline = end_at
    exam, _ = make_exam(
        teacher,
        start_at=now - timedelta(minutes=30),
        end_at=now + timedelta(minutes=5),
        duration_min=30,
    )

    login(student.username)
    body = start_session(exam.id)
    deadline = datetime.fromisoformat(body["deadline_at"])

    db_session.expire_all()
    stored_exam = db_session.get(Exam, exam.id)
    assert stored_exam is not None
    assert deadline == as_utc(stored_exam.end_at)


def test_duplicate_session_blocked_by_unique_constraint(
    client: TestClient,
    db_session: Session,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    make_exam: Callable[..., tuple[Exam, list[Question]]],
    start_session: Callable[..., dict[str, Any]],
) -> None:
    teacher = make_user("teacher-session-unique")
    student = make_user("student-session-unique", role=Role.STUDENT.value)
    exam, _ = make_exam(teacher)

    login(student.username)
    body = start_session(exam.id)

    # 直接插入第二条同 (exam, student) 会话 → 唯一约束兜底（AC-13 的并发基础）
    now = datetime.now(UTC)
    db_session.add(
        ExamSession(
            exam_id=exam.id,
            student_id=student.id,
            status="in_progress",
            started_at=now,
            deadline_at=now + timedelta(minutes=30),
            last_client_seq=0,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # 并发落败方再请求 → 200 恢复同一条会话
    again = start_session(exam.id, expect=200)
    assert again["id"] == body["id"]


def test_shuffle_is_stable_and_hides_answers(
    client: TestClient,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    make_exam: Callable[..., tuple[Exam, list[Question]]],
    start_session: Callable[..., dict[str, Any]],
) -> None:
    teacher = make_user("teacher-session-shuffle")
    student = make_user("student-session-shuffle", role=Role.STUDENT.value)
    specs = [
        {"type": "single", "points": 5, "answer": ["A"]},
        {"type": "single", "points": 5, "answer": ["B"]},
        {"type": "single", "points": 5, "answer": ["A"]},
        {"type": "single", "points": 5, "answer": ["B"]},
    ]
    exam, questions = make_exam(
        teacher,
        specs=specs,
        shuffle_questions=True,
        shuffle_options=True,
    )

    login(student.username)
    body = start_session(exam.id)
    ids = [item["question_id"] for item in body["questions"]]
    assert sorted(ids) == sorted(question.id for question in questions)
    for item in body["questions"]:
        assert "answer" not in item
        assert sorted(option["key"] for option in item["options"]) == ["A", "B"]

    # 同一会话重复读取：乱序结果稳定（不跳题）
    again = client.get(f"{API}/sessions/{body['id']}")
    assert again.status_code == 200
    assert [item["question_id"] for item in again.json()["questions"]] == ids
