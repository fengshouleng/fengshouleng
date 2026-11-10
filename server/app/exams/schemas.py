"""考试相关 Pydantic 模型（对齐 contracts/openapi.yaml v1.0.2）。

- `ExamCreate`：`publish=true` 时同事务触发试卷冻结与快照（`paper.frozen_at`、
  `exam.snapshot_version`），见 `app.exams.service`
- `ExamPatch`：状态机只允许 `draft→published`、`published→closed`（C-18）
- `Exam.snapshot_version` 保持 optional；`status ∈ {published, closed}` 时服务端保证非空
  （CHG-027 条件不变量）
- 时间字段一律以 UTC 输出（SQLite 读回的时间在 service 层用 `as_utc` 归一）
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.exam import ExamStatus


class ExamCreate(BaseModel):
    """创建考试请求（契约 `ExamCreate`）。"""

    model_config = ConfigDict(extra="forbid")

    paper_id: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=128)
    start_at: datetime
    end_at: datetime
    duration_min: int = Field(ge=10, le=300)
    shuffle_questions: bool = False
    shuffle_options: bool = False
    publish: bool = False

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("考试标题不能为空")
        return value


class ExamPatch(BaseModel):
    """修改考试配置 / 状态（契约 `ExamPatch`）。"""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=128)
    start_at: datetime | None = None
    end_at: datetime | None = None
    duration_min: int | None = Field(default=None, ge=10, le=300)
    status: ExamStatus | None = None


class ExamOut(BaseModel):
    """考试响应（契约 `Exam`）。"""

    id: int
    paper_id: int
    title: str
    start_at: datetime
    end_at: datetime
    duration_min: int
    shuffle_questions: bool
    shuffle_options: bool
    status: ExamStatus
    created_by: int
    snapshot_version: int | None = None
    updated_at: datetime
    created_at: datetime


class ExamPage(BaseModel):
    """分页响应（契约 `ExamPage`）。"""

    items: list[ExamOut]
    total: int
    page: int
    page_size: int


__all__ = [
    "ExamCreate",
    "ExamOut",
    "ExamPage",
    "ExamPatch",
]
