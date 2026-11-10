"""考试管理路由：`/exams`（AC-10 / C-18 / C-22）。

- 权限：仅 teacher / admin（学生一律 403）；教师只能操作自己的考试，admin 全部
- `POST /exams` 的 `publish=true` 与试卷冻结在同一事务内提交（见 `service.create_exam`）
- 写请求的 CSRF 由聚合路由器 + 本路由双重兜底
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.auth.csrf import enforce_csrf
from app.auth.dependencies import require_role
from app.auth.service import as_utc
from app.db import get_db
from app.exams import service
from app.exams.schemas import ExamCreate, ExamOut, ExamPage, ExamPatch
from app.models.exam import Exam, ExamStatus
from app.models.user import Role, User

_TEACHER_OR_ADMIN = require_role(Role.TEACHER.value, Role.ADMIN.value)

router = APIRouter(
    prefix="/exams",
    tags=["exams"],
    dependencies=[Depends(enforce_csrf)],
)


def exam_out(exam: Exam) -> ExamOut:
    """ORM → 契约 `Exam`（时间统一 UTC 输出，CHG-027 条件字段按状态给值）。"""
    return ExamOut(
        id=exam.id,
        paper_id=exam.paper_id,
        title=exam.title,
        start_at=as_utc(exam.start_at),
        end_at=as_utc(exam.end_at),
        duration_min=exam.duration_min,
        shuffle_questions=bool(exam.shuffle_questions),
        shuffle_options=bool(exam.shuffle_options),
        status=ExamStatus(exam.status),
        created_by=exam.created_by,
        snapshot_version=exam.snapshot_version,
        updated_at=as_utc(exam.updated_at),
        created_at=as_utc(exam.created_at),
    )


@router.get(
    "",
    response_model=ExamPage,
    operation_id="listExams",
    summary="考试列表（教师自己的；admin 全部）",
)
def list_exams(
    status: ExamStatus | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(_TEACHER_OR_ADMIN),
) -> ExamPage:
    exams, total = service.list_exams(
        db,
        user=user,
        status=status.value if status is not None else None,
        page=page,
        page_size=page_size,
    )
    return ExamPage(
        items=[exam_out(exam) for exam in exams],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "",
    response_model=ExamOut,
    status_code=201,
    operation_id="createExam",
    summary="创建并（可选）发布考试（publish=true 同事务冻结试卷）",
)
def create_exam(
    payload: ExamCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_TEACHER_OR_ADMIN),
) -> ExamOut:
    exam = service.create_exam(
        db,
        payload,
        creator=user,
        ip=audit.request_ip(request),
        user_agent=audit.request_user_agent(request),
    )
    return exam_out(exam)


@router.get(
    "/{exam_id}",
    response_model=ExamOut,
    operation_id="getExam",
    summary="考试详情",
)
def get_exam(
    exam_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(_TEACHER_OR_ADMIN),
) -> ExamOut:
    return exam_out(service.get_exam(db, exam_id, user))


@router.patch(
    "/{exam_id}",
    response_model=ExamOut,
    operation_id="updateExam",
    summary="修改考试配置 / 状态（draft→published→closed，非法迁移 409）",
)
def update_exam(
    exam_id: int,
    payload: ExamPatch,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_TEACHER_OR_ADMIN),
) -> ExamOut:
    exam = service.update_exam(
        db,
        exam_id,
        payload,
        user=user,
        ip=audit.request_ip(request),
        user_agent=audit.request_user_agent(request),
    )
    return exam_out(exam)


__all__ = ["exam_out", "router"]
