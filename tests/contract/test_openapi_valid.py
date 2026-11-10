"""tests/contract/test_openapi_valid.py — 契约结构与 AC 支撑校验（TASK-QA-000）

范围
----
* TC-CT-00   结构校验：`openapi-spec-validator` + `$ref` 全解析 + operationId 唯一
              + 全局 security / 写接口 CSRF / 分页参数一致性
* TC-CT-01..22  “契约是否足以支撑 AC-0x” 的逐条断言（不替代接口/E2E 用例）

纪律
----
* 只读 `contracts/openapi.yaml`，**不导入** `server/` 或 `web/` 任何代码；
* v1.0.1 已处置 docs/60-test-plan.md §8 的 C-01..C-22；v1.0.2（CHG-024..CHG-032）已同步
  4 处断言（TC-CT-09 单题上限 100、TC-CT-13 `replayed` 必填、TC-CT-15 `applied` 必填、
  TC-CT-25 枚举 16 值）并补 CHG-024 的 `server_time` / 到期结算用例；
* v1.0.3（CHG-033..CHG-036）已同步 docs/13 §8.1.2 的 3 处断言：
  `CHG-034` healthz 200 的 `required` 含 `db` 且 `db.type == "string"`（503 仍是统一 Error、不含 `db`）、
  `CHG-035` `AuditLog.actor_id.type == ["integer","null"]` 且 `actor_id` 仍在 `required`、
  `CHG-036` 可见性口径（学生 draft→404 / 教师同角色他人资源含 draft→403）已由
  `test_error_codes.py::test_chg_036_visibility_documented` 锁定文案、运行期用例见
  `tests/security/test_idor.py`；本文件当前**不保留 xfail**（v1.0.3 未产生新 xfail）。
  若回归或发现新契约缺陷，再按“缺陷单号 + xfail”显式标记，避免“静默跳过”；
* 本机 shell 不可用（pwsh 0xC0000142），实际执行在 CI：
      python -m pytest tests/contract -q
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterator, List, Tuple

import pytest

try:  # PyYAML 是契约测试的最小依赖，缺失时给出可执行提示
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = REPO_ROOT / "contracts" / "openapi.yaml"

HTTP_METHODS = ("get", "put", "post", "delete", "patch", "head", "options", "trace")
WRITE_METHODS = ("post", "put", "patch", "delete")
PUBLIC_OPERATIONS = {("/healthz", "get"), ("/auth/login", "post")}


# --------------------------------------------------------------------------- #
# 载入与工具
# --------------------------------------------------------------------------- #
def _load_contract() -> Dict[str, Any]:
    if yaml is None:  # pragma: no cover
        pytest.fail(
            "缺少 PyYAML：请先执行 `pip install -r tests/requirements.txt`"
            "（见 docs/60-test-plan.md §2）"
        )
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
            op = item.get(method)
            if isinstance(op, dict):
                yield path, method, op


def resolve(contract: Dict[str, Any], node: Any) -> Any:
    """递归解析内部 ``$ref``（``#/components/...``），返回目标节点本身。"""
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


def schema_of(contract: Dict[str, Any], response: Any) -> Dict[str, Any]:
    """取一个响应节点的 JSON body schema（已解引用）。"""
    resolved = resolve(contract, response)
    content = resolved.get("content", {})
    assert "application/json" in content, f"响应缺少 application/json：{resolved.keys()}"
    return resolve(contract, content["application/json"].get("schema", {}))


def op(contract: Dict[str, Any], path: str, method: str) -> Dict[str, Any]:
    try:
        return contract["paths"][path][method]
    except KeyError:  # pragma: no cover
        pytest.fail(f"契约缺少 {method.upper()} {path}")


def _walk(node: Any) -> Iterator[Any]:
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


# --------------------------------------------------------------------------- #
# TC-CT-00 结构校验
# --------------------------------------------------------------------------- #
def test_contract_file_is_non_empty_yaml(contract: Dict[str, Any]) -> None:
    assert CONTRACT_PATH.stat().st_size > 1000, "契约文件异常小，可能被截断"
    assert contract.get("openapi", "").startswith("3.1"), "契约版本应为 OpenAPI 3.1.x"
    assert contract.get("info", {}).get("version"), "info.version 缺失"
    assert contract.get("info", {}).get("title"), "info.title 缺失"


def test_tc_ct_00_openapi_spec_validator_passes() -> None:
    """TC-CT-00：`openapi-spec-validator` 校验通过（G2 硬性准出）。"""
    try:
        from openapi_spec_validator import validate
    except ImportError:  # pragma: no cover
        pytest.fail(
            "缺少 openapi-spec-validator：请执行 `pip install -r tests/requirements.txt`"
            "（openapi-spec-validator>=0.7.1 支持 OpenAPI 3.1）"
        )
    spec = _load_contract()
    # base_uri 让校验器能解析相对引用；本契约仅用内部 $ref，做防御性传入
    validate(spec, base_uri=CONTRACT_PATH.as_uri())


