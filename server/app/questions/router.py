"""题库路由：/questions CRUD 与 /questions/import（AC-05..AC-08）。

权限矩阵：student 一律 403；teacher 仅自己的资源；admin 全部。
所有写请求由聚合路由器/本路由的 `enforce_csrf` 双重兜底。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from sqlalchemy.orm import Session

from app import errors
from app.audit import service as audit
from app.auth.csrf import enforce_csrf
from app.auth.dependencies import require_role
from app.db import get_db
from app.errors import AppError
from app.models.question import QuestionType
from app.models.user import Role, User
from app.questions import service
from app.questions.importer import MAX_IMPORT_BYTES
from app.questions.schemas import (
    ImportResult,
    QuestionCreate,
    QuestionOut,
    QuestionPage,
)

_TEACHER_OR_ADMIN = require_role(Role.TEACHER.value, Role.ADMIN.value)

router = APIRouter(
    prefix="/questions",
    tags=["questions"],
    dependencies=[Depends(enforce_csrf)],
)


@router.get(
    "",
    response_model=QuestionPage,
    operation_id="listQuestions",
    summary="题库列表（教师自己的；admin 全部）",
)
def list_questions(
    keyword: str | None = Query(default=None, max_length=100),
    question_type: QuestionType | None = Query(default=None, alias="type"),
    tag: str | None = Query(default=None, max_length=32),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(_TEACHER_OR_ADMIN),
) -> QuestionPage:
    items, total = service.list_questions(
        db,
        user=user,
        keyword=keyword,
        question_type=question_type,
        tag=tag,
        page=page,
        page_size=page_size,
    )
    return QuestionPage(
        items=[QuestionOut.model_validate(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "",
    response_model=QuestionOut,
    status_code=201,
    operation_id="createQuestion",
    summary="新建题目",
)
def create_question(
    payload: QuestionCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_TEACHER_OR_ADMIN),
) -> QuestionOut:
    question = service.create_question(
        db,
        payload,
        owner=user,
        ip=audit.request_ip(request),
        user_agent=audit.request_user_agent(request),
    )
    return QuestionOut.model_validate(question)


@router.post(
    "/import",
    response_model=ImportResult,
    operation_id="importQuestions",
    summary="批量导入（CSV/XLSX，部分成功返回 200）",
)
def import_questions(
    request: Request,
    file: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(_TEACHER_OR_ADMIN),
) -> ImportResult:
    if file is None:
        # 契约里 /questions/import 的缺字段错误是 400（BadRequest），不是 422
        raise AppError(errors.VALIDATION_ERROR, "缺少上传文件字段 file", 400)
    data = file.file.read(MAX_IMPORT_BYTES + 1)
    if len(data) > MAX_IMPORT_BYTES:
        raise AppError(errors.VALIDATION_ERROR, "导入文件超过 10MB 限制", 400)
    return service.import_questions(
        db,
        owner=user,
        data=data,
        filename=file.filename or "",
        ip=audit.request_ip(request),
        user_agent=audit.request_user_agent(request),
    )


@router.get(
    "/{question_id}",
    response_model=QuestionOut,
    operation_id="getQuestion",
    summary="题目详情",
)
def get_question(
    question_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(_TEACHER_OR_ADMIN),
) -> QuestionOut:
    question = service.get_question(db, question_id, user)
    return QuestionOut.model_validate(question)


@router.put(
    "/{question_id}",
    response_model=QuestionOut,
    operation_id="updateQuestion",
    summary="修改题目",
)
def update_question(
    question_id: int,
    payload: QuestionCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_TEACHER_OR_ADMIN),
) -> QuestionOut:
    question = service.update_question(
        db,
        question_id,
        payload,
        user=user,
        ip=audit.request_ip(request),
        user_agent=audit.request_user_agent(request),
    )
    return QuestionOut.model_validate(question)


@router.delete(
    "/{question_id}",
    status_code=204,
    operation_id="deleteQuestion",
    summary="删除题目（软删除；被引用时 409）",
)
def delete_question(
    question_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_TEACHER_OR_ADMIN),
) -> None:
    service.delete_question(
        db,
        question_id,
        user=user,
        ip=audit.request_ip(request),
        user_agent=audit.request_user_agent(request),
    )


__all__ = ["router"]
