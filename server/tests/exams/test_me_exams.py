"""`GET /me/exams`（C-02 / CHG-024）：学生发现入口、会话状态、校时与到期读取结算。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.exam_session import ExamSession
from app.models.question import Question
from app.models.user import Role, User

API = "/api"


def _item_of(client: TestClient, exam_id: int) -> dict[str, Any] | None:
    body = client.get(f"{API}/me/exams", params={"page_size": 100}).json()
    for item in body["items"]:
        if item["exam_id"] == exam_id:
            return item
    return None


def _published_exam(
    create_exam: Callable[..., dict[str, Any]],
    make_question: Callable[..., Question],
    make_paper: Callable[..., dict[str, Any]],
    owner: User,
    *,
    title: str,
    publish: bool = True,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    duration_min: int = 60,
) -> dict[str, Any]:
    paper = make_paper(
        [{"question": make_question(owner, stem=f"我的考试题-{title}"), "points": 10}],
        title=f"{title}-试卷",
    )
    return create_exam(
        paper["id"],
        title=title,
        start_at=start_at,
        end_at=end_at,
        duration_min=duration_min,
        publish=publish,
    )


def test_me_exams_scope_server_time_and_permissions(
    client: TestClient,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
    make_question: Callable[..., Question],
    make_paper: Callable[..., dict[str, Any]],
    create_exam: Callable[..., dict[str, Any]],
) -> None:
    teacher = make_user("teacher-me-scope")
    student = make_user("student-me-scope", role=Role.STUDENT.value)
    now = datetime.now(UTC)

    login(teacher.username)
    published = _published_exam(
        create_exam, make_question, make_paper, teacher, title="已发布"
    )
    draft = _published_exam(
        create_exam, make_question, make_paper, teacher, title="草稿", publish=False
    )
    future = _published_exam(
        create_exam,
        make_question,
        make_paper,
        teacher,
        title="未开始",
        start_at=now + timedelta(minutes=60),
        end_at=now + timedelta(minutes=180),
    )

    login(student.username)
    body = client.get(f"{API}/me/exams").json()

    assert body["total"] == 2, body
    # start_at 倒序：未开始的更晚，排前面
    assert [item["exam_id"] for item in body["items"]] == [future["id"], published["id"]]
    assert draft["id"] not in {item["exam_id"] for item in body["items"]}

    for item in body["items"]:
        assert item["session_status"] == "not_started"
        assert item["session_id"] is None
        assert item["score"] is None
        assert item["submitted_at"] is None
        assert item["total_points"] == 10.0
        assert item["duration_min"] == 60

    # server_time 必填且为当前时间（CHG-024）
    server_time = datetime.fromisoformat(body["server_time"])
    assert abs((server_time - now).total_seconds()) < 120

    # 权限：教师 / admin 调 /me/exams → 403（docs/12 §2 第 3 步）
    login(teacher.username)
    forbidden = client.get(f"{API}/me/exams")
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "FORBIDDEN"
    admin = make_user("admin-me-scope", role=Role.ADMIN.value)
    login(admin.username)
    assert client.get(f"{API}/me/exams").status_code == 403


def test_me_exams_session_status_and_closed_visibility(
    client: TestClient,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
    make_question: Callable[..., Question],
    make_paper: Callable[..., dict[str, Any]],
    create_exam: Callable[..., dict[str, Any]],
) -> None:
    teacher = make_user("teacher-me-status")
    student = make_user("student-me-status", role=Role.STUDENT.value)
    other_student = make_user("student-me-status-2", role=Role.STUDENT.value)

    login(teacher.username)
    exam = _published_exam(
        create_exam, make_question, make_paper, teacher, title="作答链路"
    )

    login(student.username)
    started = client.post(
        f"{API}/exams/{exam['id']}/sessions",
        headers=csrf_headers(),
    )
    assert started.status_code == 201, started.text
    session_id = started.json()["id"]
    question_id = started.json()["questions"][0]["question_id"]

    item = _item_of(client, exam["id"])
    assert item is not None
    assert item["session_status"] == "in_progress"
    assert item["session_id"] == session_id
    assert item["score"] is None
    assert item["submitted_at"] is None

    # 默认答案 B → 10 分（exams/conftest 的题目答案）
    saved = client.put(
        f"{API}/sessions/{session_id}/answers",
        json={"client_seq": 1, "answers": [{"question_id": question_id, "selected": ["B"]}]},
        headers=csrf_headers(),
    )
    assert saved.status_code == 200, saved.text
    submitted = client.post(
        f"{API}/sessions/{session_id}/submit",
        headers=csrf_headers(),
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["score"] == 10.0

    item = _item_of(client, exam["id"])
    assert item is not None
    assert item["session_status"] == "submitted"
    assert item["score"] == 10.0
    assert item["submitted_at"] is not None

    # 教师关闭考试后：该生（已有会话）仍能在「我的考试」看到已考记录
    login(teacher.username)
    closed = client.patch(
        f"{API}/exams/{exam['id']}",
        json={"status": "closed"},
        headers=csrf_headers(),
    )
    assert closed.status_code == 200, closed.text

    login(student.username)
    item = _item_of(client, exam["id"])
    assert item is not None
    assert item["session_status"] == "submitted"
    assert item["score"] == 10.0

    # 没有会话的学生看不到 closed 考试（也不含 draft）
    login(other_student.username)
    body = client.get(f"{API}/me/exams").json()
    assert body["total"] == 0


def test_me_exams_settles_expired_on_read_and_filters(
    client: TestClient,
    db_session: Session,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
    make_question: Callable[..., Question],
    make_paper: Callable[..., dict[str, Any]],
    create_exam: Callable[..., dict[str, Any]],
) -> None:
    teacher = make_user("teacher-me-expire")
    student = make_user("student-me-expire", role=Role.STUDENT.value)

    login(teacher.username)
    exam = _published_exam(
        create_exam, make_question, make_paper, teacher, title="到期结算"
    )
    idle = _published_exam(
        create_exam, make_question, make_paper, teacher, title="未开始作答"
    )

    login(student.username)
    started = client.post(
        f"{API}/exams/{exam['id']}/sessions",
        headers=csrf_headers(),
    )
    assert started.status_code == 201, started.text
    session_id = started.json()["id"]
    question_id = started.json()["questions"][0]["question_id"]
    saved = client.put(
        f"{API}/sessions/{session_id}/answers",
        json={"client_seq": 1, "answers": [{"question_id": question_id, "selected": ["B"]}]},
        headers=csrf_headers(),
    )
    assert saved.status_code == 200, saved.text

    # 直接把 deadline 移到过去（模拟考试时长耗尽）
    session_row = db_session.get(ExamSession, session_id)
    assert session_row is not None
    session_row.deadline_at = datetime.now(UTC) - timedelta(minutes=1)
    db_session.commit()

    # 读取时按到期规则结算：expired + score + submitted_at（CHG-024）
    item = _item_of(client, exam["id"])
    assert item is not None
    assert item["session_status"] == "expired"
    assert item["score"] == 10.0
    assert item["submitted_at"] is not None

    db_session.expire_all()
    stored = db_session.get(ExamSession, session_id)
    assert stored is not None
    assert stored.status == "expired"
    assert float(stored.score) == 10.0
    assert stored.submitted_at is not None

    # 过滤：只剩没有会话的那场
    filtered = client.get(
        f"{API}/me/exams", params={"session_status": "not_started"}
    ).json()
    assert filtered["total"] == 1
    assert filtered["items"][0]["exam_id"] == idle["id"]

    expired_only = client.get(
        f"{API}/me/exams", params={"session_status": "expired"}
    ).json()
    assert expired_only["total"] == 1
    assert expired_only["items"][0]["exam_id"] == exam["id"]

    # 分页：total 为过滤后的总数，items 受 page_size 限制
    paged = client.get(f"{API}/me/exams", params={"page_size": 1}).json()
    assert paged["total"] == 2
    assert len(paged["items"]) == 1
    assert paged["page"] == 1
    assert paged["page_size"] == 1

    # 非法过滤值 → 422
    assert client.get(
        f"{API}/me/exams", params={"session_status": "unknown"}
    ).status_code == 422
