"""试卷路由：/papers CRUD（AC-09 / AC-10）。

- 权限矩阵：student 一律 403；teacher 仅自己的试卷；admin 全部
- 冻结通过服务函数 `freeze_paper` 由 BE-004（考试发布）同事务调用，契约未暴露
  `POST /papers/{id}/freeze` 接口，因此本路由不提供冻结端点
- 写请求的 CSRF 由聚合路由器 + 本路由双重兜底
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.auth.csrf import enforce_csrf
from app.auth.dependencies import require_role
from app.db import get_db
from app.models.paper import Paper, PaperStatus
from app.models.paper_question import PaperQuestion
from app.models.user import Role, User
from app.papers import service
from app.papers.schemas import (
    PaperCreate,
    PaperOut,
    PaperPage,
    PaperQuestionOut,
)

_TEACHER_OR_ADMIN = require_role(Role.TEACHER.value, Role.ADMIN.value)

router = APIRouter(
    prefix="/papers",
    tags=["papers"],
    dependencies=[Depends(enforce_csrf)],
)


def _question_out(row: PaperQuestion) -> PaperQuestionOut:
    return PaperQuestionOut(
        question_id=row.question_id,
        order_no=row.order_no,
        points=float(row.points),
        partial_rule=row.partial_rule,
    )


def _paper_out(paper: Paper, items: list[PaperQuestion]) -> PaperOut:
    return PaperOut(
        id=paper.id,
        owner_id=paper.owner_id,
        title=paper.title,
        total_points=float(paper.total_points),
        status=PaperStatus(paper.status),
        frozen_at=paper.frozen_at,
        questions=[_question_out(item) for item in items],
        created_at=paper.created_at,
    )


@router.get(
    "",
    response_model=PaperPage,
    operation_id="listPapers",
    summary="试卷列表（教师自己的；admin 全部）",
)
def list_papers(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(_TEACHER_OR_ADMIN),
) -> PaperPage:
    papers, total = service.list_papers(
        db,
        user=user,
        page=page,
        page_size=page_size,
    )
    grouped = service.paper_questions_map(db, [paper.id for paper in papers])
    return PaperPage(
        items=[_paper_out(paper, grouped.get(paper.id, [])) for paper in papers],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "",
    response_model=PaperOut,
    status_code=201,
    operation_id="createPaper",
    summary="创建试卷（组卷，服务端计算总分）",
)
def create_paper(
    payload: PaperCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_TEACHER_OR_ADMIN),
) -> PaperOut:
    paper = service.create_paper(
        db,
        payload,
        owner=user,
        ip=audit.request_ip(request),
        user_agent=audit.request_user_agent(request),
    )
    return _paper_out(paper, service.list_paper_questions(db, paper.id))


@router.get(
    "/{paper_id}",
    response_model=PaperOut,
    operation_id="getPaper",
    summary="试卷详情",
)
def get_paper(
    paper_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(_TEACHER_OR_ADMIN),
) -> PaperOut:
    paper = service.get_paper(db, paper_id, user)
    return _paper_out(paper, service.list_paper_questions(db, paper.id))


@router.put(
    "/{paper_id}",
    response_model=PaperOut,
    operation_id="updatePaper",
    summary="修改试卷（已冻结返回 409 PAPER_FROZEN）",
)
def update_paper(
    paper_id: int,
    payload: PaperCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_TEACHER_OR_ADMIN),
) -> PaperOut:
    paper = service.update_paper(
        db,
        paper_id,
        payload,
        user=user,
        ip=audit.request_ip(request),
        user_agent=audit.request_user_agent(request),
    )
    return _paper_out(paper, service.list_paper_questions(db, paper.id))


@router.delete(
    "/{paper_id}",
    status_code=204,
    operation_id="deletePaper",
    summary="删除试卷（已冻结或被考试引用时 409）",
)
def delete_paper(
    paper_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_TEACHER_OR_ADMIN),
) -> None:
    service.delete_paper(
        db,
        paper_id,
        user=user,
        ip=audit.request_ip(request),
        user_agent=audit.request_user_agent(request),
    )


__all__ = ["router"]
