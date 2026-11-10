"""`GET /exams/{id}/results`（C-20 / CHG-031）：汇总口径、平均分除零、归属校验。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

from app.models.exam import Exam
from app.models.question import Question
from app.models.user import Role, User

API = "/api"
_ERROR_KEYS = {"code", "message", "request_id"}


def _take(
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
    if answers:
        save_answers(session_id, 1, answers)
        submit_session(session_id)
    return session_id


def test_exam_results_summary_and_permissions(
    client: TestClient,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
    make_exam: Callable[..., tuple[Exam, list[Question]]],
    start_session: Callable[..., dict[str, Any]],
    save_answers: Callable[..., dict[str, Any]],
    submit_session: Callable[..., dict[str, Any]],
) -> None:
    teacher = make_user("teacher-exam-results")
    other_teacher = make_user("teacher-exam-results-other")
    student_one = make_user(
        "student-exam-results-1", role=Role.STUDENT.value, display_name="学生一"
    )
    student_two = make_user(
        "student-exam-results-2", role=Role.STUDENT.value, display_name="学生二"
    )
    student_three = make_user(
        "student-exam-results-3", role=Role.STUDENT.value, display_name="学生三"
    )
    student_four = make_user(
        "student-exam-results-4", role=Role.STUDENT.value, display_name="学生四"
    )

    exam, questions = make_exam(
        teacher,
        specs=[
            {"type": "single", "points": 10, "answer": ["A"]},
            {"type": "single", "points": 5, "answer": ["B"]},
        ],
        title="汇总考试",
    )
    q1, q2 = questions

    # 三份已判分：10、10、15 → 平均 35/3 = 11.67（四舍五入两位）
    _take(
        login=login,
        start_session=start_session,
        save_answers=save_answers,
        submit_session=submit_session,
        student=student_one,
        exam=exam,
        answers=[(q1.id, ["A"]), (q2.id, ["A"])],
    )
    _take(
        login=login,
        start_session=start_session,
        save_answers=save_answers,
        submit_session=submit_session,
        student=student_two,
        exam=exam,
        answers=[(q1.id, ["A"]), (q2.id, ["A"])],
    )
    _take(
        login=login,
        start_session=start_session,
        save_answers=save_answers,
        submit_session=submit_session,
        student=student_three,
        exam=exam,
        answers=[(q1.id, ["A"]), (q2.id, ["B"])],
    )
    # 第四人只开始、不交卷 → total_sessions 计入，submitted / 平均分不计
    login(student_four.username)
    start_session(exam.id)

    login(teacher.username)
    response = client.get(f"{API}/exams/{exam.id}/results")
    assert response.status_code == 200, response.text
    summary = response.json()
    assert summary["exam_id"] == exam.id
    assert summary["total_sessions"] == 4
    assert summary["submitted"] == 3
    assert summary["average_score"] == 11.67
    assert len(summary["items"]) == 4

    by_student = {item["student_id"]: item for item in summary["items"]}
    assert by_student[student_one.id]["display_name"] == "学生一"
    assert by_student[student_one.id]["status"] == "submitted"
    assert by_student[student_one.id]["score"] == 10.0
    assert by_student[student_one.id]["submitted_at"] is not None
    assert by_student[student_three.id]["score"] == 15.0
    # in_progress 条目不得露出成绩/交卷时间（response_model_exclude_none）
    in_progress = by_student[student_four.id]
    assert in_progress["status"] == "in_progress"
    assert "score" not in in_progress
    assert "submitted_at" not in in_progress

    # 归属：其他教师 → 403；学生（角色不允许）→ 403；admin → 200
    login(other_teacher.username)
    forbidden = client.get(f"{API}/exams/{exam.id}/results")
    assert forbidden.status_code == 403, forbidden.text
    assert forbidden.json()["code"] == "FORBIDDEN"
    assert set(forbidden.json()) == _ERROR_KEYS

    login(student_one.username)
    assert client.get(f"{API}/exams/{exam.id}/results").status_code == 403

    admin = make_user("admin-exam-results", role=Role.ADMIN.value)
    login(admin.username)
    admin_view = client.get(f"{API}/exams/{exam.id}/results")
    assert admin_view.status_code == 200, admin_view.text
    assert admin_view.json()["total_sessions"] == 4

    # 不存在 → 404
    login(teacher.username)
    missing = client.get(f"{API}/exams/999999/results")
    assert missing.status_code == 404
    assert missing.json()["code"] == "NOT_FOUND"


def test_exam_results_empty_exam_average_is_zero(
    client: TestClient,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    make_exam: Callable[..., tuple[Exam, list[Question]]],
) -> None:
    """无已判分会话时 average_score 必须为 0（CHG-031），不是 null、不是 5xx。"""
    teacher = make_user("teacher-empty-results")
    exam, _ = make_exam(teacher, title="无人参加的考试")

    login(teacher.username)
    response = client.get(f"{API}/exams/{exam.id}/results")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_sessions"] == 0
    assert body["submitted"] == 0
    assert body["average_score"] == 0
    assert body["items"] == []
