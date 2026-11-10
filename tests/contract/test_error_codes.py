"""tests/contract/test_error_codes.py — 错误码约定校验（TASK-QA-000）

规则（依据 docs/10-arch.md §错误码约定、docs/12-permission-matrix.md、契约 info.description）
------------------------------------------------------------------------------------
R1 所有 4xx 响应必须是 JSON，且 body 引用**同一个** `Error` schema（code/message/request_id）；
R2 `Error` 三字段必填、类型正确、request_id 为 uuid；
R3 `components.responses.*` 不得出现脱离 `Error` 的响应定义；
R4 登录失败与参数错误不得泄露“账号是否存在”（401/400 body 结构一致）；
R5 声明了 `X-CSRF-Token` 的写接口必须同时声明 CSRF 失败的 403（C-09，v1.0.1 已修复）；
R6 `Error.code` 必须可枚举，否则错误码一致性不可断言（C-03，v1.0.1 已修复）。
   v1.0.2（CHG-032）：枚举为 **16 值**，`ALREADY_SUBMITTED` 已移除（交卷幂等 200，无合法出口）。
R7 判定链的**文案口径**必须写进契约（v1.0.3 / CHG-033 认证优先、CHG-036 可见性定稿）：
   缺文案时实现无法被黑盒用例据以断言，故 R7 只校验 description/securityScheme 的关键判据。

全部用例只读 `contracts/openapi.yaml`，不导入生产代码。
执行：``python -m pytest tests/contract -q``（CI）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterator, List, Tuple

import pytest

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = REPO_ROOT / "contracts" / "openapi.yaml"

HTTP_METHODS = ("get", "put", "post", "delete", "patch", "head", "options", "trace")
ERROR_REQUIRED = {"code", "message", "request_id"}


# --------------------------------------------------------------------------- #
# 载入与工具
# --------------------------------------------------------------------------- #
def _load_contract() -> Dict[str, Any]:
    if yaml is None:  # pragma: no cover
        pytest.fail("缺少 PyYAML：`pip install -r tests/requirements.txt`")
    if not CONTRACT_PATH.is_file():
        pytest.fail(f"契约文件缺失：{CONTRACT_PATH}")
    with CONTRACT_PATH.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):  # pragma: no cover
        pytest.fail(f"契约不是 YAML 映射：{CONTRACT_PATH}")
    return data


@pytest.fixture(scope="session")
def contract() -> Dict[str, Any]:
    return _load_contract()


def iter_operations(contract: Dict[str, Any]) -> Iterator[Tuple[str, str, Dict[str, Any]]]:
    for path, item in contract.get("paths", {}).items():
        if not isinstance(item, dict):
            continue
        for method in HTTP_METHODS:
            operation = item.get(method)
            if isinstance(operation, dict):
                yield path, method, operation


def resolve(contract: Dict[str, Any], node: Any) -> Any:
    """解析内部 ``$ref``（``#/components/...``）。"""
    seen = set()
    while isinstance(node, dict) and "$ref" in node:
        ref = node["$ref"]
        assert isinstance(ref, str) and ref.startswith("#/"), f"仅支持内部引用：{ref!r}"
        if ref in seen:  # pragma: no cover
            raise AssertionError(f"循环 $ref：{ref}")
        seen.add(ref)
        cur: Any = contract
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            assert part in cur, f"悬空 $ref：{ref}（缺少 {part}）"
            cur = cur[part]
        node = cur
    return node


def error_schema(contract: Dict[str, Any]) -> Dict[str, Any]:
    return contract["components"]["schemas"]["Error"]


def is_error_schema(contract: Dict[str, Any], schema: Dict[str, Any]) -> bool:
    """判断某个响应 schema 是否就是统一的 Error（结构比较，避免对象身份依赖）。"""
    target = error_schema(contract)
    if schema is target:
        return True
    return (
        set(schema.get("required", [])) == set(target.get("required", []))
        and set(schema.get("properties", {})) == set(target.get("properties", {}))
    )


def json_schema_of(contract: Dict[str, Any], response: Any) -> Dict[str, Any]:
    resolved = resolve(contract, response)
    content = resolved.get("content", {})
    assert "application/json" in content, "4xx 响应必须是 JSON"
    return resolve(contract, content["application/json"].get("schema", {}))


def _status_is_4xx_or_5xx(status: Any) -> bool:
    text = str(status)
    return len(text) == 3 and text[0] in "45" and text.isdigit()


