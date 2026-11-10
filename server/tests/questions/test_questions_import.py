"""题库批量导入测试（AC-06 / AC-08 / C-11）。

覆盖：CSV 部分成功（200 + imported/failed/total）、重复导入幂等、
文件级错误 400、学生 403、审计不落文件内容、可选 XLSX（缺 openpyxl 则跳过）。
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.question import Question
from app.models.user import Role, User

API = "/api"
HEADER = [
    "type",
    "stem",
    "option_a",
    "option_b",
    "option_c",
    "answer",
    "explanation",
    "difficulty",
    "tags",
]


def csv_bytes(rows: list[list[str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def upload(
    client: TestClient,
    content: bytes,
    filename: str,
    headers: dict[str, str],
) -> Any:
    return client.post(
        f"{API}/questions/import",
        files={"file": (filename, content, "text/csv")},
        headers=headers,
    )


def test_import_partial_success(
    client: TestClient,
    db_session: Session,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
) -> None:
    teacher = make_user("teacher-import")
    login(teacher.username)

    content = csv_bytes(
        [
            HEADER,
            ["single", "1+1=?", "2", "3", "4", "B", "基础算术", "1", "数学|基础"],
            ["single", "坏题", "1", "2", "3", "A;B", "解析", "2", "数学"],
            ["judge", "地球是圆的", "", "", "", "A", "常识", "1", "地理"],
            ["multiple", "哪些是偶数?", "2", "3", "4", "A;C", "偶数", "2", "数学"],
        ]
    )
    response = upload(client, content, "questions.csv", csrf_headers())
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    assert body["imported"] == 3
    assert body["skipped_duplicates"] == 0
    assert body["total"] == 4
    assert len(body["failed"]) == 1
    assert body["failed"][0]["row"] == 3
    assert "单选" in body["failed"][0]["reason"]
    assert body["imported"] + body["skipped_duplicates"] + len(body["failed"]) == body["total"]

    stored = list(db_session.scalars(select(Question).order_by(Question.id)).all())
    assert len(stored) == 3
    assert all(item.owner_id == teacher.id for item in stored)
    judge = next(item for item in stored if item.type == "judge")
    assert judge.options == [{"key": "A", "text": "对"}, {"key": "B", "text": "错"}]
    multiple = next(item for item in stored if item.type == "multiple")
    assert multiple.answer == ["A", "C"]

    audit_row = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "question.import")
    )
    assert audit_row is not None
    assert audit_row.actor_id == teacher.id
    assert audit_row.detail is not None
    assert audit_row.detail["imported"] == 3
    assert audit_row.detail["failed"] == 1
    assert "1+1" not in str(audit_row.detail)
    assert "基础算术" not in str(audit_row.detail)


def test_import_twice_is_idempotent(
    client: TestClient,
    db_session: Session,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
) -> None:
    teacher = make_user("teacher-idempotent")
    login(teacher.username)
    content = csv_bytes(
        [
            HEADER,
            ["single", "唯一题干 A", "1", "2", "3", "A", "解析 A", "1", "标签"],
            ["multiple", "唯一题干 B", "1", "2", "3", "A;B", "解析 B", "2", "标签"],
        ]
    )

    first = upload(client, content, "questions.csv", csrf_headers())
    assert first.status_code == 200
    assert first.json()["imported"] == 2

    second = upload(client, content, "questions.csv", csrf_headers())
    assert second.status_code == 200
    body: dict[str, Any] = second.json()
    assert body["imported"] == 0
    assert body["skipped_duplicates"] == 2
    assert body["failed"] == []
    assert body["total"] == 2

    count = db_session.scalar(select(func.count()).select_from(Question))
    assert count == 2


def test_import_duplicate_rows_within_same_file(
    client: TestClient,
    db_session: Session,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
) -> None:
    make_user("teacher-file-dup")
    login("teacher-file-dup")
    content = csv_bytes(
        [
            HEADER,
            ["single", "文件内重复题", "1", "2", "3", "A", "解析", "3", ""],
            ["single", "文件内重复题", "1", "2", "3", "A", "解析", "3", ""],
        ]
    )

    response = upload(client, content, "questions.csv", csrf_headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["imported"] == 1
    assert body["skipped_duplicates"] == 1
    assert body["total"] == 2

    count = db_session.scalar(select(func.count()).select_from(Question))
    assert count == 1


def test_import_missing_required_header_returns_400(
    client: TestClient,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
) -> None:
    make_user("teacher-bad-header")
    login("teacher-bad-header")
    content = csv_bytes(
        [
            ["type", "stem", "option_a", "option_b", "answer"],
            ["single", "无解析列", "1", "2", "A"],
        ]
    )

    response = upload(client, content, "questions.csv", csrf_headers())
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_import_unsupported_file_type_returns_400(
    client: TestClient,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
) -> None:
    make_user("teacher-bad-file")
    login("teacher-bad-file")

    response = upload(client, b"whatever", "questions.txt", csrf_headers())
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_student_cannot_import(
    client: TestClient,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
) -> None:
    make_user("student-import", role=Role.STUDENT.value)
    login("student-import")
    content = csv_bytes(
        [HEADER, ["single", "学生导入", "1", "2", "3", "A", "解析", "1", ""]]
    )

    response = upload(client, content, "questions.csv", csrf_headers())
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


def test_import_xlsx_when_openpyxl_available(
    client: TestClient,
    db_session: Session,
    make_user: Callable[..., User],
    login: Callable[..., dict[str, Any]],
    csrf_headers: Callable[[], dict[str, str]],
) -> None:
    openpyxl: Any = pytest.importorskip("openpyxl")
    make_user("teacher-xlsx")
    login("teacher-xlsx")

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(HEADER)
    sheet.append(["single", "XLSX 题目", "1", "2", "3", "C", "表格解析", 2, "表格"])
    buffer = io.BytesIO()
    workbook.save(buffer)

    response = client.post(
        f"{API}/questions/import",
        files={
            "file": (
                "questions.xlsx",
                buffer.getvalue(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        headers=csrf_headers(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["imported"] == 1
    assert body["total"] == 1
    count = db_session.scalar(select(func.count()).select_from(Question))
    assert count == 1
