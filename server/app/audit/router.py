"""审计查询路由（只读，仅 admin；TASK-BE-006 / AC-21）。

契约：`GET /audit-logs`（contracts/openapi.yaml#/paths/~1audit-logs）
- 仅 `admin`；教师/学生一律 403 `FORBIDDEN`（判定顺序先角色后数据）
- 过滤：`action` / `actor_id` / `from` / `to`（闭区间）
- 默认时间窗：最近 180 天（`from`/`to` 均未传时）
- 分页：`items/total/page/page_size`，按 `created_at` 降序
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.audit import service
from app.audit.schemas import AuditLogOut, AuditLogPage
from app.auth.csrf import enforce_csrf
from app.auth.dependencies import require_role
from app.db import get_db
from app.models.user import Role, User

router = APIRouter(
    prefix="/audit-logs",
    tags=["audit"],
    dependencies=[Depends(enforce_csrf)],
)

_ADMIN = require_role(Role.ADMIN.value)


@router.get(
    "",
    response_model=AuditLogPage,
    operation_id="listAuditLogs",
    summary="审计日志（仅 admin；默认最近 180 天，按时间倒序）",
)
def list_audit_logs(
    action: str | None = Query(default=None, max_length=64),
    actor_id: int | None = Query(default=None),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _admin: User = Depends(_ADMIN),
) -> AuditLogPage:
    items, total = service.list_audit_logs(
        db,
        action=action,
        actor_id=actor_id,
        start=from_,
        end=to,
        page=page,
        page_size=page_size,
    )
    return AuditLogPage(
        items=[AuditLogOut.model_validate(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )


__all__ = ["router"]
