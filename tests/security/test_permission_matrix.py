"""权限矩阵负例（TASK-QA-002；docs/12-permission-matrix.md v1.3 §1/§2/§2.0）。

覆盖 AC-03（学生访问 /users → 403）、AC-18（IDOR 的 403 口径；详见 test_idor.py）、
AC-21（教师/学生读审计 → 403；admin 可读）。

本文件把 docs/12 §1 的每个「❌」组合映射成参数化用例：
- 未登录访问全部受保护接口 → 401 ``AUTH_REQUIRED``（/healthz、/auth/login 除外）；
- 学生调用 admin/teacher 接口 → 403 ``FORBIDDEN``（第 3 步角色判定，不因资源不存在而 404）；
- 教师调用学生接口 → 403 ``FORBIDDEN``（含 v1.0.2 CHG-030 的单会话结果）；
- 需要的写请求都带合法 CSRF，确保 403 来自角色而不是 CSRF；
- 公开接口 `GET /healthz` 额外断言 v1.0.3 / CHG-034 的 `db == "ok"`（见 test_healthz_is_public）。

未设置 ``EXAM_API_URL`` 时整模块 skip；账号缺失时相关用例 skip（见 README）。
"""

from __future__ import annotations

import os
from datetime import timedelta

import pytest

from conftest import (
    assert_error,
    assert_json,
    iso,
    unique,
    utc_now,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("EXAM_API_URL", "").strip(),
    reason="EXAM_API_URL 未设置：安全负例需要运行中的 API（见 tests/security/README.md）",
)


# --------------------------------------------------------------------------- #
# 请求表
# --------------------------------------------------------------------------- #
# 未登录：全部受保护接口。路径参数用不可能存在的大 id；写请求带合法形态 body，
# 但**不带** X-CSRF-Token——docs/12 §3 要求“认证先于 CSRF”，所以应 401 而非 403。
UNAUTHENTICATED_ENDPOINTS: list[tuple[str, str, str, dict]] = [
    ("GET /auth/me", "GET", "/auth/me", {}),
    ("POST /auth/logout", "POST", "/auth/logout", {}),
    ("GET /users", "GET", "/users", {}),
    (
        "POST /users",
        "POST",
        "/users",
        {
            "json_body": {
                "username": "qa-sec-should-not-exist",
                "display_name": "x",
                "role": "student",
                "password": "short",
            }
        },
    ),
    ("PATCH /users/{id}", "PATCH", "/users/999999999", {"json_body": {"role": "admin"}}),
    ("GET /questions", "GET", "/questions", {}),
    ("POST /questions", "POST", "/questions", {"json_body": {"type": "single"}}),
    ("POST /questions/import", "POST", "/questions/import", {
        "files": {"file": ("qa-sec.csv", b"type,stem,answer,explanation\n", "text/csv")}
    }),
    ("GET /questions/{id}", "GET", "/questions/999999999", {}),
    ("PUT /questions/{id}", "PUT", "/questions/999999999", {"json_body": {"type": "single"}}),
    ("DELETE /questions/{id}", "DELETE", "/questions/999999999", {}),
    ("GET /papers", "GET", "/papers", {}),
    ("POST /papers", "POST", "/papers", {"json_body": {"title": "x"}}),
    ("GET /papers/{id}", "GET", "/papers/999999999", {}),
    ("PUT /papers/{id}", "PUT", "/papers/999999999", {"json_body": {"title": "x"}}),
    ("DELETE /papers/{id}", "DELETE", "/papers/999999999", {}),
    ("GET /exams", "GET", "/exams", {}),
    ("POST /exams", "POST", "/exams", {"json_body": {"paper_id": 999999999}}),
    ("GET /exams/{id}", "GET", "/exams/999999999", {}),
    ("PATCH /exams/{id}", "PATCH", "/exams/999999999", {"json_body": {"title": "x"}}),
    ("GET /exams/{id}/results", "GET", "/exams/999999999/results", {}),
    ("POST /exams/{id}/sessions", "POST", "/exams/999999999/sessions", {}),
    ("GET /sessions/{id}", "GET", "/sessions/999999999", {}),
    (
        "PUT /sessions/{id}/answers",
        "PUT",
        "/sessions/999999999/answers",
        {
            "json_body": {
                "client_seq": 1,
                "answers": [{"question_id": 1, "selected": ["A"]}],
            }
        },
    ),
    ("POST /sessions/{id}/submit", "POST", "/sessions/999999999/submit", {}),
    ("GET /sessions/{id}/result", "GET", "/sessions/999999999/result", {}),
    ("GET /me/exams", "GET", "/me/exams", {}),
    ("GET /me/results", "GET", "/me/results", {}),
    ("GET /me/wrong-questions", "GET", "/me/wrong-questions", {}),
    ("GET /audit-logs", "GET", "/audit-logs", {}),
]


