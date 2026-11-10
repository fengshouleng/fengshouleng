"""密码、账号枚举与敏感字段基线（TASK-QA-002；AC-02/AC-22；TC-SEC-26）。

覆盖：
- 错误密码与不存在账号返回同一 401 ``AUTH_INVALID_CREDENTIALS`` + 同一 message；
- 登录/``/auth/me``/用户列表/审计响应不出现 ``password`` / ``password_hash``；
- 失败登录的明文密码不进入审计 detail；
- admin 创建弱密码账号 → 422 ``VALIDATION_ERROR``（契约 ``UserCreate.password.minLength=8``）。

黑盒限制：无法从 API 读取数据库中的哈希算法（argon2id）；这里断言“不泄漏”，
哈希实现由 ``server/tests`` 与代码评审保证。未设置 ``EXAM_API_URL`` 时整模块 skip。
"""

from __future__ import annotations

import json
import os

import pytest

from conftest import (
    TEACHER_PASSWORD,
    TEACHER_USERNAME,
    assert_error,
    assert_json,
    unique,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("EXAM_API_URL", "").strip(),
    reason="EXAM_API_URL 未设置：安全负例需要运行中的 API（见 tests/security/README.md）",
)

_USER_KEYS = {"id", "username", "display_name", "role", "is_active", "created_at"}


def _walk_keys(value) -> list[str]:
    keys: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            keys.append(str(key))
            keys.extend(_walk_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.extend(_walk_keys(item))
    return keys


def _assert_no_password_material(text: str, *, label: str) -> None:
    lowered = text.lower()
    assert "password_hash" not in lowered, f"{label}: 响应泄漏 password_hash"
    assert '"password"' not in lowered and "'password'" not in lowered, (
        f"{label}: 响应出现 password 字段"
    )


# --------------------------------------------------------------------------- #
# AC-02：账号枚举防护
# --------------------------------------------------------------------------- #
def test_wrong_password_and_unknown_user_are_indistinguishable(
    client_factory, teacher
) -> None:
    client = client_factory("login-failure-compare")
    wrong_password = f"Wrong-Passw0rd-{unique('qa')}"
    unknown_username = unique("qa-sec-no-such-user")

    existing = client.attempt_login(TEACHER_USERNAME, wrong_password)
    unknown = client.attempt_login(unknown_username, wrong_password)

    existing_body = assert_error(
        existing, 401, "AUTH_INVALID_CREDENTIALS", label="登录失败（已存在账号，错密码）"
    )
    unknown_body = assert_error(
        unknown, 401, "AUTH_INVALID_CREDENTIALS", label="登录失败（不存在账号）"
    )

    assert existing_body["code"] == unknown_body["code"]
    assert existing_body["message"] == unknown_body["message"], (
        "AC-02：不得区分“账号不存在”与“密码错误”"
    )
    assert existing_body["message"] == "用户名或密码错误"
    assert wrong_password not in existing.text
    assert wrong_password not in unknown.text


# --------------------------------------------------------------------------- #
# 响应不得泄漏 password / password_hash
# --------------------------------------------------------------------------- #
def test_login_response_does_not_expose_password_fields(client_factory) -> None:
    client = client_factory("login-secret")
    response = client.attempt_login(TEACHER_USERNAME, TEACHER_PASSWORD)
    if response.status_code == 401:
        pytest.skip(
            "teacher 账号登录 401：请先按 tests/security/README.md §2 seed-demo，"
            "或设置 EXAM_QA_PASSWORD/EXAM_TEACHER_PASSWORD。"
        )
    assert response.status_code == 200, client.describe(response)
    body = response.json()

    assert set(body) == {"user", "csrf_token"}, (
        f"LoginResponse 只能含 user/csrf_token，实际 {sorted(body)}"
    )
    assert set(body["user"]) == _USER_KEYS, (
        f"User 公开字段不符：{sorted(body['user'])}"
    )
    _assert_no_password_material(response.text, label="登录成功响应")
    assert "session_id" not in response.text, "会话 token 只能通过 Set-Cookie 下发"


def test_me_response_does_not_expose_password_fields(teacher) -> None:
    response = teacher.get("/auth/me")
    body = assert_json(response, 200, label="GET /auth/me")
    assert set(body) == {"user", "csrf_token"}
    assert set(body["user"]) == _USER_KEYS
    _assert_no_password_material(response.text, label="GET /auth/me")


def test_users_list_does_not_expose_password_fields(admin) -> None:
    response = admin.get("/users", params={"page": 1, "page_size": 100})
    body = assert_json(response, 200, label="GET /users")
    assert isinstance(body["items"], list) and body["items"]
    for item in body["items"]:
        assert set(item) == _USER_KEYS, f"用户列表项字段不符：{sorted(item)}"
    _assert_no_password_material(response.text, label="GET /users")


# --------------------------------------------------------------------------- #
# 弱密码（契约 UserCreate.password.minLength=8）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "weak_password",
    ["", "short", "1234567"],
    ids=["empty", "five-chars", "seven-chars"],
)
def test_weak_password_is_rejected_by_user_create(admin, weak_password: str) -> None:
    response = admin.post(
        "/users",
        json_body={
            "username": unique("qa-sec-weak"),
            "display_name": "弱密码用例",
            "role": "student",
            "password": weak_password,
        },
    )
    assert_error(
        response,
        422,
        "VALIDATION_ERROR",
        label=f"POST /users（弱密码 {weak_password!r}）",
    )
    assert response.status_code != 201, "弱密码不得创建成功"


# --------------------------------------------------------------------------- #
# 审计中的密码脱敏
# --------------------------------------------------------------------------- #
def test_failed_login_password_is_not_written_to_audit(
    admin, teacher, client_factory
) -> None:
    wrong_password = f"AuditSecret-{unique('qa')}"
    # 独立会话，避免影响 teacher fixture 的已登录会话
    client = client_factory("audit-secret")
    response = client.attempt_login(TEACHER_USERNAME, wrong_password)
    assert_error(
        response, 401, "AUTH_INVALID_CREDENTIALS", label="失败登录（审计脱敏前置）"
    )

    logs = assert_json(
        admin.get(
            "/audit-logs",
            params={
                "action": "auth.login_failed",
                "page": 1,
                "page_size": 100,
            },
        ),
        200,
        label="GET /audit-logs?action=auth.login_failed",
    )
    assert logs["items"], "失败登录应写审计（auth.login_failed）"
    dumped = json.dumps(logs["items"], ensure_ascii=False)
    assert wrong_password not in dumped, "审计日志泄漏失败登录的明文密码"
    for key in _walk_keys(logs["items"]):
        assert "password" not in key.lower(), f"审计 detail 出现敏感键：{key!r}"
