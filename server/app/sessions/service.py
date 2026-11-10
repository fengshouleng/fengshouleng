"""作答会话：开始/恢复、自动保存（`client_seq` 单调）、幂等交卷、到期自动交卷。

契约与决策（v1.0.2）：

- `POST /exams/{exam_id}/sessions`（C-22）：首次 201 新建；已有会话（作答中/已交卷/已到期）
  200 恢复且**不重算** `deadline_at`；`now < start_at` → 409 `EXAM_NOT_STARTED`；
  `now > end_at` 或考试 closed（无既有会话）→ 409 `EXAM_EXPIRED`；不存在/draft → 404。
  `deadline_at = min(now + duration_min, end_at)` 只在新建时计算。
- `GET /sessions/{id}`（C-01/C-13）：归属校验；到期（含考试被手动关闭）在本次读取时**同步**
  自动交卷判分（幂等），返回 200 + `status=expired` + `score`，不返回 409。
- `PUT /sessions/{id}/answers`（ADR-006/AC-15）：只接受 `client_seq > last_client_seq`；旧 seq
  返回 200 + `applied=false` + 当前 `saved_seq`；已交卷/已到期（含读到即到期）→ 409
  `EXAM_EXPIRED`（服务端已自动判分）。
- `POST /sessions/{id}/submit`（ADR-005/AC-13）：**只有 200**。事务内 `in_progress → submitted`
  原子迁移并判分；重复提交/已自动交卷返回既有成绩 + `replayed=true`，不重复计分、不新增成绩行。
- 并发：PostgreSQL 用 `SELECT ... FOR UPDATE` 锁会话行；SQLite 无行锁，用事务内重读 + 唯一约束
  `(exam_id, student_id)` 等价串行化（docs/11 §4、任务卡 BE-004）。
"""

from __future__ import annotations

import random
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import errors
from app.audit import service as audit
from app.auth.service import as_utc
from app.errors import AppError
from app.models.answer import Answer, selected_of
from app.models.exam import Exam, ExamStatus
from app.models.exam_session import ExamSession, SessionStatus
from app.models.paper import Paper
from app.models.question import QuestionType
from app.models.user import User, utcnow
from app.reports import wrong_book as wrong_book_records
from app.scoring import service as scoring
from app.sessions.schemas import (
    AnswerItemOut,
    AnswerSaveRequest,
    MyExamItem,
    MySessionStatus,
    OptionOut,
    SessionQuestionOut,
    SessionState,
)

SESSION_START = "session.start"
SESSION_SAVE = "session.save"
SESSION_SUBMIT = "session.submit"


# --------------------------------------------------------------------------- #
# 时间 / 行锁 / 到期判定
# --------------------------------------------------------------------------- #
def _now(now: datetime | None) -> datetime:
    return as_utc(now) if now is not None else utcnow()


def _lock_session(db: Session, session_id: int) -> ExamSession | None:
    """按会话行加锁并重读。

    PostgreSQL 走 `SELECT ... FOR UPDATE`（并发双提交串行化）；SQLite 没有行锁，事务内的
    重读 + 唯一约束构成等价串行化（测试用 SQLite，生产用 PostgreSQL）。
    """
    stmt = select(ExamSession).where(ExamSession.id == session_id)
    if db.get_bind().dialect.name == "postgresql":
        stmt = stmt.with_for_update()
    return db.scalar(stmt)


def is_timed_out(session: ExamSession, exam: Exam, now: datetime) -> bool:
    """会话是否应结算：到达 `deadline_at`，或考试已被手动关闭（C-13 / C-18）。"""
    if exam.status == ExamStatus.CLOSED.value:
        return True
    return now >= as_utc(session.deadline_at)


def _exam_of(db: Session, session: ExamSession) -> Exam:
    exam = db.get(Exam, session.exam_id)
    if exam is None:
        raise errors.not_found("会话关联的考试不存在")
    return exam


def _owned_session(db: Session, session_id: int, student: User, *, lock: bool) -> ExamSession:
    """归属校验：不存在 404；他人会话 403（IDOR 归 403，AC-18 / docs/12 §2）。"""
    session = _lock_session(db, session_id) if lock else db.get(ExamSession, session_id)
    if session is None:
        raise errors.not_found("会话不存在")
    if session.student_id != student.id:
        raise errors.forbidden("无权访问他人的会话")
    return session