# 学生：admin / teacher 接口。写请求带合法 CSRF；路径 id 用不存在的大 id，
# body 用合法或“即使越权也不会造成数据写入”的形态。
STUDENT_FORBIDDEN_ENDPOINTS: list[tuple[str, str, str, dict]] = [
    ("GET /users", "GET", "/users", {}),
    (
        "POST /users",
        "POST",
        "/users",
        {
            "json_body": {
                "username": "qa-sec-should-not-exist",
                "display_name": "x",
                "role": "student",
                "password": "short",  # <8：即使角色检查失效也只会 422，不建号
            }
        },
    ),
    (
        "PATCH /users/{id}",
        "PATCH",
        "/users/999999999",
        {"json_body": {"role": "admin"}},
    ),
    ("GET /questions", "GET", "/questions", {}),
    (
        "POST /questions",
        "POST",
        "/questions",
        {"json_body": {"type": "single"}},  # 非法 body：即使越权也只 422
    ),
    (
        "POST /questions/import",
        "POST",
        "/questions/import",
        {
            "files": {
                "file": ("qa-sec-empty.csv", b"type,stem,answer,explanation\n", "text/csv")
            }
        },
    ),
    ("GET /questions/{id}", "GET", "/questions/999999999", {}),
    (
        "PUT /questions/{id}",
        "PUT",
        "/questions/999999999",
        {
            "json_body": {
                "type": "single",
                "stem": unique("qa-sec-student-put"),
                "options": [{"key": "A", "text": "A"}, {"key": "B", "text": "B"}],
                "answer": ["A"],
                "explanation": "x",
                "difficulty": 1,
                "tags": [],
            }
        },
    ),
    ("DELETE /questions/{id}", "DELETE", "/questions/999999999", {}),
    ("GET /papers", "GET", "/papers", {}),
    ("POST /papers", "POST", "/papers", {"json_body": {"title": "x"}}),
    ("GET /papers/{id}", "GET", "/papers/999999999", {}),
    (
        "PUT /papers/{id}",
        "PUT",
        "/papers/999999999",
        {
            "json_body": {
                "title": "x",
                "questions": [{"question_id": 1, "order_no": 1, "points": 1.0}],
            }
        },
    ),
    ("DELETE /papers/{id}", "DELETE", "/papers/999999999", {}),
    ("GET /exams", "GET", "/exams", {}),
    ("POST /exams", "POST", "/exams", {"json_body": {"paper_id": 999999999}}),
    ("GET /exams/{id}", "GET", "/exams/999999999", {}),
    ("PATCH /exams/{id}", "PATCH", "/exams/999999999", {"json_body": {"title": "x"}}),
    ("GET /exams/{id}/results", "GET", "/exams/999999999/results", {}),
    ("GET /audit-logs", "GET", "/audit-logs", {}),
]


# 教师：admin 专属接口（docs/12 §1：users/audit-logs 对 teacher 也是 ❌）。
# 这是 AC-21 的硬性要求：教师查审计 → 403。
TEACHER_FORBIDDEN_ADMIN_ENDPOINTS: list[tuple[str, str, str, dict]] = [
    ("GET /users", "GET", "/users", {}),
    (
        "POST /users",
        "POST",
        "/users",
        {
            "json_body": {
                "username": "qa-sec-should-not-exist",
                "display_name": "x",
                "role": "student",
                "password": "short",  # 即使角色检查失效也只 422，不建号
            }
        },
    ),
    (
        "PATCH /users/{id}",
        "PATCH",
        "/users/999999999",
        {"json_body": {"role": "admin"}},
    ),
    ("GET /audit-logs（AC-21）", "GET", "/audit-logs", {}),
]


# admin：学生专属接口（docs/12 §1：admin 对 sessions/me 也是 ❌；
# 但 `/sessions/{id}/result` 是 admin ✅，不能放进这张表）。
ADMIN_FORBIDDEN_STUDENT_ENDPOINTS: list[tuple[str, str, str, dict]] = [
    ("POST /exams/{id}/sessions", "POST", "/exams/999999999/sessions", {}),
    ("GET /sessions/{id}", "GET", "/sessions/999999999", {}),
    (
        "PUT /sessions/{id}/answers",
        "PUT",
        "/sessions/999999999/answers",
        {
            "json_body": {
                "client_seq": 1,
                "answers": [{"question_id": 1, "selected": ["A"]}],
            }
        },
    ),
    ("POST /sessions/{id}/submit", "POST", "/sessions/999999999/submit", {}),
    ("GET /me/exams", "GET", "/me/exams", {}),
    ("GET /me/results", "GET", "/me/results", {}),
    ("GET /me/wrong-questions", "GET", "/me/wrong-questions", {}),
]