def test_all_refs_resolve(contract: Dict[str, Any]) -> None:
    refs = [
        node["$ref"]
        for node in _walk(contract)
        if isinstance(node, dict) and isinstance(node.get("$ref"), str)
    ]
    assert refs, "契约内没有任何 $ref，疑似结构异常"
    unresolved: List[str] = []
    for ref in refs:
        try:
            resolve(contract, {"$ref": ref})
        except AssertionError as exc:  # pragma: no cover
            unresolved.append(f"{ref} -> {exc}")
    assert not unresolved, "存在无法解析的 $ref：\n" + "\n".join(unresolved)


def test_operation_ids_unique_and_documented(contract: Dict[str, Any]) -> None:
    ids: Dict[str, str] = {}
    dups: List[str] = []
    problems: List[str] = []
    for path, method, operation in iter_operations(contract):
        alias = f"{method.upper()} {path}"
        operation_id = operation.get("operationId")
        if not operation_id:
            problems.append(f"{alias}: 缺 operationId")
            continue
        if operation_id in ids:
            dups.append(f"{operation_id}: {ids[operation_id]} 与 {alias}")
        ids[operation_id] = alias
        if not operation.get("summary"):
            problems.append(f"{alias}: 缺 summary")
        if not operation.get("tags"):
            problems.append(f"{alias}: 缺 tags")
        if not operation.get("responses"):
            problems.append(f"{alias}: 缺 responses")
    assert not dups, "operationId 重复：\n" + "\n".join(dups)
    assert not problems, "接口元数据不全：\n" + "\n".join(problems)


def test_global_security_and_public_operations(contract: Dict[str, Any]) -> None:
    assert contract.get("security") == [{"sessionCookie": []}], "全局 security 应为 sessionCookie"
    schemes = contract.get("components", {}).get("securitySchemes", {})
    assert schemes.get("sessionCookie", {}).get("in") == "cookie"
    assert schemes.get("sessionCookie", {}).get("name") == "session_id"

    public = {
        (path, method)
        for path, method, operation in iter_operations(contract)
        if operation.get("security") == []
    }
    assert public == PUBLIC_OPERATIONS, f"公开接口应仅 {PUBLIC_OPERATIONS}，实际 {public}"


def test_write_operations_require_csrf(contract: Dict[str, Any]) -> None:
    """所有非 GET 接口（除登录）必须声明 X-CSRF-Token（docs/12 §3）。"""
    missing: List[str] = []
    for path, method, operation in iter_operations(contract):
        if method not in WRITE_METHODS or (path, method) in PUBLIC_OPERATIONS:
            continue
        raw_params = operation.get("parameters", [])
        has_csrf = False
        for raw in raw_params:
            if isinstance(raw, dict) and raw.get("$ref", "").endswith("/CsrfToken"):
                has_csrf = True
            elif isinstance(raw, dict) and raw.get("name") == "X-CSRF-Token":
                has_csrf = True
        if not has_csrf:
            missing.append(f"{method.upper()} {path}")
    assert not missing, "写接口未声明 CSRF 参数：\n" + "\n".join(missing)


def test_pagination_parameters_consistent(contract: Dict[str, Any]) -> None:
    problems: List[str] = []
    for path, method, operation in iter_operations(contract):
        params = {
            p.get("name"): p
            for p in (
                resolve(contract, raw) if isinstance(raw, dict) and "$ref" in raw else raw
                for raw in operation.get("parameters", [])
            )
            if isinstance(p, dict)
        }
        page = params.get("page")
        if page is not None:
            schema = page.get("schema", {})
            if schema.get("default") != 1 or schema.get("minimum") != 1:
                problems.append(f"{method.upper()} {path}: page 应 default=1, minimum=1")
        page_size = params.get("page_size")
        if page_size is not None:
            schema = page_size.get("schema", {})
            if (
                schema.get("default") != 20
                or schema.get("minimum") != 1
                or schema.get("maximum") != 100
            ):
                problems.append(f"{method.upper()} {path}: page_size 应 default=20, min=1, max=100")
    assert not problems, "分页参数口径不一致：\n" + "\n".join(problems)


# --------------------------------------------------------------------------- #
# TC-CT-01..22 契约对 AC 的支撑（逐条）
# --------------------------------------------------------------------------- #
def test_tc_ct_01_login_contract(contract: Dict[str, Any]) -> None:
    """TC-CT-01 | AC-01：登录 200 + user/csrf_token + HttpOnly session_id Cookie。"""
    login = op(contract, "/auth/login", "post")
    assert "200" in login["responses"] and "401" in login["responses"]
    assert "Set-Cookie" in login["responses"]["200"].get("description", ""), "200 未说明下发 Cookie"
    body = schema_of(contract, login["responses"]["200"])
    assert set(body.get("required", [])) == {"user", "csrf_token"}
    assert body["properties"]["user"].get("$ref", "").endswith("/User")


