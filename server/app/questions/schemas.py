"""题目相关 Pydantic 模型（对齐 contracts/openapi.yaml v1.0.1）。

校验规则（任务卡 + AC-05/AC-06）：
- 题干/解析非空；选项 key 唯一且形如 `A`-`Z`
- 单选：恰好 1 个答案且属于选项
- 多选：至少 2 个答案且都属于选项
- 判断：选项固定 `A=对` / `B=错`，答案恰好 1 个
"""

from __future__ import annotations

from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.question import QuestionType


class Option(BaseModel):
    """Option（契约 components/schemas/Option）。"""

    key: str = Field(pattern=r"^[A-Z]$")
    text: str


class QuestionCreate(BaseModel):
    """QuestionCreate（新增/修改请求）。"""

    type: QuestionType
    stem: str = Field(min_length=1)
    options: list[Option] = Field(min_length=2)
    answer: list[str] = Field(min_length=1)
    explanation: str = Field(min_length=1)
    difficulty: int = Field(ge=1, le=5)
    tags: list[str]

    @field_validator("stem", "explanation")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("不能为空")
        return value

    @model_validator(mode="after")
    def _validate_consistency(self) -> Self:
        keys = [option.key for option in self.options]
        if len(set(keys)) != len(keys):
            raise ValueError("选项 key 不能重复")
        if len(set(self.answer)) != len(self.answer):
            raise ValueError("答案不能重复")

        key_set = set(keys)
        unknown = [item for item in self.answer if item not in key_set]
        if unknown:
            raise ValueError(f"答案必须属于选项：{', '.join(unknown)}")

        if self.type is QuestionType.SINGLE and len(self.answer) != 1:
            raise ValueError("单选题必须恰好 1 个答案")
        if self.type is QuestionType.MULTIPLE and len(self.answer) < 2:
            raise ValueError("多选题至少需要 2 个答案")
        if self.type is QuestionType.JUDGE:
            if keys != ["A", "B"]:
                raise ValueError("判断题选项必须固定为 A/B 两个")
            if len(self.answer) != 1:
                raise ValueError("判断题必须恰好 1 个答案")
        return self


class QuestionOut(BaseModel):
    """Question（响应）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_id: int
    type: QuestionType
    stem: str
    options: list[Option]
    answer: list[str]
    explanation: str
    difficulty: int
    tags: list[str]
    created_at: datetime
    updated_at: datetime


class QuestionPage(BaseModel):
    """QuestionPage（分页响应）。"""

    items: list[QuestionOut]
    total: int
    page: int
    page_size: int


class ImportFailure(BaseModel):
    """ImportFailure：单行失败明细。"""

    row: int = Field(ge=1)
    reason: str


class ImportResult(BaseModel):
    """ImportResult（AC-06 / AC-08）。

    `total` = 文件数据行数（不含表头），
    且恒等于 `imported + skipped_duplicates + len(failed)`。
    """

    imported: int = Field(ge=0)
    skipped_duplicates: int = Field(ge=0)
    failed: list[ImportFailure]
    total: int = Field(ge=0)


__all__ = [
    "ImportFailure",
    "ImportResult",
    "Option",
    "QuestionCreate",
    "QuestionOut",
    "QuestionPage",
]
