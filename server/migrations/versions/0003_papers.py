"""试卷与试卷题目明细（docs/11-data-model.md#papers / #paper_questions）

Revision ID: 0003_papers
Revises: 0002_questions
Create Date: 2026-11-09

- `papers`：组卷头（owner/title/total_points/status/frozen_snapshot/frozen_at）
- `paper_questions`：明细，PK `(paper_id, question_id)`，同试卷 `order_no` 唯一
- 约束：`paper_questions.points` 满足 `0 < points <= 100`；
  `papers.total_points` 为 `numeric(7,2)`（上限 10000.00）、由服务端汇总
- PostgreSQL 落 JSONB，SQLite（本地/测试）落 JSON
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_papers"
down_revision: str | None = "0002_questions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _bigint() -> sa.types.TypeEngine[object]:
    """PostgreSQL 用 BIGSERIAL；SQLite 退化为 INTEGER 才能自增。"""
    return sa.BigInteger().with_variant(sa.Integer, "sqlite")


def _json_type() -> sa.types.TypeEngine[object]:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "papers",
        sa.Column("id", _bigint(), primary_key=True, autoincrement=True),
        sa.Column(
            "owner_id",
            _bigint(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column(
            "total_points",
            sa.Numeric(7, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'draft'"),
        ),
        sa.Column("frozen_snapshot", _json_type(), nullable=True),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("status IN ('draft', 'frozen')", name="ck_papers_status"),
        sa.CheckConstraint(
            "total_points >= 0 AND total_points <= 10000",
            name="ck_papers_total_points",
        ),
    )
    op.create_index("ix_papers_owner", "papers", ["owner_id"])

    op.create_table(
        "paper_questions",
        sa.Column(
            "paper_id",
            _bigint(),
            sa.ForeignKey("papers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "question_id",
            _bigint(),
            sa.ForeignKey("questions.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("order_no", sa.SmallInteger(), nullable=False),
        sa.Column("points", sa.Numeric(5, 2), nullable=False),
        sa.Column(
            "partial_rule",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'all_or_nothing'"),
        ),
        sa.UniqueConstraint(
            "paper_id",
            "order_no",
            name="uq_paper_questions_order",
        ),
        sa.CheckConstraint(
            "points > 0 AND points <= 100",
            name="ck_paper_questions_points",
        ),
        sa.CheckConstraint(
            "partial_rule IN ('all_or_nothing', 'partial')",
            name="ck_paper_questions_partial_rule",
        ),
    )
    op.create_index(
        "ix_paper_questions_question",
        "paper_questions",
        ["question_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_paper_questions_question", table_name="paper_questions")
    op.drop_table("paper_questions")
    op.drop_index("ix_papers_owner", table_name="papers")
    op.drop_table("papers")