def test_c02_me_exams_contract(contract: Dict[str, Any]) -> None:
    """C-02（v1.0.1）| 学生发现考试：``GET /me/exams`` + session_status 枚举 + 仅 student。"""
    me = op(contract, "/me/exams", "get")
    assert me.get("operationId") == "listMyExams"
    assert me.get("tags") == ["me"]
    responses = me["responses"]
    assert "200" in responses and "403" in responses, "/me/exams 缺 200/403 出口"
    description = str(me.get("description", ""))
    assert "student" in description, "/me/exams description 未声明仅 student（C-02）"
    assert "403" in description and "FORBIDDEN" in description, (
        "/me/exams description 未写死教师/admin → 403（C-02）"
    )

    page_schema = schema_of(contract, responses["200"])
    assert "items" in page_schema.get("required", [])
    assert page_schema["properties"]["items"]["items"].get("$ref", "").endswith("/MyExamItem")
    item = contract["components"]["schemas"]["MyExamItem"]
    required = set(item.get("required", []))
    assert {
        "exam_id",
        "exam_title",
        "start_at",
        "end_at",
        "duration_min",
        "session_status",
    } <= required, f"MyExamItem.required 缺字段：{sorted(required)}"
    session_status = item["properties"]["session_status"]
    assert session_status.get("type") == "string"
    assert session_status.get("enum") == [
        "not_started",
        "in_progress",
        "submitted",
        "expired",
    ], "session_status 枚举未与 SessionState.status 对齐（C-02）"
    for field in ("session_id", "score", "total_points", "submitted_at"):
        assert field in item.get("properties", {}), f"MyExamItem 缺 {field}（C-02）"

    query = next(p for p in me["parameters"] if p.get("name") == "session_status")
    assert query["schema"].get("enum") == session_status["enum"], (
        "查询参数 session_status 与响应枚举不一致（C-02）"
    )
    forbidden = resolve(contract, responses["403"])
    assert "无权限" in str(forbidden.get("description", "")), "/me/exams 403 不是 Forbidden 语义"
    error = schema_of(contract, responses["403"])
    assert set(error.get("properties", {})) == {"code", "message", "request_id"}


def test_chg_024_me_exams_server_time_required(contract: Dict[str, Any]) -> None:
    """CHG-024（v1.0.2）| `GET /me/exams` 根级 `server_time` 必填 + 过期条目读取即结算。

    v1.0.1 的 `test_c02_me_exams_contract` 未覆盖本字段，v1.0.2 把它提为 `required`（前端校时），
    故单列一条：既断言 200 响应根级必带 `server_time`（date-time），也断言 description 写死
    「`session_status=in_progress` 且已过 `deadline_at` → 读取时结算为 `expired` 并带 `score`」。
    """
    me = op(contract, "/me/exams", "get")
    body = schema_of(contract, me["responses"]["200"])
    assert "server_time" in body.get("required", []), (
        "GET /me/exams 200 根级未强制返回 server_time（v1.0.2 / CHG-024）"
    )
    server_time = body["properties"]["server_time"]
    assert server_time.get("type") == "string"
    assert server_time.get("format") == "date-time", "server_time 应为 ISO 8601 date-time"

    description = str(me.get("description", ""))
    for token in ("server_time", "deadline_at", "session_status=in_progress", "expired", "score"):
        assert token in description, (
            f"/me/exams description 未写死校时/到期结算口径：缺 {token}（CHG-024）"
        )


