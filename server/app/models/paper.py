"""试卷表（docs/11-data-model.md#papers；契约 v1.0.2 `Paper`）。

- `total_points` 由 `paper_questions.points` 汇总，服务端计算，客户端不可写
- `status`：`draft`（可改）/ `frozen`（快照冻结后不可改，PUT/DELETE → 409 PAPER_FROZEN）
- `frozen_snapshot`：冻结时写入的深拷贝（题目/选项/答案/分值/给分规则），
  结构由 `app.papers.service.freeze_paper` 固定，供 BE-004 判分直接消费（ADR-007）
- `frozen_at`：冻结时间；契约 v1.0.2 要求 `status=frozen` 时必返回（CHG-027）
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.question import json_type
from app.models.user import utcnow


class PaperStatus(StrEnum):
    """试卷状态（契约 `Paper.status`）。"""

    DRAFT = "draft"
    FROZEN = "frozen"


PAPER_STATUS_VALUES: tuple[str, ...] = tuple(item.value for item in PaperStatus)


class Paper(Base):
    """试卷表（组卷头）。"""

    __tablename__ = "papers"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'frozen')", name="ck_papers_status"),
        # 契约：total_points 合法区间 1..10000；草稿初始化允许 0，服务端写入前已校验
        CheckConstraint(
            "total_points >= 0 AND total_points <= 10000",
            name="ck_papers_total_points",
        ),
        Index("ix_papers_owner", "owner_id"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    owner_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    # 契约上限 10000（100 题 × 100 分）；Numeric(7,2) 才装得下 10000.00（docs/13 CHG-025）
    total_points: Mapped[Decimal] = mapped_column(
        Numeric(7, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default=text("0"),
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=PaperStatus.DRAFT.value,
        server_default=text("'draft'"),
    )
    # 冻结时的深拷贝；结构见 app.papers.service.snapshot 文档
    frozen_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        json_type(),
        nullable=True,
    )
    frozen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
    )


__all__ = ["PAPER_STATUS_VALUES", "Paper", "PaperStatus"]
