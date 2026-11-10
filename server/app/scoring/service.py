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

from collections import OrderedDict
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Connection
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

#: 冻结快照 LRU 缓存（进程内）。`papers.frozen_snapshot` 一旦冻结就**不可变**
#: （`freeze_paper` 幂等早返回，`update_paper` 拒绝已冻结试卷），因此按
#: `(数据库 URL, paper_id)` 缓存题目元组是安全的；它把自动保存每请求的
#: 「查 papers 大 JSON 列 + 解析 100 题」降为一次 dict 命中。
#: 键里带数据库 URL：测试每个用例是独立的临时 SQLite，`paper_id` 都从 1 开始，
#: 只用 paper_id 会跨用例命中上一条的快照（2026-11-11 首次尝试就踩到这个坑）。
_SNAPSHOT_CACHE: OrderedDict[tuple[str, int], tuple[dict[str, Any], ...]] = OrderedDict()
_SNAPSHOT_CACHE_MAX = 64


def clear_snapshot_cache() -> None:
    """清空冻结快照缓存（切换数据库或测试重建库后调用）。"""
    _SNAPSHOT_CACHE.clear()


def _engine_key(db: Session) -> str:
    """缓存键里的数据库标识：同一进程内换库（测试/运维）时不会串味。"""
    bind = db.get_bind()
    engine = bind.engine if isinstance(bind, Connection) else bind
    return str(engine.url)


def _cache_snapshot(paper_id: int, url: str, items: tuple[dict[str, Any], ...]) -> None:
    _SNAPSHOT_CACHE[(url, paper_id)] = items
    _SNAPSHOT_CACHE.move_to_end((url, paper_id))
    while len(_SNAPSHOT_CACHE) > _SNAPSHOT_CACHE_MAX:
        _SNAPSHOT_CACHE.popitem(last=False)


def preload_snapshot(db: Session, paper: Paper) -> int:
    """把一份已冻结试卷的快照装进缓存（启动预热用），返回题目数。"""
    items = _items_from_paper(paper)
    _cache_snapshot(int(paper.id), _engine_key(db), items)
    return len(items)


def _items_from_paper(paper: Paper | None) -> tuple[dict[str, Any], ...]:
    """校验试卷快照结构并取出题目元组（纯函数，便于预热与读取共用）。"""
    if paper is None:
        raise AppError(errors.INTERNAL_ERROR, "考试关联的试卷不存在", 500)
    snapshot = paper.frozen_snapshot
    if not isinstance(snapshot, dict):
        raise AppError(errors.INTERNAL_ERROR, "试卷尚未冻结，无法判分", 500)
    questions = snapshot.get("questions")
    if not isinstance(questions, list) or not questions:
        raise AppError(errors.INTERNAL_ERROR, "试卷快照缺少题目，无法判分", 500)
    return tuple(item for item in questions if isinstance(item, dict))


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
    """取该考试对应试卷的冻结快照题目（判分与作答视图的唯一数据来源）。

    走进程内 LRU（见 `_SNAPSHOT_CACHE`）：冻结快照不可变，重复读取直接命中，
    省掉一次 `papers` 大 JSON 列的查询与解析——这是自动保存每请求的固定开销
    （docs/61 §10.2.11）。命中时返回的是**同内容的新 list**，调用方不得修改其中的 dict。
    """
    url = _engine_key(db)
    key = (url, int(exam.paper_id))
    cached = _SNAPSHOT_CACHE.get(key)
    if cached is not None:
        _SNAPSHOT_CACHE.move_to_end(key)
        return list(cached)
    items = _items_from_paper(db.get(Paper, exam.paper_id))
    _cache_snapshot(int(exam.paper_id), url, items)
    return list(items)



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
    "clear_snapshot_cache",
    "grade_session",
    "preload_snapshot",
    "score_question",
    "snapshot_questions",
]
