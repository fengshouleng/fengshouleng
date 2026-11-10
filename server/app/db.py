"""数据库引擎与会话依赖（SQLAlchemy 2.0）。

- `Base`：所有 ORM 模型的声明式基类（TASK-BE-000 不定义业务表）
- `build_engine` / `get_engine` / `get_sessionmaker`：engine 与 session 工厂
- `get_db`：FastAPI 依赖，按请求提供 Session 并保证关闭
- SQLite 时自动设置 `connect_args={"check_same_thread": False}`（本地与测试）
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache
from typing import Any

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.settings import Settings, get_settings

# PostgreSQL 连接池预算（TASK-QA-003 压测发现：默认 QueuePool size=5/overflow=10 在
# 100 并发下 30s 内被耗尽，导致 session start 全部 500、healthz 也超时）。
# 池大小可配置（DB_POOL_SIZE / DB_MAX_OVERFLOW / DB_POOL_TIMEOUT / DB_POOL_RECYCLE）：
# 单 worker 建议 20/30；多 worker 时按 PG max_connections 摊分（如 4 worker → 10/10）。


class Base(DeclarativeBase):
    """声明式基类；后续任务卡的模型继承自它，Alembic 通过 `Base.metadata` 自动发现。"""


def build_engine(settings: Settings | None = None) -> Engine:
    """按配置创建 Engine（测试可注入自定义 Settings）。"""
    resolved = settings or get_settings()
    kwargs: dict[str, Any] = {"pool_pre_ping": True}
    if resolved.is_sqlite:
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs.update(
            pool_size=resolved.db_pool_size,
            max_overflow=resolved.db_max_overflow,
            pool_timeout=resolved.db_pool_timeout,
            pool_recycle=resolved.db_pool_recycle,
        )
    return create_engine(resolved.database_url, **kwargs)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """进程内共享 Engine（懒加载，导入本模块不会连接数据库）。"""
    return build_engine()


@lru_cache(maxsize=1)
def get_sessionmaker() -> sessionmaker[Session]:
    """进程内共享 Session 工厂。"""
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)


def reset_engine() -> None:
    """清空缓存的 engine / sessionmaker（测试切换数据库或配置后调用）。

    同时清空业务层的进程内缓存（冻结题面视图、冻结快照），否则测试用同一 paper_id
    在不同数据库之间会命中上一条用例的缓存。
    """
    get_sessionmaker.cache_clear()
    get_engine.cache_clear()
    # 延迟导入，避免 db <-> service 循环依赖
    try:
        from app.scoring.service import clear_snapshot_cache
        from app.sessions.service import clear_sessions_cache

        clear_sessions_cache()
        clear_snapshot_cache()
    except Exception:  # pragma: no cover - 仅测试/运维路径，缓存清理失败不应中断
        pass


def get_db() -> Iterator[Session]:
    """FastAPI 依赖：yield 一个 Session，异常回滚，最终关闭。"""
    session = get_sessionmaker()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
