"""题库表 questions（docs/11-data-model.md#questions）

Revision ID: 0002_questions
Revises: 0001_auth
Create Date: 2026-11-09

- `options` / `answer` / `tags`：PostgreSQL JSONB，SQLite JSON
- `stem_hash` = md5(规范化题干)，与 `(owner_id, type)` 组成导入幂等唯一键
- 软删除 `is_deleted`；索引 owner/type；PostgreSQL 额外建 GIN(tags)
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_questions"
down_revision: str | None = "0001_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GIN_INDEX_NAME = "ix_questions_tags_gin"


def _bigint() -> sa.types.TypeEngine[object]:
    """PostgreSQL 用 BIGSERIAL；SQLite 退化为 INTEGER 才能自增。"""
    return sa.BigInteger().with_variant(sa.Integer, "sqlite")


def _json_type() -> sa.types.TypeEngine[object]:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "questions",
        sa.Column("id", _bigint(), primary_key=True, autoincrement=True),
        sa.Column(
            "owner_id",
            _bigint(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("stem", sa.Text(), nullable=False),
        sa.Column("stem_hash", sa.String(length=32), nullable=False),
        sa.Column("options", _json_type(), nullable=False),
        sa.Column("answer", _json_type(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("difficulty", sa.SmallInteger(), nullable=False, server_default=sa.text("3")),
        sa.Column("tags", _json_type(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "type IN ('single', 'multiple', 'judge')",
            name="ck_questions_type",
        ),
        sa.CheckConstraint(
            "difficulty BETWEEN 1 AND 5",
            name="ck_questions_difficulty",
        ),
    )

    op.create_index(
        "uq_questions_owner_type_stem",
        "questions",
        ["owner_id", "type", "stem_hash"],
        unique=True,
    )
    op.create_index("ix_questions_owner", "questions", ["owner_id"])
    op.create_index("ix_questions_type", "questions", ["type"])

    # GIN 索引仅 PostgreSQL 支持；SQLite（本地/测试）跳过
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            f"CREATE INDEX {GIN_INDEX_NAME} ON questions USING gin (tags)"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"DROP INDEX IF EXISTS {GIN_INDEX_NAME}")

    op.drop_index("ix_questions_type", table_name="questions")
    op.drop_index("ix_questions_owner", table_name="questions")
    op.drop_index("uq_questions_owner_type_stem", table_name="questions")
    op.drop_table("questions")
