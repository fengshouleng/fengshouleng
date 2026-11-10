"""CSRF 与会话安全负例（TASK-QA-002；docs/12 §3；AC-04/AC-11/AC-13）。

覆盖：
- 有效会话 + 缺/错 ``X-CSRF-Token`` 的写请求 → 403 ``CSRF_INVALID``；
- 认证先于 CSRF：未登录写请求即使不带 token 也返回 401，而不是 403；
- ``POST /auth/login`` 豁免 CSRF；GET/HEAD 安全方法豁免；
- 有效会话登出 → 204；旧 Cookie 再用 → 401；同一 Cookie 第二次登出 → 401（matrix §2.1 #10）；
- 伪造/失效 Cookie → 401；停用账号的既有会话立即失效 → 401（AC-04）；
- 会话 Cookie 的 HttpOnly / SameSite=Lax / 8h 属性。

未设置 ``EXAM_API_URL`` 时整模块 skip。
"""

from __future__ import annotations

import os
import uuid

import pytest

from conftest import (
    SESSION_COOKIE,
    STUDENT_A_PASSWORD,
    STUDENT_A_USERNAME,
    TEACHER_PASSWORD,
    TEACHER_USERNAME,
    assert_error,
    login_or_skip,
    unique,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("EXAM_API_URL", "").strip(),
    reason="EXAM_API_URL 未设置：安全负例需要运行中的 API（见 tests/security/README.md）",
)


def _login(client_factory, username: str, password: str, label: str):
    client = client_factory(label)
    login_or_skip(client, username, password, label)
    return client


def _session_set_cookie_headers(response) -> list[str]:
    return [
        header
        for header in response.headers.get_list("set-cookie")
        if f"{SESSION_COOKIE}=" in header.lower()
    ]


# --------------------------------------------------------------------------- #
# 登录 / 会话 Cookie 基线
# --------------------------------------------------------------------------- #
def test_login_is_csrf_exempt_and_sets_secure_cookie_attributes(client_factory) -> None:
    client = client_factory("login-cookie")
    response = client.attempt_login(TEACHER_USERNAME, TEACHER_PASSWORD)
    if response.status_code == 401:
        pytest.skip(
            "teacher 账号登录 401：请先按 tests/security/README.md §2 seed-demo，"
            "或设置 EXAM_QA_PASSWORD/EXAM_TEACHER_PASSWORD。"
        )
    assert response.status_code == 200, client.describe(response)
    body = response.json()
    assert body.get("csrf_token"), "登录成功必须下发 csrf_token（CSRF 双提交）"

    cookies = _session_set_cookie_headers(response)
    assert cookies, f"登录响应缺少 Set-Cookie {SESSION_COOKIE}：{response.headers}"
    cookie = cookies[0].lower()
    assert "httponly" in cookie, "session_id 必须是 HttpOnly（AC-01 / 契约）"
    assert "samesite=lax" in cookie, "session_id 必须是 SameSite=Lax（契约）"
    assert "path=/" in cookie, "session_id 的 Path 必须是 /"
    assert "max-age=28800" in cookie, "会话 TTL 必须为 8h=28800s（docs/12 §3）"
    # 本地 http 栈不强制 Secure；staging/prod 由 SESSION_COOKIE_SECURE 控制。
    assert "session_id" not in body, "会话 token 不得出现在 JSON 响应体"


def test_reads_do_not_require_csrf(client_factory) -> None:
    client = _login(client_factory, TEACHER_USERNAME, TEACHER_PASSWORD, "read-csrf")
    response = client.get("/auth/me")
    assert response.status_code == 200, client.describe(response)


# --------------------------------------------------------------------------- #
# CSRF 缺失 / 错误
# --------------------------------------------------------------------------- #
def test_missing_csrf_on_logout_returns_403_csrf_invalid(client_factory) -> None:
    client = _login(
        client_factory, STUDENT_A_USERNAME, STUDENT_A_PASSWORD, "student-logout-no-csrf"
    )
    response = client.post("/auth/logout", csrf=None)
    assert_error(response, 403, "CSRF_INVALID", label="POST /auth/logout（缺 CSRF）")
    # CSRF 失败发生在 handler 之前：会话必须仍然有效。
    assert client.get("/auth/me").status_code == 200