# --------------------------------------------------------------------------- #
# R1 / TC-CT-23：所有 4xx 使用统一 Error
# --------------------------------------------------------------------------- #
def test_tc_ct_23_every_4xx_uses_error_schema(contract: Dict[str, Any]) -> None:
    """TC-CT-23：遍历全部接口的全部 4xx/5xx，必须引用统一 ``Error``。"""
    problems: List[str] = []
    checked = 0
    for path, method, operation in iter_operations(contract):
        alias = f"{method.upper()} {path}"
        for status, response in operation.get("responses", {}).items():
            if not _status_is_4xx_or_5xx(status):
                continue
            checked += 1
            try:
                schema = json_schema_of(contract, response)
            except AssertionError as exc:
                problems.append(f"{alias} {status}: {exc}")
                continue
            if not is_error_schema(contract, schema):
                problems.append(
                    f"{alias} {status}: schema 不是 Error（properties={sorted(schema.get('properties', {}))}）"
                )
            resolved = resolve(contract, response)
            if not resolved.get("description"):
                problems.append(f"{alias} {status}: 缺 description")
    assert checked >= 50, f"4xx/5xx 响应数量异常（{checked}），疑似契约被裁剪"
    assert not problems, "以下错误响应未遵循统一 Error 约定：\n" + "\n".join(problems)


def test_error_schema_definition(contract: Dict[str, Any]) -> None:
    """R2：``Error`` 三字段必填、类型正确、request_id 为 uuid。"""
    err = error_schema(contract)
    assert err.get("type") == "object"
    assert set(err.get("required", [])) == ERROR_REQUIRED, (
        f"Error.required 应为 {sorted(ERROR_REQUIRED)}，实际 {sorted(err.get('required', []))}"
    )
    props = err.get("properties", {})
    assert props["code"]["type"] == "string"
    assert props["message"]["type"] == "string"
    assert props["request_id"]["type"] == "string"
    assert props["request_id"].get("format") == "uuid"


def test_components_responses_all_use_error(contract: Dict[str, Any]) -> None:
    """R3：``components.responses`` 中不得出现脱离 Error 的 4xx 定义。"""
    problems: List[str] = []
    for name, response in contract.get("components", {}).get("responses", {}).items():
        content = resolve(contract, response).get("content", {})
        if not content:
            continue
        if "application/json" not in content:
            problems.append(f"{name}: 无 application/json")
            continue
        schema = resolve(contract, content["application/json"].get("schema", {}))
        if not is_error_schema(contract, schema):
            problems.append(f"{name}: schema 不是 Error")
    assert not problems, "组件响应定义不一致：\n" + "\n".join(problems)


# --------------------------------------------------------------------------- #
# R4 / TC-CT-02：登录失败与越权不得泄露信息
# --------------------------------------------------------------------------- #
def test_tc_ct_02_login_error_uniform(contract: Dict[str, Any]) -> None:
    """TC-CT-02 | AC-02：登录 400/401 body 结构一致，不含任何“账号是否存在”的线索。"""
    login = contract["paths"]["/auth/login"]["post"]
    responses = login["responses"]
    assert "401" in responses and "400" in responses
    s400 = json_schema_of(contract, responses["400"])
    s401 = json_schema_of(contract, responses["401"])
    assert is_error_schema(contract, s400) and is_error_schema(contract, s401)
    assert set(s400.get("properties", {})) == set(s401.get("properties", {})), (
        "400 与 401 响应字段不同，可能泄露登录失败的具体原因"
    )
    leaked = {"user", "username", "exists", "account", "attempts", "remaining_attempts"}
    assert not (set(s400.get("properties", {})) & leaked), "错误体包含账号存在性线索字段"
    # 登录成功才会返回 user；失败路径不得出现 User 结构
    assert "user" not in s401.get("properties", {})


def test_401_never_leaks_user_fields(contract: Dict[str, Any]) -> None:
    """401 一律是 Error：任何接口都不得在未认证响应里返回 User/成绩等业务数据。"""
    problems: List[str] = []
    for path, method, operation in iter_operations(contract):
        response = operation.get("responses", {}).get("401")
        if response is None:
            continue
        schema = json_schema_of(contract, response)
        if not is_error_schema(contract, schema):
            problems.append(f"{method.upper()} {path}: 401 返回了非 Error 结构")
    assert not problems, "\n".join(problems)


def test_tc_ct_03_users_forbidden(contract: Dict[str, Any]) -> None:
    """TC-CT-03 | AC-03：学生访问 /users 必须 403，且响应不含用户数据。"""
    users_get = contract["paths"]["/users"]["get"]
    assert "403" in users_get["responses"], "/users 缺 403"
    schema = json_schema_of(contract, users_get["responses"]["403"])
    assert is_error_schema(contract, schema), "403 返回了非 Error 结构（可能泄露用户列表）"
    assert "items" not in schema.get("properties", {}), "403 响应含分页 items 字段"
    description = resolve(contract, users_get["responses"]["403"]).get("description", "")
    assert "无权限" in description


