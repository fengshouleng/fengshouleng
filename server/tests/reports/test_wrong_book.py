"""错题本（AC-20）：去重 + 计数 + 排序 + 到期结算记录 + 重放不重复计数。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.exam import Exam
from app.models.exam_session import ExamSession
from app.models.question import Question
from app.models.user import Role, User
from app.models.wrong_book import WrongBook

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


def test_wrong_book_dedupes_and_counts_ac20(
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
    teacher = make_user("teacher-wrong-book")
    student = make_user("student-wrong-book", role=Role.STUDENT.value)

    specs = [
        {"type": "single", "points": 10, "answer": ["A"]},
        {"type": "single", "points": 10, "answer": ["A"]},
    ]
    exam_one, questions = make_exam(teacher, specs=specs, title="错题本一")
    qa, qb = questions

    # 第一场：两题都答错 → qa=1、qb=1
    _take(
        login=login,
        start_session=start_session,
        save_answers=save_answers,
        submit_session=submit_session,
        student=student,
        exam=exam_one,
        answers=[(qa.id, ["B"]), (qb.id, ["B"])],
    )

    # 第二场复用 qb 并再次答错 → qb=2（AC-20：列表中只出现一次）
    exam_two, _ = make_exam(
        teacher,
        specs=[{"type": "single", "points": 10, "answer": ["A"]}],
        questions=[qb],
        title="错题本二",
    )
    login(student.username)
    body = start_session(exam_two.id)
    second_session = int(body["id"])
    save_answers(second_session, 1, [(qb.id, ["B"])])
    submit_session(second_session)

    # 重复提交（replayed=true）不得重复计数
    replay = submit_session(second_session)
    assert replay["replayed"] is True

    # 直接查库：两条错题行，qb 的 wrong_count=2
    db_session.expire_all()
    rows = {
        entry.question_id: entry
        for entry in db_session.scalars(
            select(WrongBook).where(WrongBook.student_id == student.id)
        ).all()
    }
    assert set(rows) == {qa.id, qb.id}
    assert int(rows[qa.id].wrong_count) == 1
    assert int(rows[qb.id].wrong_count) == 2
    assert rows[qb.id].last_wrong_at is not None

    # 接口：wrong_count 降序（qb 在前），同题只出现一次
    response = client.get(f"{API}/me/wrong-questions", params={"page": 1, "page_size": 20})
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["total"] == 2
    assert page["page"] == 1
    assert page["page_size"] == 20
    assert [item["question_id"] for item in page["items"]] == [qb.id, qa.id]

    first = page["items"][0]
    assert first["wrong_count"] == 2
    assert first["type"] == "single"
    assert first["stem"] == qb.stem
    assert first["last_wrong_at"] is not None
    assert page["items"][1]["wrong_count"] == 1

    # 分页：page_size=1 第二页是 qa
    second_page = client.get(
        f"{API}/me/wrong-questions", params={"page": 2, "page_size": 1}
    ).json()
    assert [item["question_id"] for item in second_page["items"]] == [qa.id]


def test_wrong_book_records_on_expire_and_excludes_correct(
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
    teacher = make_user("teacher-wrong-expire")
    student = make_user("student-wrong-expire", role=Role.STUDENT.value)
    exam, questions = make_exam(
        teacher,
        specs=[
            {"type": "single", "points": 10, "answer": ["A"]},
            {"type": "single", "points": 10, "answer": ["A"]},
        ],
        title="错题到期考试",
    )
    correct_question, wrong_question = questions

    login(student.username)
    body = start_session(exam.id)
    session_id = int(body["id"])
    save_answers(
        session_id,
        1,
        [(correct_question.id, ["A"]), (wrong_question.id, ["B"])],
    )
    force_deadline(session_id)

    settled = client.get(f"{API}/sessions/{session_id}")
    assert settled.status_code == 200, settled.text
    assert settled.json()["status"] == "expired"

    db_session.expire_all()
    rows = {
        entry.question_id: entry
        for entry in db_session.scalars(
            select(WrongBook).where(WrongBook.student_id == student.id)
        ).all()
    }
    # 只记录答错的题（is_correct=False）；答对的题不进错题本
    assert set(rows) == {wrong_question.id}
    assert int(rows[wrong_question.id].wrong_count) == 1

    page = client.get(f"{API}/me/wrong-questions").json()
    assert page["total"] == 1
    assert page["items"][0]["question_id"] == wrong_question.id

    # 到期结算后再交卷（重复提交/重放）仍不重复计数
    client.post(f"{API}/sessions/{session_id}/submit", headers=csrf_headers())
    db_session.expire_all()
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(WrongBook)
            .where(WrongBook.student_id == student.id)
        )
        == 1
    )
    row = db_session.scalar(
        select(WrongBook).where(
            WrongBook.student_id == student.id,
            WrongBook.question_id == wrong_question.id,
        )
    )
    assert row is not None
    assert int(row.wrong_count) == 1
