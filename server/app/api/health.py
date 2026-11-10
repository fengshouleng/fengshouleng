"""系统健康检查（含数据库探针）。

契约：`contracts/openapi.yaml#/paths/~1healthz`（operationId `healthz`，无需认证）。
服务端始终挂载两份：`/healthz`（运维探针，与 ADR-008 一致）和 `/api/healthz`
（Nginx 反代后的契约路径）。

探针语义（TASK-BE-007，处置 GAP-02）：
- 每次请求执行 `SELECT 1` 验证数据库可连接；
- 正常返回 `{status: "ok", version, db: "ok"}`；
- 数据库不可用时返回 503，响应体走统一错误格式 `{code, message, request_id}`
  （`code=INTERNAL_ERROR`，与契约 503 分支一致）。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import errors
from app.db import get_db
from app.errors import AppError
from app.settings import Settings

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    """健康检查响应：服务存活、版本与数据库状态。"""

    status: Literal["ok"] = "ok"
    version: str
    db: Literal["ok"] = "ok"


def _probe_database(db: Session) -> None:
    """执行 `SELECT 1`；失败抛 503 统一错误（由 app.main 渲染 Error 体）。"""
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise AppError(
            errors.INTERNAL_ERROR,
            "数据库不可用",
            503,
        ) from exc


@router.get(
    "/healthz",
    response_model=HealthResponse,
    operation_id="healthz",
    summary="健康检查",
    description="服务存活与数据库探针（SELECT 1），无需认证。",
)
def healthz(request: Request, db: Session = Depends(get_db)) -> HealthResponse:
    _probe_database(db)
    settings = getattr(request.app.state, "settings", None)
    version = settings.app_version if isinstance(settings, Settings) else "0.0.0"
    return HealthResponse(status="ok", version=version, db="ok")


__all__ = ["HealthResponse", "router"]
