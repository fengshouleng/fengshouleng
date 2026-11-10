"""TASK-BE-007：`check` 自检命令测试。

覆盖：
- 迁移版本、DB 连通性、管理员数量三项均正常时退出码 0
- 缺少 alembic_version 表（迁移未执行）时退出码 1，并在 stderr 给出原因
"""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from app import cli

ADMIN_PASSWORD = "check-admin-pass-1"


def _create_admin(factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHECK_ADMIN_PASSWORD", ADMIN_PASSWORD)
    exit_code = cli.main(
        [
            "create-admin",
            "--username",
            "check-admin",
            "--display-name",
            "自检管理员",
            "--password-env",
            "CHECK_ADMIN_PASSWORD",
        ],
        session_factory=factory,
    )
    assert exit_code == 0


def test_check_reports_version_connectivity_and_admin_count(
    engine: Engine,
    cli_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _create_admin(cli_session_factory, monkeypatch)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        )
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES ('0003_papers')")
        )

    exit_code = cli.main(["check"], session_factory=cli_session_factory)
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "0003_papers" in captured.out
    assert "数据库连通性: ok" in captured.out
    assert "管理员数量: 1" in captured.out
    assert captured.err == ""


def test_check_fails_when_migrations_missing(
    cli_session_factory: sessionmaker[Session],
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = cli.main(["check"], session_factory=cli_session_factory)
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "alembic" in captured.err
    assert "未知" in captured.out
