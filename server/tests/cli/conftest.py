"""CLI 测试公共 fixture。

复用根 `conftest.py` 的临时 SQLite `engine`；CLI 以独立的 Session 工厂运行，
保证测试覆盖「CLI 提交 → Web 端读库」的真实集成路径。
"""

from __future__ import annotations

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture()
def cli_session_factory(engine: Engine) -> sessionmaker[Session]:
    """指向临时 SQLite 的 Session 工厂，供 `app.cli.main(session_factory=...)` 注入。"""
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
