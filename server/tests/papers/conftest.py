"""试卷测试公共 fixture：账号工厂、登录、CSRF、题目工厂。

题目直接落库（不经过 /questions），让本卡测试只依赖已冻结的题库模型；
每个测试使用独立的临时 SQLite（见 server/tests/conftest.py）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth.service import hash_password
from app.models.paper import Paper  # noqa: F401  确保 Base.metadata 含 papers
from app.models.paper_question import PaperQuestion  # noqa: F401  metadata
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
    """创建一道合法题目；默认单选题，题干自动去重。"""
    counter = {"n": 0}

    def factory(
        owner: User,
        *,
        question_type: str = "single",
        stem: str | None = None,
        options: list[dict[str, str]] | None = None,
        answer: list[str] | None = None,
        is_deleted: bool = False,
    ) -> Question:
        counter["n"] += 1
        resolved_stem = stem or f"试卷测试题目 {counter['n']}"
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
            tags=["试卷测试"],
            is_deleted=is_deleted,
        )
        db_session.add(question)
        db_session.commit()
        return question

    return factory
