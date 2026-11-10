"""审计查询响应模型（对齐 contracts/openapi.yaml v1.0.3 / CHG-035）。

契约 `AuditLog` 把 `actor_id` / `target_type` / `target_id` / `ip` 标为 required：

- `actor_id` 允许为 null（系统操作 / CLI 建号），字段始终存在；
- `ip` 为必填字符串，系统操作约定写 `"-"`（`v1.0.3` 口径；写入侧由
  `app.audit.service.record` 兜底，历史 NULL 行在响应层同样归一为 `"-"`）。

业务写操作由各模块保证写入完整字段，测试 AC-22 对字段取值做硬断言。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


class AuditLogOut(BaseModel):
    """单条审计记录（契约 `AuditLog`）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    actor_id: int | None = None
    action: str
    target_type: str | None = None
    target_id: str | None = None
    ip: str = "-"
    user_agent: str | None = None
    detail: dict[str, Any] | None = None
    created_at: datetime

    @field_validator("ip", mode="before")
    @classmethod
    def _normalize_ip(cls, value: object) -> str:
        """系统操作/历史记录为 NULL 时统一输出 `"-"`（CHG-035）。"""
        return "-" if value is None else str(value)


class AuditLogPage(BaseModel):
    """审计分页（契约 `AuditLogPage`：items/total/page/page_size）。"""

    items: list[AuditLogOut]
    total: int
    page: int
    page_size: int


__all__ = ["AuditLogOut", "AuditLogPage"]
