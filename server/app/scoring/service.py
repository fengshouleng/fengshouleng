"""判分实现（ADR-007 / AC-16 / AC-17）。

判分**只依据试卷冻结快照**（`papers.frozen_snapshot`，结构由
`app.papers.service.freeze_paper` 固定），因此题库/试卷后续编辑不会改变历史成绩。

规则（契约 `PaperQuestion.partial_rule` 与任务卡 BE-004）：

======================  ==========================================================
题型 / 规则              得分
======================  ==========================================================
single / judge          选中集合与答案集合**完全相等** → 满分；否则 0
multiple + all_or_nothing  完全相等 → 满分；否则 0
multiple + partial      完全相等 → 满分；无错选 → round(选中正确数 / 正确总数 × points, 2)；
                        有错选（选中项不是答案子集）→ 0
======================  ==========================================================

`is_correct` 表示「完全正确」（仅完全相等为 True）；部分给分时为 False，
以便错题本（BE-005）按「未完全掌握」统计。判分与交卷状态迁移在同一事务内完成。
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import errors
from app.errors import AppError
from app.models.answer import Answer, selected_of
from app.models.exam import Exam
from app.models.exam_session import ExamSession
from app.models.paper import Paper
from app.models.paper_question import PartialRule
from app.models.question import QuestionType

_POINTS_QUANTUM = Decimal("0.01")
_ZERO = Decimal("0.00")


def as_points(value: Any) -> Decimal:
    """任意数值（JSON float / DB Decimal / str）→ 两位小数 Decimal。"""
    return Decimal(str(value)).quantize(_POINTS_QUANTUM)


def _as_set(values: Sequence[str] | None) -> frozenset[str]:
    if not values:
        return frozenset()
    return frozenset(str(value) for value in values)


def score_question(
    *,
    question_type: str,
    partial_rule: str,
    answer: Sequence[str] | None,
    selected: Sequence[str] | None,
    points: Any,
) -> tuple[bool, Decimal]:
    """单题判分：返回 `(is_correct, earned_points)`。"""
    correct = _as_set(answer)
    chosen = _as_set(selected)
    full = as_points(points)

    if not correct:
        # 脏数据兜底：没有正确答案的题不给分（不抛错，避免一条脏题毁掉整场交卷）
        return False, _ZERO

    if chosen == correct:
        return True, full

    # single / judge：部分选中或错选都是 0（答案集合必须完全相等）
    if question_type != QuestionType.MULTIPLE.value:
        return False, _ZERO

    if partial_rule != PartialRule.PARTIAL.value:
        # multiple + all_or_nothing
        return False, _ZERO

    # multiple + partial：有错选直接 0；无错选按比例给分
    if not chosen or not chosen.issubset(correct):
        return False, _ZERO
    earned = (
        Decimal(len(chosen)) / Decimal(len(correct)) * full
    ).quantize(_POINTS_QUANTUM, rounding=ROUND_HALF_UP)
    return False, earned


def snapshot_questions(db: Session, exam: Exam) -> list[dict[str, Any]]:
    """取该考试对应试卷的冻结快照题目（判分与作答视图的唯一数据来源）。"""
    paper = db.get(Paper, exam.paper_id)
    if paper is None:
        raise AppError(errors.INTERNAL_ERROR, "考试关联的试卷不存在", 500)
    snapshot = paper.frozen_snapshot
    if not isinstance(snapshot, dict):
        raise AppError(errors.INTERNAL_ERROR, "试卷尚未冻结，无法判分", 500)
    questions = snapshot.get("questions")
    if not isinstance(questions, list) or not questions:
        raise AppError(errors.INTERNAL_ERROR, "试卷快照缺少题目，无法判分", 500)
    return [item for item in questions if isinstance(item, dict)]


def grade_session(db: Session, session: ExamSession, *, exam: Exam) -> Decimal:
    """事务内逐题判分：写 `answers.is_correct/earned_points` 与 `exam_sessions.score`。

    - 幂等：重复调用得到同样结果（纯函数 + 覆写）
    - 未作答的题不建 answers 行，按 0 分计入总分
    - 不 commit：由调用方与状态迁移一起提交
    """
    items = snapshot_questions(db, exam)
    rows = db.scalars(
        select(Answer).where(Answer.session_id == session.id)
    ).all()
    by_question = {row.question_id: row for row in rows}

    total = _ZERO
    for item in items:
        try:
            question_id = int(item["question_id"])
        except (KeyError, TypeError, ValueError) as exc:  # pragma: no cover - 快照结构固定
            raise AppError(errors.INTERNAL_ERROR, "试卷快照题目结构异常", 500) from exc
        row = by_question.get(question_id)
        selected = selected_of(row) if row is not None else []
        is_correct, earned = score_question(
            question_type=str(item.get("type") or ""),
            partial_rule=str(item.get("partial_rule") or PartialRule.ALL_OR_NOTHING.value),
            answer=item.get("answer") or [],
            selected=selected,
            points=item.get("points") or 0,
        )
        if row is not None:
            row.is_correct = is_correct
            row.earned_points = earned
        total += earned

    session.score = total.quantize(_POINTS_QUANTUM)
    db.flush()
    return session.score


__all__ = [
    "as_points",
    "grade_session",
    "score_question",
    "snapshot_questions",
]
