"""作答会话路由：开始/恢复、会话状态、自动保存、交卷、我的考试列表。

- `POST /exams/{exam_id}/sessions`（201 新建 / 200 恢复）、`GET /sessions/{id}`、
  `PUT /sessions/{id}/answers`、`POST /sessions/{id}/submit`：**仅 student**
  （teacher/admin 一律 403，docs/12 §1）
- `GET /me/exams`：仅 student；`server_time` 必填用于校时（CHG-024）
- 归属：他人会话 → 403（AC-18），不存在 → 404（C-17）
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.auth.csrf import enforce_csrf
from app.auth.dependencies import require_role
from app.auth.service import as_utc
from app.db import get_db
from app.models.exam_session import ExamSession, SessionStatus
from app.models.user import Role, User, utcnow
from app.sessions import service
from app.sessions.schemas import (
    AnswerSaveRequest,
    AnswerSaveResponse,
    MyExamPage,
    MySessionStatus,
    SessionState,
    SubmitResult,
)

_STUDENT = require_role(Role.STUDENT.value)

#: 会话的读写挂在 /sessions 下
router = APIRouter(
    prefix="/sessions",
    tags=["sessions"],
    dependencies=[Depends(enforce_csrf)],
)

#: 开始/恢复会话挂在 /exams/{exam_id}/sessions（C-22）
exam_sessions_router = APIRouter(
    prefix="/exams",
    tags=["sessions"],
    dependencies=[Depends(enforce_csrf)],
)

#: 学生发现入口 /me/exams（C-02）
me_router = APIRouter(
    prefix="/me",
    tags=["me"],
    dependencies=[Depends(enforce_csrf)],
)


def _submit_result(session: ExamSession, *, replayed: bool, now: datetime) -> SubmitResult:
    submitted_at = as_utc(session.submitted_at) if session.submitted_at is not None else now
    return SubmitResult(
        session_id=session.id,
        status=SessionStatus(session.status),
        score=float(session.score if session.score is not None else 0),
        submitted_at=submitted_at,
        replayed=replayed,
    )


@exam_sessions_router.post(
    "/{exam_id}/sessions",
    response_model=SessionState,
    status_code=200,
    operation_id="startOrResumeSession",
    responses={201: {"description": "新建会话（首次进入，status=in_progress）"}},
    summary="开始或恢复本场考试（幂等：首次 201 / 恢复 200）",
)
def start_or_resume_session(
    exam_id: int,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    student: User = Depends(_STUDENT),
) -> SessionState:
    moment = utcnow()
    session, exam, created = service.start_or_resume_session(
        db,
        exam_id,
        student=student,
        ip=audit.request_ip(request),
        user_agent=audit.request_user_agent(request),
        now=moment,
    )
    response.status_code = 201 if created else 200
    return service.build_session_state(db, session, exam=exam, now=moment)


@router.get(
    "/{session_id}",
    response_model=SessionState,
    operation_id="getSession",
    summary="会话状态与题目（到期自动交卷，返回 200 + expired）",
)
def get_session(
    session_id: int,
    request: Request,
    db: Session = Depends(get_db),
    student: User = Depends(_STUDENT),
) -> SessionState:
    moment = utcnow()
    session, exam = service.get_session_state(
        db,
        session_id,
        student=student,
        ip=audit.request_ip(request),
        user_agent=audit.request_user_agent(request),
        now=moment,
    )
    return service.build_session_state(db, session, exam=exam, now=moment)


@router.put(
    "/{session_id}/answers",
    response_model=AnswerSaveResponse,
    operation_id="saveAnswers",
    summary="自动保存答案（client_seq 单调，旧 seq 返回 applied=false）",
)
def save_answers(
    session_id: int,
    payload: AnswerSaveRequest,
    request: Request,
    db: Session = Depends(get_db),
    student: User = Depends(_STUDENT),
) -> AnswerSaveResponse:
    saved_seq, saved_at, applied = service.save_answers(
        db,
        session_id,
        payload,
        student=student,
        ip=audit.request_ip(request),
        user_agent=audit.request_user_agent(request),
        now=utcnow(),
    )
    return AnswerSaveResponse(
        saved_seq=saved_seq,
        saved_at=saved_at,
        applied=applied,
    )


@router.post(
    "/{session_id}/submit",
    response_model=SubmitResult,
    operation_id="submitSession",
    summary="交卷并判分（幂等：重复提交 200 + 既有成绩 + replayed=true）",
)
def submit_session(
    session_id: int,
    request: Request,
    db: Session = Depends(get_db),
    student: User = Depends(_STUDENT),
) -> SubmitResult:
    moment = utcnow()
    session, _exam, replayed = service.submit_session(
        db,
        session_id,
        student=student,
        ip=audit.request_ip(request),
        user_agent=audit.request_user_agent(request),
        now=moment,
    )
    return _submit_result(session, replayed=replayed, now=moment)


@me_router.get(
    "/exams",
    response_model=MyExamPage,
    operation_id="listMyExams",
    summary="我的考试列表（含会话状态与成绩，server_time 校时）",
)
def list_my_exams(
    request: Request,
    session_status: MySessionStatus | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    student: User = Depends(_STUDENT),
) -> MyExamPage:
    moment = utcnow()
    items, total, server_time = service.list_my_exams(
        db,
        student=student,
        session_status=session_status,
        page=page,
        page_size=page_size,
        ip=audit.request_ip(request),
        user_agent=audit.request_user_agent(request),
        now=moment,
    )
    return MyExamPage(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        server_time=server_time,
    )


__all__ = ["exam_sessions_router", "me_router", "router"]
