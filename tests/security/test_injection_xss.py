"""注入与 XSS 安全基线（TASK-QA-002；AC-05；TC-SEC-24/25）。

覆盖：
- 题干/解析包含 SQL 元字符 ``' OR 1=1 --`` 与 ``<script>alert(1)</script>`` 时：
  创建/详情/列表不返回 500，JSON 原样（或 HTML 转义）返回，不执行；
- 搜索关键词注入不泄漏其他教师的数据（参数化查询 + owner 过滤）；
- 路径/分页参数的注入形态返回 422（稳定校验错误），而不是 500；
- 导入文件中的注入 payload 同样不产生 500。

“不执行”的断言口径：接口必须是 ``application/json``，响应体不是 HTML；
JSON 解码后的值等于注入原文，或是其 HTML 转义形式（按实现口径二选一）。
未设置 ``EXAM_API_URL`` 时整模块 skip。
"""

from __future__ import annotations

import csv
import html
import io
import os

import pytest

from conftest import assert_error, assert_json, unique

pytestmark = pytest.mark.skipif(
    not os.environ.get("EXAM_API_URL", "").strip(),
    reason="EXAM_API_URL 未设置：安全负例需要运行中的 API（见 tests/security/README.md）",
)

SQL_INJECTION = "' OR 1=1 --"
XSS = "<script>alert(1)</script>"


def _payload(
    *,
    stem: str,
    explanation: str,
    tags: list[str] | None = None,
) -> dict:
    return {
        "type": "single",
        "stem": stem,
        "options": [
            {"key": "A", "text": "选项 A"},
            {"key": "B", "text": "选项 B"},
            {"key": "C", "text": "选项 C"},
            {"key": "D", "text": "选项 D"},
        ],
        "answer": ["B"],
        "explanation": explanation,
        "difficulty": 1,
        "tags": tags or ["qa-sec"],
    }


def _assert_round_trip(actual: str, expected: str) -> None:
    """JSON 原样返回或 HTML 转义返回都接受；两者都不是则失败。"""
    assert actual == expected or actual == html.escape(expected), (
        f"字段值既不是原文也不是 HTML 转义：actual={actual!r}, expected={expected!r}"
    )


def _create_question(teacher, *, stem: str, explanation: str) -> dict:
    payload = _payload(stem=stem, explanation=explanation)
    response = teacher.post("/questions", json_body=payload)
    body = assert_json(response, 201, label="POST /questions（注入/XSS payload）")
    return body


# --------------------------------------------------------------------------- #
# SQL 元字符 + XSS 在题干/解析中
# --------------------------------------------------------------------------- #
def test_sql_metacharacters_and_script_round_trip(teacher) -> None:
    tag = unique("qa-sec-inj")
    stem = f"[{tag}] {SQL_INJECTION} {XSS}"
    explanation = f"[{tag}] 解析：{SQL_INJECTION} {XSS}"

    created = _create_question(teacher, stem=stem, explanation=explanation)
    assert created["stem"] == stem, "题干不应被截断/改写"
    assert created["explanation"] == explanation, "解析不应被截断/改写"

    detail = assert_json(
        teacher.get(f"/questions/{created['id']}"),
        200,
        label="GET /questions/{id}（注入/XSS 回读）",
    )
    assert detail["stem"] == stem
    assert detail["explanation"] == explanation

    # 关键词搜索含 SQL 元字符：不得 500，且必须仍返回自己的题（参数化查询）
    listing = assert_json(
        teacher.get(
            "/questions",
            params={"keyword": SQL_INJECTION, "page": 1, "page_size": 100},
        ),
        200,
        label="GET /questions?keyword=SQL注入",
    )
    ids = {int(item["id"]) for item in listing["items"]}
    assert int(created["id"]) in ids, "合法关键词搜索应能命中自己的题干"


