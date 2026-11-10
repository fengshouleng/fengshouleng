"""TASK-BE-007：`create-admin` CLI 集成测试。

覆盖：
- `--password-env` 建号成功，打印用户名/角色且不泄露密码
- 密码也可从 stdin 读取；命令行不接受明文 `--password`
- 建号后可通过 `POST /api/auth/login` + `GET /api/auth/me` 真实登录
- 同名用户 → 退出码 2；密码来源缺失 → 退出码 2
"""

from __future__ import annotations

import io
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app import cli
from app.models.user import Role, User

API = "/api"
PASSWORD = "admin-strong-pass-1"
ENV_VAR = "TEST_ADMIN_PASSWORD"


def _run_create_admin(
    factory: sessionmaker[Session],
    *,
    username: str = "ops-admin",
    display_name: str = "运维管理员",
) -> int:
    return cli.main(
        [
            "create-admin",
            "--username",
            username,
            "--display-name",
            display_name,
            "--password-env",
            ENV_VAR,
        ],
        session_factory=factory,
    )


def test_create_admin_with_password_env_then_login(
    client: TestClient,
    db_session: Session,
    cli_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(ENV_VAR, PASSWORD)

    exit_code = _run_create_admin(cli_session_factory)
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "ops-admin" in captured.out
    assert Role.ADMIN.value in captured.out
    assert PASSWORD not in captured.out
    assert PASSWORD not in captured.err

    user = db_session.scalar(select(User).where(User.username == "ops-admin"))
    assert user is not None
    assert user.role == Role.ADMIN.value
    assert user.display_name == "运维管理员"
    assert user.password_hash != PASSWORD
    assert PASSWORD not in user.password_hash

    login = client.post(
        f"{API}/auth/login",
        json={"username": "ops-admin", "password": PASSWORD},
    )
    assert login.status_code == 200, login.text
    assert login.json()["user"]["role"] == "admin"

    me = client.get(f"{API}/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["user"]["username"] == "ops-admin"


def test_create_admin_reads_password_from_stdin(
    client: TestClient,
    db_session: Session,
    cli_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(f"{PASSWORD}\n"))

    exit_code = cli.main(
        [
            "create-admin",
            "--username",
            "stdin-admin",
            "--display-name",
            "标准输入管理员",
        ],
        session_factory=cli_session_factory,
    )
    assert exit_code == 0

    user = db_session.scalar(select(User).where(User.username == "stdin-admin"))
    assert user is not None and user.role == Role.ADMIN.value

    login = client.post(
        f"{API}/auth/login",
        json={"username": "stdin-admin", "password": PASSWORD},
    )
    assert login.status_code == 200, login.text


def test_create_admin_rejects_duplicate_username(
    cli_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(ENV_VAR, PASSWORD)

    assert _run_create_admin(cli_session_factory) == 0
    capsys.readouterr()

    duplicate_code = _run_create_admin(cli_session_factory)
    captured = capsys.readouterr()

    assert duplicate_code == 2
    assert "已存在" in captured.err
    assert PASSWORD not in captured.err


def test_create_admin_requires_password_source(
    cli_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("MISSING_PASSWORD_VAR", raising=False)

    exit_code = cli.main(
        [
            "create-admin",
            "--username",
            "no-password-admin",
            "--display-name",
            "无密码管理员",
            "--password-env",
            "MISSING_PASSWORD_VAR",
        ],
        session_factory=cli_session_factory,
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "未设置或为空" in captured.err


def test_create_admin_rejects_plaintext_password_argument(
    cli_session_factory: sessionmaker[Session],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(
            [
                "create-admin",
                "--username",
                "plain-admin",
                "--display-name",
                "明文密码管理员",
                "--password",
                "plaintext-password",
            ],
            session_factory=cli_session_factory,
        )

    assert exc_info.value.code == 2
