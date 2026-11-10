"""考试 / 考试会话 / 作答明细（docs/11-data-model.md#exams / #exam_sessions / #answers）

Revision ID: 0004_exams
Revises: 0003_papers
Create Date: 2026-11-09

- `exams`：试卷 + 时间窗 + 时长 + 乱序 + 发布状态（`created_by` 归属教师）
- `exam_sessions`：`UNIQUE(exam_id, student_id)` 兜底「一人一次」与并发幂等；
  `deadline_at` 只在新建时计算（C-01/C-22）；`score` 为 `numeric(7,2)`
  （100 题 × 100 分 = 10000.00，`numeric(6,2)` 会溢出）
- `answers`：PK `(session_id, question_id)`，`selected` 落 JSONB/JSON，
  `is_correct` / `earned_points` 判分时写入（未判分为 NULL）
- PostgreSQL 落 JSONB，SQLite（本地/测试）落 JSON，时间统一 UTC
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_exams"
down_revision: str | None = "0003_papers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _bigint() -> sa.types.TypeEngine[object]:
    """PostgreSQL 用 BIGSERIAL；SQLite 退化为 INTEGER 才能自增。"""
    return sa.BigInteger().with_variant(sa.Integer, "sqlite")


def _json_type() -> sa.types.TypeEngine[object]:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "exams",
        sa.Column("id", _bigint(), primary_key=True, autoincrement=True),
        sa.Column(
            "paper_id",
            _bigint(),
            sa.ForeignKey("papers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_min", sa.Integer(), nullable=False),
        sa.Column(
            "shuffle_questions",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "shuffle_options",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'draft'"),
        ),
        sa.Column("snapshot_version", sa.Integer(), nullable=True),
        sa.Column(
            "created_by",
            _bigint(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
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
            "status IN ('draft', 'published', 'closed')",
            name="ck_exams_status",
        ),
        sa.CheckConstraint("end_at > start_at", name="ck_exams_window"),
        sa.CheckConstraint(
            "duration_min BETWEEN 10 AND 300",
            name="ck_exams_duration",
        ),
    )
    op.create_index("ix_exams_status_start", "exams", ["status", "start_at"])
    op.create_index("ix_exams_created_by", "exams", ["created_by"])

    op.create_table(
        "exam_sessions",
        sa.Column("id", _bigint(), primary_key=True, autoincrement=True),
        sa.Column(
            "exam_id",
            _bigint(),
            sa.ForeignKey("exams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "student_id",
            _bigint(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'in_progress'"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("score", sa.Numeric(7, 2), nullable=True),
        sa.Column(
            "last_client_seq",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
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
        sa.UniqueConstraint("exam_id", "student_id", name="ux_exam_student"),
        sa.CheckConstraint(
            "status IN ('in_progress', 'submitted', 'expired')",
            name="ck_exam_sessions_status",
        ),
        sa.CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 10000)",
            name="ck_exam_sessions_score",
        ),
    )
    op.create_index(
        "ix_sessions_student_status",
        "exam_sessions",
        ["student_id", "status"],
    )

    op.create_table(
        "answers",
        sa.Column(
            "session_id",
            _bigint(),
            sa.ForeignKey("exam_sessions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "question_id",
            _bigint(),
            sa.ForeignKey("questions.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("selected", _json_type(), nullable=False),
        sa.Column("is_correct", sa.Boolean(), nullable=True),
        sa.Column("earned_points", sa.Numeric(5, 2), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "earned_points IS NULL OR (earned_points >= 0 AND earned_points <= 100)",
            name="ck_answers_earned_points",
        ),
    )
    op.create_index("ix_answers_question", "answers", ["question_id"])


def downgrade() -> None:
    op.drop_index("ix_answers_question", table_name="answers")
    op.drop_table("answers")
    op.drop_index("ix_sessions_student_status", table_name="exam_sessions")
    op.drop_table("exam_sessions")
    op.drop_index("ix_exams_created_by", table_name="exams")
    op.drop_index("ix_exams_status_start", table_name="exams")
    op.drop_table("exams")
