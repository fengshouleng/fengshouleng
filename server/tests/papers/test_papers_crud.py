"""试卷 CRUD 与组卷校验（AC-09 / AC-18 / AC-22）。

覆盖：创建/列表/详情/更新/删除、服务端总分计算、分值边界、重复题/缺失题/
软删题/非法 order_no/非法 points/客户端传 total、归属 403、admin 全量、
教师越权使用他人题目、考试引用删除 409、审计。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.exam import Exam
from app.models.paper import Paper
from app.models.paper_question import PaperQuestion
from app.models.question import Question
from app.models.user import Role, User

API = "/api"


def paper_payload(title: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {"title": title, "questions": entries}


def entry(
    question: Question | int,
    order_no: int,
    points: float,
    **extra: Any,
) -> dict[str, Any]:
    question_id = question if isinstance(question, int) else question.id
    data: dict[str, Any] = {
        "question_id": question_id,
        "order_no": order_no,
        "points": points,
    }
    data.update(extra)
    return data


def test_create_paper_computes_total(
    client: TestClient,
    db_session: Session,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
    make_question: Callable[..., Question],
) -> None:
    teacher = make_user("teacher-paper-create")
    login(teacher.username)
    single = make_question(teacher, stem="单选：1+1=?", question_type="single")
    multiple = make_question(teacher, stem="多选：哪些是偶数？", question_type="multiple")
    judge = make_question(teacher, stem="判断：地球是圆的", question_type="judge")

    response = client.post(
        f"{API}/papers",
        json=paper_payload(
            "期中测试卷",
            [
                entry(single, 1, 10),
                entry(multiple, 2, 20.5),
                entry(judge, 3, 5.25),
            ],
        ),
        headers=csrf_headers(),
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()

    assert set(body) == {
        "id",
        "owner_id",
        "title",
        "total_points",
        "status",
        "frozen_at",
        "questions",
        "created_at",
    }
    assert body["owner_id"] == teacher.id
    assert body["title"] == "期中测试卷"
    assert body["total_points"] == 35.75
    assert body["status"] == "draft"
    assert body["frozen_at"] is None
    assert [item["order_no"] for item in body["questions"]] == [1, 2, 3]
    assert [item["question_id"] for item in body["questions"]] == [
        single.id,
        multiple.id,
        judge.id,
    ]
    assert [item["points"] for item in body["questions"]] == [10.0, 20.5, 5.25]
    for item in body["questions"]:
        assert set(item) == {"question_id", "order_no", "points", "partial_rule"}
    # 多选未显式给 partial_rule → 默认 all_or_nothing
    assert body["questions"][1]["partial_rule"] == "all_or_nothing"

    listed = client.get(f"{API}/papers")
    assert listed.status_code == 200, listed.text
    page = listed.json()
    assert set(page) == {"items", "total", "page", "page_size"}
    assert page["total"] == 1
    assert page["items"][0]["id"] == body["id"]
    assert page["items"][0]["total_points"] == 35.75

    detail = client.get(f"{API}/papers/{body['id']}")
    assert detail.status_code == 200
    assert detail.json()["questions"][1]["question_id"] == multiple.id

    db_session.expire_all()
    stored = db_session.get(Paper, body["id"])
    assert stored is not None
    assert float(stored.total_points) == 35.75
    rows = list(
        db_session.scalars(
            select(PaperQuestion)
            .where(PaperQuestion.paper_id == body["id"])
            .order_by(PaperQuestion.order_no)
        ).all()
    )
    assert [row.question_id for row in rows] == [single.id, multiple.id, judge.id]
    assert [float(row.points) for row in rows] == [10.0, 20.5, 5.25]
    assert [row.partial_rule for row in rows] == [
        "all_or_nothing",
        "all_or_nothing",
        "all_or_nothing",
    ]

    audit_row = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "paper.create")
    )
    assert audit_row is not None
    assert audit_row.actor_id == teacher.id
    assert audit_row.target_type == "paper"
    assert audit_row.target_id == str(body["id"])


def test_partial_rule_only_applies_to_multiple(
    client: TestClient,
    db_session: Session,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
    make_question: Callable[..., Question],
) -> None:
    teacher = make_user("teacher-paper-partial")
    login(teacher.username)
    multiple = make_question(teacher, stem="多选：partial 规则", question_type="multiple")
    single = make_question(teacher, stem="单选：partial 被归一", question_type="single")

    response = client.post(
        f"{API}/papers",
        json=paper_payload(
            "给分规则测试",
            [
                entry(multiple, 1, 30, partial_rule="partial"),
                entry(single, 2, 10, partial_rule="partial"),
            ],
        ),
        headers=csrf_headers(),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["questions"][0]["partial_rule"] == "partial"
    # 非多选的 partial 归一为 all_or_nothing（契约：仅多选生效）
    assert body["questions"][1]["partial_rule"] == "all_or_nothing"

    db_session.expire_all()
    rows = {
        row.question_id: row
        for row in db_session.scalars(
            select(PaperQuestion).where(PaperQuestion.paper_id == body["id"])
        )
    }
    assert rows[multiple.id].partial_rule == "partial"
    assert rows[single.id].partial_rule == "all_or_nothing"


def test_total_points_upper_bound_10000(
    client: TestClient,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
    make_question: Callable[..., Question],
) -> None:
    """100 题 × 100 分 = 10000，恰好在契约上限内（CHG-025/026）。"""
    teacher = make_user("teacher-paper-max")
    login(teacher.username)
    questions = [
        make_question(teacher, stem=f"边界题 {index}")
        for index in range(1, 101)
    ]

    response = client.post(
        f"{API}/papers",
        json=paper_payload(
            "满分试卷",
            [entry(question, index, 100) for index, question in enumerate(questions, 1)],
        ),
        headers=csrf_headers(),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["total_points"] == 10000.0
    assert len(body["questions"]) == 100


def test_create_validation_errors(
    client: TestClient,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
    make_question: Callable[..., Question],
) -> None:
    teacher = make_user("teacher-paper-invalid")
    login(teacher.username)
    q1 = make_question(teacher, stem="校验题 A")
    q2 = make_question(teacher, stem="校验题 B")
    deleted = make_question(teacher, stem="已软删的题", is_deleted=True)

    cases: list[tuple[str, dict[str, Any]]] = [
        (
            "duplicate-question",
            paper_payload("重复题", [entry(q1, 1, 10), entry(q1, 2, 10)]),
        ),
        (
            "order-gap",
            paper_payload("序号空洞", [entry(q1, 1, 10), entry(q2, 3, 10)]),
        ),
        (
            "order-duplicate",
            paper_payload("序号重复", [entry(q1, 1, 10), entry(q2, 1, 10)]),
        ),
        (
            "question-missing",
            paper_payload("不存在题", [entry(999999, 1, 10)]),
        ),
        (
            "question-soft-deleted",
            paper_payload("软删题", [entry(deleted, 1, 10)]),
        ),
        ("points-zero", paper_payload("零分", [entry(q1, 1, 0)])),
        ("points-over-100", paper_payload("超上限", [entry(q1, 1, 100.01)])),
        ("points-not-cent", paper_payload("非两位小数", [entry(q1, 1, 0.001)])),
        ("total-below-one", paper_payload("总分不足", [entry(q1, 1, 0.5)])),
        ("questions-empty", paper_payload("空试卷", [])),
        (
            "questions-over-100",
            paper_payload(
                "超过 100 题",
                [entry(q1, index, 1) for index in range(1, 102)],
            ),
        ),
        (
            "client-total-points",
            {
                **paper_payload("客户端总分", [entry(q1, 1, 10)]),
                "total_points": 10,
            },
        ),
        ("blank-title", paper_payload("   ", [entry(q1, 1, 10)])),
        ("title-too-long", paper_payload("x" * 129, [entry(q1, 1, 10)])),
    ]

    for label, payload in cases:
        response = client.post(
            f"{API}/papers",
            json=payload,
            headers=csrf_headers(),
        )
        assert response.status_code == 422, f"{label}: {response.text}"
        assert response.json()["code"] == "VALIDATION_ERROR", label


def test_teacher_isolation_and_admin_visibility(
    client: TestClient,
    db_session: Session,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
    make_question: Callable[..., Question],
) -> None:
    owner = make_user("teacher-paper-owner")
    other = make_user("teacher-paper-other")
    admin = make_user("admin-papers", role=Role.ADMIN.value)
    student = make_user("student-papers", role=Role.STUDENT.value)

    owner_question = make_question(owner, stem="owner 的题")
    other_question = make_question(other, stem="other 的题")

    login(owner.username)
    created = client.post(
        f"{API}/papers",
        json=paper_payload("owner 的试卷", [entry(owner_question, 1, 10)]),
        headers=csrf_headers(),
    )
    assert created.status_code == 201, created.text
    paper_id = created.json()["id"]

    login(other.username)
    assert client.get(f"{API}/papers").json()["total"] == 0
    assert client.get(f"{API}/papers/{paper_id}").status_code == 403
    forbidden_put = client.put(
        f"{API}/papers/{paper_id}",
        json=paper_payload("越权修改", [entry(other_question, 1, 10)]),
        headers=csrf_headers(),
    )
    assert forbidden_put.status_code == 403
    assert forbidden_put.json()["code"] == "FORBIDDEN"
    assert (
        client.delete(f"{API}/papers/{paper_id}", headers=csrf_headers()).status_code
        == 403
    )
    # 用他人题目组卷同样 403（归属校验）
    foreign_question = client.post(
        f"{API}/papers",
        json=paper_payload("借用他人题目", [entry(owner_question, 1, 10)]),
        headers=csrf_headers(),
    )
    assert foreign_question.status_code == 403
    assert foreign_question.json()["code"] == "FORBIDDEN"

    login(admin.username)
    admin_list = client.get(f"{API}/papers").json()
    assert admin_list["total"] == 1
    assert client.get(f"{API}/papers/{paper_id}").status_code == 200
    admin_put = client.put(
        f"{API}/papers/{paper_id}",
        json=paper_payload("管理员修改", [entry(owner_question, 1, 12.5)]),
        headers=csrf_headers(),
    )
    assert admin_put.status_code == 200, admin_put.text
    assert admin_put.json()["owner_id"] == owner.id
    assert admin_put.json()["total_points"] == 12.5
    admin_create = client.post(
        f"{API}/papers",
        json=paper_payload("管理员组卷", [entry(owner_question, 1, 10)]),
        headers=csrf_headers(),
    )
    assert admin_create.status_code == 201, admin_create.text

    login(student.username)
    assert client.get(f"{API}/papers").status_code == 403
    student_create = client.post(
        f"{API}/papers",
        json=paper_payload("学生组卷", [entry(owner_question, 1, 10)]),
        headers=csrf_headers(),
    )
    assert student_create.status_code == 403
    assert student_create.json()["code"] == "FORBIDDEN"


def test_update_replaces_items_and_writes_audit(
    client: TestClient,
    db_session: Session,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
    make_question: Callable[..., Question],
) -> None:
    teacher = make_user("teacher-paper-update")
    login(teacher.username)
    q1 = make_question(teacher, stem="更新前题一")
    q2 = make_question(teacher, stem="更新前题二")
    q3 = make_question(teacher, stem="更新后题三")

    created = client.post(
        f"{API}/papers",
        json=paper_payload("待更新试卷", [entry(q1, 1, 10), entry(q2, 2, 10)]),
        headers=csrf_headers(),
    )
    assert created.status_code == 201, created.text
    paper_id = created.json()["id"]

    updated = client.put(
        f"{API}/papers/{paper_id}",
        json=paper_payload(
            "更新后的试卷",
            [entry(q2, 1, 7.5), entry(q3, 2, 12.5)],
        ),
        headers=csrf_headers(),
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["title"] == "更新后的试卷"
    assert body["total_points"] == 20.0
    assert [item["question_id"] for item in body["questions"]] == [q2.id, q3.id]
    assert [item["points"] for item in body["questions"]] == [7.5, 12.5]

    db_session.expire_all()
    rows = list(
        db_session.scalars(
            select(PaperQuestion)
            .where(PaperQuestion.paper_id == paper_id)
            .order_by(PaperQuestion.order_no)
        ).all()
    )
    assert [row.question_id for row in rows] == [q2.id, q3.id]
    assert [float(row.points) for row in rows] == [7.5, 12.5]

    audit_row = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "paper.update")
    )
    assert audit_row is not None
    assert audit_row.actor_id == teacher.id
    assert audit_row.target_id == str(paper_id)

    assert client.get(f"{API}/papers/999999").status_code == 404
    missing_put = client.put(
        f"{API}/papers/999999",
        json=paper_payload("不存在", [entry(q1, 1, 10)]),
        headers=csrf_headers(),
    )
    assert missing_put.status_code == 404
    assert missing_put.json()["code"] == "NOT_FOUND"


def test_delete_paper_and_exam_reference_conflict(
    client: TestClient,
    db_session: Session,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
    make_question: Callable[..., Question],
) -> None:
    teacher = make_user("teacher-paper-delete")
    login(teacher.username)
    q1 = make_question(teacher, stem="删除测试题一")
    q2 = make_question(teacher, stem="删除测试题二")

    first = client.post(
        f"{API}/papers",
        json=paper_payload("可删除试卷", [entry(q1, 1, 10)]),
        headers=csrf_headers(),
    )
    first_id = first.json()["id"]
    deleted = client.delete(f"{API}/papers/{first_id}", headers=csrf_headers())
    assert deleted.status_code == 204, deleted.text
    assert client.get(f"{API}/papers/{first_id}").status_code == 404

    db_session.expire_all()
    assert db_session.get(Paper, first_id) is None
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(PaperQuestion)
            .where(PaperQuestion.paper_id == first_id)
        )
        == 0
    )
    audit_row = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "paper.delete")
    )
    assert audit_row is not None
    assert audit_row.target_id == str(first_id)

    # 建一场引用该试卷的考试（真实 exams 表由模型创建）→ 删除必须 409
    second = client.post(
        f"{API}/papers",
        json=paper_payload("被考试引用", [entry(q2, 1, 10)]),
        headers=csrf_headers(),
    )
    second_id = second.json()["id"]
    window_start = datetime(2026, 1, 1, tzinfo=UTC)
    db_session.add(
        Exam(
            paper_id=second_id,
            title="引用试卷的考试",
            start_at=window_start,
            end_at=window_start + timedelta(hours=2),
            duration_min=60,
            created_by=teacher.id,
        )
    )
    db_session.commit()

    conflicted = client.delete(
        f"{API}/papers/{second_id}", headers=csrf_headers()
    )
    assert conflicted.status_code == 409, conflicted.text
    assert conflicted.json()["code"] == "CONFLICT"
    assert client.get(f"{API}/papers/{second_id}").status_code == 200