# --------------------------------------------------------------------------- #
# 自动交卷（到期 / 交卷共用同一判分路径）
# --------------------------------------------------------------------------- #
def _finalize(
    db: Session,
    session: ExamSession,
    *,
    exam: Exam,
    status: str,
    now: datetime,
    ip: str | None = None,
    user_agent: str | None = None,
    auto: bool = False,
) -> None:
    """状态迁移 + 判分 + 审计（不 commit，由调用方与业务写入一起提交）。"""
    session.status = status
    session.submitted_at = now
    score = scoring.grade_session(db, session, exam=exam)
    # 错题本 upsert（TASK-BE-005）：仅真正发生 in_progress→submitted/expired 时走到这里，
    # 与判分/审计同一事务提交；重复提交（replayed=true）不经过 _finalize，不会重复计数。
    wrong_book_records.record_session_wrong_answers(db, session, now=now)
    audit.record(
        db,
        action=SESSION_SUBMIT,
        actor_id=session.student_id,
        target_type="exam_session",
        target_id=session.id,
        ip=ip,
        user_agent=user_agent,
        detail={
            "status": status,
            "score": float(score),
            "replayed": False,
            "auto": auto,
        },
    )
    db.flush()


def _expire_if_needed(
    db: Session,
    session: ExamSession,
    exam: Exam,
    now: datetime,
    ip: str | None,
    user_agent: str | None,
) -> bool:
    """`in_progress` 且已到期（或考试已关闭）→ 自动交卷判分；幂等。"""
    if session.status != SessionStatus.IN_PROGRESS.value:
        return False
    if not is_timed_out(session, exam, now):
        return False
    _finalize(
        db,
        session,
        exam=exam,
        status=SessionStatus.EXPIRED.value,
        now=now,
        ip=ip,
        user_agent=user_agent,
        auto=True,
    )
    return True


# --------------------------------------------------------------------------- #
# 视图组装
# --------------------------------------------------------------------------- #
def _ordered_questions(
    items: list[dict[str, Any]],
    *,
    session_id: int,
    shuffle: bool,
) -> list[dict[str, Any]]:
    """题序：不启用乱序时按快照顺序；启用时按会话 id 稳定洗牌（刷新不跳题）。"""
    ordered = list(items)
    if shuffle:
        random.Random(f"exam-session-{session_id}").shuffle(ordered)
    return ordered


def _option_list(item: dict[str, Any], *, session_id: int, shuffle: bool) -> list[OptionOut]:
    options = [
        OptionOut(key=str(option.get("key")), text=str(option.get("text") or ""))
        for option in (item.get("options") or [])
        if isinstance(option, dict)
    ]
    if shuffle:
        random.Random(
            f"exam-session-{session_id}-question-{item.get('question_id')}"
        ).shuffle(options)
    return options


#: 冻结题面缓存（性能优化，2026-11-11）：试卷一旦发布，快照不可变；未启用乱序时
#: 同一场考试所有学生的 100 题视图完全一致，可跨会话复用已构建的 pydantic 模型，
#: 避免每次请求重建 100 个 SessionQuestionOut（实测约 0.9ms/请求）。
#: 键 = (paper_id, shuffle_questions, shuffle_options, exam_id)；乱序开启时不缓存（按会话洗牌）。
_QUESTIONS_CACHE: OrderedDict[
    tuple[int, bool, bool, int], tuple[SessionQuestionOut, ...]
] = OrderedDict()
_QUESTIONS_CACHE_MAX = 32


def _build_questions(
    db: Session,
    exam: Exam,
    *,
    session_id: int,
) -> list[SessionQuestionOut]:
    """按冻结快照构建题目视图（可乱序时按会话稳定洗牌）。"""
    items = scoring.snapshot_questions(db, exam)
    return [
        SessionQuestionOut(
            question_id=int(item["question_id"]),
            type=QuestionType(str(item["type"])),
            stem=str(item.get("stem") or ""),
            options=_option_list(
                item,
                session_id=session_id,
                shuffle=bool(exam.shuffle_options),
            ),
            order_no=int(item.get("order_no") or 0),
            points=float(item.get("points") or 0),
        )
        for item in _ordered_questions(
            items,
            session_id=session_id,
            shuffle=bool(exam.shuffle_questions),
        )
    ]


