"""账号管理业务逻辑（权限在路由层用 `require_role("admin")` 强制）。"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import errors
from app.audit import service as audit
from app.auth import service as auth_service
from app.errors import AppError
from app.models.user import User
from app.users.schemas import UserCreate, UserPatch


def list_users(
    db: Session,
    *,
    role: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[User], int]:
    """分页查询账号（按 id 升序，支持角色过滤）。"""
    total_stmt = select(func.count()).select_from(User)
    list_stmt = select(User)
    if role is not None:
        total_stmt = total_stmt.where(User.role == role)
        list_stmt = list_stmt.where(User.role == role)
    total = int(db.scalar(total_stmt) or 0)
    list_stmt = (
        list_stmt.order_by(User.id).offset((page - 1) * page_size).limit(page_size)
    )
    return list(db.scalars(list_stmt).all()), total


def create_user(
    db: Session,
    payload: UserCreate,
    *,
    actor_id: int | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> User:
    """创建账号：用户名唯一，密码只存 argon2id 哈希。"""
    existing = db.scalar(select(User).where(User.username == payload.username))
    if existing is not None:
        raise AppError(errors.CONFLICT, "用户名已存在", 409)

    user = User(
        username=payload.username,
        display_name=payload.display_name,
        role=payload.role.value,
        password_hash=auth_service.hash_password(payload.password),
        is_active=True,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:  # 并发创建同名账号时由唯一索引兜底
        db.rollback()
        raise AppError(errors.CONFLICT, "用户名已存在", 409) from exc

    audit.record(
        db,
        action=audit.USER_CREATE,
        actor_id=actor_id,
        target_type="user",
        target_id=user.id,
        ip=ip,
        user_agent=user_agent,
        detail={"username": user.username, "role": user.role},
    )
    db.commit()
    return user


def update_user(
    db: Session,
    user_id: int,
    payload: UserPatch,
    *,
    actor_id: int | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> User:
    """修改账号：角色 / 显示名 / 启停用 / 重置密码。

    - 停用账号（is_active=false）时吊销该账号全部会话 → 现有会话立即 401
    - 重置密码同样吊销全部会话（会话固定防护）
    """
    user = db.get(User, user_id)
    if user is None:
        raise errors.not_found("用户不存在")

    changes = payload.model_dump(exclude_unset=True)
    changed_fields: list[str] = []
    credentials_changed = False
    sessions_revoked = 0

    display_name = changes.get("display_name")
    if display_name is not None and display_name != user.display_name:
        user.display_name = display_name
        changed_fields.append("display_name")

    role = changes.get("role")
    if role is not None and role.value != user.role:
        user.role = role.value
        changed_fields.append("role")

    is_active = changes.get("is_active")
    if is_active is not None and is_active != user.is_active:
        user.is_active = is_active
        changed_fields.append("is_active")
        if is_active is False:
            sessions_revoked += auth_service.revoke_all_sessions(db, user.id)

    password = changes.get("password")
    if password is not None:
        user.password_hash = auth_service.hash_password(password)
        credentials_changed = True
        changed_fields.append("credentials")
        sessions_revoked += auth_service.revoke_all_sessions(db, user.id)

    detail: dict[str, object] = {
        "fields": changed_fields,
        "credentials_changed": credentials_changed,
    }
    if sessions_revoked:
        detail["sessions_revoked"] = sessions_revoked

    audit.record(
        db,
        action=audit.USER_UPDATE,
        actor_id=actor_id,
        target_type="user",
        target_id=user.id,
        ip=ip,
        user_agent=user_agent,
        detail=detail,
    )
    db.commit()
    return user


__all__ = ["create_user", "list_users", "update_user"]
