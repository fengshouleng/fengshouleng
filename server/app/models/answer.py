"""作答明细表（docs/11-data-model.md#answers）。

- PK `(session_id, question_id)`：一个会话内每题只有一行，自动保存用 upsert 语义
- `selected`：选中选项 key 数组（JSONB/JSON）；空数组表示「已清空该题作答」
- `is_correct` / `earned_points`：**判分时**写入；未判分为 null（`in_progress` 阶段）
- `earned_points` 用 `Numeric(5, 2)`：单题分值上限 100（CHG-025），5,2 足够
- 未作答的题**不建行**：`SessionState.answers` 只回读已保存答案（C-01）；
  逐题结果由 BE-005 从试卷快照 + 本表合成（缺失行 = 未作答、0 分）
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.question import json_type
from app.models.user import utcnow


class Answer(Base):
    """会话内单题作答。"""

    __tablename__ = "answers"
    __table_args__ = (
        CheckConstraint(
            "earned_points IS NULL OR (earned_points >= 0 AND earned_points <= 100)",
            name="ck_answers_earned_points",
        ),
        Index("ix_answers_question", "question_id"),
    )

    session_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("exam_sessions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    question_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("questions.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    selected: Mapped[list[str]] = mapped_column(json_type(), nullable=False)
    is_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    earned_points: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
    )


def selected_of(row: Answer) -> list[Any]:
    """安全读取 `selected`（NULL/异常类型一律当空数组）。"""
    value = row.selected
    return list(value) if isinstance(value, list | tuple) else []


__all__ = ["Answer", "selected_of"]
