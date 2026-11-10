"""审计 detail 脱敏兜底：递归丢弃敏感键、截断长字符串。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.audit import service as audit
from app.models.audit_log import AuditLog
from app.models.user import User


def test_sanitize_detail_removes_sensitive_keys_recursively(
    db_session: Session,
    make_user: Callable[..., User],
) -> None:
    actor = make_user("teacher-audit-sanitize")
    raw_detail: dict[str, Any] = {
        "password": "plain-password",
        "session_token": "server-token",
        "client_secret": "secret-value",
        "authorization": "Bearer abc",
        "cookie": "session_id=xxx",
        "answer": ["A"],
        "keep": "visible",
        "nested": {
            "token": "nested-token",
            "keep": "nested-visible",
            "deeper": {"cookie": "nested-cookie", "value": 1},
        },
        "items": [
            {"password": "row-password", "label": "first"},
            {"answer": "B", "label": "second"},
        ],
        "long": "x" * 300,
    }

    entry = audit.record(
        db_session,
        action="test.sanitize",
        actor_id=actor.id,
        target_type="test",
        target_id=1,
        ip="127.0.0.1",
        detail=raw_detail,
    )
    db_session.commit()
    db_session.expire_all()  # 强制从 JSON 列重新读取，验证落库结果

    stored = db_session.get(AuditLog, entry.id)
    assert stored is not None
    assert stored.detail == {
        "keep": "visible",
        "nested": {
            "keep": "nested-visible",
            "deeper": {"value": 1},
        },
        "items": [
            {"label": "first"},
            {"label": "second"},
        ],
        "long": "x" * 255,
    }

    # 调用方传入的 dict 不被就地修改
    assert raw_detail["password"] == "plain-password"
    assert raw_detail["nested"]["token"] == "nested-token"


def test_sanitize_detail_handles_none_and_empty() -> None:
    assert audit.sanitize_detail(None) is None
    assert audit.sanitize_detail({}) == {}
