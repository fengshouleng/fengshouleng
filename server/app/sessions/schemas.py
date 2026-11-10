"""作答会话相关 Pydantic 模型（对齐 contracts/openapi.yaml v1.0.2）。

- `SessionState`：`additionalProperties: false`，字段与契约完全一致；`answers`（已保存答案）与
  `server_time`（校时）为必填，`score`/`submitted_at` 在 `submitted|expired` 时必有值（CHG-024/028）
- `SubmitResult.replayed` 必填（CHG-028）：首次交卷 false、重复提交/已自动交卷 true
- `AnswerSaveResponse.applied` 必填（CHG-028）：true=已落库，false=旧 `client_seq` 被忽略
- `MyExamItem.session_status`：`not_started` + `SessionState.status` 同枚举（C-02）；`MyExamPage`
  根级 `server_time` 必填（CHG-024）
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.models.exam_session import SessionStatus
from app.models.question import QuestionType


class OptionOut(BaseModel):
    """选项（契约 `Option`）。"""

    key: str
    text: str


class SessionQuestionOut(BaseModel):
    """作答视图的题目（契约 `SessionQuestion`）：不含正确答案。"""

    question_id: int
    type: QuestionType
    stem: str
    options: list[OptionOut]
    order_no: int
    points: float


class AnswerItemOut(BaseModel):
    """已保存答案（契约 `AnswerItem`）。"""

    question_id: int
    selected: list[str]


class SessionState(BaseModel):
    """会话状态 + 作答所需全部数据（契约 `SessionState`，C-01 恢复回读）。"""

    model_config = ConfigDict(extra="forbid")

    id: int
    exam_id: int
    status: SessionStatus
    started_at: datetime
    deadline_at: datetime
    server_time: datetime
    last_client_seq: int
    submitted_at: datetime | None = None
    score: float | None = None
    answers: list[AnswerItemOut]
    questions: list[SessionQuestionOut]


class AnswerSaveItem(BaseModel):
    """增量保存的单题作答（契约 `AnswerItem`，`selected` 必填）。"""

    model_config = ConfigDict(extra="forbid")

    question_id: int = Field(ge=1)
    selected: list[str]


class AnswerSaveRequest(BaseModel):
    """自动保存请求（契约 `AnswerSaveRequest`）：`client_seq` 单调递增（ADR-006）。"""

    model_config = ConfigDict(extra="forbid")

    client_seq: int = Field(ge=1)
    answers: list[AnswerSaveItem] = Field(min_length=1, max_length=100)


class AnswerSaveResponse(BaseModel):
    """自动保存结果（契约 `AnswerSaveResponse`）。"""

    saved_seq: int
    saved_at: datetime
    applied: bool


class SubmitResult(BaseModel):
    """交卷结果（契约 `SubmitResult`）：重复提交 200 + 既有成绩 + `replayed=true`。"""

    session_id: int
    status: SessionStatus
    score: float
    submitted_at: datetime
    replayed: bool


class MySessionStatus(StrEnum):
    """我的考试列表里的会话状态（`not_started` = 无会话，C-02）。"""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"
    EXPIRED = "expired"


class MyExamItem(BaseModel):
    """学生视角的考试条目（契约 `MyExamItem`）。"""

    exam_id: int
    exam_title: str
    start_at: datetime
    end_at: datetime
    duration_min: int
    session_status: MySessionStatus
    session_id: int | None = None
    score: float | None = None
    total_points: float | None = None
    submitted_at: datetime | None = None


class MyExamPage(BaseModel):
    """我的考试分页（契约 `MyExamPage`）：`server_time` 必填用于校时（CHG-024）。"""

    items: list[MyExamItem]
    total: int
    page: int
    page_size: int
    server_time: datetime


__all__ = [
    "AnswerItemOut",
    "AnswerSaveItem",
    "AnswerSaveRequest",
    "AnswerSaveResponse",
    "MyExamItem",
    "MyExamPage",
    "MySessionStatus",
    "OptionOut",
    "SessionQuestionOut",
    "SessionState",
    "SubmitResult",
]
