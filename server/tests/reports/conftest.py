"""reports（成绩/错题本/考试汇总）测试 fixture。

与 `tests/sessions/conftest.py` 同构，但增加两点：
- `make_exam(questions=[...])` 复用已有题目，用于构造「同一学生同一题错两次」的 AC-20 场景；
- 提供 `submit_session` 便捷 fixture。

考试直接落库：题目 → 试卷 → `papers.service.freeze_paper`（真实冻结快照）→ exam(published)，
因此逐题结果走的是真实快照路径（ADR-007）。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth.service import as_utc, hash_password
from app.models.answer import Answer  # noqa: F401  确保 Base.metadata 含 answers
from app.models.exam import Exam, ExamStatus
from app.models.exam_session import ExamSession
from app.models.paper import Paper, PaperStatus
from app.models.paper_question import PaperQuestion
from app.models.question import Question
from app.models.user import Role, User
from app.models.wrong_book import WrongBook  # noqa: F401  确保 Base.metadata 含 wrong_book
from app.papers import service as papers_service
from app.questions.service import compute_stem_hash

API = "/api"
PASSWORD = "correct-horse-123"
CSRF_HEADER = "X-CSRF-Token"

_OPTIONS: dict[str, list[dict[str, str]]] = {
    "single": [{"key": "A", "text": "选项一"}, {"key": "B", "text": "选项二"}],
    "judge": [{"key": "A", "text": "对"}, {"key": "B", "text": "错"}],
    "multiple": [
        {"key": "A", "text": "选项一"},
        {"key": "B", "text": "选项二"},
        {"key": "C", "text": "选项三"},
        {"key": "D", "text": "选项四"},
    ],
}

DEFAULT_SPECS: list[dict[str, Any]] = [
    {"type": "single", "points": 10, "answer": ["A"]},
]


@pytest.fixture()
def make_user(db_session: Session) -> Callable[..., User]:
    """创建并提交一个账号（默认教师）。"""

    def factory(
        username: str,
        *,
        role: str = Role.TEACHER.value,
        display_name: str | None = None,
        is_active: bool = True,
    ) -> User:
        user = User(
            username=username,
            display_name=display_name or username,
            role=role,
            password_hash=hash_password(PASSWORD),
            is_active=is_active,
        )
        db_session.add(user)
        db_session.commit()
        return user

    return factory


@pytest.fixture()
def login(client: TestClient) -> Callable[..., dict[str, Any]]:
    """登录并返回 `{user, csrf_token}`。"""

    def factory(username: str, password: str = PASSWORD) -> dict[str, Any]:
        response = client.post(
            f"{API}/auth/login",
            json={"username": username, "password": password},
        )
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        return body

    return factory


@pytest.fixture()
def csrf_headers(client: TestClient) -> Callable[[], dict[str, str]]:
    """读取当前会话的 CSRF token 并构造请求头。"""

    def factory() -> dict[str, str]:
        response = client.get(f"{API}/auth/me")
        assert response.status_code == 200, response.text
        token: str = response.json()["csrf_token"]
        return {CSRF_HEADER: token}

    return factory


@pytest.fixture()
def make_exam(
    db_session: Session,
) -> Callable[..., tuple[Exam, list[Question]]]:
    """直接建一场考试（含真实冻结快照），返回 `(exam, questions)`。

    `questions=[...]` 存在时复用已有题目（题数需与 specs 一致），用于跨考试同题场景。
    """

    def factory(
        teacher: User,
        *,
        specs: list[dict[str, Any]] | None = None,
        questions: list[Question] | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        duration_min: int = 60,
        status: str = ExamStatus.PUBLISHED.value,
        shuffle_questions: bool = False,
        shuffle_options: bool = False,
        title: str = "成绩测试考试",
    ) -> tuple[Exam, list[Question]]:
        now = datetime.now(UTC)
        starts = as_utc(start_at) if start_at is not None else now - timedelta(minutes=30)
        ends = as_utc(end_at) if end_at is not None else now + timedelta(minutes=90)
        resolved = list(specs or DEFAULT_SPECS)

        reuse = list(questions or [])
        if reuse and len(reuse) != len(resolved):
            raise AssertionError("questions 与 specs 数量必须一致")

        created: list[Question] = []
        total = Decimal("0.00")
        for index, spec in enumerate(resolved, start=1):
            if reuse:
                question = reuse[index - 1]
                created.append(question)
                total += Decimal(str(spec.get("points", 10)))
                continue
            question_type = str(spec.get("type", "single"))
            stem = f"成绩题目-{uuid4().hex[:10]}-{index}"
            points = Decimal(str(spec.get("points", 10)))
            question = Question(
                owner_id=teacher.id,
                type=question_type,
                stem=stem,
                stem_hash=compute_stem_hash(stem),
                options=spec.get("options") or _OPTIONS[question_type],
                answer=list(spec.get("answer") or ["A"]),
                explanation="成绩测试解析",
                difficulty=3,
                tags=["成绩测试"],
            )
            db_session.add(question)
            created.append(question)
            total += points
        db_session.flush()

        paper = Paper(
            owner_id=teacher.id,
            title=f"{title}-试卷-{uuid4().hex[:6]}",
            total_points=total,
            status=PaperStatus.DRAFT.value,
        )
        db_session.add(paper)
        db_session.flush()
        for index, (question, spec) in enumerate(zip(created, resolved, strict=True), start=1):
            db_session.add(
                PaperQuestion(
                    paper_id=paper.id,
                    question_id=question.id,
                    order_no=index,
                    points=Decimal(str(spec.get("points", 10))),
                    partial_rule=str(spec.get("partial_rule") or "all_or_nothing"),
                )
            )
        db_session.flush()
        papers_service.freeze_paper(db_session, paper, now=now)

        exam = Exam(
            paper_id=paper.id,
            title=title,
            start_at=starts,
            end_at=ends,
            duration_min=duration_min,
            shuffle_questions=shuffle_questions,
            shuffle_options=shuffle_options,
            status=status,
            snapshot_version=1 if status != ExamStatus.DRAFT.value else None,
            created_by=teacher.id,
        )
        db_session.add(exam)
        db_session.commit()
        return exam, created

    return factory


@pytest.fixture()
def start_session(
    client: TestClient,
    csrf_headers: Callable[[], dict[str, str]],
) -> Callable[..., dict[str, Any]]:
    """`POST /exams/{id}/sessions`：默认断言 201 并返回响应体。"""

    def factory(exam_id: int, *, expect: int = 201) -> dict[str, Any]:
        response = client.post(
            f"{API}/exams/{exam_id}/sessions",
            headers=csrf_headers(),
        )
        assert response.status_code == expect, response.text
        body: dict[str, Any] = response.json()
        return body

    return factory


@pytest.fixture()
def save_answers(
    client: TestClient,
    csrf_headers: Callable[[], dict[str, str]],
) -> Callable[..., dict[str, Any]]:
    """`PUT /sessions/{id}/answers`：answers=[(question_id, selected)]。"""

    def factory(
        session_id: int,
        client_seq: int,
        answers: list[tuple[int, list[str]]],
        *,
        expect: int = 200,
    ) -> dict[str, Any]:
        payload = {
            "client_seq": client_seq,
            "answers": [
                {"question_id": question_id, "selected": selected}
                for question_id, selected in answers
            ],
        }
        response = client.put(
            f"{API}/sessions/{session_id}/answers",
            json=payload,
            headers=csrf_headers(),
        )
        assert response.status_code == expect, response.text
        body: dict[str, Any] = response.json()
        return body

    return factory


@pytest.fixture()
def submit_session(
    client: TestClient,
    csrf_headers: Callable[[], dict[str, str]],
) -> Callable[..., dict[str, Any]]:
    """`POST /sessions/{id}/submit`：默认断言 200 并返回响应体。"""

    def factory(session_id: int, *, expect: int = 200) -> dict[str, Any]:
        response = client.post(
            f"{API}/sessions/{session_id}/submit",
            headers=csrf_headers(),
        )
        assert response.status_code == expect, response.text
        body: dict[str, Any] = response.json()
        return body

    return factory


@pytest.fixture()
def force_deadline(db_session: Session) -> Callable[..., ExamSession]:
    """把会话的 `deadline_at` 改到过去，用于「到期自动交卷」场景。"""

    def factory(
        session_id: int,
        *,
        when: datetime | None = None,
    ) -> ExamSession:
        session = db_session.get(ExamSession, session_id)
        assert session is not None
        session.deadline_at = when or (datetime.now(UTC) - timedelta(minutes=1))
        db_session.commit()
        return session

    return factory


def take_exam(
    *,
    login: Callable[..., dict[str, Any]],
    start_session: Callable[..., dict[str, Any]],
    save_answers: Callable[..., dict[str, Any]],
    submit_session: Callable[..., dict[str, Any]],
    student: User,
    exam: Exam,
    answers: list[tuple[int, list[str]]],
) -> int:
    """登录学生 → 开始考试 → 保存答案 → 交卷；返回 session_id。"""
    login(student.username)
    body = start_session(exam.id)
    session_id = int(body["id"])
    if answers:
        save_answers(session_id, 1, answers)
    submit_session(session_id)
    return session_id


__all__ = ["API", "CSRF_HEADER", "DEFAULT_SPECS", "PASSWORD", "take_exam"]