def test_tc_ct_18_result_forbidden(contract: Dict[str, Any]) -> None:
    """TC-CT-18 | AC-18：越权访问他人 result 必须是 403（Error），不存在才 404（C-17）。"""
    result_get = contract["paths"]["/sessions/{session_id}/result"]["get"]
    responses = result_get["responses"]
    assert "403" in responses and "404" in responses, "result 应同时声明 403（IDOR）与 404（不存在）"
    for status in ("403", "404"):
        schema = json_schema_of(contract, responses[status])
        assert is_error_schema(contract, schema), f"{status} 返回了非 Error 结构"
        assert set(schema.get("properties", {})) == {"code", "message", "request_id"}, (
            f"{status} 响应夹带业务字段（C-17）"
        )
    for leaked in ("items", "score", "selected", "correct_answer"):
        assert leaked not in json_schema_of(contract, responses["403"]).get("properties", {}), (
            f"403 响应泄露字段 {leaked}"
        )
    description = resolve(contract, responses["403"]).get("description", "")
    assert ("无权限" in description) or ("归属" in description)

    # 判定顺序：AC-18 的 IDOR 硬性为 403；只有“不存在/不可见”才 404
    operation_description = str(result_get.get("description", ""))
    for token in ("403", "404", "AC-18", "非本人"):
        assert token in operation_description, f"result description 未写死 403/404 判定：缺 {token}"
    forbidden = resolve(contract, contract["components"]["responses"]["Forbidden"])
    forbidden_description = str(forbidden.get("description", ""))
    for token in ("角色", "归属", "不可见", "AC-18"):
        assert token in forbidden_description, f"Forbidden 描述未覆盖判定顺序：缺 {token}"
    not_found = resolve(contract, contract["components"]["responses"]["NotFound"])
    not_found_description = str(not_found.get("description", ""))
    assert "不区分" in not_found_description and "404" in not_found_description, (
        "NotFound 未写死“不可见=不存在=404”的防枚举口径（C-17）"
    )

    # 四类会话资源都必须能表达“本人 403 / 不存在 404”，不能只靠 409
    for path, method in (
        ("/sessions/{session_id}", "get"),
        ("/sessions/{session_id}/answers", "put"),
        ("/sessions/{session_id}/submit", "post"),
        ("/sessions/{session_id}/result", "get"),
    ):
        operation = contract["paths"][path][method]
        assert {"403", "404"} <= set(operation.get("responses", {})), (
            f"{method.upper()} {path} 缺 403/404 出口（C-17）"
        )


# --------------------------------------------------------------------------- #
# R5 / R6 / TC-CT-24..25：CSRF 与 code 可枚举（C-09/C-03 已修复）
# --------------------------------------------------------------------------- #
def test_tc_ct_24_csrf_failure_documented(contract: Dict[str, Any]) -> None:
    """TC-CT-24：声明 X-CSRF-Token 的写接口必须同时声明 403（缺 token/不匹配，C-09）。"""
    missing: List[str] = []
    for path, method, operation in iter_operations(contract):
        if method not in ("post", "put", "patch", "delete"):
            continue
        declares_csrf = any(
            isinstance(raw, dict) and raw.get("$ref", "").endswith("/CsrfToken")
            for raw in operation.get("parameters", [])
        )
        if not declares_csrf:
            continue
        if "403" not in operation.get("responses", {}):
            missing.append(f"{method.upper()} {path}")
    assert not missing, "以下写接口声明了 CSRF 却无 403 出口：\n" + "\n".join(missing)
    # logout 是 C-09 的典型漏项，显式锁定
    logout = contract["paths"]["/auth/logout"]["post"]
    assert "403" in logout["responses"], "/auth/logout 缺 CSRF 失败 403（C-09）"
    csrf = resolve(contract, contract["components"]["responses"]["CsrfForbidden"])
    assert "CSRF_INVALID" in str(csrf.get("description", "")), "CsrfForbidden 未固定 CSRF_INVALID"