def _questions_for(db: Session, exam: Exam, *, session_id: int) -> list[SessionQuestionOut]:
    """取题目视图；未启用乱序时走进程内缓存（同一 paper_id 的题面不可变）。"""
    shuffle_q = bool(exam.shuffle_questions)
    shuffle_o = bool(exam.shuffle_options)
    if shuffle_q or shuffle_o:
        return _build_questions(db, exam, session_id=session_id)
    key = (exam.paper_id, False, False, exam.id)
    cached = _QUESTIONS_CACHE.get(key)
    if cached is not None:
        _QUESTIONS_CACHE.move_to_end(key)
        return list(cached)
    built = list(_build_questions(db, exam, session_id=session_id))
    _QUESTIONS_CACHE[key] = tuple(built)
    if len(_QUESTIONS_CACHE) > _QUESTIONS_CACHE_MAX:
        _QUESTIONS_CACHE.popitem(last=False)
    return built


def build_session_state(
    db: Session,
    session: ExamSession,
    *,
    exam: Exam,
    now: datetime,
) -> SessionState:
    """组装 SessionState：题目（不含答案、可乱序）+ 已保存答案 + 校时字段。"""
    questions = _questions_for(db, exam, session_id=session.id)
    rows = db.scalars(
        select(Answer)
        .where(Answer.session_id == session.id)
        .order_by(Answer.question_id)
    ).all()
    return SessionState(
        id=session.id,
        exam_id=session.exam_id,
        status=SessionStatus(session.status),
        started_at=as_utc(session.started_at),
        deadline_at=as_utc(session.deadline_at),
        server_time=now,
        last_client_seq=session.last_client_seq,
        submitted_at=as_utc(session.submitted_at) if session.submitted_at else None,
        score=float(session.score) if session.score is not None else None,
        answers=[
            AnswerItemOut(question_id=row.question_id, selected=selected_of(row))
            for row in rows
        ],
        questions=questions,
    )


# --------------------------------------------------------------------------- #
# 用例
# --------------------------------------------------------------------------- #
def start_or_resume_session(
    db: Session,
    exam_id: int,
    *,
    student: User,
    ip: str | None = None,
    user_agent: str | None = None,
    now: datetime | None = None,
) -> tuple[ExamSession, Exam, bool]:
    """开始或恢复本场考试；返回 `(session, exam, created)`，created=False 表示恢复。"""
    moment = _now(now)
    exam = db.get(Exam, exam_id)
    if exam is None or exam.status == ExamStatus.DRAFT.value:
        # 防枚举：不存在与不可见（draft）统一 404（C-17 / C-22）
        raise errors.not_found("考试不存在")

    existing = db.scalar(
        select(ExamSession).where(
            ExamSession.exam_id == exam.id,
            ExamSession.student_id == student.id,
        )
    )
    if existing is not None:
        # 恢复：不重算 deadline；已到期则先结算为 expired 再返回（C-01/C-15）
        if _expire_if_needed(db, existing, exam, moment, ip, user_agent):
            db.commit()
        return existing, exam, False

    if exam.status == ExamStatus.CLOSED.value:
        raise AppError(errors.EXAM_EXPIRED, "考试已结束", 409)
    if moment < as_utc(exam.start_at):
        raise AppError(errors.EXAM_NOT_STARTED, "考试尚未开始", 409)
    if moment >= as_utc(exam.end_at):
        raise AppError(errors.EXAM_EXPIRED, "考试已截止", 409)

    deadline = min(moment + timedelta(minutes=exam.duration_min), as_utc(exam.end_at))
    session = ExamSession(
        exam_id=exam.id,
        student_id=student.id,
        status=SessionStatus.IN_PROGRESS.value,
        started_at=moment,
        deadline_at=deadline,
        submitted_at=None,
        score=None,
        last_client_seq=0,
    )
    db.add(session)
    try:
        db.flush()
    except IntegrityError:
        # 并发双请求：唯一约束 (exam_id, student_id) 兜底，落败方按恢复处理（200）
        db.rollback()
        existing = db.scalar(
            select(ExamSession).where(
                ExamSession.exam_id == exam_id,
                ExamSession.student_id == student.id,
            )
        )
        if existing is None:
            raise
        return existing, exam, False

    audit.record(
        db,
        action=SESSION_START,
        actor_id=student.id,
        target_type="exam_session",
        target_id=session.id,
        ip=ip,
        user_agent=user_agent,
        detail={
            "exam_id": exam.id,
            "deadline_at": deadline.isoformat(),
            "duration_min": exam.duration_min,
        },
    )
    db.commit()
    return session, exam, True


