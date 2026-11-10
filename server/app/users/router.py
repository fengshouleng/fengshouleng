"""用户管理路由：GET/POST /users、PATCH /users/{user_id}（仅 admin）。

权限矩阵见 docs/12-permission-matrix.md：学生/教师一律 403。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.auth.csrf import enforce_csrf
from app.auth.dependencies import require_role
from app.db import get_db
from app.models.user import Role, User
from app.users import service
from app.users.schemas import UserCreate, UserOut, UserPage, UserPatch

router = APIRouter(
    prefix="/users",
    tags=["users"],
    dependencies=[Depends(enforce_csrf)],
)


@router.get(
    "",
    response_model=UserPage,
    operation_id="listUsers",
    summary="用户列表（仅 admin）",
)
def list_users(
    role: Role | None = Query(default=None, description="按角色过滤"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role(Role.ADMIN.value)),
) -> UserPage:
    items, total = service.list_users(
        db,
        role=None if role is None else role.value,
        page=page,
        page_size=page_size,
    )
    return UserPage(
        items=[UserOut.model_validate(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "",
    response_model=UserOut,
    status_code=201,
    operation_id="createUser",
    summary="创建账号（仅 admin）",
)
def create_user(
    payload: UserCreate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_role(Role.ADMIN.value)),
) -> UserOut:
    user = service.create_user(
        db,
        payload,
        actor_id=admin.id,
        ip=audit.request_ip(request),
        user_agent=audit.request_user_agent(request),
    )
    return UserOut.model_validate(user)


@router.patch(
    "/{user_id}",
    response_model=UserOut,
    operation_id="updateUser",
    summary="修改角色/停用/重置密码（仅 admin）",
)
def update_user(
    user_id: int,
    payload: UserPatch,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_role(Role.ADMIN.value)),
) -> UserOut:
    user = service.update_user(
        db,
        user_id,
        payload,
        actor_id=admin.id,
        ip=audit.request_ip(request),
        user_agent=audit.request_user_agent(request),
    )
    return UserOut.model_validate(user)


__all__ = ["router"]