# 教师：学生专属接口（docs/12 §1 + v1.0.2 CHG-030）。路径 id 不存在，
# 但角色判定先于资源存在性，所以仍应 403。
TEACHER_FORBIDDEN_STUDENT_ENDPOINTS: list[tuple[str, str, str, dict]] = [
    ("POST /exams/{id}/sessions", "POST", "/exams/999999999/sessions", {}),
    ("GET /sessions/{id}", "GET", "/sessions/999999999", {}),
    (
        "PUT /sessions/{id}/answers",
        "PUT",
        "/sessions/999999999/answers",
        {
            "json_body": {
                "client_seq": 1,
                "answers": [{"question_id": 1, "selected": ["A"]}],
            }
        },
    ),
    ("POST /sessions/{id}/submit", "POST", "/sessions/999999999/submit", {}),
    ("GET /sessions/{id}/result（CHG-030）", "GET", "/sessions/999999999/result", {}),
    ("GET /me/exams", "GET", "/me/exams", {}),
    ("GET /me/results", "GET", "/me/results", {}),
    ("GET /me/wrong-questions", "GET", "/me/wrong-questions", {}),
]


# --------------------------------------------------------------------------- #
# 公开接口（严格等于 {GET /healthz, POST /auth/login}）
# --------------------------------------------------------------------------- #
def test_healthz_is_public(client_factory) -> None:
    """公开接口；v1.0.3 / CHG-034：200 schema 必带 `db`，实现返回 `"ok"`。"""
    client = client_factory("healthz")
    response = client.get("/healthz")
    assert response.status_code == 200, client.describe(response)
    body = response.json()
    assert body.get("status") == "ok", body
    assert body.get("db") == "ok", (
        f"healthz.db 应为 \"ok\"（v1.0.3 / CHG-034 / P-010），实际 {body.get('db')!r}；body={body!r}"
    )


def test_login_is_public_and_csrf_exempt(client_factory) -> None:
    """POST /auth/login 不要求 X-CSRF-Token（docs/12 §3 / 契约 security: []）。

    用不存在的账号也能区分：若登录端点误挂 CSRF，会得到 403 CSRF_INVALID；
    正确实现是无 CSRF 头也进入登录逻辑，返回 401 AUTH_INVALID_CREDENTIALS。
    """
    client = client_factory("login-public")
    response = client.attempt_login(
        unique("qa-sec-no-such-user"), f"wrong-{unique('qa')}"
    )
    assert_error(
        response, 401, "AUTH_INVALID_CREDENTIALS", label="POST /auth/login（公开、免 CSRF）"
    )


# --------------------------------------------------------------------------- #
# 未登录 → 401 AUTH_REQUIRED
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "case_id,method,path,kwargs",
    UNAUTHENTICATED_ENDPOINTS,
    ids=[case[0] for case in UNAUTHENTICATED_ENDPOINTS],
)
def test_unauthenticated_returns_401_auth_required(
    client_factory, case_id: str, method: str, path: str, kwargs: dict
) -> None:
    client = client_factory(case_id)
    response = client.request(method, path, csrf=None, **kwargs)
    assert_error(response, 401, "AUTH_REQUIRED", label=case_id)


# --------------------------------------------------------------------------- #
# 学生 → admin/teacher 接口 403
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "case_id,method,path,kwargs",
    STUDENT_FORBIDDEN_ENDPOINTS,
    ids=[case[0] for case in STUDENT_FORBIDDEN_ENDPOINTS],
)
def test_student_forbidden_on_admin_and_teacher_endpoints(
    student_a, case_id: str, method: str, path: str, kwargs: dict
) -> None:
    response = student_a.request(method, path, **kwargs)
    assert_error(response, 403, "FORBIDDEN", label=case_id)


def test_student_cannot_change_own_role(student_a) -> None:
    """matrix §2.1 #3：学生 PATCH 自己的 role → 403，不得自我提权。"""
    user_id = student_a.user["id"]
    response = student_a.patch(
        f"/users/{user_id}",
        json_body={"role": "admin"},
    )
    assert_error(
        response,
        403,
        "FORBIDDEN",
        label="PATCH /users/{自己}（学生自我提权）",
    )


# --------------------------------------------------------------------------- #
# 教师 → admin 接口 403（AC-21）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "case_id,method,path,kwargs",
    TEACHER_FORBIDDEN_ADMIN_ENDPOINTS,
    ids=[case[0] for case in TEACHER_FORBIDDEN_ADMIN_ENDPOINTS],
)
def test_teacher_forbidden_on_admin_endpoints(
    teacher, case_id: str, method: str, path: str, kwargs: dict
) -> None:
    response = teacher.request(method, path, **kwargs)
    assert_error(response, 403, "FORBIDDEN", label=case_id)


