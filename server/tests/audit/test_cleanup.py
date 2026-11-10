"""保留策略 CLI：`python -m app.audit.cleanup --days 180 [--dry-run]`。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.audit import cleanup
from app.models.audit_log import AuditLog


def _factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _seed(db: Session, action: str, *, days_ago: float) -> AuditLog:
    row = AuditLog(
        actor_id=None,
        action=action,
        target_type="test",
        target_id="1",
        ip="127.0.0.1",
        detail=None,
        created_at=datetime.now(UTC) - timedelta(days=days_ago),
    )
    db.add(row)
    db.commit()
    return row


def _count(db: Session) -> int:
    return int(db.scalar(select(func.count()).select_from(AuditLog)) or 0)


def test_dry_run_counts_but_does_not_delete(
    engine: Engine,
    db_session: Session,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed(db_session, "test.cleanup.old.1", days_ago=200)
    _seed(db_session, "test.cleanup.old.2", days_ago=300)
    _seed(db_session, "test.cleanup.recent", days_ago=10)

    code = cleanup.main(
        ["--days", "180", "--dry-run"],
        session_factory=_factory(engine),
    )
    assert code == cleanup.EXIT_OK
    output = capsys.readouterr().out
    assert "dry-run" in output
    assert "2" in output
    assert _count(db_session) == 3  # 一条未删


def test_delete_expired_records(
    engine: Engine,
    db_session: Session,
    capsys: pytest.CaptureFixture[str],
) -> None:
    old = _seed(db_session, "test.cleanup.old", days_ago=200)
    recent = _seed(db_session, "test.cleanup.recent", days_ago=10)
    old_id = old.id
    recent_id = recent.id

    code = cleanup.main(["--days", "180"], session_factory=_factory(engine))
    assert code == cleanup.EXIT_OK
    assert "已删除 1 条" in capsys.readouterr().out

    remaining = db_session.scalars(select(AuditLog).order_by(AuditLog.id)).all()
    assert [row.id for row in remaining] == [recent_id]
    assert old_id not in {row.id for row in remaining}

    # 再跑一次幂等：没有可删记录，退出码仍为 0
    again = cleanup.main(["--days", "180"], session_factory=_factory(engine))
    assert again == cleanup.EXIT_OK


def test_custom_days_and_cutoff_boundary(
    db_session: Session,
) -> None:
    moment = datetime(2026, 11, 9, 3, 0, 0, tzinfo=UTC)
    exact = AuditLog(
        actor_id=None,
        action="test.cleanup.exact",
        created_at=moment - timedelta(days=30),
    )
    expired = AuditLog(
        actor_id=None,
        action="test.cleanup.expired",
        created_at=moment - timedelta(days=30, seconds=1),
    )
    db_session.add_all([exact, expired])
    db_session.commit()
    exact_id = exact.id
    expired_id = expired.id

    # 严格小于 cutoff 才算过期：恰好 30 天的记录保留
    removed = cleanup.cleanup(db_session, days=30, dry_run=True, now=moment)
    assert removed == 1

    # 直接调用非 dry-run 也返回条数并提交
    removed = cleanup.cleanup(db_session, days=30, now=moment)
    assert removed == 1
    remaining_ids = set(db_session.scalars(select(AuditLog.id)).all())
    assert exact_id in remaining_ids
    assert expired_id not in remaining_ids


def test_database_failure_returns_non_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def broken_factory() -> Session:
        raise SQLAlchemyError("database is down")

    code = cleanup.main(["--days", "180"], session_factory=broken_factory)
    assert code == cleanup.EXIT_FAILURE
    error = capsys.readouterr().err
    assert "审计清理失败" in error
    assert "database is down" not in error


def test_invalid_days_is_usage_error(
    engine: Engine,
    db_session: Session,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed(db_session, "test.cleanup.keep", days_ago=10)

    code = cleanup.main(["--days", "0"], session_factory=_factory(engine))
    assert code == cleanup.EXIT_USAGE
    assert "--days" in capsys.readouterr().err
    assert _count(db_session) == 1
