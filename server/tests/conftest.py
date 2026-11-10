"""pytest 公共 fixture：临时 SQLite + 覆盖配置 + TestClient。

不依赖真实数据库：每个测试用 `tmp_path` 下的独立 SQLite 文件，
并通过 `create_app(settings=...)` 注入测试配置、通过 dependency_overrides 注入 Session。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base, get_db
from app.main import create_app
from app.settings import Settings


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    """测试配置：临时 SQLite，短会话 TTL。"""
    return Settings(
        app_env="test",
        app_version="1.0.0",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        session_ttl_hours=1,
        request_id_header="X-Request-ID",
        api_prefix="/api",
    )


@pytest.fixture()
def engine(settings: Settings) -> Iterator[Engine]:
    """指向临时 SQLite 文件的 Engine（与 app.db 的缓存 engine 隔离）。"""
    created = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )
    Base.metadata.create_all(bind=created)
    try:
        yield created
    finally:
        created.dispose()


@pytest.fixture()
def db_session(engine: Engine) -> Iterator[Session]:
    """测试用 Session，测试结束关闭。"""
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def app(settings: Settings, db_session: Session) -> FastAPI:
    """应用实例，配置与数据库依赖均替换为测试版。"""
    application = create_app(settings=settings)

    def override_get_db() -> Iterator[Session]:
        yield db_session

    application.dependency_overrides[get_db] = override_get_db
    return application


@pytest.fixture()
def client(app: FastAPI) -> Iterator[TestClient]:
    """FastAPI TestClient（带 lifespan 上下文）。"""
    with TestClient(app) as test_client:
        yield test_client