# --------------------------------------------------------------------------- #
# 教师 → 学生接口 403
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "case_id,method,path,kwargs",
    TEACHER_FORBIDDEN_STUDENT_ENDPOINTS,
    ids=[case[0] for case in TEACHER_FORBIDDEN_STUDENT_ENDPOINTS],
)
def test_teacher_forbidden_on_student_endpoints(
    teacher, case_id: str, method: str, path: str, kwargs: dict
) -> None:
    response = teacher.request(method, path, **kwargs)
    assert_error(response, 403, "FORBIDDEN", label=case_id)


# --------------------------------------------------------------------------- #
# admin → 学生专属接口 403（矩阵 §1 的 ❌；admin 读单会话结果仍是 ✅）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "case_id,method,path,kwargs",
    ADMIN_FORBIDDEN_STUDENT_ENDPOINTS,
    ids=[case[0] for case in ADMIN_FORBIDDEN_STUDENT_ENDPOINTS],
)
def test_admin_forbidden_on_student_only_endpoints(
    admin, case_id: str, method: str, path: str, kwargs: dict
) -> None:
    response = admin.request(method, path, **kwargs)
    assert_error(response, 403, "FORBIDDEN", label=case_id)


# --------------------------------------------------------------------------- #
# admin 正例 + 审计（AC-21/22）
# --------------------------------------------------------------------------- #
def test_admin_can_list_users_and_audit_logs(admin) -> None:
    users = assert_json(admin.get("/users"), 200, label="GET /users（admin）")
    assert isinstance(users.get("items"), list) and users["items"]
    logs = assert_json(admin.get("/audit-logs"), 200, label="GET /audit-logs（admin）")
    assert isinstance(logs.get("items"), list)
    assert set(logs) == {"items", "total", "page", "page_size"}


def test_audit_records_write_operations(admin, teacher) -> None:
    """AC-22：写操作后 admin 能查到含 actor/action/target/ip/时间的记录。"""
    payload = {
        "type": "single",
        "stem": unique("qa-sec-audit-stem"),
        "options": [{"key": "A", "text": "A"}, {"key": "B", "text": "B"}],
        "answer": ["A"],
        "explanation": "审计用例解析",
        "difficulty": 1,
        "tags": ["qa-sec"],
    }
    created = assert_json(
        teacher.post("/questions", json_body=payload),
        201,
        label="POST /questions（AC-22 前置写）",
    )

    moment = utc_now()
    logs = assert_json(
        admin.get(
            "/audit-logs",
            params={
                "action": "question.create",
                "actor_id": teacher.user["id"],
                "from": iso(moment - timedelta(minutes=1)),
                "to": iso(moment + timedelta(minutes=1)),
                "page_size": 100,
            },
        ),
        200,
        label="GET /audit-logs?action=question.create",
    )
    matching = [
        item
        for item in logs["items"]
        if str(item.get("target_id")) == str(created["id"])
    ]
    assert matching, f"审计日志未记录 question.create（target_id={created['id']}）"
    item = matching[0]
    assert item["actor_id"] == teacher.user["id"]
    assert item["action"] == "question.create"
    assert item["target_type"] == "question"
    assert item.get("ip"), "AC-22：审计 ip 必填"
    assert item.get("created_at"), "AC-22：审计 created_at 必填"


@pytest.mark.xfail(
    reason="docs/12 §4 把“越权尝试写审计”列为可选；当前实现未记录，已记入 open_questions",
    strict=False,
)
def test_denied_attempt_is_audited_known_gap(admin, student_a) -> None:
    """若实现开始记录越权尝试则 XPASS；当前预期 xfail（不阻断 G4）。"""
    before = utc_now() - timedelta(seconds=2)
    response = student_a.post(
        "/questions",
        json_body={"type": "single", "stem": unique("qa-sec-denied")},
    )
    assert_error(response, 403, "FORBIDDEN", label="学生越权 POST /questions")

    logs = assert_json(
        admin.get(
            "/audit-logs",
            params={
                "actor_id": student_a.user["id"],
                "from": iso(before),
                "to": iso(utc_now() + timedelta(seconds=3)),
                "page_size": 100,
            },
        ),
        200,
        label="GET /audit-logs（越权尝试）",
    )
    denied = [
        item
        for item in logs["items"]
        if item.get("target_type") == "question"
        or "question" in str(item.get("action", ""))
        or "denied" in str(item.get("action", ""))
        or "forbidden" in str(item.get("action", ""))
    ]
    assert denied, (
        "学生越权 POST /questions 后未在 audit_logs 中找到记录；"
        "docs/12 §4 将“越权尝试”标为可选，已记入 open_questions"
    )
