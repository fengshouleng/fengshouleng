"""试卷业务逻辑（组卷校验 / 快照冻结 / 归属与引用保护 / 审计）。

权限（docs/12-permission-matrix.md §2）：
- 教师只能看到/修改自己的试卷；admin 可见全部
- 不存在 → 404；存在但归属他人 → 403
- `status=frozen` 后 PUT/DELETE → 409 `PAPER_FROZEN`（AC-10）
- 被 `exams` 引用时删除 → 409（BE-004 尚未建表时按未引用处理）

组卷校验（契约 v1.0.2 / CHG-025/026）：
- `question_ids` 必须存在、未软删，且（教师）归属自己
- 同一题不可重复；`order_no` 必须恰为 `1..N` 且唯一
- 每题 `0 < points <= 100` 且为 0.01 的整数倍；`1 <= Σpoints <= 10000`
- `total_points` 由服务端计算写入，请求里不允许出现该字段（schema `extra=forbid`）
- 多选题未显式给 `partial_rule` 时默认 `all_or_nothing`（ADR-007）
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import errors
from app.audit import service as audit
from app.errors import AppError
from app.models.paper import Paper, PaperStatus
from app.models.paper_question import PaperQuestion, PartialRule
from app.models.question import Question, QuestionType
from app.models.user import Role, User, utcnow
from app.papers.schemas import PaperCreate, PaperQuestionIn

PAPER_CREATE = "paper.create"
PAPER_UPDATE = "paper.update"
PAPER_DELETE = "paper.delete"
PAPER_FREEZE = "paper.freeze"

SNAPSHOT_VERSION = 1
_POINTS_QUANTUM = Decimal("0.01")
_MIN_TOTAL = Decimal("1")
_MAX_TOTAL = Decimal("10000")


@dataclass(frozen=True, slots=True)
class ResolvedItem:
    """校验通过后的单题组卷明细。"""

    question: Question
    order_no: int
    points: Decimal
    partial_rule: str


def _as_decimal(value: Any) -> Decimal:
    """JSON 浮点 / DB Decimal → 两位小数 Decimal，避免浮点合计误差。"""
    return Decimal(str(value)).quantize(_POINTS_QUANTUM)


def _as_utc(value: datetime) -> datetime:
    """统一为带时区 UTC（SQLite 读出的时间可能丢失 tzinfo）。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _normalize_partial_rule(question_type: str, partial_rule: str) -> str:
    """`partial_rule` 仅对多选生效；其余题型统一归一为 all_or_nothing。"""
    if question_type != QuestionType.MULTIPLE.value:
        return PartialRule.ALL_OR_NOTHING.value
    if partial_rule == PartialRule.PARTIAL.value:
        return PartialRule.PARTIAL.value
    return PartialRule.ALL_OR_NOTHING.value


def _resolve_items(
    db: Session,
    payload: PaperCreate,
    *,
    user: User,
) -> tuple[list[ResolvedItem], Decimal]:
    """校验组卷请求并计算总分；任何非法输入 → 422 VALIDATION_ERROR。"""
    items: list[PaperQuestionIn] = list(payload.questions)
    question_ids = [item.question_id for item in items]
    if len(set(question_ids)) != len(question_ids):
        raise errors.validation_error("同一题目不能重复加入试卷")

    order_nos = sorted(item.order_no for item in items)
    if order_nos != list(range(1, len(items) + 1)):
        raise errors.validation_error("order_no 必须从 1 开始连续且唯一")

    rows = db.scalars(select(Question).where(Question.id.in_(question_ids))).all()
    by_id = {question.id: question for question in rows}

    resolved: list[ResolvedItem] = []
    for item in items:
        question = by_id.get(item.question_id)
        if question is None or question.is_deleted:
            raise errors.validation_error(
                f"题目不存在或已删除：{item.question_id}"
            )
        if user.role != Role.ADMIN.value and question.owner_id != user.id:
            raise errors.forbidden("只能使用自己创建的题目组卷")
        resolved.append(
            ResolvedItem(
                question=question,
                order_no=item.order_no,
                points=_as_decimal(item.points),
                partial_rule=_normalize_partial_rule(
                    question.type,
                    item.partial_rule.value,
                ),
            )
        )

    total = sum((item.points for item in resolved), Decimal("0.00"))
    if total < _MIN_TOTAL or total > _MAX_TOTAL:
        raise errors.validation_error("试卷总分必须在 1 到 10000 之间")
    return resolved, total


def _replace_items(db: Session, paper: Paper, items: list[ResolvedItem]) -> None:
    """用校验后的明细整体替换试卷题目（PUT 是全量覆盖）。

    先逐条删除并 flush，再插入新明细：避免同一 `question_id` 被保留时
    SQLAlchemy 在同一 flush 里出现「先 INSERT 后 DELETE」的 PK 冲突。
    """
    existing = db.scalars(
        select(PaperQuestion).where(PaperQuestion.paper_id == paper.id)
    ).all()
    for row in existing:
        db.delete(row)
    if existing:
        db.flush()

    for item in items:
        db.add(
            PaperQuestion(
                paper_id=paper.id,
                question_id=item.question.id,
                order_no=item.order_no,
                points=item.points,
                partial_rule=item.partial_rule,
            )
        )
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(errors.CONFLICT, "试卷题目顺序冲突，请重试", 409) from exc


