"""用户账号表（docs/11-data-model.md#users）。

- 密码只存 argon2id 哈希（`password_hash`），明文永不落库
- 角色限定为 student / teacher / admin：数据库 CHECK + Pydantic 枚举双重约束
- `id` 生产为 bigserial；SQLite（本地/测试）退化为 INTEGER 才能自增
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    func,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> datetime:
    """当前 UTC 时间（带时区）；供各模型作为列默认值复用。"""
    return datetime.now(UTC)


class Role(StrEnum):
    """账号角色（契约 components/schemas/User.role）。"""

    STUDENT = "student"
    TEACHER = "teacher"
    ADMIN = "admin"


ROLE_VALUES: tuple[str, ...] = tuple(role.value for role in Role)


class User(Base):
    """账号表。"""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('student', 'teacher', 'admin')", name="ck_users_role"),
        Index("ux_users_username", "username", unique=True),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=Role.STUDENT.value,
        server_default=text("'student'"),
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
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


__all__ = ["ROLE_VALUES", "Role", "User", "utcnow"]
