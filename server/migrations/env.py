"""Alembic 迁移环境。

- 连接串来自 `app.settings`（环境变量 `DATABASE_URL`），不写死在 alembic.ini
- 元数据来自 `app.db.Base.metadata`（业务模型全部继承 Base）
- TASK-BE-000 只建立空基线；业务表迁移从 TASK-BE-001 起添加
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import Connection

# 导入全部 ORM 模型模块，保证 `Base.metadata` 完整（autogenerate 需要）。
# 注意：仅导入 `app.models` 包不会触发子模块，必须显式逐个导入。
import app.models.answer  # noqa: F401
import app.models.audit_log  # noqa: F401
import app.models.auth_session  # noqa: F401
import app.models.exam  # noqa: F401
import app.models.exam_session  # noqa: F401
import app.models.paper  # noqa: F401
import app.models.paper_question  # noqa: F401
import app.models.question  # noqa: F401
import app.models.user  # noqa: F401
import app.models.wrong_book  # noqa: F401
from app.db import Base
from app.settings import get_settings

config = context.config
settings = get_settings()

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# configparser 会把 % 当插值符，URL 中的百分号需转义
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不建立连接。"""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：按 DATABASE_URL 建立连接后执行迁移。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        _run_migrations(connection)
        connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
