"""错题本（docs/11-data-model.md#wrong_book；AC-20 / TASK-BE-005）

Revision ID: 0005_wrong_book
Revises: 0004_exams
Create Date: 2026-11-09

- PK `(student_id, question_id)`：同一学生同一题只有一行，交卷时按此键 upsert
  （`wrong_count + 1`、`last_wrong_at = now`），从而「同题错两次只出现一次且 wrong_count=2」
- `wrong_count` 默认 1，CHECK > 0
- `last_wrong_at` 为最近一次答错时间（UTC）
- 索引 `ix_wrong_book_student_order` 服务 `GET /me/wrong-questions` 的
  `WHERE student_id = ? ORDER BY wrong_count DESC, last_wrong_at DESC`
- 软删除题目不物理删除：`question_id` 用 `ON DELETE RESTRICT` 保证历史错题可读
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_wrong_book"
down_revision: str | None = "0004_exams"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _bigint() -> sa.types.TypeEngine[object]:
    """PostgreSQL 用 BIGSERIAL；SQLite 退化为 INTEGER 才能自增。"""
    return sa.BigInteger().with_variant(sa.Integer, "sqlite")


def upgrade() -> None:
    op.create_table(
        "wrong_book",
        sa.Column(
            "student_id",
            _bigint(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "question_id",
            _bigint(),
            sa.ForeignKey("questions.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "wrong_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "last_wrong_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("wrong_count > 0", name="ck_wrong_book_count"),
    )
    op.create_index(
        "ix_wrong_book_student_order",
        "wrong_book",
        ["student_id", "wrong_count", "last_wrong_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_wrong_book_student_order", table_name="wrong_book")
    op.drop_table("wrong_book")