def test_chg_034_healthz_db_required(contract: Dict[str, Any]) -> None:
    """CHG-034（v1.0.3 / P-010）| `GET /healthz` 200 schema 必填 `{status, db}`，`db` 为 string。

    docs/13 §8.1.2 第 1 条（QA 必须同步的 3 处之一）：
    * 契约断言：`{"status","db"} <= set(required)` 且 `properties["db"]["type"] == "string"`，
      **不得**再断言 `required == ["status"]`；
    * `version` 保持 optional（实现在返回，为兼容不强制）；
    * 503 分支仍是统一 `Error{code,message,request_id}`，且**不含** `db`（依赖探针失败时不返回
      健康对象，而是整体 503）。
    """
    health = op(contract, "/healthz", "get")
    responses = health["responses"]
    assert "200" in responses and "503" in responses, "/healthz 应同时声明 200 与 503"

    body = schema_of(contract, responses["200"])
    required = set(body.get("required", []))
    assert {"status", "db"} <= required, (
        f"/healthz 200 的 required 必须含 status 与 db（v1.0.3 / CHG-034），实际 {sorted(required)}"
    )
    db = body.get("properties", {}).get("db", {})
    assert db.get("type") == "string", (
        f"/healthz 200.properties.db.type 应为 string（CHG-034），实际 {db.get('type')!r}"
    )
    assert body["properties"]["status"].get("type") == "string"
    assert "version" not in required, (
        "version 应保持 optional（CHG-034：实现在返回但契约不强制，向后兼容）"
    )
    if "version" in body.get("properties", {}):
        assert body["properties"]["version"].get("type") == "string"

    # 200 的 description 必须写死探针语义与 503 出口，避免实现只在字段上“看起来对”
    health_200_description = str(responses["200"].get("description", ""))
    for token in ("db", "SELECT 1", "503", "CHG-034"):
        assert token in health_200_description, (
            f"/healthz 200 description 未写死探针/503 口径：缺 {token}（CHG-034）"
        )

    # 503 分支：统一 Error 三字段，且没有 db（健康对象只在 200 出现）
    unavailable = schema_of(contract, responses["503"])
    assert set(unavailable.get("properties", {})) == {"code", "message", "request_id"}, (
        f"/healthz 503 必须是统一 Error 三字段（C-19），实际 {sorted(unavailable.get('properties', {}))}"
    )
    assert "db" not in unavailable.get("properties", {}), (
        "/healthz 503 不得携带 db 字段（P-010：探针失败整体 503，不返回健康对象）"
    )
    unavailable_description = str(resolve(contract, responses["503"]).get("description", ""))
    assert "INTERNAL_ERROR" in unavailable_description, (
        "/healthz 503 description 未写死 code=INTERNAL_ERROR（C-19/CHG-021）"
    )


def test_tc_ct_04_401_documented_everywhere(contract: Dict[str, Any]) -> None:
    """TC-CT-04 | AC-04：会话失效/账号停用后，所有受保护接口都必须有 401 出口。"""
    missing = [
        f"{method.upper()} {path}"
        for path, method, operation in iter_operations(contract)
        if (path, method) not in PUBLIC_OPERATIONS and "401" not in operation.get("responses", {})
    ]
    assert not missing, "受保护接口缺 401 响应：\n" + "\n".join(missing)


def test_tc_ct_05_question_explanation_required(contract: Dict[str, Any]) -> None:
    """TC-CT-05 | AC-05：新建题必须能落全“题干/选项/答案/解析”（C-10 已修复）。"""
    create = contract["components"]["schemas"]["QuestionCreate"]
    assert "explanation" in create.get("required", []), "QuestionCreate.required 缺 explanation"
    assert create["properties"]["explanation"].get("minLength") == 1, (
        "QuestionCreate.explanation 应限制 minLength=1（C-10）"
    )
    question = contract["components"]["schemas"]["Question"]
    assert "explanation" in question.get("required", []), "Question.required 缺 explanation"
    assert "解析" in question["properties"]["explanation"].get("description", ""), (
        "Question.explanation 未声明解析必填口径（C-10）"
    )


def test_tc_ct_06_import_result_contract(contract: Dict[str, Any]) -> None:
    """TC-CT-06 | AC-06：导入结果必须能表达“成功条数 + 失败行号与原因”。"""
    result = contract["components"]["schemas"]["ImportResult"]
    assert {"imported", "failed", "total"} <= set(result.get("required", []))
    assert result["properties"]["imported"]["type"] == "integer"
    failure = contract["components"]["schemas"]["ImportFailure"]
    assert {"row", "reason"} <= set(failure.get("required", []))
    assert failure["properties"]["row"]["type"] == "integer"
    # 部分成功必须仍走 200（不是 4xx）
    imports = op(contract, "/questions/import", "post")
    assert "200" in imports["responses"]
    # `failed[]` 必须引用 ImportFailure（$ref 名为 ImportFailure，大小写敏感，不能用 "failed" 子串判断）
    failed_items = result["properties"]["failed"]["items"]
    assert failed_items.get("$ref", "").endswith("/ImportFailure"), (
        f"ImportResult.failed.items 应引用 ImportFailure，实际 {failed_items.get('$ref')!r}"
    )


def test_tc_ct_07_question_in_use_conflict(contract: Dict[str, Any]) -> None:
    """TC-CT-07 | AC-07：被引用题目删除 → 409，且语义为“被引用/软删除”。"""
    delete_q = op(contract, "/questions/{question_id}", "delete")
    assert "409" in delete_q["responses"]
    conflict = resolve(contract, delete_q["responses"]["409"])
    assert "引用" in conflict.get("description", ""), "409 描述未提到“被引用”"
    assert "软删除" in delete_q.get("summary", ""), "删除接口 summary 未声明软删除"


