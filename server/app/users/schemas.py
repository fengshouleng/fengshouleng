"""用户相关 Pydantic 模型（对齐 contracts/openapi.yaml 的 User 系列 schema）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.user import Role


class UserOut(BaseModel):
    """User（响应）：`id/username/display_name/role/is_active/created_at`。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str
    role: Role
    is_active: bool
    created_at: datetime


class UserCreate(BaseModel):
    """UserCreate（请求）：username 3-64，password 8-128。"""

    username: str = Field(min_length=3, max_length=64)
    display_name: str = Field(min_length=1, max_length=64)
    role: Role
    password: str = Field(min_length=8, max_length=128)


class UserPatch(BaseModel):
    """UserPatch（请求）：全部字段可选，只更新显式提供的字段。"""

    display_name: str | None = Field(default=None, max_length=64)
    role: Role | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)


class UserPage(BaseModel):
    """UserPage（分页响应）：items/total/page/page_size。"""

    items: list[UserOut]
    total: int
    page: int
    page_size: int


__all__ = ["UserCreate", "UserOut", "UserPage", "UserPatch"]