def test_search_injection_does_not_leak_other_teacher_questions(
    teacher, teacher_b
) -> None:
    """参数化 + owner 过滤：注入串不能把教师 B 的题带出来。"""
    other_stem = f"[{unique('qa-sec-other')}] 教师 B 私有题 {SQL_INJECTION}"
    other = teacher_b.api.post(
        "/questions",
        json_body=_payload(stem=other_stem, explanation="教师 B 解析"),
    )
    other_body = assert_json(
        other, 201, label="POST /questions（教师 B 前置数据）"
    )

    response = teacher.get(
        "/questions",
        params={"keyword": SQL_INJECTION, "page": 1, "page_size": 100},
    )
    body = assert_json(response, 200, label="GET /questions（教师 A 搜索注入串）")
    ids = {int(item["id"]) for item in body["items"]}
    assert int(other_body["id"]) not in ids, "搜索注入串泄漏了教师 B 的题目"
    assert all(
        int(item["owner_id"]) == int(teacher.user["id"]) for item in body["items"]
    ), "教师题库列表只能返回自己的题（owner 过滤）"


# --------------------------------------------------------------------------- #
# 注入形态的路径/分页参数 → 422，不能 500
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "method,path,kwargs",
    [
        ("GET", "/questions/not-an-int", {}),
        ("GET", "/questions", {"params": {"page": SQL_INJECTION}}),
        ("GET", "/questions", {"params": {"page_size": SQL_INJECTION}}),
        ("GET", "/papers/not-an-int", {}),
        ("GET", "/exams/not-an-int", {}),
    ],
    ids=[
        "question-path-string",
        "questions-page-injection",
        "questions-page-size-injection",
        "paper-path-string",
        "exam-path-string",
    ],
)
def test_injection_in_path_or_pagination_returns_422(
    teacher, method: str, path: str, kwargs: dict
) -> None:
    response = teacher.request(method, path, **kwargs)
    assert_error(response, 422, "VALIDATION_ERROR", label=f"{method} {path}")


# --------------------------------------------------------------------------- #
# XSS：JSON 数据，不是 HTML 文档
# --------------------------------------------------------------------------- #
def test_xss_payload_is_returned_as_json_data(teacher) -> None:
    tag = unique("qa-sec-xss")
    stem = f"[{tag}] {XSS}"
    explanation = f"[{tag}] 解析 {XSS}"

    created = _create_question(teacher, stem=stem, explanation=explanation)
    detail_response = teacher.get(f"/questions/{created['id']}")
    detail = assert_json(
        detail_response, 200, label="GET /questions/{id}（XSS 回读）"
    )

    content_type = detail_response.headers.get("content-type", "").lower()
    assert "text/html" not in content_type, (
        f"题目详情不得返回 text/html（XSS 执行面）：{content_type!r}"
    )
    assert detail_response.text.lstrip().startswith("{"), (
        "题目详情必须是 JSON 对象，而不是浏览器会执行的 HTML"
    )

    _assert_round_trip(detail["stem"], stem)
    _assert_round_trip(detail["explanation"], explanation)


# --------------------------------------------------------------------------- #
# 导入通道中的注入/XSS payload
# --------------------------------------------------------------------------- #
def test_import_with_injection_payload_returns_200_not_500(teacher) -> None:
    """导入解析/落库不得因为 SQL 元字符或 <script> 报 500。"""
    tag = unique("qa-sec-import")
    header = [
        "type",
        "stem",
        "option_a",
        "option_b",
        "option_c",
        "option_d",
        "answer",
        "explanation",
        "difficulty",
        "tags",
    ]
    row = [
        "single",
        f"[{tag}] {SQL_INJECTION} {XSS}",
        "选项 A",
        "选项 B",
        "选项 C",
        "选项 D",
        "B",
        f"[{tag}] 导入解析 {SQL_INJECTION} {XSS}",
        "1",
        "qa-sec|import",
    ]
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerow(row)
    content = buffer.getvalue().encode("utf-8")

    response = teacher.post(
        "/questions/import",
        files={"file": (f"qa-sec-{tag}.csv", content, "text/csv")},
    )
    body = assert_json(response, 200, label="POST /questions/import（注入/XSS）")
    assert body["total"] == 1
    assert body["failed"] == [], f"注入 payload 不应导致行级失败：{body!r}"
    assert body["imported"] == 1, f"合法注入 payload 应正常入库：{body!r}"