def test_tc_ct_08_import_dedupe_visible(contract: Dict[str, Any]) -> None:
    """TC-CT-08 | AC-08：第二次导入必须能从响应看出“被去重”而不是“全失败”。

    v1.0.1 / C-11 的判定键统一为契约实际字段 ``skipped_duplicates``（CHG-011）；
    同时校验 required 与 ``total`` 口径，防止只加字段不定义统计语义。
    """
    result = contract["components"]["schemas"]["ImportResult"]
    props = set(result.get("properties", {}))
    assert "skipped_duplicates" in props, (
        f"ImportResult 无法表达去重跳过数（当前字段：{sorted(props)}）"
    )
    assert "skipped_duplicates" in result.get("required", []), (
        "ImportResult.skipped_duplicates 应为必填（C-11）"
    )
    skipped = result["properties"]["skipped_duplicates"]
    assert skipped.get("type") == "integer", "skipped_duplicates 应为整数计数"
    assert skipped.get("minimum") == 0, "skipped_duplicates 不应为负"
    description = str(result.get("description", ""))
    for token in ("total", "imported", "skipped_duplicates", "failed"):
        assert token in description, f"ImportResult.description 未定义 total 口径：缺 {token}"


def test_tc_ct_09_paper_points_constraints(contract: Dict[str, Any]) -> None:
    """TC-CT-09 | AC-09：分值必须 >0，且试卷总分为 100 的口径可断言（C-08 已修复）。

    v1.0.2 / CHG-025：单题上限由 **1000 收紧为 100**（`100 题 × 100 分 = 10000` 与
    `Paper.total_points.maximum` 自洽）；`total_points` 仍由服务端按 Σpoints 计算（CHG-026）。
    """
    pq = contract["components"]["schemas"]["PaperQuestion"]
    points = pq["properties"]["points"]
    assert points.get("exclusiveMinimum") == 0 or points.get("minimum", 0) > 0, "points 未约束 >0"
    assert points.get("maximum") == 100, (
        "points 上限应为 100（v1.0.2 / CHG-025；v1.0.1 的 1000 与 total_points 10000 不自洽）"
    )
    assert points.get("multipleOf") == 0.01, "points 应为两位小数（C-08/C-14）"

    paper = contract["components"]["schemas"]["Paper"]
    total = paper["properties"]["total_points"]
    assert total.get("multipleOf") == 0.01, "Paper.total_points 应为两位小数（C-08）"
    assert total.get("minimum") == 0, "Paper.total_points 应有下界 0（C-08）"
    assert total.get("maximum") == 10000, "Paper.total_points 上限应为 10000（CHG-025）"
    # 上下限自洽（CHG-025）：单题上限 × 组卷题数上限 == 总分上限
    questions = contract["components"]["schemas"]["PaperCreate"]["properties"]["questions"]
    assert questions.get("maxItems") == 100, "PaperCreate.questions.maxItems 应为 100（CHG-026）"
    assert points["maximum"] * questions["maxItems"] == total["maximum"], (
        "单题上限 × 题数上限 必须等于 total_points 上限（v1.0.2 / CHG-025 自洽性）"
    )

    paper_create = contract["components"]["schemas"]["PaperCreate"]
    text = str(paper_create.get("description", "")) + str(pq.get("description", ""))
    assert "100" in text, "契约未写明组卷合计 100 分的校验口径"
    assert "total_points" in text and "Σ" in text, (
        "PaperCreate 未写明 total_points = Σ questions[].points（C-08）"
    )
    assert "422" in text, "组卷合计不一致应明确为 422（C-08）"


def test_tc_ct_10_paper_frozen(contract: Dict[str, Any]) -> None:
    """TC-CT-10 | AC-10：已冻结试卷再改 → 409，且状态字段可表达 frozen。"""
    put_paper = op(contract, "/papers/{paper_id}", "put")
    assert "409" in put_paper["responses"]
    paper = contract["components"]["schemas"]["Paper"]
    status = paper["properties"]["status"]
    assert set(status.get("enum", [])) == {"draft", "frozen"}
    assert paper["properties"]["total_points"]["type"] == "number"
    assert "questions" in paper.get("required", []), "Paper 未强制返回明细，无法核对分值"


