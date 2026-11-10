"""题库业务逻辑（CRUD + 批量导入 + 归属/引用校验 + 审计）。

权限（docs/12-permission-matrix.md）：
- 教师只能看到/修改自己的题目；admin 可见全部
- 不存在或已软删 → 404；存在但不属于当前教师 → 403（先 404 后 403）
- 被试卷引用时禁止删除 → 409 `QUESTION_IN_USE`（AC-07）
"""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import cast, exists, func, inspect, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import errors
from app.audit import service as audit
from app.errors import AppError
from app.models.question import Question, QuestionType
from app.models.user import Role, User
from app.questions.importer import parse_import_file
from app.questions.schemas import ImportFailure, ImportResult, QuestionCreate

QUESTION_CREATE = "question.create"
QUESTION_UPDATE = "question.update"
QUESTION_DELETE = "question.delete"
QUESTION_IMPORT = "question.import"


def compute_stem_hash(stem: str) -> str:
    """导入幂等键的第三列：md5(规范化题干)。"""
    normalized = stem.strip().encode("utf-8")
    return hashlib.md5(normalized, usedforsecurity=False).hexdigest()


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _build_conditions(
    user: User,
    *,
    keyword: str | None,
    question_type: QuestionType | None,
    tag: str | None,
    dialect_name: str,
) -> list[Any]:
    conditions: list[Any] = [Question.is_deleted.is_(False)]
    if user.role != Role.ADMIN.value:
        conditions.append(Question.owner_id == user.id)
    if keyword:
        pattern = f"%{_escape_like(keyword)}%"
        conditions.append(Question.stem.ilike(pattern, escape="\\"))
    if question_type is not None:
        conditions.append(Question.type == question_type.value)
    if tag:
        if dialect_name == "postgresql":
            conditions.append(cast(Question.tags, postgresql.JSONB()).contains([tag]))
        else:
            tag_rows = func.json_each(Question.tags).table_valued("value")
            conditions.append(
                exists(select(1).select_from(tag_rows).where(tag_rows.c.value == tag))
            )
    return conditions


