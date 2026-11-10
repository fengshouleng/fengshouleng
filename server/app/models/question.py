"""题库表（docs/11-data-model.md#questions）。

- `options` / `answer` / `tags` 使用 JSON：PostgreSQL 落 JSONB，SQLite 落 JSON
- `stem_hash` = md5(规范化题干)；`(owner_id, type, stem_hash)` 唯一索引用于
  导入幂等去重（C-11 / AC-08）
- `is_deleted` 为软删除标记：列表/详情/修改/删除都只作用于未删除记录
- 生产库为 PostgreSQL；SQLite 只是本地/测试退化的 INTEGER 自增
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
    text,
    true,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeEngine

from app.db import Base
from app.models.user import utcnow


class QuestionType(StrEnum):
    """题目类型（契约 components/schemas/QuestionType）。"""

    SINGLE = "single"
    MULTIPLE = "multiple"
    JUDGE = "judge"


QUESTION_TYPE_VALUES: tuple[str, ...] = tuple(item.value for item in QuestionType)


def json_type() -> TypeEngine[Any]:
    """JSONB（PostgreSQL）/ JSON（SQLite）通用列类型。"""
    return JSON().with_variant(postgresql.JSONB(), "postgresql")


class Question(Base):
    """题目表。"""

    __tablename__ = "questions"
    __table_args__ = (
        CheckConstraint(
            "type IN ('single', 'multiple', 'judge')",
            name="ck_questions_type",
        ),
        CheckConstraint(
            "difficulty BETWEEN 1 AND 5",
            name="ck_questions_difficulty",
        ),
        # 幂等去重键：同一出题人 + 题型 + 规范化题干 只能有一条
        Index(
            "uq_questions_owner_type_stem",
            "owner_id",
            "type",
            "stem_hash",
            unique=True,
        ),
        Index("ix_questions_owner", "owner_id"),
        Index("ix_questions_type", "type"),
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
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    stem: Mapped[str] = mapped_column(Text, nullable=False)
    stem_hash: Mapped[str] = mapped_column(String(32), nullable=False)
    options: Mapped[list[dict[str, Any]]] = mapped_column(json_type(), nullable=False)
    answer: Mapped[list[str]] = mapped_column(json_type(), nullable=False)
    # 契约 v1.0.1（C-10）要求解析必填；数据库保持可空以兼容后续补齐任务
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    difficulty: Mapped[int] = mapped_column(
        SmallInteger(),
        nullable=False,
        default=3,
        server_default=text("3"),
    )
    tags: Mapped[list[str]] = mapped_column(json_type(), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=true(),
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


__all__ = ["QUESTION_TYPE_VALUES", "Question", "QuestionType", "json_type"]
