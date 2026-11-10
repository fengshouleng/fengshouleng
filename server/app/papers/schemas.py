"""试卷相关 Pydantic 模型（对齐 contracts/openapi.yaml v1.0.2）。

契约要点：
- `PaperCreate` **只含** `title` + `questions`；`total_points` 是服务端计算的响应字段，
  请求里出现即 422（`extra="forbid"`，CHG-026）
- 每题 `0 < points <= 100` 且为 `0.01` 的整数倍（CHG-025）
- `partial_rule` 缺省 `all_or_nothing`（ADR-007），仅多选题生效
- `Paper.frozen_at` optional；`status=frozen` 时服务端保证返回非 null（CHG-027）
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.paper import PaperStatus
from app.models.paper_question import PartialRule

_POINTS_QUANTUM = Decimal("0.01")


class PaperQuestionIn(BaseModel):
    """`PaperCreate.questions[]`（契约 `PaperQuestion` 的请求子集）。"""

    model_config = ConfigDict(extra="forbid")

    question_id: int = Field(ge=1)
    order_no: int = Field(ge=1)
    points: float = Field(gt=0, le=100)
    partial_rule: PartialRule = PartialRule.ALL_OR_NOTHING

    @field_validator("points")
    @classmethod
    def _points_multiple_of_cent(cls, value: float) -> float:
        decimal_value = Decimal(str(value))
        if decimal_value != decimal_value.quantize(_POINTS_QUANTUM):
            raise ValueError("points 必须为 0.01 的整数倍")
        return value


class PaperCreate(BaseModel):
    """组卷请求（契约 `PaperCreate`）：不含 `total_points`。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=128)
    questions: list[PaperQuestionIn] = Field(min_length=1, max_length=100)

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("试卷标题不能为空")
        return value


class PaperQuestionOut(BaseModel):
    """试卷题目明细（契约 `PaperQuestion`）。"""

    question_id: int
    order_no: int
    points: float
    partial_rule: PartialRule


class PaperOut(BaseModel):
    """试卷响应（契约 `Paper`）。

    注意：`frozen_snapshot` 属于内部判分数据，不对外暴露（契约未定义该字段）。
    """

    id: int
    owner_id: int
    title: str
    total_points: float
    status: PaperStatus
    frozen_at: datetime | None = None
    questions: list[PaperQuestionOut]
    created_at: datetime


class PaperPage(BaseModel):
    """分页响应（契约 `PaperPage`）。"""

    items: list[PaperOut]
    total: int
    page: int
    page_size: int


__all__ = [
    "PaperCreate",
    "PaperOut",
    "PaperPage",
    "PaperQuestionIn",
    "PaperQuestionOut",
]
