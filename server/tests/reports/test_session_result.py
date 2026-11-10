"""`GET /sessions/{id}/result`（AC-18 / AC-19 / C-21 / CHG-030）。

覆盖：
- 学生本人：逐题 selected / correct_answer / is_correct / earned_points / points / explanation
- 学生 B 越权 → 403，响应体只有 code/message/request_id（不含 items/score/selected）
- 教师（即使是该考试的创建者）→ 403（角色判定先于归属）
- admin → 200 可读全部
- 未交卷 → 409 `NOT_SUBMITTED`；到期自动交卷后（expired）可读
- 不存在 → 404
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.exam import Exam
from app.models.exam_session import ExamSession
from app.models.question import Question
from app.models.user import Role, User

API = "/api"
_ERROR_KEYS = {"code", "message", "request_id"}


def _make_exam_with_two_questions(
    db_session: Session,
    make_exam: Callable[..., tuple[Exam, list[Question]]],
    teacher: User,
) -> tuple[Exam, list[Question]]:
    return make_exam(
        teacher,
        specs=[
            {"type": "single", "points": 10, "answer": ["A"]},
            {"type": "single", "points": 20, "answer": ["B"]},
        ],
        title="成绩明细考试",
    )


def _take_exam(
    *,
    login: Callable[..., dict[str, Any]],
    start_session: Callable[..., dict[str, Any]],
    save_answers: Callable[..., dict[str, Any]],
    submit_session: Callable[..., dict[str, Any]],
    student: User,
    exam: Exam,
    answers: list[tuple[int, list[str]]],
) -> int:
    login(student.username)
    body = start_session(exam.id)
    session_id = int(body["id"])
    save_answers(session_id, 1, answers)
    submit_session(session_id)
    return session_id


def test_result_detail_owner_admin_and_teacher_forbidden(
    client: TestClient,
    db_session: Session,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
    make_exam: Callable[..., tuple[Exam, list[Question]]],
    start_session: Callable[..., dict[str, Any]],
    save_answers: Callable[..., dict[str, Any]],
    submit_session: Callable[..., dict[str, Any]],
) -> None:
    teacher = make_user("teacher-result-owner")
    student_a = make_user("student-result-a", role=Role.STUDENT.value)
    student_b = make_user("student-result-b", role=Role.STUDENT.value)
    admin = make_user("admin-result", role=Role.ADMIN.value)
    exam, questions = _make_exam_with_two_questions(db_session, make_exam, teacher)
    q1, q2 = questions

    session_id = _take_exam(
        login=login,
        start_session=start_session,
        save_answers=save_answers,
        submit_session=submit_session,
        student=student_a,
        exam=exam,
        answers=[(q1.id, ["A"]), (q2.id, ["A"])],
    )

    # 1) 本人可读：逐题字段与判分一致（q1 正确满分，q2 错误 0 分）
    login(student_a.username)
    response = client.get(f"{API}/sessions/{session_id}/result")
    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail["session_id"] == session_id
    assert detail["exam_id"] == exam.id
    assert detail["score"] == 10.0
    assert detail["total_points"] == 30.0
    assert detail["submitted_at"] is not None
    assert [item["question_id"] for item in detail["items"]] == [q1.id, q2.id]

    first, second = detail["items"]
    assert first["type"] == "single"
    assert first["selected"] == ["A"]
    assert first["correct_answer"] == ["A"]
    assert first["is_correct"] is True
    assert first["earned_points"] == 10.0
    assert first["points"] == 10.0
    assert first["stem"]
    assert first["explanation"] == "成绩测试解析"

    assert second["selected"] == ["A"]
    assert second["correct_answer"] == ["B"]
    assert second["is_correct"] is False
    assert second["earned_points"] == 0.0
    assert second["points"] == 20.0

    # 2) 学生 B 越权（AC-18）→ 403，且错误体不含任何业务字段
    login(student_b.username)
    forbidden = client.get(f"{API}/sessions/{session_id}/result")
    assert forbidden.status_code == 403, forbidden.text
    body = forbidden.json()
    assert body["code"] == "FORBIDDEN"
    assert set(body) == _ERROR_KEYS
    for leaked in ("items", "score", "selected", "correct_answer"):
        assert leaked not in body

    # 3) 教师即使是考试创建者也一律 403（CHG-030，角色判定先于归属）
    login(teacher.username)
    teacher_response = client.get(f"{API}/sessions/{session_id}/result")
    assert teacher_response.status_code == 403, teacher_response.text
    assert teacher_response.json()["code"] == "FORBIDDEN"
    assert set(teacher_response.json()) == _ERROR_KEYS

    # 4) admin 可读全部
    login(admin.username)
    admin_response = client.get(f"{API}/sessions/{session_id}/result")
    assert admin_response.status_code == 200, admin_response.text
    assert len(admin_response.json()["items"]) == 2
    assert admin_response.json()["score"] == 10.0

    # 5) 不存在 → 404（学生路径）
    login(student_a.username)
    missing = client.get(f"{API}/sessions/999999/result")
    assert missing.status_code == 404
    assert missing.json()["code"] == "NOT_FOUND"


def test_result_requires_submitted_and_expired_is_readable(
    client: TestClient,
    db_session: Session,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
    make_exam: Callable[..., tuple[Exam, list[Question]]],
    start_session: Callable[..., dict[str, Any]],
    save_answers: Callable[..., dict[str, Any]],
    submit_session: Callable[..., dict[str, Any]],
    force_deadline: Callable[..., ExamSession],
) -> None:
    teacher = make_user("teacher-result-409")
    student = make_user("student-result-409", role=Role.STUDENT.value)
    exam, questions = make_exam(
        teacher,
        specs=[{"type": "single", "points": 10, "answer": ["A"]}],
        title="未交卷考试",
    )
    question = questions[0]

    login(student.username)
    body = start_session(exam.id)
    session_id = int(body["id"])
    save_answers(session_id, 1, [(question.id, ["A"])])

    # 未交卷（in_progress）→ 409 NOT_SUBMITTED（AC-19）
    pending = client.get(f"{API}/sessions/{session_id}/result")
    assert pending.status_code == 409, pending.text
    assert pending.json()["code"] == "NOT_SUBMITTED"
    assert set(pending.json()) == _ERROR_KEYS

    # 教师对同一 in_progress 会话仍是 403（角色判定先于状态判定）
    login(teacher.username)
    assert client.get(f"{API}/sessions/{session_id}/result").status_code == 403

    # 到期后 GET /sessions/{id} 同步自动交卷判分；随后逐题结果可读（C-13/C-20）
    login(student.username)
    force_deadline(session_id)
    settled = client.get(f"{API}/sessions/{session_id}")
    assert settled.status_code == 200, settled.text
    assert settled.json()["status"] == "expired"

    expired = client.get(f"{API}/sessions/{session_id}/result")
    assert expired.status_code == 200, expired.text
    payload = expired.json()
    assert payload["score"] == 10.0
    assert payload["items"][0]["is_correct"] is True

    db_session.expire_all()
    stored = db_session.get(ExamSession, session_id)
    assert stored is not None
    assert stored.status == "expired"
