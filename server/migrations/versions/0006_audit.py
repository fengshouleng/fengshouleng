"""审计索引补齐（TASK-BE-006；docs/11-data-model.md#audit_logs）

Revision ID: 0006_audit
Revises: 0005_wrong_book
Create Date: 2026-11-09

背景：`audit_logs` 表由 `0001_auth` 创建，该迁移在全新库上已同时建立
`ix_audit_created` 与 `ix_audit_actor_action`。本迁移**不重建表**，只在
已有库恰好缺少索引时补建，因此必须幂等：

- 在线模式用 `sqlalchemy.inspect(bind).get_indexes("audit_logs")` 先查再建；
- 两个索引都存在时 upgrade 不执行任何 DDL（全新库升级路径即此情形）；
- 离线模式（`--sql`）无法反射，改用 `CREATE INDEX IF NOT EXISTS`（同样幂等）。

downgrade 故意为 no-op：索引归属 `0001_auth`，若在 0006 回滚时删除它们，
会破坏 0001 的 downgrade 预期（重复 drop）。本迁移只在 0001 未建索引的
异常历史库上产生实际变更，保留索引比回滚更安全。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "0006_audit"
down_revision: str | None = "0005_wrong_book"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "audit_logs"
_INDEXES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ix_audit_created", ("created_at",)),
    ("ix_audit_actor_action", ("actor_id", "action")),
)


def _existing_index_names(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {
        str(index["name"])
        for index in inspector.get_indexes(table)
        if index.get("name") is not None
    }


def upgrade() -> None:
    # 离线模式（`alembic upgrade --sql`）无法反射数据库，改用幂等 DDL；
    # PostgreSQL / SQLite 均支持 CREATE INDEX IF NOT EXISTS。
    if context.is_offline_mode():
        for name, columns in _INDEXES:
            op.execute(
                f"CREATE INDEX IF NOT EXISTS {name} ON {_TABLE} "
                f"({', '.join(columns)})"
            )
        return

    existing = _existing_index_names(_TABLE)
    for name, columns in _INDEXES:
        if name in existing:
            continue
        op.create_index(name, _TABLE, list(columns))


def downgrade() -> None:
    """无操作：索引由 0001_auth 负责创建/删除（见模块 docstring）。"""
    return None
