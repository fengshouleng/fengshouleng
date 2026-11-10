"""考试表（docs/11-data-model.md#exams；契约 v1.0.2 `Exam`）。

- `status` 状态机：`draft → published → closed`，只进不退（docs/11 §3、C-18）
- `paper_id`：发布时冻结试卷快照（ADR-007），**判分只认快照**，不随题库/试卷后续编辑变化
- `snapshot_version`：发布时写入（契约条件不变量：`status ∈ {published, closed}` 时必返回，
  draft 阶段为 null，CHG-027）
- 时间窗与 `duration_min` 由 `app.exams.service` 校验：`start_at < end_at` 且
  `duration_min` 不超过时间窗长度（契约 `ExamPatch` description）
- `created_by` 为创建者（教师）id：教师只能操作自己的考试，admin 全部（AC-10 / RBAC，C-04）
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    false,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.user import utcnow


class ExamStatus(StrEnum):
    """考试状态（契约 `Exam.status`）。"""

    DRAFT = "draft"
    PUBLISHED = "published"
    CLOSED = "closed"


EXAM_STATUS_VALUES: tuple[str, ...] = tuple(status.value for status in ExamStatus)

#: 唯一合法的状态迁移（draft→published、published→closed）
LEGAL_EXAM_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
        (ExamStatus.DRAFT.value, ExamStatus.PUBLISHED.value),
        (ExamStatus.PUBLISHED.value, ExamStatus.CLOSED.value),
    }
)


class Exam(Base):
    """考试表（试卷 + 时间窗 + 时长 + 乱序配置）。"""

    __tablename__ = "exams"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'published', 'closed')",
            name="ck_exams_status",
        ),
        CheckConstraint("end_at > start_at", name="ck_exams_window"),
        CheckConstraint(
            "duration_min BETWEEN 10 AND 300",
            name="ck_exams_duration",
        ),
        Index("ix_exams_status_start", "status", "start_at"),
        Index("ix_exams_created_by", "created_by"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    paper_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("papers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    start_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    end_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    duration_min: Mapped[int] = mapped_column(Integer, nullable=False)
    shuffle_questions: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )
    shuffle_options: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=ExamStatus.DRAFT.value,
        server_default=text("'draft'"),
    )
    # 发布时写入 1；draft 阶段为 null（契约 CHG-027）
    snapshot_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
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
    "EXAM_STATUS_VALUES",
    "LEGAL_EXAM_TRANSITIONS",
    "Exam",
    "ExamStatus",
]
