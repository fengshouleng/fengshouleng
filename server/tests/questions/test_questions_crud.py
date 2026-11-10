"""题库 CRUD 接口测试（AC-05 / AC-07 / AC-18 / AC-22）。

覆盖：CRUD、列表过滤与分页、教师归属 403、admin 全量、软删除、
试卷引用 409 QUESTION_IN_USE、参数校验、审计脱敏。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.question import Question
from app.models.user import Role, User

API = "/api"
QUESTION_FIELDS = {
    "id",
    "owner_id",
    "type",
    "stem",
    "options",
    "answer",
    "explanation",
    "difficulty",
    "tags",
    "created_at",
    "updated_at",
}


def payload(**overrides: Any) -> dict[str, Any]:
    """默认一道合法单选题；overrides 覆盖字段。"""
    data: dict[str, Any] = {
        "type": "single",
        "stem": "以下哪个是质数？",
        "options": [
            {"key": "A", "text": "4"},
            {"key": "B", "text": "7"},
            {"key": "C", "text": "9"},
        ],
        "answer": ["B"],
        "explanation": "7 只有 1 和自身两个因数",
        "difficulty": 3,
        "tags": ["数学", "质数"],
    }
    data.update(overrides)
    return data


def multiple_payload(stem: str, tags: list[str]) -> dict[str, Any]:
    return payload(
        type="multiple",
        stem=stem,
        options=[
            {"key": "A", "text": "2"},
            {"key": "B", "text": "3"},
            {"key": "C", "text": "4"},
        ],
        answer=["A", "C"],
        explanation="2 和 4 是偶数",
        tags=tags,
    )


def judge_payload(stem: str, tags: list[str]) -> dict[str, Any]:
    return payload(
        type="judge",
        stem=stem,
        options=[{"key": "A", "text": "对"}, {"key": "B", "text": "错"}],
        answer=["A"],
        explanation="常识题",
        tags=tags,
    )


def test_create_list_detail(
    client: TestClient,
    db_session: Session,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
) -> None:
    teacher = make_user("teacher-crud")
    login(teacher.username)

    response = client.post(
        f"{API}/questions",
        json=payload(),
        headers=csrf_headers(),
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    assert set(body) == QUESTION_FIELDS
    assert body["owner_id"] == teacher.id
    assert body["type"] == "single"
    assert body["answer"] == ["B"]
    assert body["options"][1] == {"key": "B", "text": "7"}
    assert body["difficulty"] == 3
    assert body["tags"] == ["数学", "质数"]

    list_response = client.get(f"{API}/questions")
    assert list_response.status_code == 200, list_response.text
    page = list_response.json()
    assert set(page) == {"items", "total", "page", "page_size"}
    assert page["total"] == 1
    assert page["items"][0]["id"] == body["id"]

    detail = client.get(f"{API}/questions/{body['id']}")
    assert detail.status_code == 200
    assert detail.json()["stem"] == payload()["stem"]

    audit_row = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "question.create")
    )
    assert audit_row is not None
    assert audit_row.actor_id == teacher.id
    assert audit_row.target_type == "question"
    assert audit_row.target_id == str(body["id"])
    detail_text = str(audit_row.detail).lower()
    assert "answer" not in detail_text
    assert "options" not in detail_text
    assert "7 只有" not in str(audit_row.detail)


def test_list_filters_and_pagination(
    client: TestClient,
    db_session: Session,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
) -> None:
    teacher = make_user("teacher-filter")
    login(teacher.username)

    for item in (
        payload(stem="下列哪个是质数？", tags=["数学", "质数"]),
        multiple_payload("下列哪些是偶数？", ["数学"]),
        judge_payload("地球是圆的。", ["地理"]),
    ):
        created = client.post(
            f"{API}/questions", json=item, headers=csrf_headers()
        )
        assert created.status_code == 201, created.text

    by_type = client.get(f"{API}/questions", params={"type": "judge"}).json()
    assert by_type["total"] == 1
    assert by_type["items"][0]["type"] == "judge"

    by_tag = client.get(f"{API}/questions", params={"tag": "数学"}).json()
    assert by_tag["total"] == 2

    by_keyword = client.get(f"{API}/questions", params={"keyword": "偶数"}).json()
    assert by_keyword["total"] == 1
    assert "偶数" in by_keyword["items"][0]["stem"]

    paged = client.get(
        f"{API}/questions", params={"page": 2, "page_size": 2}
    ).json()
    assert paged["total"] == 3
    assert paged["page"] == 2
    assert paged["page_size"] == 2
    assert len(paged["items"]) == 1

    invalid_page = client.get(f"{API}/questions", params={"page_size": 101})
    assert invalid_page.status_code == 422


def test_teacher_isolation_and_admin_visibility(
    client: TestClient,
    db_session: Session,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
) -> None:
    owner = make_user("teacher-owner")
    other = make_user("teacher-other")
    admin = make_user("admin-questions", role=Role.ADMIN.value)

    login(owner.username)
    created = client.post(f"{API}/questions", json=payload(), headers=csrf_headers())
    assert created.status_code == 201, created.text
    question_id = created.json()["id"]

    login(other.username)
    other_list = client.get(f"{API}/questions").json()
    assert other_list["total"] == 0
    assert client.get(f"{API}/questions/{question_id}").status_code == 403
    forbidden_put = client.put(
        f"{API}/questions/{question_id}",
        json=payload(stem="越权修改"),
        headers=csrf_headers(),
    )
    assert forbidden_put.status_code == 403
    assert forbidden_put.json()["code"] == "FORBIDDEN"
    assert client.delete(
        f"{API}/questions/{question_id}", headers=csrf_headers()
    ).status_code == 403

    login(admin.username)
    admin_list = client.get(f"{API}/questions").json()
    assert admin_list["total"] == 1
    assert client.get(f"{API}/questions/{question_id}").status_code == 200
    admin_update = client.put(
        f"{API}/questions/{question_id}",
        json=payload(stem="管理员修改后的题干"),
        headers=csrf_headers(),
    )
    assert admin_update.status_code == 200, admin_update.text
    assert admin_update.json()["owner_id"] == owner.id
    assert admin_update.json()["stem"] == "管理员修改后的题干"


def test_update_and_soft_delete(
    client: TestClient,
    db_session: Session,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
) -> None:
    teacher = make_user("teacher-delete")
    login(teacher.username)
    created = client.post(f"{API}/questions", json=payload(), headers=csrf_headers())
    question_id = created.json()["id"]

    updated = client.put(
        f"{API}/questions/{question_id}",
        json=payload(stem="修改后的题干", difficulty=5, tags=["更新"]),
        headers=csrf_headers(),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["stem"] == "修改后的题干"
    assert updated.json()["difficulty"] == 5
    assert updated.json()["tags"] == ["更新"]

    deleted = client.delete(
        f"{API}/questions/{question_id}", headers=csrf_headers()
    )
    assert deleted.status_code == 204

    assert client.get(f"{API}/questions/{question_id}").status_code == 404
    assert client.get(f"{API}/questions").json()["total"] == 0

    db_session.expire_all()
    stored = db_session.get(Question, question_id)
    assert stored is not None
    assert stored.is_deleted is True

    audit_row = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "question.delete")
    )
    assert audit_row is not None
    assert audit_row.target_id == str(question_id)


def test_question_in_use_returns_409(
    client: TestClient,
    db_session: Session,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
) -> None:
    teacher = make_user("teacher-in-use")
    login(teacher.username)
    created = client.post(f"{API}/questions", json=payload(), headers=csrf_headers())
    question_id = created.json()["id"]

    # BE-003 尚未实现试卷表：本测试手工建最小引用表，验证 409 分支
    db_session.execute(
        text(
            "CREATE TABLE IF NOT EXISTS paper_questions ("
            "paper_id INTEGER NOT NULL, "
            "question_id INTEGER NOT NULL, "
            "order_no INTEGER NOT NULL, "
            "points NUMERIC NOT NULL, "
            "PRIMARY KEY (paper_id, question_id))"
        )
    )
    db_session.execute(
        text(
            "INSERT INTO paper_questions (paper_id, question_id, order_no, points) "
            "VALUES (1, :question_id, 1, 5)"
        ),
        {"question_id": question_id},
    )
    db_session.commit()

    response = client.delete(
        f"{API}/questions/{question_id}", headers=csrf_headers()
    )
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "QUESTION_IN_USE"

    db_session.expire_all()
    stored = db_session.get(Question, question_id)
    assert stored is not None
    assert stored.is_deleted is False


def test_not_found(
    client: TestClient,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
) -> None:
    teacher = make_user("teacher-404")
    login(teacher.username)

    assert client.get(f"{API}/questions/999999").status_code == 404
    put_response = client.put(
        f"{API}/questions/999999", json=payload(), headers=csrf_headers()
    )
    assert put_response.status_code == 404
    assert put_response.json()["code"] == "NOT_FOUND"
    delete_response = client.delete(
        f"{API}/questions/999999", headers=csrf_headers()
    )
    assert delete_response.status_code == 404


def test_student_cannot_access_question_bank(
    client: TestClient,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
) -> None:
    make_user("student-questions", role=Role.STUDENT.value)
    login("student-questions")

    assert client.get(f"{API}/questions").status_code == 403
    created = client.post(
        f"{API}/questions", json=payload(), headers=csrf_headers()
    )
    assert created.status_code == 403
    assert created.json()["code"] == "FORBIDDEN"


@pytest.mark.parametrize(
    "overrides",
    [
        {"answer": ["A", "B"]},
        {"type": "multiple", "answer": ["A"]},
        {
            "type": "judge",
            "options": [
                {"key": "A", "text": "对"},
                {"key": "B", "text": "错"},
                {"key": "C", "text": "不确定"},
            ],
        },
        {
            "type": "judge",
            "answer": ["A", "B"],
            "options": [
                {"key": "A", "text": "对"},
                {"key": "B", "text": "错"},
            ],
        },
        {"answer": ["D"]},
        {
            "options": [
                {"key": "A", "text": "1"},
                {"key": "A", "text": "2"},
            ]
        },
        {"stem": "   "},
        {"explanation": ""},
        {"difficulty": 6},
        {"options": []},
    ],
    ids=[
        "single-two-answers",
        "multiple-one-answer",
        "judge-three-options",
        "judge-two-answers",
        "answer-not-in-options",
        "duplicate-option-key",
        "blank-stem",
        "empty-explanation",
        "difficulty-out-of-range",
        "too-few-options",
    ],
)
def test_validation_rules(
    client: TestClient,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
    overrides: dict[str, Any],
) -> None:
    teacher = make_user("teacher-validation")
    login(teacher.username)

    response = client.post(
        f"{API}/questions",
        json=payload(**overrides),
        headers=csrf_headers(),
    )
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_create_duplicate_stem_conflict(
    client: TestClient,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
) -> None:
    teacher = make_user("teacher-dup-stem")
    login(teacher.username)

    first = client.post(f"{API}/questions", json=payload(), headers=csrf_headers())
    assert first.status_code == 201
    second = client.post(
        f"{API}/questions",
        json=payload(stem="  " + payload()["stem"] + "  "),
        headers=csrf_headers(),
    )
    assert second.status_code == 409
    assert second.json()["code"] == "CONFLICT"
