"""考试管理：发布（同事务冻结试卷）/ 列表 / 状态机 / 归属校验 / 审计。

契约要点（v1.0.2）：

- `POST /exams`：校验时间窗（`start_at < end_at`、`duration_min` 不超过时间窗）、试卷存在且为
  draft；`publish=true` 时**同一事务**内调用 `papers.service.freeze_paper` 写快照，再提交
  （`freeze_paper` 刻意不 commit，见 TASK-BE-003）。
- 状态机（C-18）：只允许 `draft → published`（冻结 + `snapshot_version=1`）与
  `published → closed`（手动结束）；回退、跳跃、重复设置需要副作用的状态一律 409 `CONFLICT`。
- 已发布考试修改时间窗/时长：仅 `now < start_at` 允许，否则 409 `CONFLICT`；已结束（closed）
  的考试不可再改配置。
- 归属（C-17 / docs/12 §2）：不存在 → 404；存在但非本人且非 admin → 403（教师只能操作自己的
  考试，admin 全部）。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import errors
from app.audit import service as audit
from app.auth.service import as_utc
from app.errors import AppError
from app.exams.schemas import ExamCreate, ExamPatch
from app.models.exam import LEGAL_EXAM_TRANSITIONS, Exam, ExamStatus
from app.models.paper import Paper, PaperStatus
from app.models.user import Role, User, utcnow
from app.papers import service as papers_service
from app.papers.service import SNAPSHOT_VERSION

EXAM_CREATE = "exam.create"
EXAM_UPDATE = "exam.update"
EXAM_PUBLISH = "exam.publish"
EXAM_CLOSE = "exam.close"

_TIME_FIELDS: tuple[str, ...] = ("start_at", "end_at", "duration_min")


def _validate_window(
    start_at: datetime,
    end_at: datetime,
    duration_min: int,
) -> tuple[datetime, datetime]:
    """校验时间窗并归一为 UTC；非法 → 422 `VALIDATION_ERROR`（契约 CHG-026 口径）。"""
    start = as_utc(start_at)
    end = as_utc(end_at)
    if start >= end:
        raise errors.validation_error("结束时间必须晚于开始时间")
    if timedelta(minutes=duration_min) > (end - start):
        raise errors.validation_error("考试时长不能超过考试时间窗")
    return start, end


def list_exams(
    db: Session,
    *,
    user: User,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Exam], int]:
    """分页列表：教师仅自己创建的，admin 全部（学生无权调用，路由层 403）。"""
    conditions = []
    if user.role != Role.ADMIN.value:
        conditions.append(Exam.created_by == user.id)
    if status is not None:
        conditions.append(Exam.status == status)

    count_stmt = select(func.count()).select_from(Exam)
    list_stmt = select(Exam)
    if conditions:
        count_stmt = count_stmt.where(*conditions)
        list_stmt = list_stmt.where(*conditions)

    total = int(db.scalar(count_stmt) or 0)
    list_stmt = (
        list_stmt.order_by(Exam.start_at.desc(), Exam.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(db.scalars(list_stmt).all()), total


def get_exam(db: Session, exam_id: int, user: User) -> Exam:
    """取详情：不存在 404；存在但非本人且非 admin → 403。"""
    exam = db.get(Exam, exam_id)
    if exam is None:
        raise errors.not_found("考试不存在")
    if user.role != Role.ADMIN.value and exam.created_by != user.id:
        raise errors.forbidden("无权操作他人的考试")
    return exam


def _load_draft_paper(db: Session, payload: ExamCreate, *, creator: User) -> Paper:
    """创建考试用的试卷必须是存在、可用的草稿，且（教师）归属自己。"""
    paper = db.get(Paper, payload.paper_id)
    if paper is None or paper.status != PaperStatus.DRAFT.value:
        raise errors.validation_error("试卷不存在或不是草稿状态，无法创建考试")
    if creator.role != Role.ADMIN.value and paper.owner_id != creator.id:
        raise errors.forbidden("只能使用自己的试卷创建考试")
    return paper


def create_exam(
    db: Session,
    payload: ExamCreate,
    *,
    creator: User,
    ip: str | None = None,
    user_agent: str | None = None,
    now: datetime | None = None,
) -> Exam:
    """创建考试；`publish=true` 时与试卷冻结在同一事务内提交。"""
    start, end = _validate_window(payload.start_at, payload.end_at, payload.duration_min)
    paper = _load_draft_paper(db, payload, creator=creator)
    moment = as_utc(now) if now is not None else utcnow()

    exam = Exam(
        paper_id=paper.id,
        title=payload.title.strip(),
        start_at=start,
        end_at=end,
        duration_min=payload.duration_min,
        shuffle_questions=payload.shuffle_questions,
        shuffle_options=payload.shuffle_options,
        status=ExamStatus.DRAFT.value,
        snapshot_version=None,
        created_by=creator.id,
    )
    db.add(exam)
    db.flush()

    if payload.publish:
        # freeze_paper 不 commit：快照 / paper.frozen_at / exam 状态同一事务落库
        papers_service.freeze_paper(
            db,
            paper,
            now=moment,
            actor_id=creator.id,
            ip=ip,
            user_agent=user_agent,
        )
        exam.status = ExamStatus.PUBLISHED.value
        exam.snapshot_version = SNAPSHOT_VERSION

    audit.record(
        db,
        action=EXAM_CREATE,
        actor_id=creator.id,
        target_type="exam",
        target_id=exam.id,
        ip=ip,
        user_agent=user_agent,
        detail={
            "paper_id": paper.id,
            "title": exam.title,
            "start_at": start.isoformat(),
            "end_at": end.isoformat(),
            "duration_min": exam.duration_min,
            "status": exam.status,
        },
    )
    if payload.publish:
        audit.record(
            db,
            action=EXAM_PUBLISH,
            actor_id=creator.id,
            target_type="exam",
            target_id=exam.id,
            ip=ip,
            user_agent=user_agent,
            detail={
                "paper_id": paper.id,
                "snapshot_version": exam.snapshot_version,
            },
        )
    db.commit()
    return exam


def _resolve_target_status(exam: Exam, raw_status: object) -> str | None:
    """校验状态迁移合法性（C-18）：非法迁移 / 重复的副作用状态 → 409。"""
    if raw_status is None:
        return None
    target = raw_status.value if isinstance(raw_status, ExamStatus) else str(raw_status)
    if target != exam.status and (exam.status, target) not in LEGAL_EXAM_TRANSITIONS:
        raise AppError(errors.CONFLICT, "非法的考试状态迁移", 409)
    if target == exam.status and target in (
        ExamStatus.PUBLISHED.value,
        ExamStatus.CLOSED.value,
    ):
        # 重复发布/重复结束都要求副作用（冻结/审计），按冲突处理（契约 PATCH description）
        raise AppError(errors.CONFLICT, "重复设置同一状态（需要副作用），已拒绝", 409)
    return target


def _coerce_datetime(value: object, fallback: datetime) -> datetime:
    return as_utc(value) if isinstance(value, datetime) else as_utc(fallback)


def _apply_config_changes(
    exam: Exam,
    changes: dict[str, object],
    *,
    moment: datetime,
) -> list[str]:
    """应用 title / 时间窗 / 时长；返回实际变更字段名。"""
    config_fields = [
        field for field in ("title", *_TIME_FIELDS) if changes.get(field) is not None
    ]
    if not config_fields:
        return []

    if exam.status == ExamStatus.CLOSED.value:
        raise AppError(errors.CONFLICT, "已结束的考试不可修改", 409)
    time_changes = [field for field in config_fields if field in _TIME_FIELDS]
    if (
        exam.status == ExamStatus.PUBLISHED.value
        and time_changes
        and moment >= as_utc(exam.start_at)
    ):
        raise AppError(errors.CONFLICT, "考试已开始，不能修改时间窗或时长", 409)

    new_start = _coerce_datetime(changes.get("start_at"), exam.start_at)
    new_end = _coerce_datetime(changes.get("end_at"), exam.end_at)
    raw_duration = changes.get("duration_min")
    new_duration = int(raw_duration) if isinstance(raw_duration, int) else exam.duration_min
    _validate_window(new_start, new_end, new_duration)

    changed: list[str] = []
    title = changes.get("title")
    if isinstance(title, str) and title.strip() != exam.title:
        exam.title = title.strip()
        changed.append("title")
    if "start_at" in config_fields and new_start != as_utc(exam.start_at):
        exam.start_at = new_start
        changed.append("start_at")
    if "end_at" in config_fields and new_end != as_utc(exam.end_at):
        exam.end_at = new_end
        changed.append("end_at")
    if "duration_min" in config_fields and new_duration != exam.duration_min:
        exam.duration_min = new_duration
        changed.append("duration_min")
    return changed


def update_exam(
    db: Session,
    exam_id: int,
    payload: ExamPatch,
    *,
    user: User,
    ip: str | None = None,
    user_agent: str | None = None,
    now: datetime | None = None,
) -> Exam:
    """修改配置 / 迁移状态（draft→published 触发冻结；published→closed 手动结束）。"""
    exam = get_exam(db, exam_id, user)
    moment = as_utc(now) if now is not None else utcnow()
    changes: dict[str, object] = dict(payload.model_dump(exclude_unset=True))

    target = _resolve_target_status(exam, changes.get("status"))
    changed = _apply_config_changes(exam, changes, moment=moment)

    if target is not None and target != exam.status:
        from_status = exam.status
        if target == ExamStatus.PUBLISHED.value:
            paper = db.get(Paper, exam.paper_id)
            if paper is None:
                raise errors.not_found("考试关联的试卷不存在")
            papers_service.freeze_paper(
                db,
                paper,
                now=moment,
                actor_id=user.id,
                ip=ip,
                user_agent=user_agent,
            )
            exam.status = ExamStatus.PUBLISHED.value
            exam.snapshot_version = SNAPSHOT_VERSION
            audit.record(
                db,
                action=EXAM_PUBLISH,
                actor_id=user.id,
                target_type="exam",
                target_id=exam.id,
                ip=ip,
                user_agent=user_agent,
                detail={
                    "paper_id": exam.paper_id,
                    "snapshot_version": exam.snapshot_version,
                },
            )
        else:  # closed
            exam.status = ExamStatus.CLOSED.value
            audit.record(
                db,
                action=EXAM_CLOSE,
                actor_id=user.id,
                target_type="exam",
                target_id=exam.id,
                ip=ip,
                user_agent=user_agent,
                detail={"from": from_status, "to": exam.status},
            )
        changed.append("status")

    if changed and changed != ["status"]:
        audit.record(
            db,
            action=EXAM_UPDATE,
            actor_id=user.id,
            target_type="exam",
            target_id=exam.id,
            ip=ip,
            user_agent=user_agent,
            detail={"fields": changed},
        )

    db.commit()
    return exam


__all__ = [
    "EXAM_CLOSE",
    "EXAM_CREATE",
    "EXAM_PUBLISH",
    "EXAM_UPDATE",
    "create_exam",
    "get_exam",
    "list_exams",
    "update_exam",
]
