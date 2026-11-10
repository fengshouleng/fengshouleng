"""试卷题目明细表（docs/11-data-model.md#paper_questions）。

- PK `(paper_id, question_id)`：同一题在同一试卷里只能出现一次
- `order_no` 在同一试卷内唯一，服务层另外强制「从 1 开始连续」（契约 min=1）
- `points`：`0 < points <= 100`（契约 v1.0.2 / CHG-025）
- `partial_rule`：多选题给分规则（ADR-007），非多选在写入时归一为 `all_or_nothing`
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class PartialRule(StrEnum):
    """多选给分规则（契约 `PaperQuestion.partial_rule`）。"""

    ALL_OR_NOTHING = "all_or_nothing"
    PARTIAL = "partial"


PARTIAL_RULE_VALUES: tuple[str, ...] = tuple(item.value for item in PartialRule)


class PaperQuestion(Base):
    """试卷题目明细。"""

    __tablename__ = "paper_questions"
    __table_args__ = (
        UniqueConstraint("paper_id", "order_no", name="uq_paper_questions_order"),
        CheckConstraint(
            "points > 0 AND points <= 100",
            name="ck_paper_questions_points",
        ),
        CheckConstraint(
            "partial_rule IN ('all_or_nothing', 'partial')",
            name="ck_paper_questions_partial_rule",
        ),
        Index("ix_paper_questions_question", "question_id"),
    )

    paper_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("papers.id", ondelete="CASCADE"),
        primary_key=True,
    )
    question_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("questions.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    order_no: Mapped[int] = mapped_column(SmallInteger(), nullable=False)
    points: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    partial_rule: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=PartialRule.ALL_OR_NOTHING.value,
        server_default=text("'all_or_nothing'"),
    )


__all__ = ["PARTIAL_RULE_VALUES", "PaperQuestion", "PartialRule"]
