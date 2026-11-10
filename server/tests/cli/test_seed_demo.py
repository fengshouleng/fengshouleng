"""TASK-BE-007：`seed-demo` 幂等与数据完整性测试。

覆盖：
- 空库写入 1 教师 + 2 学生 + 3 题（单选/多选/判断各 1）+ 1 份 100 分试卷
- 明细分值 40/30/30，且可直接用开发默认密码登录
- 重复执行幂等跳过（退出码 0，不产生重复数据）
- `--password-env` 覆盖默认密码；非空库拒绝执行（退出码 2）
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app import cli
from app.auth.service import hash_password
from app.models.paper import Paper
from app.models.paper_question import PaperQuestion
from app.models.question import Question
from app.models.user import Role, User

API = "/api"


def _seed(factory: sessionmaker[Session], *args: str) -> int:
    return cli.main(["seed-demo", *args], session_factory=factory)


def test_seed_demo_creates_expected_data_and_login_works(
    client: TestClient,
    db_session: Session,
    cli_session_factory: sessionmaker[Session],
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = _seed(cli_session_factory)
    captured = capsys.readouterr()

    assert exit_code == 0
    assert cli.DEFAULT_DEV_PASSWORD not in captured.out
    assert cli.DEFAULT_DEV_PASSWORD not in captured.err

    users = db_session.scalars(select(User).order_by(User.id)).all()
    by_role = {role.value: [user for user in users if user.role == role.value] for role in Role}
    assert len(by_role[Role.TEACHER.value]) == 1
    assert len(by_role[Role.STUDENT.value]) == 2
    assert {user.username for user in by_role[Role.TEACHER.value]} == {
        cli.DEMO_TEACHER_USERNAME
    }
    assert {user.username for user in by_role[Role.STUDENT.value]} == set(
        cli.DEMO_STUDENT_USERNAMES
    )

    questions = db_session.scalars(select(Question).order_by(Question.id)).all()
    assert sorted(question.type for question in questions) == [
        "judge",
        "multiple",
        "single",
    ]
    assert all(question.explanation for question in questions)

    paper = db_session.scalar(select(Paper))
    assert paper is not None
    assert paper.title == cli.DEMO_PAPER_TITLE
    assert paper.status == "draft"
    assert Decimal(paper.total_points) == Decimal("100.00")

    paper_questions = db_session.scalars(
        select(PaperQuestion).order_by(PaperQuestion.order_no)
    ).all()
    assert [Decimal(item.points) for item in paper_questions] == [
        Decimal("40.00"),
        Decimal("30.00"),
        Decimal("30.00"),
    ]
    assert [item.order_no for item in paper_questions] == [1, 2, 3]

    login = client.post(
        f"{API}/auth/login",
        json={
            "username": cli.DEMO_TEACHER_USERNAME,
            "password": cli.DEFAULT_DEV_PASSWORD,
        },
    )
    assert login.status_code == 200, login.text
    assert login.json()["user"]["role"] == "teacher"


def test_seed_demo_is_idempotent(
    db_session: Session,
    cli_session_factory: sessionmaker[Session],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _seed(cli_session_factory) == 0
    capsys.readouterr()

    second_code = _seed(cli_session_factory)
    captured = capsys.readouterr()

    assert second_code == 0
    assert "跳过" in captured.out

    assert len(db_session.scalars(select(User)).all()) == 3
    assert len(db_session.scalars(select(Question)).all()) == 3
    assert len(db_session.scalars(select(Paper)).all()) == 1
    assert len(db_session.scalars(select(PaperQuestion)).all()) == 3


def test_seed_demo_uses_password_env(
    client: TestClient,
    cli_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    custom_password = "custom-demo-pass-9"
    monkeypatch.setenv("DEMO_SEED_PASSWORD", custom_password)

    exit_code = _seed(cli_session_factory, "--password-env", "DEMO_SEED_PASSWORD")
    assert exit_code == 0

    login = client.post(
        f"{API}/auth/login",
        json={"username": cli.DEMO_TEACHER_USERNAME, "password": custom_password},
    )
    assert login.status_code == 200, login.text


def test_seed_demo_refuses_non_empty_database(
    db_session: Session,
    cli_session_factory: sessionmaker[Session],
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_session.add(
        User(
            username="existing-user",
            display_name="已有用户",
            role=Role.STUDENT.value,
            password_hash=hash_password("existing-pass-123"),
            is_active=True,
        )
    )
    db_session.commit()

    exit_code = _seed(cli_session_factory)
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "空库" in captured.err
    assert len(db_session.scalars(select(User)).all()) == 1
