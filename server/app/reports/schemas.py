"""成绩 / 错题本响应模型（对齐 contracts/openapi.yaml v1.0.2）。

- `ResultItem` 在契约中 `additionalProperties: false` → `extra="forbid"`
- `ResultDetail` 的 `items` 逐题给出 selected / correct_answer / is_correct /
  earned_points / points / explanation；`score` / `total_points` 两位小数
- `MyResultPage` / `WrongQuestionPage` 分页字段与全局约定一致（items/total/page/page_size）
- `ExamResultSummary`：`average_score` 为必填 number（无已判分返回 0，CHG-031）；
  名单条目 `score` / `submitted_at` 在 `in_progress` 时为 null，路由按
  `response_model_exclude_none=True` 输出，避免出现契约未声明的 null
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.exam_session import SessionStatus
from app.models.question import QuestionType


class ResultItemOut(BaseModel):
    """逐题判分结果（契约 `ResultItem`）。"""

    model_config = ConfigDict(extra="forbid")

    question_id: int
    type: QuestionType
    stem: str
    selected: list[str]
    correct_answer: list[str]
    is_correct: bool
    earned_points: float
    points: float
    explanation: str


class ResultDetail(BaseModel):
    """成绩明细（契约 `ResultDetail`；未交卷 409 `NOT_SUBMITTED`）。"""

    session_id: int
    exam_id: int
    score: float
    total_points: float
    submitted_at: datetime
    items: list[ResultItemOut]


class MyResultItem(BaseModel):
    """我的成绩条目（契约 `MyResultItem`：带 session_id 供跳转逐题结果）。"""

    session_id: int
    exam_id: int
    exam_title: str
    score: float
    total_points: float
    submitted_at: datetime


class MyResultPage(BaseModel):
    """我的成绩分页（契约 `MyResultPage`）。"""

    items: list[MyResultItem]
    total: int
    page: int
    page_size: int


class WrongQuestionOut(BaseModel):
    """错题本条目（契约 `WrongQuestion`）。"""

    question_id: int
    type: QuestionType
    stem: str
    wrong_count: int
    last_wrong_at: datetime


class WrongQuestionPage(BaseModel):
    """错题本分页（契约 `WrongQuestionPage`）。"""

    items: list[WrongQuestionOut]
    total: int
    page: int
    page_size: int


class ExamResultItemOut(BaseModel):
    """考试汇总中的学生条目（契约 `ExamResultSummary.items[]`）。"""

    student_id: int
    display_name: str
    status: SessionStatus
    score: float | None = None
    submitted_at: datetime | None = None


class ExamResultSummary(BaseModel):
    """教师端考试汇总（契约 `ExamResultSummary`）。"""

    exam_id: int
    total_sessions: int
    submitted: int
    average_score: float
    items: list[ExamResultItemOut]


__all__ = [
    "ExamResultItemOut",
    "ExamResultSummary",
    "MyResultItem",
    "MyResultPage",
    "ResultDetail",
    "ResultItemOut",
    "WrongQuestionOut",
    "WrongQuestionPage",
]