def test_tc_ct_11_exam_not_started(contract: Dict[str, Any]) -> None:
    """TC-CT-11 | AC-11/13/14：会话创建返回码表 201/200/409/404 与 code 写死（C-22）。"""
    start = op(contract, "/exams/{exam_id}/sessions", "post")
    responses = start["responses"]
    assert {"200", "201", "404", "409"} <= set(responses), (
        f"POST /exams/{{exam_id}}/sessions 缺返回码（当前 {sorted(responses)}）"
    )
    description = str(start.get("description", ""))
    for token in ("201", "200", "409", "404", "EXAM_NOT_STARTED", "EXAM_EXPIRED", "NOT_FOUND"):
        assert token in description, f"会话创建 description 未写死码表：缺 {token}"
    assert "幂等" in description, "会话创建 description 未声明幂等"
    # 201 新建与 200 恢复必须是同一个 SessionState（含 answers，C-01）
    assert schema_of(contract, responses["201"]) == schema_of(contract, responses["200"]), (
        "201/200 应返回同一个 SessionState schema"
    )
    assert "新建" in str(responses["201"].get("description", ""))
    assert "恢复" in str(responses["200"].get("description", ""))

    conflict = resolve(contract, responses["409"])
    conflict_desc = str(conflict.get("description", ""))
    assert "未开始" in conflict_desc, "Conflict 描述未覆盖“考试未开始”"
    assert "EXAM_EXPIRED" in conflict_desc, "Conflict 描述未覆盖“已截止/已到期”"
    # 未发布/不存在统一 404，不新增 EXAM_NOT_PUBLISHED（C-22 替代方案）
    not_found = resolve(contract, responses["404"])
    assert "404" in str(not_found.get("description", ""))
    assert "防枚举" in description


def test_tc_ct_12_session_answers_resume(contract: Dict[str, Any]) -> None:
    """TC-CT-12 | AC-12：恢复会话必须能取回服务端已保存的答案（C-01 已修复）。"""
    state_schema = contract["components"]["schemas"]["SessionState"]
    state = state_schema["properties"]
    assert "answers" in state, "SessionState 无 answers，断线恢复不可证（C-01）"
    assert "answers" in state_schema.get("required", []), "SessionState.answers 应为必填（C-01）"
    assert state["answers"]["items"].get("$ref", "").endswith("/AnswerItem")
    assert "submitted_at" in state, "SessionState 缺 submitted_at（C-01）"

    # 刷新/恢复两个入口都返回 SessionState，且 answers 必填
    for path, method in (
        ("/sessions/{session_id}", "get"),
        ("/exams/{exam_id}/sessions", "post"),
    ):
        response = op(contract, path, method)["responses"]["200"]
        body = schema_of(contract, response)
        assert "answers" in body.get("required", []), (
            f"{method.upper()} {path} 的 SessionState 未强制返回 answers（C-01）"
        )
    answer_item = contract["components"]["schemas"]["AnswerItem"]
    assert {"question_id", "selected"} <= set(answer_item.get("required", []))


def test_tc_ct_13_submit_idempotent(contract: Dict[str, Any]) -> None:
    """TC-CT-13 | AC-13：重复交卷返回 200 同一成绩（幂等），submit 不再声明 409（C-15）。

    v1.0.2 / CHG-028：`replayed` 已由 optional 提为 required，required 集合为 5 字段
    （原「`replayed` 应保持 optional」的断言随 CHG-002 口径一并删除）。
    """
    submit = op(contract, "/sessions/{session_id}/submit", "post")
    assert "200" in submit["responses"]
    assert "409" not in submit["responses"], "submit 不应再声明 409（C-15：重复提交也是 200）"
    assert "幂等" in submit.get("summary", ""), "submit summary 未声明幂等"
    description = str(submit.get("description", ""))
    assert "不存在 409" in description, "submit description 未写死“只有 200”（C-15）"
    assert "replayed=true" in description, "submit description 未说明重复提交 replayed=true"

    result = contract["components"]["schemas"]["SubmitResult"]
    # v1.0.2 / CHG-028：replayed 由 optional 提为 required（服务端 200 必显式返回 true/false）
    assert set(result.get("required", [])) == {
        "session_id",
        "status",
        "score",
        "submitted_at",
        "replayed",
    }
    assert result["properties"]["score"]["type"] == "number"
    assert "replayed" in result["properties"], "SubmitResult 缺 replayed（C-15）"
    assert result["properties"]["replayed"].get("type") == "boolean"

    conflict = resolve(contract, contract["components"]["responses"]["Conflict"])
    assert "交卷接口不再使用 409" in str(conflict.get("description", "")), (
        "Conflict 描述未声明交卷不再使用 409（C-15）"
    )


def test_tc_ct_14_expired_autosubmit(contract: Dict[str, Any]) -> None:
    """TC-CT-14 | AC-14：保存 409，但 GET /sessions 对过期会话返回 200+expired+score（C-13）。"""
    save = op(contract, "/sessions/{session_id}/answers", "put")
    assert "409" in save["responses"]
    conflict = resolve(contract, save["responses"]["409"])
    assert "已截止" in conflict.get("description", "")
    assert "EXAM_EXPIRED" in str(conflict.get("description", ""))
    # C-13：错误体不扩字段，409 仍是统一 Error 三字段
    error = schema_of(contract, save["responses"]["409"])
    assert set(error.get("properties", {})) == {"code", "message", "request_id"}, (
        "EXAM_EXPIRED 错误体被扩字段（C-13 要求保持统一 Error）"
    )

    # 到期可观测：GET /sessions/{id} 返回 200（不是 409）+ status=expired + score/submitted_at
    get_session = op(contract, "/sessions/{session_id}", "get")
    assert "409" not in get_session["responses"], "GET /sessions 不应以 409 表达到期（C-13）"
    description = str(get_session.get("description", ""))
    for token in ("expired", "score", "submitted_at", "200", "不返回 409"):
        assert token in description, f"GET /sessions 未写死到期可观测语义：缺 {token}"
    state = contract["components"]["schemas"]["SessionState"]
    assert "expired" in state["properties"]["status"].get("enum", [])
    assert state["properties"]["score"].get("type") == ["number", "null"], (
        "SessionState.score 应为 number|null，以表达 in_progress 无成绩（C-13）"
    )
    assert state["properties"]["submitted_at"].get("type") == ["string", "null"]

    submit_result = contract["components"]["schemas"]["SubmitResult"]
    assert "expired" in submit_result["properties"]["status"].get("enum", [])