def test_tc_ct_25_error_code_enum(contract: Dict[str, Any]) -> None:
    """TC-CT-25：``Error.code`` 必须可枚举且覆盖 v1.0.2 的 16 个稳定错误码（C-03 / CHG-032）。"""
    code = error_schema(contract)["properties"]["code"]
    assert code.get("enum") or code.get("const") or code.get("pattern"), (
        f"Error.code 仅 {code}，无法约束 QUESTION_IN_USE/EXAM_NOT_STARTED 等具体错误码"
    )
    expected = {
        "VALIDATION_ERROR",
        "AUTH_REQUIRED",
        "AUTH_INVALID_CREDENTIALS",
        "CSRF_INVALID",
        "FORBIDDEN",
        "NOT_FOUND",
        "CONFLICT",
        "METHOD_NOT_ALLOWED",
        "INTERNAL_ERROR",
        "HTTP_ERROR",
        "EXAM_NOT_STARTED",
        "EXAM_EXPIRED",
        "QUESTION_IN_USE",
        "PAPER_FROZEN",
        "NOT_SUBMITTED",
        "IMPORT_PARTIAL",
    }
    assert len(expected) == 16, "v1.0.2 的 Error.code 应恰为 16 值（CHG-032 移除 ALREADY_SUBMITTED）"
    assert set(code.get("enum", [])) == expected, (
        f"Error.code 枚举与 docs/10 不一致：多 {sorted(set(code.get('enum', [])) - expected)}，"
        f"缺 {sorted(expected - set(code.get('enum', [])))}"
    )
    # CHG-032：废弃码必须不在枚举中（返回即违契），防止实现/文档回潮
    assert "ALREADY_SUBMITTED" not in code.get("enum", []), (
        "ALREADY_SUBMITTED 已从 Error.code 移除（v1.0.2 / CHG-032：交卷重复提交是幂等 200）"
    )
    # C-22 替代方案：draft/未发布不引入独立错误码，统一 404 NOT_FOUND
    assert "EXAM_NOT_PUBLISHED" not in code.get("enum", [])


def test_tc_ct_25b_state_conflicts_have_distinct_codes(contract: Dict[str, Any]) -> None:
    """TC-CT-25b（硬断言）：每个 409 的 description 存在，说明冲突语义有文字口径。"""
    problems: List[str] = []
    for path, method, operation in iter_operations(contract):
        response = operation.get("responses", {}).get("409")
        if response is None:
            continue
        description = resolve(contract, response).get("description", "")
        if not description:
            problems.append(f"{method.upper()} {path}: 409 无 description")
    assert not problems, "\n".join(problems)


# --------------------------------------------------------------------------- #
# R7 / v1.0.3：认证优先（CHG-033）与可见性判定（CHG-036）的文案口径
# --------------------------------------------------------------------------- #
def test_chg_033_auth_before_csrf_and_validation_documented(
    contract: Dict[str, Any],
) -> None:
    """CHG-033（v1.0.3）| 无有效会话 → 401，**优先于** CSRF(403) 与参数校验(422)。

    契约把该判定链写进 `securitySchemes.sessionCookie.description`、`Unauthorized` 与
    `CsrfForbidden`；黑盒用例（`tests/security/test_csrf_session.py`）据同一顺序断言，
    因此文案缺失即视为契约不可测（docs/12 §2 第 1 步 / §3）。
    """
    scheme = contract["components"]["securitySchemes"]["sessionCookie"]
    scheme_description = str(scheme.get("description", ""))
    for token in ("401", "优先", "CSRF", "422", "CHG-033"):
        assert token in scheme_description, (
            f"securitySchemes.sessionCookie.description 未写死认证优先：缺 {token}（CHG-033）"
        )

    unauthorized = resolve(contract, contract["components"]["responses"]["Unauthorized"])
    unauthorized_description = str(unauthorized.get("description", ""))
    for token in ("401", "AUTH_REQUIRED", "优先", "CHG-033"):
        assert token in unauthorized_description, (
            f"Unauthorized description 未写死 401 优先于参数校验：缺 {token}（CHG-033）"
        )

    csrf = resolve(contract, contract["components"]["responses"]["CsrfForbidden"])
    csrf_description = str(csrf.get("description", ""))
    for token in ("CSRF_INVALID", "已认证", "401", "CHG-033"):
        assert token in csrf_description, (
            f"CsrfForbidden description 未写死“仅在已认证会话上校验”：缺 {token}（CHG-033）"
        )


def test_chg_036_visibility_documented(contract: Dict[str, Any]) -> None:
    """CHG-036（v1.0.3）| 学生 draft→404、教师同角色他人资源（含 draft）→403。

    这是 `tests/security/README.md` §6.11 记录的口径张力的定稿结论：教师同角色他人资源
    **不得因“未发布”返回 404**。文案必须同时出现在 `Forbidden` 与 `NotFound` description，
    否则实现无法被黑盒用例约束（docs/12 §2/§2.0、§2.1 #2/#4/#11/#13）。
    """
    forbidden = resolve(contract, contract["components"]["responses"]["Forbidden"])
    forbidden_description = str(forbidden.get("description", ""))
    for token in ("draft", "403", "教师", "404", "CHG-036"):
        assert token in forbidden_description, (
            f"Forbidden description 未写死可见域规则：缺 {token}（CHG-036）"
        )

    not_found = resolve(contract, contract["components"]["responses"]["NotFound"])
    not_found_description = str(not_found.get("description", ""))
    for token in ("不区分", "学生", "draft", "404", "教师", "CHG-036"):
        assert token in not_found_description, (
            f"NotFound description 未写死“学生视角 draft=404 / 教师视角不适用”：缺 {token}（CHG-036）"
        )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
