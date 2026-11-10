"""会话测试公共 fixture：账号工厂、登录、CSRF、考试工厂（直接落库 + 真冻结）。

会话测试关注运行时行为（时间窗/到期/判分/幂等），因此考试直接落库：
题目 → 试卷 → `papers.service.freeze_paper`（真实快照）→ exam(published)。
时间控制通过 `make_exam(start_at=..., end_at=...)` 与 `force_deadline` 完成。
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
from app.models.answer import Answer  # noqa: F401  确保 Base.metadata 含三张新表
from app.models.exam import Exam, ExamStatus
from app.models.exam_session import ExamSession
from app.models.paper import Paper, PaperStatus
from app.models.paper_question import PaperQuestion
from app.models.question import Question
from app.models.user import Role, User
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
        is_active: bool = True,
    ) -> User:
        user = User(
            username=username,
            display_name=username,
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
    """直接建一场考试（含真实冻结快照），返回 `(exam, questions)`。"""

    def factory(
        teacher: User,
        *,
        specs: list[dict[str, Any]] | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        duration_min: int = 60,
        status: str = ExamStatus.PUBLISHED.value,
        shuffle_questions: bool = False,
        shuffle_options: bool = False,
        title: str = "会话测试考试",
    ) -> tuple[Exam, list[Question]]:
        now = datetime.now(UTC)
        starts = as_utc(start_at) if start_at is not None else now - timedelta(minutes=30)
        ends = as_utc(end_at) if end_at is not None else now + timedelta(minutes=90)
        resolved = list(specs or DEFAULT_SPECS)

        questions: list[Question] = []
        total = Decimal("0.00")
        for index, spec in enumerate(resolved, start=1):
            question_type = str(spec.get("type", "single"))
            stem = f"会话题目-{uuid4().hex[:10]}-{index}"
            points = Decimal(str(spec.get("points", 10)))
            question = Question(
                owner_id=teacher.id,
                type=question_type,
                stem=stem,
                stem_hash=compute_stem_hash(stem),
                options=spec.get("options") or _OPTIONS[question_type],
                answer=list(spec.get("answer") or ["A"]),
                explanation="会话测试解析",
                difficulty=3,
                tags=["会话测试"],
            )
            db_session.add(question)
            questions.append(question)
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
        for index, (question, spec) in enumerate(zip(questions, resolved, strict=True), start=1):
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
        return exam, questions

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


__all__ = ["API", "CSRF_HEADER", "DEFAULT_SPECS", "PASSWORD"]