def test_tc_ct_15_client_seq_monotonic(contract: Dict[str, Any]) -> None:
    """TC-CT-15 | AC-15：保存带单调 client_seq，旧 seq 返回 200 + applied=false（C-16）。

    v1.0.2 / CHG-028：`applied` 已由 optional 提为 required（服务端 200 必显式返回 true/false）。
    """
    save = op(contract, "/sessions/{session_id}/answers", "put")
    description = str(save.get("summary", "")) + str(save.get("description", ""))
    assert "client_seq" in description
    req = contract["components"]["schemas"]["AnswerSaveRequest"]
    assert {"client_seq", "answers"} <= set(req.get("required", []))
    assert req["properties"]["client_seq"]["type"] == "integer"
    assert req["properties"]["client_seq"]["minimum"] == 1
    resp = contract["components"]["schemas"]["AnswerSaveResponse"]
    assert {"saved_seq", "saved_at", "applied"} <= set(resp.get("required", [])), (
        "AnswerSaveResponse.required 缺 applied（v1.0.2 / CHG-028）"
    )
    assert "applied" in resp["properties"], "AnswerSaveResponse 缺 applied（C-16）"
    assert resp["properties"]["applied"].get("type") == "boolean"
    assert "applied=false" in description, "保存接口 description 未写明 applied=false 语义（C-16）"


def test_tc_ct_16_scoring_output_fields(contract: Dict[str, Any]) -> None:
    """TC-CT-16 | AC-16：逐题结果必须能表达对错与得分（单选/判断判分可证）。"""
    item = contract["components"]["schemas"]["ResultItem"]
    assert {"is_correct", "earned_points", "points"} <= set(item.get("required", []))
    assert item["properties"]["is_correct"]["type"] == "boolean"
    assert item["properties"]["earned_points"]["type"] == "number"
    detail = contract["components"]["schemas"]["ResultDetail"]
    assert {"score", "total_points", "items"} <= set(detail.get("required", []))


def test_tc_ct_17_multiple_partial_rule(contract: Dict[str, Any]) -> None:
    """TC-CT-17 | AC-17：多选两档给分规则必须在试卷明细上可配置。"""
    pq = contract["components"]["schemas"]["PaperQuestion"]
    rule = pq["properties"]["partial_rule"]
    assert set(rule.get("enum", [])) == {"all_or_nothing", "partial"}
    result = contract["components"]["schemas"]["ResultItem"]
    assert "earned_points" in result["properties"], "无 earned_points，部分给分无法体现"


def test_tc_ct_19_not_submitted(contract: Dict[str, Any]) -> None:
    """TC-CT-19 | AC-19：未交卷请求 result → 409。"""
    result_op = op(contract, "/sessions/{session_id}/result", "get")
    assert "409" in result_op["responses"], "result 接口缺 409（NOT_SUBMITTED）"


def test_tc_ct_20_wrong_book_contract(contract: Dict[str, Any]) -> None:
    """TC-CT-20 | AC-20：错题本必须能表达按题聚合的 wrong_count。"""
    wrong = contract["components"]["schemas"]["WrongQuestion"]
    assert "wrong_count" in wrong.get("required", [])
    assert wrong["properties"]["wrong_count"]["type"] == "integer"
    page = contract["components"]["schemas"]["WrongQuestionPage"]
    # 列表项必须引用 WrongQuestion（$ref 值不含 "wrong_count"，按 $ref 指向判断再回查字段）
    # 分页容器统一为 {items: {type: array, items: {$ref: ...}}}，因此 $ref 在数组项上。
    items_ref = page["properties"]["items"].get("items", {}).get("$ref", "")
    assert items_ref.endswith("/WrongQuestion"), (
        f"WrongQuestionPage.items 应引用 WrongQuestion，实际 {items_ref!r}"
    )
    assert "wrong_count" in contract["components"]["schemas"]["WrongQuestion"].get(
        "properties", {}
    ), "WrongQuestion 缺 wrong_count"