def list_questions(
    db: Session,
    *,
    user: User,
    keyword: str | None = None,
    question_type: QuestionType | None = None,
    tag: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Question], int]:
    """分页列表：教师仅自己的，admin 全部；支持 keyword/type/tag。"""
    conditions = _build_conditions(
        user,
        keyword=keyword,
        question_type=question_type,
        tag=tag,
        dialect_name=db.get_bind().dialect.name,
    )
    total = int(
        db.scalar(select(func.count()).select_from(Question).where(*conditions)) or 0
    )
    stmt = (
        select(Question)
        .where(*conditions)
        .order_by(Question.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(db.scalars(stmt).all()), total


def get_question(db: Session, question_id: int, user: User) -> Question:
    """取详情：软删/不存在 404，非本人且非 admin 403。"""
    question = db.get(Question, question_id)
    if question is None or question.is_deleted:
        raise errors.not_found("题目不存在")
    if user.role != Role.ADMIN.value and question.owner_id != user.id:
        raise errors.forbidden()
    return question


def _apply_payload(question: Question, payload: QuestionCreate) -> None:
    question.type = payload.type.value
    question.stem = payload.stem.strip()
    question.stem_hash = compute_stem_hash(payload.stem)
    question.options = [option.model_dump() for option in payload.options]
    question.answer = list(payload.answer)
    question.explanation = payload.explanation
    question.difficulty = payload.difficulty
    question.tags = list(payload.tags)


def _ensure_unique(
    db: Session,
    *,
    owner_id: int,
    question_type: str,
    stem_hash: str,
    exclude_id: int | None = None,
) -> None:
    stmt = select(Question.id).where(
        Question.owner_id == owner_id,
        Question.type == question_type,
        Question.stem_hash == stem_hash,
    )
    if exclude_id is not None:
        stmt = stmt.where(Question.id != exclude_id)
    if db.scalar(stmt) is not None:
        raise AppError(errors.CONFLICT, "同题干同类型的题目已存在", 409)


def create_question(
    db: Session,
    payload: QuestionCreate,
    *,
    owner: User,
    ip: str | None = None,
    user_agent: str | None = None,
) -> Question:
    """新建题目：写审计并提交；重复题干 409。"""
    stem_hash = compute_stem_hash(payload.stem)
    _ensure_unique(
        db,
        owner_id=owner.id,
        question_type=payload.type.value,
        stem_hash=stem_hash,
    )

    question = Question(owner_id=owner.id, is_deleted=False)
    _apply_payload(question, payload)
    db.add(question)
    try:
        db.flush()
    except IntegrityError as exc:  # 并发下由唯一索引兜底
        db.rollback()
        raise AppError(errors.CONFLICT, "同题干同类型的题目已存在", 409) from exc

    audit.record(
        db,
        action=QUESTION_CREATE,
        actor_id=owner.id,
        target_type="question",
        target_id=question.id,
        ip=ip,
        user_agent=user_agent,
        detail={
            "type": question.type,
            "difficulty": question.difficulty,
            "tag_count": len(question.tags),
        },
    )
    db.commit()
    return question


def update_question(
    db: Session,
    question_id: int,
    payload: QuestionCreate,
    *,
    user: User,
    ip: str | None = None,
    user_agent: str | None = None,
) -> Question:
    """全量修改：归属校验后覆盖字段，写审计并提交。"""
    question = get_question(db, question_id, user)
    stem_hash = compute_stem_hash(payload.stem)
    _ensure_unique(
        db,
        owner_id=question.owner_id,
        question_type=payload.type.value,
        stem_hash=stem_hash,
        exclude_id=question.id,
    )

    changed: list[str] = []
    if payload.type.value != question.type:
        changed.append("type")
    if payload.stem.strip() != question.stem:
        changed.append("stem")
    if payload.explanation != question.explanation:
        changed.append("explanation")
    if payload.difficulty != question.difficulty:
        changed.append("difficulty")
    if list(payload.tags) != list(question.tags):
        changed.append("tags")

    _apply_payload(question, payload)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(errors.CONFLICT, "同题干同类型的题目已存在", 409) from exc

    audit.record(
        db,
        action=QUESTION_UPDATE,
        actor_id=user.id,
        target_type="question",
        target_id=question.id,
        ip=ip,
        user_agent=user_agent,
        detail={
            "fields": changed,
            "type": question.type,
            "difficulty": question.difficulty,
        },
    )
    db.commit()
    return question


def _is_referenced_by_paper(db: Session, question_id: int) -> bool:
    """探测 paper_questions 是否引用该题。

    BE-003 之前试卷表还不存在：用 inspector 判断，表未建时视为未引用，
    避免把组卷任务的建表顺序变成题库任务的硬依赖。
    """
    inspector = inspect(db.get_bind())
    if not inspector.has_table("paper_questions"):
        return False
    count = db.execute(
        text("SELECT COUNT(*) FROM paper_questions WHERE question_id = :question_id"),
        {"question_id": question_id},
    ).scalar_one()
    return bool(count)


def delete_question(
    db: Session,
    question_id: int,
    *,
    user: User,
    ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    """软删除：被试卷引用时 409；否则置 is_deleted=true。"""
    question = get_question(db, question_id, user)
    if _is_referenced_by_paper(db, question.id):
        raise AppError(
            errors.QUESTION_IN_USE,
            "题目已被试卷引用，无法删除",
            409,
        )

    question.is_deleted = True
    audit.record(
        db,
        action=QUESTION_DELETE,
        actor_id=user.id,
        target_type="question",
        target_id=question.id,
        ip=ip,
        user_agent=user_agent,
        detail={"type": question.type, "soft_deleted": True},
    )
    db.commit()


def import_questions(
    db: Session,
    *,
    owner: User,
    data: bytes,
    filename: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> ImportResult:
    """批量导入：逐行校验、部分成功、按 (owner, type, stem_hash) 幂等跳过。"""
    parsed_rows = parse_import_file(data, filename)
    total = len(parsed_rows)
    imported = 0
    skipped_duplicates = 0
    failed: list[ImportFailure] = []

    for row in parsed_rows:
        if row.payload is None:
            failed.append(
                ImportFailure(row=row.row, reason=row.reason or "题目校验失败")
            )
            continue

        payload = row.payload
        stem_hash = compute_stem_hash(payload.stem)
        existing = db.scalar(
            select(Question.id).where(
                Question.owner_id == owner.id,
                Question.type == payload.type.value,
                Question.stem_hash == stem_hash,
            )
        )
        if existing is not None:
            skipped_duplicates += 1
            continue

        question = Question(owner_id=owner.id, is_deleted=False)
        _apply_payload(question, payload)
        try:
            with db.begin_nested():
                db.add(question)
                db.flush()
        except IntegrityError:
            # 同文件/并发重复：按幂等语义跳过而非失败
            skipped_duplicates += 1
            continue
        imported += 1

    audit.record(
        db,
        action=QUESTION_IMPORT,
        actor_id=owner.id,
        target_type="question",
        target_id="import",
        ip=ip,
        user_agent=user_agent,
        detail={
            "total": total,
            "imported": imported,
            "skipped_duplicates": skipped_duplicates,
            "failed": len(failed),
        },
    )
    db.commit()
    return ImportResult(
        imported=imported,
        skipped_duplicates=skipped_duplicates,
        failed=failed,
        total=total,
    )


__all__ = [
    "QUESTION_CREATE",
    "QUESTION_DELETE",
    "QUESTION_IMPORT",
    "QUESTION_UPDATE",
    "compute_stem_hash",
    "create_question",
    "delete_question",
    "get_question",
    "import_questions",
    "list_questions",
    "update_question",
]
