"""冻结快照缓存与启动预热（docs/61 §10.2.11）。

缓存的前提是 `papers.frozen_snapshot` 冻结后不可变（`freeze_paper` 幂等早返回、
`update_paper` 拒绝已冻结试卷）。这里用「绕过业务层直接改库里的快照」来证明缓存
**确实命中**，再用 `clear_snapshot_cache()` 证明失效路径可控——避免将来有人给
「重新冻结」加能力时，读到旧快照却没人发现。

另一类用例守 `warm_up()` 的降级契约：SQLite / 关闭开关 / 注入配置与进程级 engine
不一致时直接跳过，且绝不抛异常（预热失败不能阻塞启动）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.models.paper import Paper
from app.models.user import User
from app.scoring import service as scoring
from app.settings import Settings
from app.warmup import warm_connection_pool, warm_up

SPECS: list[dict[str, Any]] = [{"type": "single", "points": 10, "answer": ["A"]}]


def test_snapshot_cache_hit_then_invalidated(
    db_session: Session,
    make_user: Any,
    make_exam: Any,
) -> None:
    teacher: User = make_user("teacher-snapshot-cache")
    exam, questions = make_exam(teacher, specs=SPECS)

    first = scoring.snapshot_questions(db_session, exam)
    assert [item["question_id"] for item in first] == [questions[0].id]

    paper = db_session.get(Paper, exam.paper_id)
    assert paper is not None
    mutated = dict(paper.frozen_snapshot or {})
    mutated["questions"] = [
        {**item, "stem": "被改过的题干"} if index == 0 else item
        for index, item in enumerate(mutated.get("questions", []))
    ]
    paper.frozen_snapshot = mutated
    db_session.commit()

    # 缓存命中：库里已经改了，但没清缓存就读不到（这正是「冻结快照不可变」的护栏）
    cached = scoring.snapshot_questions(db_session, exam)
    assert cached[0]["stem"] != "被改过的题干"

    scoring.clear_snapshot_cache()
    refreshed = scoring.snapshot_questions(db_session, exam)
    assert refreshed[0]["stem"] == "被改过的题干"


def test_snapshot_cache_returns_fresh_list_each_call(
    db_session: Session,
    make_user: Any,
    make_exam: Any,
) -> None:
    """命中时返回新 list（调用方 append 不能污染缓存）。"""
    teacher: User = make_user("teacher-snapshot-copy")
    exam, _ = make_exam(teacher, specs=SPECS)

    first = scoring.snapshot_questions(db_session, exam)
    first.append({"question_id": -1})
    second = scoring.snapshot_questions(db_session, exam)
    assert all(int(item["question_id"]) > 0 for item in second)
    assert len(second) == 1


def test_snapshot_cache_key_includes_database_url(
    db_session: Session,
    make_user: Any,
    make_exam: Any,
) -> None:
    """同一 paper_id 在不同数据库里不能串味（2026-11-11 首次加缓存踩过的坑）。"""
    teacher: User = make_user("teacher-snapshot-key")
    exam, _ = make_exam(teacher, specs=SPECS)
    scoring.clear_snapshot_cache()
    scoring.snapshot_questions(db_session, exam)
    assert len(scoring._SNAPSHOT_CACHE) == 1
    (key,) = scoring._SNAPSHOT_CACHE
    assert key[0].startswith("sqlite")  # 键里带数据库 URL，不只是 paper_id
    assert key[1] == exam.paper_id


def test_reset_engine_clears_business_caches(
    db_session: Session,
    make_user: Any,
    make_exam: Any,
) -> None:
    """`reset_engine()` 是切换数据库的唯一入口，必须同时清业务缓存。"""
    from app.db import reset_engine

    teacher: User = make_user("teacher-reset-engine")
    exam, _ = make_exam(teacher, specs=SPECS)
    scoring.snapshot_questions(db_session, exam)
    assert scoring._SNAPSHOT_CACHE

    reset_engine()

    assert not scoring._SNAPSHOT_CACHE


def test_warm_up_skips_sqlite(settings: Settings) -> None:
    """SQLite（测试/本地默认）没有连接池冷启动成本：直接跳过且不抛异常。"""
    assert warm_up(settings) == {"enabled": True, "skipped": "sqlite"}


def test_warm_up_disabled_by_setting(settings: Settings) -> None:
    disabled = settings.model_copy(update={"warmup_enabled": False})
    assert warm_up(disabled) == {"enabled": False}


def test_warm_up_skips_when_engine_does_not_match_settings(settings: Settings) -> None:
    """注入的 PostgreSQL 配置与进程级 engine（默认 SQLite）不一致时不预热。"""
    custom = settings.model_copy(
        update={
            "database_url": "postgresql+psycopg://exam:secret@127.0.0.1:55432/exam",
            "db_pool_size": 1,
            "db_max_overflow": 0,
        }
    )
    assert warm_up(custom) == {"enabled": True, "skipped": "settings-mismatch"}


def test_warm_connection_pool_skips_sqlite(engine: Engine) -> None:
    assert warm_connection_pool(engine, 8) == 0
