"""考试测试公共 fixture：账号工厂、登录、CSRF、题目/试卷/考试工厂。

考试一律通过公开 API 创建（覆盖 `publish` 同事务冻结与状态机），题目直接落库、
试卷走 `POST /papers`（复用 TASK-BE-003 的组卷校验）。每个测试使用独立临时 SQLite。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth.service import hash_password
from app.models.answer import Answer  # noqa: F401  确保 Base.metadata 含三张新表
from app.models.exam import Exam  # noqa: F401
from app.models.exam_session import ExamSession  # noqa: F401
from app.models.paper import Paper  # noqa: F401
from app.models.paper_question import PaperQuestion  # noqa: F401
from app.models.question import Question
from app.models.user import Role, User
from app.questions.service import compute_stem_hash

API = "/api"
PASSWORD = "correct-horse-123"
CSRF_HEADER = "X-CSRF-Token"


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
def make_question(db_session: Session) -> Callable[..., Question]:
    """创建一道合法题目；默认单选（答案 B），题干自动去重。"""
    counter = {"n": 0}

    def factory(
        owner: User,
        *,
        question_type: str = "single",
        stem: str | None = None,
        options: list[dict[str, str]] | None = None,
        answer: list[str] | None = None,
    ) -> Question:
        counter["n"] += 1
        resolved_stem = stem or f"考试测试题目 {counter['n']}-{counter['n'] * 7919}"
        if options is None:
            if question_type == "judge":
                options = [{"key": "A", "text": "对"}, {"key": "B", "text": "错"}]
                answer = answer or ["A"]
            elif question_type == "multiple":
                options = [
                    {"key": "A", "text": "选项一"},
                    {"key": "B", "text": "选项二"},
                    {"key": "C", "text": "选项三"},
                ]
                answer = answer or ["A", "C"]
            else:
                options = [
                    {"key": "A", "text": "选项一"},
                    {"key": "B", "text": "选项二"},
                ]
                answer = answer or ["B"]

        question = Question(
            owner_id=owner.id,
            type=question_type,
            stem=resolved_stem,
            stem_hash=compute_stem_hash(resolved_stem),
            options=options,
            answer=answer,
            explanation="测试解析",
            difficulty=3,
            tags=["考试测试"],
        )
        db_session.add(question)
        db_session.commit()
        return question

    return factory


@pytest.fixture()
def make_paper(
    client: TestClient,
    csrf_headers: Callable[[], dict[str, str]],
) -> Callable[..., dict[str, Any]]:
    """通过 `POST /papers` 组卷；entries=[{question, points, partial_rule?}]。"""

    def factory(
        entries: list[dict[str, Any]],
        *,
        title: str = "考试用试卷",
    ) -> dict[str, Any]:
        questions: list[dict[str, Any]] = []
        for index, entry in enumerate(entries, start=1):
            item: dict[str, Any] = {
                "question_id": entry["question"].id,
                "order_no": index,
                "points": entry["points"],
            }
            if entry.get("partial_rule") is not None:
                item["partial_rule"] = entry["partial_rule"]
            questions.append(item)
        response = client.post(
            f"{API}/papers",
            json={"title": title, "questions": questions},
            headers=csrf_headers(),
        )
        assert response.status_code == 201, response.text
        body: dict[str, Any] = response.json()
        return body

    return factory


@pytest.fixture()
def create_exam(
    client: TestClient,
    csrf_headers: Callable[[], dict[str, str]],
) -> Callable[..., dict[str, Any]]:
    """通过 `POST /exams` 创建考试；默认时间窗为 [now-10min, now+110min]。"""

    def factory(
        paper_id: int,
        *,
        title: str = "期末考试",
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        duration_min: int = 60,
        publish: bool = False,
        shuffle_questions: bool = False,
        shuffle_options: bool = False,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        payload = {
            "paper_id": paper_id,
            "title": title,
            "start_at": (start_at or now - timedelta(minutes=10)).isoformat(),
            "end_at": (end_at or now + timedelta(minutes=110)).isoformat(),
            "duration_min": duration_min,
            "shuffle_questions": shuffle_questions,
            "shuffle_options": shuffle_options,
            "publish": publish,
        }
        response = client.post(
            f"{API}/exams",
            json=payload,
            headers=csrf_headers(),
        )
        assert response.status_code == 201, response.text
        body: dict[str, Any] = response.json()
        return body

    return factory


__all__ = ["API", "CSRF_HEADER", "PASSWORD"]
