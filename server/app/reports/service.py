"""成绩明细 / 我的成绩 / 错题本 / 考试汇总（读接口，TASK-BE-005）。

契约口径（contracts/openapi.yaml v1.0.2）：

- `GET /sessions/{id}/result`（C-21 / AC-18 / AC-19）
  - 角色判定先于归属（docs/12 §2）：`teacher` **一律 403**，即使会话属于自己创建的考试；
    `student` 仅本人；`admin` 全部
  - 不存在 → 404；学生非本人 → 403（IDOR 归 403，不改成 404）
  - 未交卷（`in_progress`）→ 409 `NOT_SUBMITTED`；`submitted` / `expired` 可读
    （到期会话由 `GET /sessions/{id}` / `GET /me/exams` 在读取时同步结算为 `expired`，
    与 BE-004 同一事务路径；本接口只读，不触发结算，避免 GET 产生业务写入）
  - 逐题结果只依据**冻结快照**（`papers.frozen_snapshot`，ADR-007）+ `answers` 判分列合成，
    与当前题库内容无关；`explanation` 快照未包含，按 `question_id` 回读题库解析（软删除仍可读），
    缺失时给空串（字段在契约中可选）
- `GET /me/results`（C-06）：仅当前学生，`status ∈ {submitted, expired}`，按交卷时间倒序
- `GET /me/wrong-questions`（AC-20）：`wrong_count` 降序、`last_wrong_at` 降序；同题只出现一次
- `GET /exams/{id}/results`（C-20 / CHG-031）：仅创建者或 admin；`total_sessions` 含进行中，
  `submitted` = `status ∈ {submitted, expired}`，`average_score` 只对已判分会话求均值，
  无已判分返回 0，四舍五入两位小数

写侧（交卷时 upsert 错题本）在 `app.reports.wrong_book`，由 `app.sessions.service` 调用。
"""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import errors
from app.auth.service import as_utc
from app.errors import AppError
from app.models.answer import Answer, selected_of
from app.models.exam import Exam
from app.models.exam_session import ExamSession, SessionStatus
from app.models.paper import Paper
from app.models.question import Question, QuestionType
from app.models.user import Role, User
from app.models.wrong_book import WrongBook
from app.reports.schemas import (
    ExamResultItemOut,
    ExamResultSummary,
    MyResultItem,
    MyResultPage,
    ResultDetail,
    ResultItemOut,
    WrongQuestionOut,
    WrongQuestionPage,
)
from app.scoring import service as scoring

#: 契约 `Error.code`：未交卷读取成绩（409；AC-19）。`app.errors` 尚未定义该码，
#: 按契约 v1.0.2 在 reports 侧本地声明，避免改动公共错误模块。
NOT_SUBMITTED: Final[str] = "NOT_SUBMITTED"

#: 已判分/可读的终态（submitted 与 expired 语义等价，C-20）
_GRADED_STATUSES: tuple[str, ...] = (
    SessionStatus.SUBMITTED.value,
    SessionStatus.EXPIRED.value,
)

_POINTS_QUANTUM = Decimal("0.01")
_ZERO = Decimal("0.00")


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def _as_points(value: object) -> Decimal:
    """任意数值 → 两位小数 Decimal（与判分模块同一量化口径）。"""
    return scoring.as_points(value)


def _submitted_at(session: ExamSession) -> datetime:
    """交卷时间；终态必有值，历史脏数据兜底用 `updated_at`。"""
    moment = session.submitted_at if session.submitted_at is not None else session.updated_at
    return as_utc(moment)


def _explanations(db: Session, question_ids: list[int]) -> dict[int, str]:
    """回读题库解析（快照未含 explanation）；软删除题目也可读，缺失给空串。"""
    if not question_ids:
        return {}
    rows = db.execute(
        select(Question.id, Question.explanation).where(Question.id.in_(question_ids))
    ).all()
    return {int(question_id): (text or "") for question_id, text in rows}


def _page_offset(page: int, page_size: int) -> int:
    return (page - 1) * page_size