def test_wrong_csrf_on_logout_returns_403_csrf_invalid(client_factory) -> None:
    client = _login(
        client_factory, STUDENT_A_USERNAME, STUDENT_A_PASSWORD, "student-logout-bad-csrf"
    )
    response = client.post("/auth/logout", csrf=f"wrong-{uuid.uuid4().hex}")
    assert_error(response, 403, "CSRF_INVALID", label="POST /auth/logout（错 CSRF）")
    assert client.get("/auth/me").status_code == 200


def test_missing_csrf_on_teacher_write_returns_403_csrf_invalid(client_factory) -> None:
    client = _login(client_factory, TEACHER_USERNAME, TEACHER_PASSWORD, "teacher-no-csrf")
    # body 故意不合法：CSRF 是路由级依赖，必须先于 body 校验返回 403。
    response = client.post("/questions", json_body={"type": "single"}, csrf=None)
    assert_error(response, 403, "CSRF_INVALID", label="POST /questions（缺 CSRF）")


def test_wrong_csrf_on_teacher_write_returns_403_csrf_invalid(client_factory) -> None:
    client = _login(client_factory, TEACHER_USERNAME, TEACHER_PASSWORD, "teacher-bad-csrf")
    response = client.post(
        "/questions",
        json_body={"type": "single"},
        csrf=f"wrong-{uuid.uuid4().hex}",
    )
    assert_error(response, 403, "CSRF_INVALID", label="POST /questions（错 CSRF）")


def test_missing_csrf_on_student_answer_save_returns_403(client_factory) -> None:
    client = _login(
        client_factory, STUDENT_A_USERNAME, STUDENT_A_PASSWORD, "student-answers-no-csrf"
    )
    response = client.put(
        "/sessions/999999999/answers",
        json_body={
            "client_seq": 1,
            "answers": [{"question_id": 1, "selected": ["A"]}],
        },
        csrf=None,
    )
    assert_error(
        response, 403, "CSRF_INVALID", label="PUT /sessions/{id}/answers（缺 CSRF）"
    )


def test_missing_csrf_on_admin_user_create_returns_403(admin) -> None:
    response = admin.post(
        "/users",
        json_body={
            "username": unique("qa-sec-csrf-user"),
            "display_name": "x",
            "role": "student",
            "password": "short",  # <8：若 CSRF 检查失效也只会 422，不会建号
        },
        csrf=None,
    )
    assert_error(response, 403, "CSRF_INVALID", label="POST /users（缺 CSRF）")


# --------------------------------------------------------------------------- #
# 认证先于 CSRF
# --------------------------------------------------------------------------- #
def test_unauthenticated_write_returns_401_before_csrf(client_factory) -> None:
    """认证优先（v1.0.2 / CHG-029；v1.0.3 / CHG-033 扩展到参数校验）。

    无有效会话时：401 ``AUTH_REQUIRED`` **优先于** CSRF(403) **与** 422 参数校验——
    即使请求同时缺 ``X-CSRF-Token`` 且 body/参数非法，也必须是 401（docs/12 §2 第 1 步）。
    """
    client = client_factory("unauth-write")
    response = client.post("/auth/logout", csrf=None)
    assert_error(
        response, 401, "AUTH_REQUIRED", label="POST /auth/logout（未登录且无 CSRF）"
    )
    response = client.post("/questions", json_body={}, csrf=None)
    assert_error(
        response, 401, "AUTH_REQUIRED", label="POST /questions（未登录且无 CSRF）"
    )

    # CHG-033：无会话 + 请求体/参数非法 → 仍是 401，不得先报 422 VALIDATION_ERROR
    invalid_body = client.post(
        "/questions",
        json_body={"type": "not-a-real-type", "stem": "", "options": []},
        csrf=None,
    )
    assert_error(
        invalid_body,
        401,
        "AUTH_REQUIRED",
        label="POST /questions（未登录 + body 非法；CHG-033 401 优先于 422）",
    )
    invalid_seq = client.put(
        "/sessions/999999999/answers",
        json_body={"client_seq": 0, "answers": "not-a-list"},
        csrf=None,
    )
    assert_error(
        invalid_seq,
        401,
        "AUTH_REQUIRED",
        label="PUT /sessions/{id}/answers（未登录 + client_seq=0；CHG-033）",
    )
    invalid_exam = client.post("/exams/not-an-int/sessions", csrf=None)
    assert_error(
        invalid_exam,
        401,
        "AUTH_REQUIRED",
        label="POST /exams/{非整数}/sessions（未登录 + 路径参数非法；CHG-033）",
    )


