"""试卷快照冻结（AC-10 / ADR-007 / CHG-027）。

覆盖：冻结后快照结构（version/paper_id/total_points/frozen_at/questions）、
选项/答案/分值的深拷贝与总分一致、重复冻结幂等（不刷新 frozen_at、不重复审计）、
冻结后 PUT/DELETE → 409 PAPER_FROZEN、冻结后题库再编辑不影响快照。
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.paper import Paper, PaperStatus
from app.models.question import Question
from app.models.user import User
from app.papers import service as papers_service
from app.papers.service import SNAPSHOT_VERSION

API = "/api"
FROZEN_AT = datetime(2026, 11, 9, 2, 3, 4, tzinfo=UTC)
LATER = datetime(2026, 11, 10, 5, 6, 7, tzinfo=UTC)


def entry(question: Question, order_no: int, points: float, **extra: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "question_id": question.id,
        "order_no": order_no,
        "points": points,
    }
    data.update(extra)
    return data


def _freeze_audit_count(db_session: Session) -> int:
    return int(
        db_session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == "paper.freeze")
        )
        or 0
    )


def test_freeze_snapshot_structure_and_idempotency(
    client: TestClient,
    db_session: Session,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
    make_question: Callable[..., Question],
) -> None:
    teacher = make_user("teacher-paper-freeze")
    login(teacher.username)
    single = make_question(
        teacher,
        stem="单选：水的化学式？",
        question_type="single",
        options=[{"key": "A", "text": "H2O"}, {"key": "B", "text": "CO2"}],
        answer=["A"],
    )
    multiple = make_question(
        teacher,
        stem="多选：哪些是偶数？",
        question_type="multiple",
        options=[
            {"key": "A", "text": "2"},
            {"key": "B", "text": "3"},
            {"key": "C", "text": "4"},
        ],
        answer=["A", "C"],
    )
    judge = make_question(teacher, stem="判断：地球是圆的", question_type="judge")

    created = client.post(
        f"{API}/papers",
        json={
            "title": "冻结测试卷",
            "questions": [
                entry(single, 1, 10),
                entry(multiple, 2, 20.5, partial_rule="partial"),
                entry(judge, 3, 5.25),
            ],
        },
        headers=csrf_headers(),
    )
    assert created.status_code == 201, created.text
    paper_id = created.json()["id"]

    paper = db_session.get(Paper, paper_id)
    assert paper is not None
    frozen = papers_service.freeze_paper(db_session, paper, now=FROZEN_AT)
    db_session.commit()
    assert frozen is paper

    # 从库里重新读，验证 snapshot 已持久化
    db_session.expire_all()
    stored = db_session.get(Paper, paper_id)
    assert stored is not None
    assert stored.status == PaperStatus.FROZEN.value
    assert stored.frozen_at is not None
    assert stored.frozen_at.replace(tzinfo=UTC) == FROZEN_AT
    snapshot = stored.frozen_snapshot
    assert snapshot is not None

    assert set(snapshot) == {
        "version",
        "paper_id",
        "total_points",
        "frozen_at",
        "questions",
    }
    assert snapshot["version"] == SNAPSHOT_VERSION == 1
    assert snapshot["paper_id"] == paper_id
    assert snapshot["total_points"] == 35.75
    assert snapshot["frozen_at"] == FROZEN_AT.isoformat()

    snapshot_questions = snapshot["questions"]
    assert len(snapshot_questions) == 3
    assert [item["question_id"] for item in snapshot_questions] == [
        single.id,
        multiple.id,
        judge.id,
    ]
    assert [item["order_no"] for item in snapshot_questions] == [1, 2, 3]
    assert [item["type"] for item in snapshot_questions] == [
        "single",
        "multiple",
        "judge",
    ]
    assert [item["points"] for item in snapshot_questions] == [10.0, 20.5, 5.25]
    assert [item["partial_rule"] for item in snapshot_questions] == [
        "all_or_nothing",
        "partial",
        "all_or_nothing",
    ]
    for item in snapshot_questions:
        assert set(item) == {
            "question_id",
            "order_no",
            "type",
            "stem",
            "options",
            "answer",
            "points",
            "partial_rule",
        }

    # 深拷贝内容与题库一致
    assert snapshot_questions[0]["stem"] == single.stem
    assert snapshot_questions[0]["options"] == [
        {"key": "A", "text": "H2O"},
        {"key": "B", "text": "CO2"},
    ]
    assert snapshot_questions[0]["answer"] == ["A"]
    assert snapshot_questions[1]["options"] == [
        {"key": "A", "text": "2"},
        {"key": "B", "text": "3"},
        {"key": "C", "text": "4"},
    ]
    assert snapshot_questions[1]["answer"] == ["A", "C"]
    # 快照总分 = Σ 每题分值 = 试卷 total
    assert sum(item["points"] for item in snapshot_questions) == snapshot["total_points"]
    assert float(stored.total_points) == snapshot["total_points"]

    # 审计：冻结动作写了一条，actor 为试卷 owner
    assert _freeze_audit_count(db_session) == 1
    freeze_audit = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "paper.freeze")
    )
    assert freeze_audit is not None
    assert freeze_audit.actor_id == teacher.id
    assert freeze_audit.target_type == "paper"
    assert freeze_audit.target_id == str(paper_id)

    # 幂等：重复冻结不刷新 frozen_at / snapshot，也不重复审计
    snapshot_before = copy.deepcopy(snapshot)
    returned = papers_service.freeze_paper(db_session, stored, now=LATER)
    db_session.commit()
    assert returned is stored

    db_session.expire_all()
    again = db_session.get(Paper, paper_id)
    assert again is not None
    assert again.frozen_snapshot == snapshot_before
    assert again.frozen_at is not None
    assert again.frozen_at.replace(tzinfo=UTC) == FROZEN_AT
    assert _freeze_audit_count(db_session) == 1

    detail = client.get(f"{API}/papers/{paper_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["status"] == "frozen"
    assert body["frozen_at"] is not None


def test_frozen_paper_rejects_update_and_delete(
    client: TestClient,
    db_session: Session,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
    make_question: Callable[..., Question],
) -> None:
    teacher = make_user("teacher-paper-frozen-lock")
    login(teacher.username)
    question = make_question(teacher, stem="冻结锁定题")
    created = client.post(
        f"{API}/papers",
        json={"title": "冻结锁定卷", "questions": [entry(question, 1, 10)]},
        headers=csrf_headers(),
    )
    paper_id = created.json()["id"]

    paper = db_session.get(Paper, paper_id)
    assert paper is not None
    papers_service.freeze_paper(db_session, paper, now=FROZEN_AT)
    db_session.commit()

    put_response = client.put(
        f"{API}/papers/{paper_id}",
        json={"title": "试图改题", "questions": [entry(question, 1, 99)]},
        headers=csrf_headers(),
    )
    assert put_response.status_code == 409, put_response.text
    assert put_response.json()["code"] == "PAPER_FROZEN"

    delete_response = client.delete(
        f"{API}/papers/{paper_id}", headers=csrf_headers()
    )
    assert delete_response.status_code == 409, delete_response.text
    assert delete_response.json()["code"] == "PAPER_FROZEN"

    assert client.get(f"{API}/papers/{paper_id}").status_code == 200
    db_session.expire_all()
    stored = db_session.get(Paper, paper_id)
    assert stored is not None
    assert stored.status == "frozen"
    assert float(stored.total_points) == 10.0

    assert (
        db_session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action.in_(["paper.update", "paper.delete"]))
        )
        == 0
    )


def test_snapshot_is_isolated_from_later_question_edits(
    client: TestClient,
    db_session: Session,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
    make_question: Callable[..., Question],
) -> None:
    """AC-10：冻结后改题库，旧快照（考试判分依据）不受影响。"""
    teacher = make_user("teacher-paper-snapshot-isolation")
    login(teacher.username)
    question = make_question(
        teacher,
        stem="原始题干",
        options=[{"key": "A", "text": "原始 A"}, {"key": "B", "text": "原始 B"}],
        answer=["A"],
    )
    created = client.post(
        f"{API}/papers",
        json={"title": "快照隔离卷", "questions": [entry(question, 1, 10)]},
        headers=csrf_headers(),
    )
    paper_id = created.json()["id"]

    paper = db_session.get(Paper, paper_id)
    assert paper is not None
    papers_service.freeze_paper(db_session, paper, now=FROZEN_AT)
    db_session.commit()

    stored_question = db_session.get(Question, question.id)
    assert stored_question is not None
    stored_question.stem = "冻结后被修改的题干"
    stored_question.options = [
        {"key": "A", "text": "新 A"},
        {"key": "B", "text": "新 B"},
    ]
    stored_question.answer = ["B"]
    db_session.commit()

    db_session.expire_all()
    reloaded = db_session.get(Paper, paper_id)
    assert reloaded is not None
    snapshot = reloaded.frozen_snapshot
    assert snapshot is not None
    item = snapshot["questions"][0]
    assert item["stem"] == "原始题干"
    assert item["options"] == [
        {"key": "A", "text": "原始 A"},
        {"key": "B", "text": "原始 B"},
    ]
    assert item["answer"] == ["A"]