# --------------------------------------------------------------------------- #
# GET /sessions/{session_id}/result
# --------------------------------------------------------------------------- #
def get_session_result(db: Session, session_id: int, *, user: User) -> ResultDetail:
    """逐题成绩明细：本人 / admin 可读；teacher 一律 403；未交卷 409。"""
    # 第 3 步：角色判定先于归属与存在性（docs/12 §2 / CHG-030）
    if user.role == Role.TEACHER.value:
        raise errors.forbidden("教师不能读取单会话逐题结果，请使用考试汇总接口")

    session = db.get(ExamSession, session_id)
    if session is None:
        raise errors.not_found("会话不存在")
    # 第 4 步：学生仅本人（AC-18，IDOR 归 403）
    if user.role != Role.ADMIN.value and session.student_id != user.id:
        raise errors.forbidden("无权访问他人的成绩")

    if session.status not in _GRADED_STATUSES:
        raise AppError(NOT_SUBMITTED, "尚未交卷，成绩不可读", 409)

    exam = db.get(Exam, session.exam_id)
    if exam is None:
        raise AppError(errors.INTERNAL_ERROR, "会话关联的考试不存在", 500)
    paper = db.get(Paper, exam.paper_id)
    if paper is None:
        raise AppError(errors.INTERNAL_ERROR, "考试关联的试卷不存在", 500)

    snapshot = sorted(
        scoring.snapshot_questions(db, exam),
        key=lambda item: int(item.get("order_no") or 0),
    )
    answers = db.scalars(
        select(Answer).where(Answer.session_id == session.id)
    ).all()
    by_question = {row.question_id: row for row in answers}
    explanations = _explanations(
        db,
        [int(item["question_id"]) for item in snapshot],
    )

    items: list[ResultItemOut] = []
    for item in snapshot:
        question_id = int(item["question_id"])
        row = by_question.get(question_id)
        selected = [str(value) for value in selected_of(row)] if row is not None else []
        # 未作答（没有 answers 行）按错误处理，但只有真正答错的题才进错题本
        is_correct = row is not None and row.is_correct is True
        earned = (
            _as_points(row.earned_points)
            if row is not None and row.earned_points is not None
            else _ZERO
        )
        items.append(
            ResultItemOut(
                question_id=question_id,
                type=QuestionType(str(item.get("type") or QuestionType.SINGLE.value)),
                stem=str(item.get("stem") or ""),
                selected=selected,
                correct_answer=[str(value) for value in (item.get("answer") or [])],
                is_correct=is_correct,
                earned_points=float(earned.quantize(_POINTS_QUANTUM)),
                points=float(_as_points(item.get("points") or 0)),
                explanation=explanations.get(question_id, ""),
            )
        )

    total_points = _as_points(
        paper.total_points if paper.total_points is not None else _ZERO
    )
    score = _as_points(session.score if session.score is not None else _ZERO)
    return ResultDetail(
        session_id=session.id,
        exam_id=exam.id,
        score=float(score),
        total_points=float(total_points),
        submitted_at=_submitted_at(session),
        items=items,
    )


# --------------------------------------------------------------------------- #
# GET /me/results
# --------------------------------------------------------------------------- #
def list_my_results(
    db: Session,
    *,
    student: User,
    page: int = 1,
    page_size: int = 20,
) -> MyResultPage:
    """当前学生已判分成绩：按交卷时间倒序分页（C-06）。"""
    conditions = (
        ExamSession.student_id == student.id,
        ExamSession.status.in_(_GRADED_STATUSES),
    )
    total = int(
        db.scalar(
            select(func.count())
            .select_from(ExamSession)
            .where(*conditions)
        )
        or 0
    )
    rows = db.execute(
        select(ExamSession, Exam, Paper)
        .join(Exam, Exam.id == ExamSession.exam_id)
        .join(Paper, Paper.id == Exam.paper_id)
        .where(*conditions)
        .order_by(ExamSession.submitted_at.desc(), ExamSession.id.desc())
        .offset(_page_offset(page, page_size))
        .limit(page_size)
    ).all()

    items = [
        MyResultItem(
            session_id=session.id,
            exam_id=exam.id,
            exam_title=exam.title,
            score=float(_as_points(session.score if session.score is not None else _ZERO)),
            total_points=float(
                _as_points(paper.total_points if paper.total_points is not None else _ZERO)
            ),
            submitted_at=_submitted_at(session),
        )
        for session, exam, paper in rows
    ]
    return MyResultPage(items=items, total=total, page=page, page_size=page_size)