# --------------------------------------------------------------------------- #
# 登出 / 旧 Cookie / 伪造 Cookie / 停用账号
# --------------------------------------------------------------------------- #
def test_valid_logout_204_then_old_cookie_is_401(client_factory) -> None:
    client = _login(
        client_factory, STUDENT_A_USERNAME, STUDENT_A_PASSWORD, "student-logout-valid"
    )
    old_token = client.session_token()
    old_csrf = client.csrf
    assert old_token, "登录后 Cookie jar 中应有 session_id"
    assert old_csrf, "登录后应有 CSRF token"

    response = client.post("/auth/logout")
    assert response.status_code == 204, client.describe(response)
    headers = _session_set_cookie_headers(response)
    assert headers, "登出必须清 Cookie（Set-Cookie 置过期）"
    cleared = headers[0].lower()
    assert "max-age=0" in cleared or "expires=" in cleared, (
        f"登出 Cookie 未置过期：{headers[0]!r}"
    )

    # 清掉客户端 jar 中已被置空的 Cookie，确保后续请求只带“旧 token”这一枚。
    client.clear_cookies()

    # 旧 Cookie 已吊销：读接口 401（不得返回用户数据）
    old_cookie_read = client.get("/auth/me", cookies={SESSION_COOKIE: old_token})
    assert_error(
        old_cookie_read, 401, "AUTH_REQUIRED", label="GET /auth/me（登出后旧 Cookie）"
    )

    # 同一 Cookie 第二次登出：401，不是 204；且认证先于 CSRF（不会因 token 旧而 403）
    second_logout = client.post(
        "/auth/logout",
        csrf=old_csrf,
        cookies={SESSION_COOKIE: old_token},
    )
    assert_error(
        second_logout,
        401,
        "AUTH_REQUIRED",
        label="POST /auth/logout（同一 Cookie 第二次，matrix §2.1 #10）",
    )


def test_tampered_or_expired_session_cookie_is_401(client_factory) -> None:
    """真实 8h TTL 无法在黑盒内等待；用伪造/失效 token 断言同一 401 口径。"""
    client = client_factory("tampered-cookie")
    fake_token = f"tampered-{uuid.uuid4().hex}"
    response = client.get("/auth/me", cookies={SESSION_COOKIE: fake_token})
    assert_error(response, 401, "AUTH_REQUIRED", label="GET /auth/me（伪造 Cookie）")

    # 无有效会话时写请求仍是 401（认证先于 CSRF），即使给了 CSRF 头
    response = client.post(
        "/auth/logout",
        csrf=f"wrong-{uuid.uuid4().hex}",
        cookies={SESSION_COOKIE: fake_token},
    )
    assert_error(
        response, 401, "AUTH_REQUIRED", label="POST /auth/logout（伪造 Cookie）"
    )


def test_disabled_account_existing_session_is_revoked(disabled_student) -> None:
    """AC-04：admin 停用账号后，该账号 8h 内的既有会话立即 401。"""
    client = disabled_student.handle.api
    response = client.get(
        "/auth/me", cookies={SESSION_COOKIE: disabled_student.token}
    )
    assert_error(
        response, 401, "AUTH_REQUIRED", label="GET /auth/me（停用账号旧 Cookie）"
    )

    # 再登录：固定 401 AUTH_INVALID_CREDENTIALS（不区分停用/密码错）
    login_response = client.attempt_login(
        disabled_student.handle.username,
        disabled_student.handle.password,
    )
    body = assert_error(
        login_response,
        401,
        "AUTH_INVALID_CREDENTIALS",
        label="POST /auth/login（停用账号）",
    )
    assert body["message"] == "用户名或密码错误"
