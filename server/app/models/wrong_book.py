"""错题本表（docs/11-data-model.md#wrong_book；AC-20 / C-07）。

- PK `(student_id, question_id)`：同一学生的同一道题只占一行，交卷时按此键 upsert，
  因此「同题错两次只出现一次、`wrong_count=2`」（AC-20）
- `wrong_count`：累计答错次数（只有真正发生 `in_progress → submitted/expired` 状态迁移时累加；
  重复提交 `replayed=true` 不重复计数，见 `app.reports.wrong_book`）
- `last_wrong_at`：最近一次答错时间（UTC），列表按 `wrong_count` / `last_wrong_at` 倒序
- 记录口径：`answers.is_correct = False`（BE-004 判分：仅「完全正确」为 True，部分给分为 False），
  未作答（没有 answers 行）不计入错题
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.user import utcnow


class WrongBook(Base):
    """学生错题本（去重后的错题 + 累计错误次数）。"""

    __tablename__ = "wrong_book"
    __table_args__ = (
        CheckConstraint("wrong_count > 0", name="ck_wrong_book_count"),
        # 列表查询：WHERE student_id = ? ORDER BY wrong_count DESC, last_wrong_at DESC
        Index(
            "ix_wrong_book_student_order",
            "student_id",
            "wrong_count",
            "last_wrong_at",
        ),
    )

    student_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    question_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("questions.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    wrong_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    last_wrong_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        server_default=func.now(),
    )


__all__ = ["WrongBook"]