def test_tc_ct_21_audit_role_gate(contract: Dict[str, Any]) -> None:
    """TC-CT-21 | AC-21：审计查询仅管理员，教师必须拿到 403。"""
    audit = op(contract, "/audit-logs", "get")
    assert "403" in audit["responses"], "审计接口缺 403（教师拒绝）"
    forbidden = resolve(contract, audit["responses"]["403"])
    assert "无权限" in forbidden.get("description", "")


def test_tc_ct_22_audit_fields_required(contract: Dict[str, Any]) -> None:
    """TC-CT-22 | AC-22：审计记录必须含 actor/action/target/ip/时间（C-05 已修复）。"""
    audit = contract["components"]["schemas"]["AuditLog"]
    required = set(audit.get("required", []))
    missing = {"actor_id", "ip", "action", "target_type", "target_id", "created_at"} - required
    assert not missing, f"AuditLog.required 缺 {sorted(missing)}（当前 {sorted(required)}）"
    assert "user_agent" in audit.get("properties", {}), "AuditLog 缺可选 user_agent（C-05）"
    assert "detail" in audit.get("properties", {}), "AuditLog 缺可选 detail（C-05）"
    audit_description = str(op(contract, "/audit-logs", "get").get("description", ""))
    assert "180" in audit_description, "审计查询未写死默认近 180 天（C-05）"
    assert "倒序" in audit_description, "审计查询未写死 created_at 倒序（C-05）"
    assert "只增不改" in audit_description, "审计查询未写死只增不改（C-05）"


def test_chg_035_audit_actor_id_nullable(contract: Dict[str, Any]) -> None:
    """CHG-035（v1.0.3 / P-011）| `AuditLog.actor_id` 可空但字段仍必填。

    docs/13 §8.1.2 第 2 条（QA 必须同步的 3 处之一）：字段必填（**总是出现**）≠ 值不可空。
    * 契约断言：`type` 为 `["integer","null"]`（或含 `"null"`），`required` **仍含** `actor_id`；
    * `ip` 仍为必填字符串，系统操作记 `"-"`（不是 null、不是空串）；
    * `AuditLog` / `AuditLogPage` 的 description 必须写死该口径，防止实现回潮成“必为整数”。
    """
    audit = contract["components"]["schemas"]["AuditLog"]
    actor = audit.get("properties", {}).get("actor_id", {})
    actor_type = actor.get("type")
    assert isinstance(actor_type, list) and set(actor_type) == {"integer", "null"}, (
        "AuditLog.actor_id.type 应为 [integer, \"null\"]（v1.0.3 / CHG-035：系统操作 actor_id=None），"
        f"实际 {actor_type!r}"
    )
    assert "actor_id" in audit.get("required", []), (
        "AuditLog.required 仍须含 actor_id（字段必填、值可为 null，二者不可混为一谈）"
    )

    ip = audit["properties"]["ip"]
    assert ip.get("type") == "string", "AuditLog.ip 必须是非空字符串类型（系统操作记 \"-\"，不是 null）"

    audit_description = str(audit.get("description", ""))
    for token in ("null", "系统操作", "CHG-035"):
        assert token in audit_description, (
            f"AuditLog.description 未写死 actor_id 可空/系统操作口径：缺 {token}（CHG-035）"
        )
    assert '"-"' in audit_description or "`-`" in audit_description, (
        'AuditLog.description 未写死系统操作 ip 固定为 "-"（CHG-035）'
    )
    assert '"-"' in str(ip.get("description", "")), (
        'AuditLog.ip.description 未说明系统操作固定记 "-"（CHG-035）'
    )

    page_description = str(
        contract["components"]["schemas"]["AuditLogPage"].get("description", "")
    )
    assert "null" in page_description and '"-"' in page_description, (
        "AuditLogPage.description 未同步 items[].actor_id 可空 / items[].ip 为 \"-\"（CHG-035）"
    )


def test_c21_response_objects_minimized(contract: Dict[str, Any]) -> None:
    """C-21（v1.0.1）| 关键响应对象 ``additionalProperties: false``，防止误带敏感字段。"""
    schemas = contract["components"]["schemas"]
    expected = ("User", "LoginResponse", "MeResponse", "SessionState", "ResultItem")
    for name in expected:
        schema = schemas[name]
        assert schema.get("additionalProperties") is False, (
            f"{name} 未设 additionalProperties: false（C-21）"
        )
        props = set(schema.get("properties", {}))
        assert "password_hash" not in props, f"{name} 暴露 password_hash（C-21）"
    # 会话/逐题结果不得携带他人数据字段
    for name in ("SessionState", "ResultItem"):
        props = set(schemas[name].get("properties", {}))
        assert not ({"username", "display_name", "role"} & props), (
            f"{name} 夹带用户字段（C-21）"
        )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
