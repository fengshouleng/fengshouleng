"""考试管理接口（AC-10 / C-18 / C-22）：创建、发布冻结、列表范围、状态机、校验、权限。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.paper import Paper, PaperStatus
from app.models.question import Question
from app.models.user import Role, User

API = "/api"


def _paper(
    make_question: Callable[..., Question],
    make_paper: Callable[..., dict[str, Any]],
    owner: User,
    *,
    count: int = 2,
    points: float = 25,
    title: str = "考试用试卷",
) -> dict[str, Any]:
    entries = [
        {
            "question": make_question(
                owner,
                stem=f"考试题 {index}-{owner.username}-{title}",
            ),
            "points": points,
        }
        for index in range(1, count + 1)
    ]
    return make_paper(entries, title=title)


def _patch(
    client: TestClient,
    csrf_headers: Callable[[], dict[str, str]],
    exam_id: int,
    payload: dict[str, Any],
) -> Any:
    return client.patch(
        f"{API}/exams/{exam_id}",
        json=payload,
        headers=csrf_headers(),
    )


def test_create_draft_exam_and_scope(
    client: TestClient,
    db_session: Session,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
    make_question: Callable[..., Question],
    make_paper: Callable[..., dict[str, Any]],
    create_exam: Callable[..., dict[str, Any]],
) -> None:
    teacher = make_user("teacher-exam-owner")
    other_teacher = make_user("teacher-exam-other")
    admin = make_user("admin-exam", role=Role.ADMIN.value)
    student = make_user("student-exam", role=Role.STUDENT.value)

    login(teacher.username)
    paper = _paper(make_question, make_paper, teacher)
    body = create_exam(paper["id"], title="期中测验")

    assert body["status"] == "draft"
    assert body["created_by"] == teacher.id
    assert body["paper_id"] == paper["id"]
    assert body["snapshot_version"] is None
    assert body["shuffle_questions"] is False
    assert body["shuffle_options"] is False
    assert body["duration_min"] == 60

    # draft 阶段试卷不冻结（快照只随发布写入）
    db_session.expire_all()
    stored_paper = db_session.get(Paper, paper["id"])
    assert stored_paper is not None
    assert stored_paper.status == PaperStatus.DRAFT.value
    assert stored_paper.frozen_at is None
    assert stored_paper.frozen_snapshot is None

    # 列表范围：本人可见
    listed = client.get(f"{API}/exams").json()
    assert listed["total"] == 1
    assert listed["items"][0]["id"] == body["id"]

    # 他人（教师）看不到、也读不到 → 403
    login(other_teacher.username)
    assert client.get(f"{API}/exams").json()["total"] == 0
    forbidden = client.get(f"{API}/exams/{body['id']}")
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "FORBIDDEN"
    assert set(forbidden.json()) == {"code", "message", "request_id"}

    # admin 全部可见
    login(admin.username)
    assert client.get(f"{API}/exams").json()["total"] == 1
    assert client.get(f"{API}/exams/{body['id']}").status_code == 200
    assert client.get(f"{API}/exams/999999").status_code == 404

    # 学生：考试管理整体 403
    login(student.username)
    assert client.get(f"{API}/exams").status_code == 403
    created = client.post(
        f"{API}/exams",
        json={
            "paper_id": paper["id"],
            "title": "学生越权",
            "start_at": datetime.now(UTC).isoformat(),
            "end_at": (datetime.now(UTC) + timedelta(minutes=60)).isoformat(),
            "duration_min": 30,
        },
        headers=csrf_headers(),
    )
    assert created.status_code == 403


def test_publish_freezes_paper_and_audits(
    client: TestClient,
    db_session: Session,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
    make_question: Callable[..., Question],
    make_paper: Callable[..., dict[str, Any]],
    create_exam: Callable[..., dict[str, Any]],
) -> None:
    teacher = make_user("teacher-exam-publish")
    login(teacher.username)
    paper = _paper(make_question, make_paper, teacher, count=2, points=25)
    body = create_exam(paper["id"], title="发布考试", publish=True)

    assert body["status"] == "published"
    assert body["snapshot_version"] == 1

    # 发布 = 同一事务内冻结试卷（paper.frozen_at / frozen_snapshot / total_points）
    db_session.expire_all()
    stored = db_session.get(Paper, paper["id"])
    assert stored is not None
    assert stored.status == PaperStatus.FROZEN.value
    assert stored.frozen_at is not None
    snapshot = stored.frozen_snapshot
    assert snapshot is not None
    assert len(snapshot["questions"]) == 2
    assert float(stored.total_points) == 50.0
    assert snapshot["total_points"] == 50.0

    # 审计：exam.create + exam.publish，actor/ip/target 齐全（AC-22 / C-05）
    audits = db_session.scalars(
        select(AuditLog)
        .where(AuditLog.action.in_(["exam.create", "exam.publish"]))
        .order_by(AuditLog.id)
    ).all()
    assert [row.action for row in audits] == ["exam.create", "exam.publish"]
    for row in audits:
        assert row.actor_id == teacher.id
        assert row.target_type == "exam"
        assert row.target_id == str(body["id"])
        assert row.ip

    detail = client.get(f"{API}/exams/{body['id']}").json()
    assert detail["status"] == "published"
    assert detail["snapshot_version"] == 1


def test_state_machine_transitions(
    client: TestClient,
    db_session: Session,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
    make_question: Callable[..., Question],
    make_paper: Callable[..., dict[str, Any]],
    create_exam: Callable[..., dict[str, Any]],
) -> None:
    teacher = make_user("teacher-exam-state")
    login(teacher.username)
    paper = _paper(make_question, make_paper, teacher)
    exam = create_exam(paper["id"], title="状态机考试")
    exam_id = exam["id"]

    # draft → closed 是跳跃 → 409
    response = _patch(client, csrf_headers, exam_id, {"status": "closed"})
    assert response.status_code == 409
    assert response.json()["code"] == "CONFLICT"

    # draft → published 合法：冻结 + snapshot_version=1
    response = _patch(client, csrf_headers, exam_id, {"status": "published"})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "published"
    assert response.json()["snapshot_version"] == 1
    db_session.expire_all()
    stored = db_session.get(Paper, paper["id"])
    assert stored is not None
    assert stored.status == PaperStatus.FROZEN.value

    # 重复发布（需要副作用）→ 409
    response = _patch(client, csrf_headers, exam_id, {"status": "published"})
    assert response.status_code == 409
    assert response.json()["code"] == "CONFLICT"

    # published → draft 回退 → 409
    response = _patch(client, csrf_headers, exam_id, {"status": "draft"})
    assert response.status_code == 409

    # published → closed 合法
    response = _patch(client, csrf_headers, exam_id, {"status": "closed"})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "closed"
    assert response.json()["snapshot_version"] == 1

    # closed 是终态：回到 published、重复关闭、改标题全部 409
    for payload in ({"status": "published"}, {"status": "closed"}, {"title": "改不动的标题"}):
        response = _patch(client, csrf_headers, exam_id, payload)
        assert response.status_code == 409, payload
        assert response.json()["code"] == "CONFLICT"

    actions = [
        row.action
        for row in db_session.scalars(
            select(AuditLog).where(
                AuditLog.action.in_(["exam.publish", "exam.close", "exam.update"])
            )
        ).all()
    ]
    assert actions.count("exam.publish") == 1
    assert actions.count("exam.close") == 1


def test_create_validation(
    client: TestClient,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
    make_question: Callable[..., Question],
    make_paper: Callable[..., dict[str, Any]],
    create_exam: Callable[..., dict[str, Any]],
) -> None:
    teacher = make_user("teacher-exam-validate")
    other_teacher = make_user("teacher-exam-validate-2")
    login(teacher.username)
    paper = _paper(make_question, make_paper, teacher)
    now = datetime.now(UTC)

    def post(payload: dict[str, Any]) -> Any:
        return client.post(f"{API}/exams", json=payload, headers=csrf_headers())

    base: dict[str, Any] = {
        "paper_id": paper["id"],
        "title": "校验考试",
        "start_at": now.isoformat(),
        "end_at": (now + timedelta(minutes=60)).isoformat(),
        "duration_min": 30,
    }

    # start_at >= end_at → 422
    response = post(
        {**base, "start_at": now.isoformat(), "end_at": now.isoformat()}
    )
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"

    # duration 超过时间窗（60 分钟窗 vs 90 分钟时长）→ 422
    response = post(
        {
            **base,
            "end_at": (now + timedelta(minutes=60)).isoformat(),
            "duration_min": 90,
        }
    )
    assert response.status_code == 422

    # duration_min 越界（<10）→ 422（Pydantic 字段校验）
    assert post({**base, "duration_min": 5}).status_code == 422

    # 试卷不存在 → 422（POST /exams 契约无 404 分支）
    assert post({**base, "paper_id": 999999}).status_code == 422

    # 冻结后的试卷不能再建考试
    create_exam(paper["id"], publish=True)
    assert post(base).status_code == 422

    # 他人草稿试卷 → 403（归属不符）
    login(other_teacher.username)
    other_paper = _paper(
        make_question,
        make_paper,
        other_teacher,
        title="他人试卷",
    )
    login(teacher.username)
    assert post({**base, "paper_id": other_paper["id"]}).status_code == 403


def test_published_time_window_edit_rules(
    client: TestClient,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
    make_question: Callable[..., Question],
    make_paper: Callable[..., dict[str, Any]],
    create_exam: Callable[..., dict[str, Any]],
) -> None:
    teacher = make_user("teacher-exam-window")
    login(teacher.username)
    now = datetime.now(UTC)

    # 未开始的已发布考试：允许改时长/时间窗
    paper = _paper(make_question, make_paper, teacher, title="未开始卷")
    future = create_exam(
        paper["id"],
        title="未开始考试",
        start_at=now + timedelta(minutes=60),
        end_at=now + timedelta(minutes=180),
        duration_min=60,
        publish=True,
    )
    response = _patch(client, csrf_headers, future["id"], {"duration_min": 45})
    assert response.status_code == 200, response.text
    assert response.json()["duration_min"] == 45

    response = _patch(
        client,
        csrf_headers,
        future["id"],
        {"start_at": (now + timedelta(minutes=70)).isoformat()},
    )
    assert response.status_code == 200, response.text
    assert response.json()["start_at"] is not None

    # 已开始的考试：改时间窗/时长 → 409
    paper2 = _paper(make_question, make_paper, teacher, title="已开始卷")
    started = create_exam(paper2["id"], title="已开始考试", publish=True)
    response = _patch(client, csrf_headers, started["id"], {"duration_min": 30})
    assert response.status_code == 409
    assert response.json()["code"] == "CONFLICT"

    # 非法时间窗（end <= start）→ 422（422 在时间窗校验，不被「已开始」的 409 遮蔽）
    response = _patch(
        client,
        csrf_headers,
        future["id"],
        {"end_at": (now + timedelta(minutes=30)).isoformat()},
    )
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "VALIDATION_ERROR"