# --------------------------------------------------------------------------- #
# GET /me/wrong-questions
# --------------------------------------------------------------------------- #
def list_my_wrong_questions(
    db: Session,
    *,
    student: User,
    page: int = 1,
    page_size: int = 20,
) -> WrongQuestionPage:
    """当前学生错题本：wrong_count 降序、last_wrong_at 降序（AC-20）。"""
    condition = WrongBook.student_id == student.id
    total = int(
        db.scalar(
            select(func.count())
            .select_from(WrongBook)
            .where(condition)
        )
        or 0
    )
    rows = db.execute(
        select(WrongBook, Question)
        .join(Question, Question.id == WrongBook.question_id)
        .where(condition)
        .order_by(
            WrongBook.wrong_count.desc(),
            WrongBook.last_wrong_at.desc(),
            WrongBook.question_id.asc(),
        )
        .offset(_page_offset(page, page_size))
        .limit(page_size)
    ).all()

    items = [
        WrongQuestionOut(
            question_id=entry.question_id,
            type=QuestionType(question.type),
            stem=question.stem,
            wrong_count=int(entry.wrong_count or 0),
            last_wrong_at=as_utc(entry.last_wrong_at),
        )
        for entry, question in rows
    ]
    return WrongQuestionPage(items=items, total=total, page=page, page_size=page_size)


# --------------------------------------------------------------------------- #
# GET /exams/{exam_id}/results
# --------------------------------------------------------------------------- #
def _average_score(scores: list[object]) -> float:
    """平均分：两位小数、四舍五入；无已判分会话返回 0（CHG-031，不返回 null）。"""
    if not scores:
        return 0.0
    total = sum((_as_points(value) for value in scores), _ZERO)
    mean = total / Decimal(len(scores))
    return float(mean.quantize(_POINTS_QUANTUM, rounding=ROUND_HALF_UP))


def get_exam_results(db: Session, exam_id: int, *, user: User) -> ExamResultSummary:
    """考试汇总 + 学生明细：仅创建者或 admin（教师自己的考试）。"""
    exam = db.get(Exam, exam_id)
    if exam is None:
        raise errors.not_found("考试不存在")
    if user.role != Role.ADMIN.value and exam.created_by != user.id:
        raise errors.forbidden("无权查看他人考试的汇总")

    rows = db.execute(
        select(ExamSession, User)
        .join(User, User.id == ExamSession.student_id)
        .where(ExamSession.exam_id == exam.id)
        .order_by(ExamSession.student_id.asc())
    ).all()

    graded_scores: list[object] = []
    submitted_count = 0
    items: list[ExamResultItemOut] = []
    for session, student in rows:
        graded = session.status in _GRADED_STATUSES
        if graded:
            submitted_count += 1
            if session.score is not None:
                graded_scores.append(session.score)
        items.append(
            ExamResultItemOut(
                student_id=session.student_id,
                display_name=student.display_name,
                status=SessionStatus(session.status),
                score=(
                    float(_as_points(session.score))
                    if graded and session.score is not None
                    else None
                ),
                submitted_at=(
                    _submitted_at(session)
                    if graded and session.submitted_at is not None
                    else None
                ),
            )
        )

    return ExamResultSummary(
        exam_id=exam.id,
        total_sessions=len(rows),
        submitted=submitted_count,
        average_score=_average_score(graded_scores),
        items=items,
    )


__all__ = [
    "NOT_SUBMITTED",
    "get_exam_results",
    "get_session_result",
    "list_my_results",
    "list_my_wrong_questions",
]
