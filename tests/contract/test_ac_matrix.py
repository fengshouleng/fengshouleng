"""tests/contract/test_ac_matrix.py — AC 覆盖矩阵校验（TASK-QA-000）

门控作用（G2/G3/G4 都跑）
------------------------
* AC-01..AC-22 必须全部出现在 `docs/60-test-plan.md` 的机器可读矩阵里，缺一条即 fail；
* 每条 AC 至少映射 1 个用例；用例 ID 全局唯一、层级/依赖/可自动化/证据路径齐全；
* 标记 `implemented` 或 `skeleton` 的用例，必须在 `tests/` 里有实体（docstring 含该 ID），
  防止“计划里写了、代码里没有”的假覆盖；
* markdown 映射表与 YAML 矩阵必须一致（人读的表与机器读的块不能漂移）。

本文件本身不写任何用例 ID 字面量，避免自我满足（扫描时排除自身）。
执行：``python -m pytest tests/contract -q``（CI）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAN_PATH = REPO_ROOT / "docs" / "60-test-plan.md"
TESTS_ROOT = REPO_ROOT / "tests"
BEGIN_MARK = "<!-- AC-MATRIX:BEGIN -->"
END_MARK = "<!-- AC-MATRIX:END -->"

AC_IDS: List[str] = [f"AC-{i:02d}" for i in range(1, 23)]
CASE_TOKEN_RE = re.compile(r"\bTC-[A-Z0-9]+-\d{2,3}\b")
TABLE_ROW_RE = re.compile(r"^\|\s*(AC-\d{2})\s*\|")
VALID_LAYERS = {"契约", "接口", "E2E", "安全", "性能"}
VALID_STATUS = {"implemented", "skeleton", "planned"}
CASE_FIELDS = {"id", "layer", "depends_on", "automated", "status", "evidence"}


# --------------------------------------------------------------------------- #
# 解析工具
# --------------------------------------------------------------------------- #
def _plan_text() -> str:
    if not PLAN_PATH.is_file():
        pytest.fail(f"测试计划缺失：{PLAN_PATH}")
    return PLAN_PATH.read_text(encoding="utf-8")


def _matrix_payload(text: str) -> Dict[str, Any]:
    if BEGIN_MARK not in text or END_MARK not in text:
        pytest.fail(f"docs/60-test-plan.md 缺少 AC 矩阵标记 {BEGIN_MARK} / {END_MARK}")
    body = text.split(BEGIN_MARK, 1)[1].split(END_MARK, 1)[0]
    lines = body.strip("\n").splitlines()
    # 去掉 ```yaml / ``` 围栏
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    while lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    if yaml is None:  # pragma: no cover
        pytest.fail("缺少 PyYAML：`pip install -r tests/requirements.txt`")
    try:
        payload = yaml.safe_load("\n".join(lines))
    except yaml.YAMLError as exc:  # pragma: no cover
        pytest.fail(f"AC 矩阵 YAML 解析失败：{exc}")
    if not isinstance(payload, dict):
        pytest.fail("AC 矩阵块不是 YAML 映射")
    return payload


def _cases(matrix: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    """展开 (ac, case) 对，便于全局校验。"""
    out: List[Tuple[str, Dict[str, Any]]] = []
    for row in matrix.get("ac_matrix", []):
        ac = row.get("ac", "?")
        for case in row.get("cases", []) or []:
            out.append((ac, case))
    return out


def _scan_test_tokens() -> Dict[str, List[str]]:
    """扫描 tests/ 下所有 .py 的 docstring/注释里出现的用例 ID（排除本文件）。"""
    registry: Dict[str, List[str]] = {}
    self_path = Path(__file__).resolve()
    for path in sorted(TESTS_ROOT.rglob("*.py")):
        if path.resolve() == self_path:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):  # pragma: no cover
            continue
        for token in CASE_TOKEN_RE.findall(text):
            registry.setdefault(token, []).append(str(path.relative_to(REPO_ROOT)))
    return registry


def _table_rows(text: str) -> List[Tuple[str, str]]:
    """解析 §4.1 的 markdown 表：返回 [(AC, 用例 ID 单元格)]。"""
    rows: List[Tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not TABLE_ROW_RE.match(line):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        rows.append((cells[0], cells[1]))
    return rows


# --------------------------------------------------------------------------- #
# 用例
# --------------------------------------------------------------------------- #
def test_plan_document_exists() -> None:
    text = _plan_text()
    assert len(text) > 4000, "测试计划过短，疑似被截断"
    for keyword in ("测试环境", "数据", "G4", "E2E"):
        assert keyword in text, f"测试计划缺关键内容：{keyword}"


def test_permission_matrix_me_exams_aligned() -> None:
    """C-02：docs/12（v1.1 起、v1.3 现版）的 `/me/exams` 行必须与契约“仅 student”一致。"""
    matrix_path = REPO_ROOT / "docs" / "12-permission-matrix.md"
    assert matrix_path.is_file(), f"权限矩阵缺失：{matrix_path}"
    text = matrix_path.read_text(encoding="utf-8")
    rows = [
        line.strip()
        for line in text.splitlines()
        if line.strip().startswith("|") and "GET /me/exams" in line
    ]
    assert rows, "docs/12 未登记 GET /me/exams（C-02）"
    cells = [cell.strip() for cell in rows[0].strip("|").split("|")]
    assert len(cells) >= 4, f"/me/exams 权限行格式异常：{rows[0]}"
    # 列顺序：接口 | student | teacher | admin
    assert "✅" in cells[1], "docs/12 未允许 student 调用 /me/exams（C-02）"
    assert "❌" in cells[2], "docs/12 未禁止 teacher 调用 /me/exams（C-02）"
    assert "❌" in cells[3], "docs/12 未禁止 admin 调用 /me/exams（C-02）"
    assert "教师调用 `/me/exams` → 403" in text, "docs/12 未写死教师调用 /me/exams → 403"


def test_ac_matrix_block_parses() -> None:
    text = _plan_text()
    matrix = _matrix_payload(text)
    assert matrix.get("ac_total") == 22, f"ac_total 应为 22，实际 {matrix.get('ac_total')}"
    rows = matrix.get("ac_matrix")
    assert isinstance(rows, list) and len(rows) == 22, f"ac_matrix 应有 22 行，实际 {len(rows or [])}"


def test_all_22_acs_mapped() -> None:
    matrix = _matrix_payload(_plan_text())
    declared = {row.get("ac") for row in matrix.get("ac_matrix", [])}
    missing = [ac for ac in AC_IDS if ac not in declared]
    extra = sorted(str(x) for x in declared - set(AC_IDS))
    assert not missing, f"以下 AC 未出现在 AC 矩阵中（必须映射用例）：{missing}"
    assert not extra, f"AC 矩阵出现未知 AC：{extra}"


def test_each_ac_has_at_least_one_case() -> None:
    matrix = _matrix_payload(_plan_text())
    empty = [
        row.get("ac")
        for row in matrix.get("ac_matrix", [])
        if not (row.get("cases") or [])
    ]
    assert not empty, f"以下 AC 没有任何用例映射：{empty}"


def test_case_entries_are_complete() -> None:
    matrix = _matrix_payload(_plan_text())
    problems: List[str] = []
    for ac, case in _cases(matrix):
        missing = CASE_FIELDS - set(case)
        if missing:
            problems.append(f"{ac} {case.get('id', '?')}: 缺字段 {sorted(missing)}")
            continue
        if case["layer"] not in VALID_LAYERS:
            problems.append(f"{ac} {case['id']}: 非法层级 {case['layer']}")
        if case["status"] not in VALID_STATUS:
            problems.append(f"{ac} {case['id']}: 非法状态 {case['status']}")
        if not isinstance(case["depends_on"], list) or not case["depends_on"]:
            problems.append(f"{ac} {case['id']}: depends_on 不能为空（须写明依赖任务卡）")
        if not case["evidence"]:
            problems.append(f"{ac} {case['id']}: evidence 不能为空")
        if not isinstance(case["automated"], bool):
            problems.append(f"{ac} {case['id']}: automated 应为 bool")
    assert not problems, "AC 矩阵条目不完整：\n" + "\n".join(problems)


def test_case_ids_unique_per_ac_and_consistent() -> None:
    """同一 AC 下用例 ID 不得重复；跨 AC 复用同一用例时层级/状态必须一致。"""
    matrix = _matrix_payload(_plan_text())
    seen: Dict[Tuple[str, str], str] = {}
    attrs: Dict[str, Tuple[str, str]] = {}
    problems: List[str] = []
    for ac, case in _cases(matrix):
        key = (ac, str(case.get("id")))
        if key in seen:
            problems.append(f"{ac}: 用例 {case.get('id')} 重复登记")
        seen[key] = ac
        signature = (str(case.get("layer")), str(case.get("status")))
        if case.get("id") in attrs and attrs[case["id"]] != signature:
            problems.append(
                f"{case['id']}: 跨 AC 的层级/状态不一致 {attrs[case['id']]} vs {signature}"
            )
        attrs[str(case.get("id"))] = signature
    assert not problems, "用例登记冲突：\n" + "\n".join(problems)


def test_primary_case_belongs_to_its_ac() -> None:
    matrix = _matrix_payload(_plan_text())
    problems: List[str] = []
    for row in matrix.get("ac_matrix", []):
        primary = row.get("primary")
        ids = {case.get("id") for case in row.get("cases", []) or []}
        if not primary:
            problems.append(f"{row.get('ac')}: 缺 primary 用例")
        elif primary not in ids:
            problems.append(f"{row.get('ac')}: primary={primary} 不在 cases={sorted(ids)}")
    assert not problems, "\n".join(problems)


def test_implemented_cases_exist_in_tests() -> None:
    """计划与代码一致性：implemented / skeleton 的用例必须能在 tests/ 里找到实体。"""
    matrix = _matrix_payload(_plan_text())
    registry = _scan_test_tokens()
    assert registry, "tests/ 下没有扫描到任何用例 ID，疑似用例缺少 docstring 标注"
    missing: List[str] = []
    for ac, case in _cases(matrix):
        if case.get("status") not in {"implemented", "skeleton"}:
            continue
        if case["id"] not in registry:
            missing.append(
                f"{case['id']}（{ac}, status={case['status']}）在 tests/ 中不存在——计划与代码漂移"
            )
    assert not missing, "\n".join(missing)


def test_markdown_table_matches_matrix() -> None:
    text = _plan_text()
    matrix = _matrix_payload(text)
    rows = _table_rows(text)
    table_acs = [ac for ac, _ in rows]
    matrix_acs = [row.get("ac") for row in matrix.get("ac_matrix", [])]

    assert len(rows) == 22, f"§4.1 映射表应有 22 行，实际 {len(rows)}"
    assert sorted(table_acs) == sorted(AC_IDS), (
        f"映射表 AC 不完整：缺 {sorted(set(AC_IDS) - set(table_acs))}，"
        f"多 {sorted(set(table_acs) - set(AC_IDS))}"
    )
    assert table_acs == matrix_acs, "markdown 表顺序与 YAML 矩阵顺序不一致（易造成人工误读）"

    by_ac = {row.get("ac"): {case.get("id") for case in row.get("cases", []) or []}
             for row in matrix.get("ac_matrix", [])}
    problems: List[str] = []
    for ac, case_cell in rows:
        listed = set(CASE_TOKEN_RE.findall(case_cell))
        if not listed:
            problems.append(f"{ac}: 用例 ID 列为空")
            continue
        unknown = listed - by_ac.get(ac, set())
        if unknown:
            problems.append(f"{ac}: 表里出现 YAML 未登记的用例 {sorted(unknown)}")
    assert not problems, "\n".join(problems)


def test_manual_cases_declare_reason() -> None:
    """不可自动化项必须写清人工判据（PRD §5 的首屏/备份演练在 §4.3 登记）。"""
    text = _plan_text()
    matrix = _matrix_payload(text)
    for ac, case in _cases(matrix):
        if case.get("automated") is False:
            assert case.get("note") or case.get("evidence"), f"{ac} {case['id']}: 人工用例缺判据"
    # 非功能层（§4.3）必须显式标出“否”，避免把人工项偷偷算成自动覆盖
    assert "TC-PERF-02" in text, "§4.3 未登记首屏人工用例"
    assert "人工" in text, "§4.3 未说明不可自动化的判据"


def test_gate_order_and_g4_criteria_documented() -> None:
    text = _plan_text()
    for heading in ("门控执行顺序", "G4 放行判据", "测试数据策略", "E2E 关键链路"):
        assert heading in text, f"测试计划缺章节：{heading}"
    for item in ("P95", "回滚", "备份恢复", "退出码", "缺陷单"):
        assert item in text, f"G4 判据缺要素：{item}"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