def list_papers(
    db: Session,
    *,
    user: User,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Paper], int]:
    """分页列表：教师仅自己的，admin 全部。"""
    conditions: list[Any] = []
    if user.role != Role.ADMIN.value:
        conditions.append(Paper.owner_id == user.id)
    count_stmt = select(func.count()).select_from(Paper)
    stmt = select(Paper)
    if conditions:
        count_stmt = count_stmt.where(*conditions)
        stmt = stmt.where(*conditions)
    total = int(db.scalar(count_stmt) or 0)
    stmt = (
        stmt.order_by(Paper.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(db.scalars(stmt).all()), total


def get_paper(db: Session, paper_id: int, user: User) -> Paper:
    """取详情：不存在 404；存在但非本人且非 admin 403。"""
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise errors.not_found("试卷不存在")
    if user.role != Role.ADMIN.value and paper.owner_id != user.id:
        raise errors.forbidden()
    return paper


def list_paper_questions(db: Session, paper_id: int) -> list[PaperQuestion]:
    """按 order_no 返回试卷明细。"""
    return list(
        db.scalars(
            select(PaperQuestion)
            .where(PaperQuestion.paper_id == paper_id)
            .order_by(PaperQuestion.order_no)
        ).all()
    )


def paper_questions_map(
    db: Session,
    paper_ids: list[int],
) -> dict[int, list[PaperQuestion]]:
    """批量取多张试卷的明细，避免列表接口 N+1。"""
    if not paper_ids:
        return {}
    grouped: dict[int, list[PaperQuestion]] = {}
    for row in db.scalars(
        select(PaperQuestion)
        .where(PaperQuestion.paper_id.in_(paper_ids))
        .order_by(PaperQuestion.paper_id, PaperQuestion.order_no)
    ):
        grouped.setdefault(row.paper_id, []).append(row)
    return grouped


def create_paper(
    db: Session,
    payload: PaperCreate,
    *,
    owner: User,
    ip: str | None = None,
    user_agent: str | None = None,
) -> Paper:
    """新建试卷：校验 → 汇总总分 → 写明细 → 审计 → 提交。"""
    items, total = _resolve_items(db, payload, user=owner)
    paper = Paper(
        owner_id=owner.id,
        title=payload.title.strip(),
        total_points=total,
        status=PaperStatus.DRAFT.value,
        frozen_snapshot=None,
        frozen_at=None,
    )
    db.add(paper)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(errors.CONFLICT, "试卷创建失败，请重试", 409) from exc

    _replace_items(db, paper, items)
    audit.record(
        db,
        action=PAPER_CREATE,
        actor_id=owner.id,
        target_type="paper",
        target_id=paper.id,
        ip=ip,
        user_agent=user_agent,
        detail={
            "question_count": len(items),
            "total_points": float(total),
        },
    )
    db.commit()
    return paper


def update_paper(
    db: Session,
    paper_id: int,
    payload: PaperCreate,
    *,
    user: User,
    ip: str | None = None,
    user_agent: str | None = None,
) -> Paper:
    """全量修改：冻结后 409；校验通过后替换明细并重算总分。"""
    paper = get_paper(db, paper_id, user)
    if paper.status == PaperStatus.FROZEN.value:
        raise AppError(errors.PAPER_FROZEN, "试卷已冻结，无法修改", 409)

    items, total = _resolve_items(db, payload, user=user)
    changed: list[str] = []
    if paper.title != payload.title.strip():
        changed.append("title")
    if _as_decimal(paper.total_points) != total:
        changed.append("total_points")
    changed.append("questions")

    paper.title = payload.title.strip()
    paper.total_points = total
    _replace_items(db, paper, items)
    audit.record(
        db,
        action=PAPER_UPDATE,
        actor_id=user.id,
        target_type="paper",
        target_id=paper.id,
        ip=ip,
        user_agent=user_agent,
        detail={
            "fields": changed,
            "question_count": len(items),
            "total_points": float(total),
        },
    )
    db.commit()
    return paper


def _is_referenced_by_exam(db: Session, paper_id: int) -> bool:
    """探测 exams 是否引用该试卷。

    BE-004 之前 exams 表还不存在：用 inspector 判断，表未建时视为未引用，
    避免把考试任务的建表顺序变成组卷任务的硬依赖。
    """
    inspector = inspect(db.get_bind())
    if not inspector.has_table("exams"):
        return False
    count = db.execute(
        text("SELECT COUNT(*) FROM exams WHERE paper_id = :paper_id"),
        {"paper_id": paper_id},
    ).scalar_one()
    return bool(count)


def delete_paper(
    db: Session,
    paper_id: int,
    *,
    user: User,
    ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    """删除试卷：已冻结 409；被考试引用 409；否则连同明细一起删除。"""
    paper = get_paper(db, paper_id, user)
    if paper.status == PaperStatus.FROZEN.value:
        raise AppError(errors.PAPER_FROZEN, "试卷已冻结，无法删除", 409)
    if _is_referenced_by_exam(db, paper.id):
        raise AppError(errors.CONFLICT, "试卷已被考试引用，无法删除", 409)

    existing = db.scalars(
        select(PaperQuestion).where(PaperQuestion.paper_id == paper.id)
    ).all()
    for row in existing:
        db.delete(row)
    if existing:
        db.flush()
    audit.record(
        db,
        action=PAPER_DELETE,
        actor_id=user.id,
        target_type="paper",
        target_id=paper.id,
        ip=ip,
        user_agent=user_agent,
        detail={
            "title": paper.title,
            "total_points": float(_as_decimal(paper.total_points)),
        },
    )
    db.delete(paper)
    db.commit()


def _snapshot_question(item: PaperQuestion, question: Question) -> dict[str, Any]:
    """单题快照：题目/选项/答案/分值/规则的深拷贝（不保留 ORM 引用）。"""
    options = [
        {"key": str(option.get("key")), "text": str(option.get("text"))}
        for option in (question.options or [])
        if isinstance(option, dict)
    ]
    answer = [str(value) for value in (question.answer or [])]
    return {
        "question_id": int(item.question_id),
        "order_no": int(item.order_no),
        "type": question.type,
        "stem": question.stem,
        "options": options,
        "answer": answer,
        "points": float(_as_decimal(item.points)),
        "partial_rule": _normalize_partial_rule(
            question.type,
            item.partial_rule or PartialRule.ALL_OR_NOTHING.value,
        ),
    }


def freeze_paper(
    db: Session,
    paper: Paper,
    *,
    now: datetime | None = None,
    actor_id: int | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> Paper:
    """冻结试卷并写入 `frozen_snapshot`（供 BE-004 判分直接消费）。

    快照结构（固定 v1，字段名不可随意增删）::

        {"version": 1, "paper_id": int, "total_points": float,
         "frozen_at": iso8601,
         "questions": [{"question_id": int, "order_no": int,
                        "type": "single|multiple|judge", "stem": str,
                        "options": [{"key": str, "text": str}],
                        "answer": [str], "points": float,
                        "partial_rule": "all_or_nothing|partial"}]}

    - 幂等：已冻结且快照存在的试卷直接返回，不刷新 `frozen_at`、不重复写审计
    - **不提交事务**：由调用方（BE-004 发布考试时）与业务写入同事务 commit
    """
    if paper.status == PaperStatus.FROZEN.value:
        if paper.frozen_snapshot is None:
            raise AppError(errors.PAPER_FROZEN, "试卷已冻结但缺少快照", 409)
        return paper

    frozen_at = _as_utc(now) if now is not None else utcnow()
    items = list_paper_questions(db, paper.id)
    if not items:
        raise AppError(errors.CONFLICT, "试卷没有题目，无法冻结", 409)

    question_ids = [item.question_id for item in items]
    rows = db.scalars(select(Question).where(Question.id.in_(question_ids))).all()
    by_id = {question.id: question for question in rows}

    snapshot_questions: list[dict[str, Any]] = []
    for item in items:
        question = by_id.get(item.question_id)
        if question is None:
            raise AppError(
                errors.CONFLICT,
                f"试卷引用的题目不存在：{item.question_id}",
                409,
            )
        snapshot_questions.append(_snapshot_question(item, question))

    total_points = sum(
        (_as_decimal(item.points) for item in items),
        Decimal("0.00"),
    )

    paper.frozen_snapshot = {
        "version": SNAPSHOT_VERSION,
        "paper_id": int(paper.id),
        "total_points": float(total_points),
        "frozen_at": frozen_at.isoformat(),
        "questions": snapshot_questions,
    }
    paper.total_points = total_points
    paper.status = PaperStatus.FROZEN.value
    paper.frozen_at = frozen_at

    audit.record(
        db,
        action=PAPER_FREEZE,
        actor_id=paper.owner_id if actor_id is None else actor_id,
        target_type="paper",
        target_id=paper.id,
        ip=ip,
        user_agent=user_agent,
        detail={
            "question_count": len(snapshot_questions),
            "total_points": float(total_points),
            "frozen_at": frozen_at.isoformat(),
        },
    )
    db.flush()
    return paper


__all__ = [
    "PAPER_CREATE",
    "PAPER_DELETE",
    "PAPER_FREEZE",
    "PAPER_UPDATE",
    "SNAPSHOT_VERSION",
    "ResolvedItem",
    "create_paper",
    "delete_paper",
    "freeze_paper",
    "get_paper",
    "list_paper_questions",
    "list_papers",
    "paper_questions_map",
    "update_paper",
]
