"""TASK-BE-007：`/healthz` 数据库探针测试。

覆盖：
- 正常时 200 + `{status: "ok", version, db: "ok"}`（`/healthz` 与 `/api/healthz`）
- DB 不可用时 503，响应体为统一错误格式 `{code, message, request_id}`
  （`code=INTERNAL_ERROR`），并保留入站 X-Request-ID
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import get_db

ERROR_KEYS = {"code", "message", "request_id"}
HEALTH_PATHS = ("/healthz", "/api/healthz")


def _override_with_broken_database(
    app: FastAPI,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """把 get_db 覆盖为指向不存在目录的 SQLite 会话，模拟 DB 不可用。"""
    database_path = tmp_path / "missing-directory" / "broken.db"
    broken_engine = create_engine(f"sqlite:///{database_path}")
    broken_factory = sessionmaker(
        bind=broken_engine,
        autoflush=False,
        expire_on_commit=False,
    )

    def broken_db() -> Iterator[Session]:
        session = broken_factory()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setitem(app.dependency_overrides, get_db, broken_db)


@pytest.mark.parametrize("path", HEALTH_PATHS)
def test_healthz_reports_database_ok(client: TestClient, path: str) -> None:
    response = client.get(path)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["version"]
    assert payload["db"] == "ok"


@pytest.mark.parametrize("path", HEALTH_PATHS)
def test_healthz_returns_503_when_database_unavailable(
    app: FastAPI,
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    _override_with_broken_database(app, tmp_path, monkeypatch)

    response = client.get(path, headers={"X-Request-ID": "req-db-down"})

    assert response.status_code == 503
    payload = response.json()
    assert set(payload) == ERROR_KEYS
    assert payload["code"] == "INTERNAL_ERROR"
    assert isinstance(payload["message"], str) and payload["message"]
    assert payload["request_id"] == "req-db-down"
    assert response.headers["X-Request-ID"] == "req-db-down"
