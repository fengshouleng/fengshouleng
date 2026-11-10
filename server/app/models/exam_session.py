"""考试会话表（docs/11-data-model.md#exam_sessions）。

- `UNIQUE(exam_id, student_id)`：一人一次机会；并发双请求只有一个能插入成功（C-22 / AC-13）
- `status`：`in_progress → submitted`（主动交卷）/ `expired`（到期自动交卷，语义等价 submitted）
- `deadline_at = min(started_at + duration_min, exam.end_at)`，**只在新建时计算**，恢复不重算
  （C-01 / C-22）
- `score` 用 `Numeric(7, 2)`：100 题 × 100 分 = 10000.00，`Numeric(6, 2)` 会溢出
- `last_client_seq`：服务端已接受的最大 `client_seq`（ADR-006，防自动保存乱序）
- `created_at` / `updated_at` 为运维字段（契约不暴露）
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.user import utcnow


class SessionStatus(StrEnum):
    """会话状态（契约 `SessionState.status` / `SubmitResult.status`）。"""

    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"
    EXPIRED = "expired"


SESSION_STATUS_VALUES: tuple[str, ...] = tuple(status.value for status in SessionStatus)

#: 终态（已判分、成绩可读）；重复提交/重复到期结算都走「返回既有成绩」分支
TERMINAL_SESSION_STATUSES: frozenset[str] = frozenset(
    {SessionStatus.SUBMITTED.value, SessionStatus.EXPIRED.value}
)


class ExamSession(Base):
    """某学生参加某场考试的一次尝试。"""

    __tablename__ = "exam_sessions"
    __table_args__ = (
        UniqueConstraint("exam_id", "student_id", name="ux_exam_student"),
        CheckConstraint(
            "status IN ('in_progress', 'submitted', 'expired')",
            name="ck_exam_sessions_status",
        ),
        # 总分上限 = 100 题 × 100 分 = 10000.00（CHG-025）
        CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 10000)",
            name="ck_exam_sessions_score",
        ),
        Index("ix_sessions_student_status", "student_id", "status"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    exam_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("exams.id", ondelete="CASCADE"),
        nullable=False,
    )
    student_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=SessionStatus.IN_PROGRESS.value,
        server_default=text("'in_progress'"),
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    deadline_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    score: Mapped[Decimal | None] = mapped_column(Numeric(7, 2), nullable=True)
    last_client_seq: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
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


__all__ = [
    "SESSION_STATUS_VALUES",
    "TERMINAL_SESSION_STATUSES",
    "ExamSession",
    "SessionStatus",
]
