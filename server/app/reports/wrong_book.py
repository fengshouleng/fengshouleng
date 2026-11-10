"""错题本写入（TASK-BE-005 的写侧，只被 `app.sessions.service._finalize` 调用）。

口径（契约 v1.0.2 / AC-20 / docs/11 §2）：

- 只统计 `answers.is_correct = False` 的题（BE-004：仅「完全正确」为 True，部分给分也是 False）；
  未作答（没有 answers 行）不写错题本
- 去重键 `(student_id, question_id)`：已有记录 `wrong_count + 1` 并刷新 `last_wrong_at`；
  没有则插入 `wrong_count = 1`
- **不 commit**：与状态迁移 + 判分 + 审计同一事务提交（docs/11 §4.5/§4.7）；
  调用点只在真正的 `in_progress → submitted/expired` 迁移里，重复提交（`replayed=true`）
  不会走到这里，因此不会重复计数
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.service import as_utc
from app.models.answer import Answer
from app.models.exam_session import ExamSession
from app.models.user import utcnow
from app.models.wrong_book import WrongBook


def record_session_wrong_answers(
    db: Session,
    session: ExamSession,
    *,
    now: datetime | None = None,
) -> int:
    """把本次判分中 `is_correct=False` 的题 upsert 进错题本；返回涉及的题目数。

    先按 `(student_id, question_id)` 批量读出已有行再逐条更新/新增，语义等价 upsert，
    且不依赖具体方言的 `ON CONFLICT` 写法（PostgreSQL 生产 / SQLite 测试同一份代码）。
    """
    moment = as_utc(now) if now is not None else utcnow()
    wrong_rows = db.scalars(
        select(Answer).where(
            Answer.session_id == session.id,
            Answer.is_correct.is_(False),
        )
    ).all()
    if not wrong_rows:
        return 0

    question_ids = [row.question_id for row in wrong_rows]
    existing = {
        entry.question_id: entry
        for entry in db.scalars(
            select(WrongBook).where(
                WrongBook.student_id == session.student_id,
                WrongBook.question_id.in_(question_ids),
            )
        ).all()
    }

    for row in wrong_rows:
        entry = existing.get(row.question_id)
        if entry is None:
            entry = WrongBook(
                student_id=session.student_id,
                question_id=row.question_id,
                wrong_count=1,
                last_wrong_at=moment,
            )
            db.add(entry)
            existing[row.question_id] = entry
        else:
            entry.wrong_count = int(entry.wrong_count or 0) + 1
            entry.last_wrong_at = moment

    db.flush()
    return len(wrong_rows)


__all__ = ["record_session_wrong_answers"]
