"""判分规则（AC-16 / AC-17 / ADR-007）：表驱动 + 端到端落库校验。"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.answer import Answer
from app.models.exam import Exam
from app.models.exam_session import ExamSession
from app.models.question import Question
from app.models.user import Role, User
from app.scoring.service import score_question

API = "/api"


@pytest.mark.parametrize(
    (
        "question_type",
        "partial_rule",
        "answer",
        "selected",
        "points",
        "expected_is_correct",
        "expected_points",
    ),
    [
        # single / judge：答案集合完全相等才是满分
        ("single", "all_or_nothing", ["A"], ["A"], "10", True, "10.00"),
        ("single", "all_or_nothing", ["A"], ["B"], "10", False, "0.00"),
        ("single", "all_or_nothing", ["A"], ["A", "B"], "10", False, "0.00"),
        ("single", "all_or_nothing", ["A"], [], "10", False, "0.00"),
        ("judge", "all_or_nothing", ["B"], ["B"], "5.25", True, "5.25"),
        ("judge", "all_or_nothing", ["B"], ["A"], "5", False, "0.00"),
        # multiple + all_or_nothing：全对满分，否则 0
        ("multiple", "all_or_nothing", ["A", "C"], ["C", "A"], "20", True, "20.00"),
        ("multiple", "all_or_nothing", ["A", "C"], ["A"], "20", False, "0.00"),
        ("multiple", "all_or_nothing", ["A", "C"], ["A", "B", "C"], "20", False, "0.00"),
        ("multiple", "all_or_nothing", ["A", "C"], [], "20", False, "0.00"),
        # multiple + partial：无错选按比例；有错选 0
        ("multiple", "partial", ["A", "B", "C"], ["A"], "30", False, "10.00"),
        ("multiple", "partial", ["A", "B", "C"], ["A", "B"], "10", False, "6.67"),
        ("multiple", "partial", ["A", "B", "C"], ["A", "B"], "5.25", False, "3.50"),
        ("multiple", "partial", ["A", "B", "C"], ["A", "B", "C"], "10", True, "10.00"),
        ("multiple", "partial", ["A", "B", "C"], ["A", "D"], "10", False, "0.00"),
        ("multiple", "partial", ["A", "B", "C"], [], "10", False, "0.00"),
    ],
)
def test_score_question_table(
    question_type: str,
    partial_rule: str,
    answer: list[str],
    selected: list[str],
    points: str,
    expected_is_correct: bool,
    expected_points: str,
) -> None:
    is_correct, earned = score_question(
        question_type=question_type,
        partial_rule=partial_rule,
        answer=answer,
        selected=selected,
        points=Decimal(points),
    )
    assert is_correct is expected_is_correct
    assert earned == Decimal(expected_points)


def test_score_question_without_correct_answer_is_zero() -> None:
    """脏数据兜底：没有正确答案的题不给分（不抛错）。"""
    is_correct, earned = score_question(
        question_type="single",
        partial_rule="all_or_nothing",
        answer=[],
        selected=["A"],
        points=Decimal("10"),
    )
    assert (is_correct, earned) == (False, Decimal("0.00"))


SPECS: list[dict[str, Any]] = [
    {"type": "single", "points": 10, "answer": ["A"]},
    {"type": "multiple", "points": 20, "answer": ["A", "C"]},
    {"type": "multiple", "points": 30, "answer": ["A", "B", "C"], "partial_rule": "partial"},
    {"type": "judge", "points": 5, "answer": ["A"]},
    {"type": "multiple", "points": 5.25, "answer": ["A", "B", "C"], "partial_rule": "partial"},
    {"type": "multiple", "points": 10, "answer": ["A", "B"], "partial_rule": "partial"},
]

SELECTED: list[list[str]] = [
    ["A"],  # 全对
    ["A"],  # 少选（all_or_nothing → 0）
    ["A"],  # 少选无错选（partial → 1/3 × 30）
    ["B"],  # 错选
    ["A", "B"],  # 少选无错选（partial → 2/3 × 5.25）
    ["A", "C"],  # 有错选（partial → 0）
]

EXPECTED: list[tuple[bool, str]] = [
    (True, "10.00"),
    (False, "0.00"),
    (False, "10.00"),
    (False, "0.00"),
    (False, "3.50"),
    (False, "0.00"),
]


def test_end_to_end_scoring_persists_answers_and_session_score(
    client: TestClient,
    db_session: Session,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
    make_exam: Callable[..., tuple[Exam, list[Question]]],
    start_session: Callable[..., dict[str, Any]],
    save_answers: Callable[..., dict[str, Any]],
) -> None:
    teacher = make_user("teacher-scoring")
    student = make_user("student-scoring", role=Role.STUDENT.value)
    exam, questions = make_exam(teacher, specs=SPECS)
    assert len(questions) == len(SELECTED) == len(EXPECTED)

    login(student.username)
    body = start_session(exam.id)
    session_id = body["id"]
    save_answers(
        session_id,
        1,
        [
            (question.id, selected)
            for question, selected in zip(questions, SELECTED, strict=True)
        ],
    )

    submitted = client.post(
        f"{API}/sessions/{session_id}/submit",
        headers=csrf_headers(),
    )
    assert submitted.status_code == 200, submitted.text
    payload = submitted.json()
    assert payload["status"] == "submitted"
    # 10 + 0 + 10.00 + 0 + 3.50 + 0 = 23.50
    assert payload["score"] == pytest.approx(23.5)

    db_session.expire_all()
    stored = db_session.get(ExamSession, session_id)
    assert stored is not None
    assert float(stored.score or 0) == pytest.approx(23.5)

    rows = {
        row.question_id: row
        for row in db_session.scalars(
            select(Answer).where(Answer.session_id == session_id)
        ).all()
    }
    for question, (is_correct, earned) in zip(questions, EXPECTED, strict=True):
        row = rows[question.id]
        assert row.is_correct is is_correct, question.id
        assert float(row.earned_points or 0) == pytest.approx(float(Decimal(earned))), question.id

    # 交卷后的会话状态（含成绩）可读
    state = client.get(f"{API}/sessions/{session_id}").json()
    assert state["status"] == "submitted"
    assert state["score"] == pytest.approx(23.5)
    assert state["submitted_at"] is not None
