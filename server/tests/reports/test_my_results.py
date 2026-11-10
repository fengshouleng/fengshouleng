"""`GET /me/results`（C-06）：当前学生已判分成绩分页；仅 student。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

from app.models.exam import Exam
from app.models.question import Question
from app.models.user import Role, User

API = "/api"


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
    save_answers(session_id, 1, answers)
    submit_session(session_id)
    return session_id


def test_my_results_scope_pagination_and_permissions(
    client: TestClient,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
    make_exam: Callable[..., tuple[Exam, list[Question]]],
    start_session: Callable[..., dict[str, Any]],
    save_answers: Callable[..., dict[str, Any]],
    submit_session: Callable[..., dict[str, Any]],
) -> None:
    teacher = make_user("teacher-my-results")
    student = make_user("student-my-results", role=Role.STUDENT.value)
    other = make_user("student-my-results-other", role=Role.STUDENT.value)

    exam_one, questions_one = make_exam(
        teacher,
        specs=[{"type": "single", "points": 10, "answer": ["A"]}],
        title="我的成绩一",
    )
    exam_two, questions_two = make_exam(
        teacher,
        specs=[{"type": "single", "points": 5, "answer": ["A"]}],
        title="我的成绩二",
    )
    # 该生第二场只开始不交卷，不应出现在成绩列表
    in_progress_exam, _ = make_exam(
        teacher,
        specs=[{"type": "single", "points": 10, "answer": ["A"]}],
        title="未交卷场次",
    )

    first_session = _take(
        login=login,
        start_session=start_session,
        save_answers=save_answers,
        submit_session=submit_session,
        student=student,
        exam=exam_one,
        answers=[(questions_one[0].id, ["A"])],
    )
    second_session = _take(
        login=login,
        start_session=start_session,
        save_answers=save_answers,
        submit_session=submit_session,
        student=student,
        exam=exam_two,
        answers=[(questions_two[0].id, ["B"])],
    )
    # 同场考试另一学生的成绩不应出现
    _take(
        login=login,
        start_session=start_session,
        save_answers=save_answers,
        submit_session=submit_session,
        student=other,
        exam=exam_one,
        answers=[(questions_one[0].id, ["A"])],
    )

    login(student.username)
    start_session(in_progress_exam.id)

    page = client.get(f"{API}/me/results", params={"page": 1, "page_size": 10})
    assert page.status_code == 200, page.text
    body = page.json()
    assert body["total"] == 2
    assert body["page"] == 1
    assert body["page_size"] == 10
    assert len(body["items"]) == 2

    by_session = {item["session_id"]: item for item in body["items"]}
    assert set(by_session) == {first_session, second_session}

    first = by_session[first_session]
    assert first["exam_id"] == exam_one.id
    assert first["exam_title"] == "我的成绩一"
    assert first["score"] == 10.0
    assert first["total_points"] == 10.0
    assert first["submitted_at"] is not None
    assert by_session[second_session]["score"] == 0.0
    assert by_session[second_session]["total_points"] == 5.0

    # 分页：page_size=1 两页，按交卷时间倒序（第二场更晚 → 第一页）
    first_page = client.get(f"{API}/me/results", params={"page": 1, "page_size": 1}).json()
    assert first_page["total"] == 2
    assert len(first_page["items"]) == 1
    assert first_page["items"][0]["session_id"] == second_session
    second_page = client.get(f"{API}/me/results", params={"page": 2, "page_size": 1}).json()
    assert [item["session_id"] for item in second_page["items"]] == [first_session]

    # 仅 student：teacher / admin 一律 403
    login(teacher.username)
    assert client.get(f"{API}/me/results").status_code == 403
    admin = make_user("admin-my-results", role=Role.ADMIN.value)
    login(admin.username)
    assert client.get(f"{API}/me/results").status_code == 403
    assert client.get(f"{API}/me/wrong-questions").status_code == 403