def get_session_state(
    db: Session,
    session_id: int,
    *,
    student: User,
    ip: str | None = None,
    user_agent: str | None = None,
    now: datetime | None = None,
) -> tuple[ExamSession, Exam]:
    """读取会话（唯一恢复入口）：到期时同步自动交卷判分后返回 expired。"""
    moment = _now(now)
    session = _owned_session(db, session_id, student, lock=False)
    exam = _exam_of(db, session)
    if _expire_if_needed(db, session, exam, moment, ip, user_agent):
        db.commit()
    return session, exam


def save_answers(
    db: Session,
    session_id: int,
    payload: AnswerSaveRequest,
    *,
    student: User,
    ip: str | None = None,
    user_agent: str | None = None,
    now: datetime | None = None,
) -> tuple[int, datetime, bool]:
    """自动保存：返回 `(saved_seq, saved_at, applied)`；旧 seq 不覆盖较新答案。"""
    moment = _now(now)
    session = _owned_session(db, session_id, student, lock=True)
    exam = _exam_of(db, session)

    if session.status == SessionStatus.IN_PROGRESS.value and is_timed_out(
        session, exam, moment
    ):
        # 到期后仍写答案：服务端先自动交卷判分（AC-14），再按 409 引导客户端跳成绩页
        _finalize(
            db,
            session,
            exam=exam,
            status=SessionStatus.EXPIRED.value,
            now=moment,
            ip=ip,
            user_agent=user_agent,
            auto=True,
        )
        db.commit()
        raise AppError(errors.EXAM_EXPIRED, "考试已到期，答案不再接受", 409)

    if session.status != SessionStatus.IN_PROGRESS.value:
        raise AppError(errors.EXAM_EXPIRED, "会话已交卷，答案不再接受", 409)

    if payload.client_seq <= session.last_client_seq:
        # 乱序到达的旧请求：HTTP 200 + applied=false（ADR-006 / C-16 / AC-15）
        db.commit()
        return session.last_client_seq, moment, False

    items = scoring.snapshot_questions(db, exam)
    allowed = {int(item["question_id"]) for item in items}
    unknown = sorted({answer.question_id for answer in payload.answers} - allowed)
    if unknown:
        raise errors.validation_error(f"题目不属于本场考试：{unknown}")

    question_ids = [answer.question_id for answer in payload.answers]
    rows = db.scalars(
        select(Answer).where(
            Answer.session_id == session.id,
            Answer.question_id.in_(question_ids),
        )
    ).all()
    by_question = {row.question_id: row for row in rows}

    for item in payload.answers:
        row = by_question.get(item.question_id)
        if row is None:
            row = Answer(session_id=session.id, question_id=item.question_id, selected=[])
            db.add(row)
            by_question[item.question_id] = row
        row.selected = _normalize_selected(item.selected)
        # 重新作答后旧的判分标记失效（下次交卷/到期时重算）
        row.is_correct = None
        row.earned_points = None
        row.updated_at = moment

    session.last_client_seq = payload.client_seq
    audit.record(
        db,
        action=SESSION_SAVE,
        actor_id=student.id,
        target_type="exam_session",
        target_id=session.id,
        ip=ip,
        user_agent=user_agent,
        detail={
            "client_seq": payload.client_seq,
            "question_count": len(payload.answers),
            "applied": True,
        },
    )
    db.commit()
    return payload.client_seq, moment, True


def _normalize_selected(values: list[str]) -> list[str]:
    """去重并保持顺序；空数组表示「已清空该题作答」。"""
    normalized: list[str] = []
    for value in values:
        key = str(value)
        if key not in normalized:
            normalized.append(key)
    return normalized


