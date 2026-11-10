"""成绩 / 错题本 / 考试汇总路由（TASK-BE-005）。

四个只读接口（contracts/openapi.yaml v1.0.2）：

- `GET /sessions/{session_id}/result`：`student`（本人）/ `admin`；`teacher` 一律 403；
  未交卷 409 `NOT_SUBMITTED`（AC-18 / AC-19）
- `GET /me/results`、`GET /me/wrong-questions`：仅 `student`（teacher/admin 403）
- `GET /exams/{exam_id}/results`：`teacher`（仅自己创建的考试）/ `admin`

权限依赖复用 `app.auth.dependencies.require_role` / `get_current_user`；
会话逐题结果的 teacher 403 由 service 做第 3 步角色判定（先角色后归属，docs/12 §2）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth.csrf import enforce_csrf
from app.auth.dependencies import get_current_user, require_role
from app.db import get_db
from app.models.user import Role, User
from app.reports import service
from app.reports.schemas import (
    ExamResultSummary,
    MyResultPage,
    ResultDetail,
    WrongQuestionPage,
)

_STUDENT = require_role(Role.STUDENT.value)
_TEACHER_OR_ADMIN = require_role(Role.TEACHER.value, Role.ADMIN.value)

#: 逐题结果挂 /sessions（与 sessions 模块的 /sessions/{id} 同级，不冲突）
sessions_result_router = APIRouter(
    prefix="/sessions",
    tags=["sessions"],
    dependencies=[Depends(enforce_csrf)],
)

#: 我的成绩 / 错题本挂 /me（与 sessions 模块的 /me/exams 同一前缀）
me_reports_router = APIRouter(
    prefix="/me",
    tags=["me"],
    dependencies=[Depends(enforce_csrf)],
)

#: 考试汇总挂 /exams
exams_results_router = APIRouter(
    prefix="/exams",
    tags=["exams"],
    dependencies=[Depends(enforce_csrf)],
)


@sessions_result_router.get(
    "/{session_id}/result",
    response_model=ResultDetail,
    operation_id="getSessionResult",
    summary="成绩明细（本人/admin；teacher 403；未交卷 409 NOT_SUBMITTED）",
)
def get_session_result(
    session_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ResultDetail:
    return service.get_session_result(db, session_id, user=user)


@me_reports_router.get(
    "/results",
    response_model=MyResultPage,
    operation_id="listMyResults",
    summary="我的成绩列表（分页，按交卷时间倒序）",
)
def list_my_results(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    student: User = Depends(_STUDENT),
) -> MyResultPage:
    return service.list_my_results(
        db,
        student=student,
        page=page,
        page_size=page_size,
    )


@me_reports_router.get(
    "/wrong-questions",
    response_model=WrongQuestionPage,
    operation_id="listMyWrongQuestions",
    summary="我的错题本（wrong_count 降序、last_wrong_at 降序）",
)
def list_my_wrong_questions(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    student: User = Depends(_STUDENT),
) -> WrongQuestionPage:
    return service.list_my_wrong_questions(
        db,
        student=student,
        page=page,
        page_size=page_size,
    )


@exams_results_router.get(
    "/{exam_id}/results",
    response_model=ExamResultSummary,
    response_model_exclude_none=True,
    operation_id="getExamResults",
    summary="考试汇总（创建者/admin：total_sessions/submitted/average_score + 学生明细）",
)
def get_exam_results(
    exam_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(_TEACHER_OR_ADMIN),
) -> ExamResultSummary:
    return service.get_exam_results(db, exam_id, user=user)


__all__ = [
    "exams_results_router",
    "me_reports_router",
    "sessions_result_router",
]