def submit_session(
    db: Session,
    session_id: int,
    *,
    student: User,
    ip: str | None = None,
    user_agent: str | None = None,
    now: datetime | None = None,
) -> tuple[ExamSession, Exam, bool]:
    """交卷（幂等）：返回 `(session, exam, replayed)`；replayed=True 表示返回既有成绩。"""
    moment = _now(now)
    session = _owned_session(db, session_id, student, lock=True)
    exam = _exam_of(db, session)

    if session.status != SessionStatus.IN_PROGRESS.value:
        # 重复提交 / 已自动交卷：200 + 既有成绩，不重复计分（ADR-005 / C-15 / AC-13）
        audit.record(
            db,
            action=SESSION_SUBMIT,
            actor_id=student.id,
            target_type="exam_session",
            target_id=session.id,
            ip=ip,
            user_agent=user_agent,
            detail={
                "status": session.status,
                "score": float(session.score if session.score is not None else 0),
                "replayed": True,
                "auto": False,
            },
        )
        db.commit()
        return session, exam, True

    target = (
        SessionStatus.EXPIRED.value
        if is_timed_out(session, exam, moment)
        else SessionStatus.SUBMITTED.value
    )
    _finalize(
        db,
        session,
        exam=exam,
        status=target,
        now=moment,
        ip=ip,
        user_agent=user_agent,
        auto=target == SessionStatus.EXPIRED.value,
    )
    db.commit()
    return session, exam, False


def _papers_for(db: Session, paper_ids: set[int]) -> dict[int, Paper]:
    if not paper_ids:
        return {}
    return {
        paper.id: paper
        for paper in db.scalars(select(Paper).where(Paper.id.in_(paper_ids))).all()
    }


def _my_exam_item(
    exam: Exam,
    session: ExamSession | None,
    paper: Paper | None,
) -> MyExamItem:
    status = (
        MySessionStatus.NOT_STARTED
        if session is None
        else MySessionStatus(session.status)
    )
    return MyExamItem(
        exam_id=exam.id,
        exam_title=exam.title,
        start_at=as_utc(exam.start_at),
        end_at=as_utc(exam.end_at),
        duration_min=exam.duration_min,
        session_status=status,
        session_id=session.id if session is not None else None,
        score=(
            float(session.score)
            if session is not None and session.score is not None
            else None
        ),
        total_points=float(paper.total_points) if paper is not None else None,
        submitted_at=(
            as_utc(session.submitted_at)
            if session is not None and session.submitted_at is not None
            else None
        ),
    )


def list_my_exams(
    db: Session,
    *,
    student: User,
    session_status: MySessionStatus | None = None,
    page: int = 1,
    page_size: int = 20,
    ip: str | None = None,
    user_agent: str | None = None,
    now: datetime | None = None,
) -> tuple[list[MyExamItem], int, datetime]:
    """学生发现入口（C-02）：published 全部 + 该生已有会话的 closed；不含 draft。

    `in_progress` 但已过 `deadline_at` 的条目在**本次读取时**结算为 `expired` 并给成绩
    （CHG-024，与 `GET /sessions/{id}` 同一事务路径）。
    """
    moment = _now(now)
    stmt = (
        select(Exam, ExamSession)
        .outerjoin(
            ExamSession,
            and_(
                ExamSession.exam_id == Exam.id,
                ExamSession.student_id == student.id,
            ),
        )
        .where(
            or_(
                Exam.status == ExamStatus.PUBLISHED.value,
                and_(
                    Exam.status == ExamStatus.CLOSED.value,
                    ExamSession.id.isnot(None),
                ),
            )
        )
        .order_by(Exam.start_at.desc(), Exam.id.desc())
    )
    rows = db.execute(stmt).all()
    papers = _papers_for(db, {exam.paper_id for exam, _ in rows})

    settled = False
    items: list[MyExamItem] = []
    for exam, session in rows:
        if session is not None and _expire_if_needed(
            db, session, exam, moment, ip, user_agent
        ):
            settled = True
        items.append(_my_exam_item(exam, session, papers.get(exam.paper_id)))
    if settled:
        db.commit()

    if session_status is not None:
        items = [item for item in items if item.session_status == session_status]
    total = len(items)
    start = (page - 1) * page_size
    return items[start : start + page_size], total, moment


__all__ = [
    "SESSION_SAVE",
    "SESSION_START",
    "SESSION_SUBMIT",
    "build_session_state",
    "get_session_state",
    "is_timed_out",
    "list_my_exams",
    "save_answers",
    "start_or_resume_session",
    "submit_session",
]
