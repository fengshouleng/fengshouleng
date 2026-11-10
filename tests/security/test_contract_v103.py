"""契约 v1.0.3 运行时黑盒断言（TASK-QA-002；docs/13-contract-changes.md §8.1.2 第 1–2 条）。

只读运行中的 API（不 import ``server/``），覆盖 v1.0.3 的两处**可观测字段**增量：

1. **CHG-034（P-010）`GET /healthz`**（§8.1.2 第 1 条）：
   200 且 ``status == "ok"``、``db == "ok"``；``version`` 若返回必须是字符串（契约保持 optional）。
   503 分支（依赖不可用 → 统一 ``Error`` 且**无** ``db``）无法在没有真实故障时黑盒触发，
   故默认 skip，需显式 ``EXAM_HEALTHZ_503=1``（先停 DB / 让迁移未就绪）再跑。
   契约侧的静态断言见
   ``tests/contract/test_openapi_valid.py::test_chg_034_healthz_db_required``。

2. **CHG-035（P-011）`GET /audit-logs`**（§8.1.2 第 2 条）：
   * 系统记录（CLI ``create-admin``、备份清理、定时任务、无请求上下文的动作）：
     ``actor_id is None``（键仍存在）且 ``ip == "-"``；
   * 用户记录：``actor_id`` 为整数、``ip`` 为非空字符串且不是 ``"-"``。

未设置 ``EXAM_API_URL`` 时整模块 skip；系统记录不存在时该用例 skip 并提示先执行 create-admin
（seed 流程见 ``tests/security/README.md`` §2.2）。
"""

from __future__ import annotations

import os
from datetime import timedelta
from typing import Any, Iterator

import pytest

from conftest import assert_json, iso, unique, utc_now

pytestmark = pytest.mark.skipif(
    not os.environ.get("EXAM_API_URL", "").strip(),
    reason="EXAM_API_URL 未设置：契约运行时断言需要运行中的 API（见 tests/security/README.md）",
)

#: 触发 503 分支需要人为制造依赖故障，默认不跑（避免把环境问题误判为缺陷）。
_TRUTHY = {"1", "true", "yes", "on"}


def _healthz_503_enabled() -> bool:
    return os.environ.get("EXAM_HEALTHZ_503", "").strip().lower() in _TRUTHY


def _iter_audit_items(
    admin: Any, *, max_pages: int = 5, page_size: int = 100
) -> Iterator[dict[str, Any]]:
    """按页遍历审计记录（默认近 180 天、created_at 倒序）。

    CLI 产生的系统记录通常早于本次 run 的用户记录，单页 100 条可能取不到，
    故最多翻 ``max_pages`` 页；取到 ``total`` 或无数据即停。
    """
    seen = 0
    for page in range(1, max_pages + 1):
        body = assert_json(
            admin.get("/audit-logs", params={"page": page, "page_size": page_size}),
            200,
            label=f"GET /audit-logs?page={page}（CHG-035 前置）",
        )
        items = list(body.get("items") or [])
        for item in items:
            yield item
        seen += len(items)
        total = int(body.get("total") or 0)
        if not items or seen >= total:
            return


# --------------------------------------------------------------------------- #
# CHG-034：GET /healthz 200 schema 必带 db，实现返回 "ok"
# --------------------------------------------------------------------------- #
def test_healthz_returns_db_ok(client_factory) -> None:
    """CHG-034：`GET /healthz` → 200 `{status:"ok", db:"ok"}`（不需要登录）。"""
    client = client_factory("healthz-db")
    body = assert_json(client.get("/healthz"), 200, label="GET /healthz（CHG-034）")
    assert body.get("status") == "ok", f"healthz.status 应为 ok，实际 {body.get('status')!r}"
    assert body.get("db") == "ok", (
        "healthz.db 应为 \"ok\"（v1.0.3 / CHG-034：实现执行 SELECT 1 探针后返回 \"ok\"），"
        f"实际 {body.get('db')!r}；body={body!r}"
    )
    if "version" in body:
        assert isinstance(body["version"], str) and body["version"].strip(), (
            f"healthz.version 若返回必须是非空字符串，实际 {body['version']!r}"
        )


def test_healthz_503_is_unified_error_without_db(client_factory) -> None:
    """CHG-034 / C-19：依赖不可用 → 503 统一 `Error`，且**不含** `db`。

    需要真实依赖故障（DB 不可连接 / 迁移未就绪），无法在正常栈上触发；
    设 ``EXAM_HEALTHZ_503=1`` 再跑（例如临时停掉 DB）。
    """
    if not _healthz_503_enabled():
        pytest.skip(
            "503 分支需要真实依赖故障：先停 DB / 让迁移未就绪，并设 EXAM_HEALTHZ_503=1 后重跑；"
            "契约侧静态断言见 test_openapi_valid.py::test_chg_034_healthz_db_required"
        )
    client = client_factory("healthz-503")
    response = client.get("/healthz")
    content_type = response.headers.get("content-type", "")
    assert "application/json" in content_type.lower(), (
        f"healthz 503 必须是 application/json，实际 {content_type!r}"
    )
    body = response.json()
    assert response.status_code == 503, (
        f"依赖不可用时 healthz 应为 503，实际 {response.status_code}；{client.describe(response)}"
    )
    assert set(body) == {"code", "message", "request_id"}, (
        "healthz 503 必须是统一 Error 三字段（C-19），"
        f"实际 {sorted(body)}；不得在 503 携带 db/status 等健康对象字段"
    )
    assert body["code"] == "INTERNAL_ERROR", (
        f"healthz 503 的 code 应为 INTERNAL_ERROR，实际 {body['code']!r}"
    )
    assert "db" not in body, "healthz 503 不得携带 db 字段（P-010：探针失败整体 503，不返回健康对象）"


# --------------------------------------------------------------------------- #
# CHG-035：AuditLog.actor_id 可空（系统记录），用户记录仍为整数
# --------------------------------------------------------------------------- #
def test_audit_system_record_has_null_actor_and_dash_ip(admin) -> None:
    """CHG-035：系统记录 `actor_id is None`（键存在）且 `ip == "-"`。

    系统记录的典型来源是 CLI ``create-admin``（``user.create``，无登录主体，
    由 ``app.audit.service.record`` 将 None ip 归一为 ``"-"``）。

    注意：``auth.login_failed`` 也会出现 ``actor_id=None``（账号不存在），但它属于
    “匿名失败尝试”而不是“系统操作”，其 ip 是真实来源地址——不能当作系统记录断言，
    否则用例会随数据顺序偶发失败（曾出现 flaky）。
    因此这里只筛 ``action == "user.create"`` 且 ``actor_id is None`` 的记录。
    一条都没有时 skip：该用例证明“可空可反序列化”，前提是环境里确实产生了系统记录。
    """
    system_items = [
        item
        for item in _iter_audit_items(admin)
        if item.get("action") == "user.create"
        and "actor_id" in item
        and item["actor_id"] is None
    ]
    if not system_items:
        pytest.skip(
            "审计日志中未发现 CLI 系统记录（action=user.create 且 actor_id=null）。"
            "请先按 README §2.2 执行 `python -m app.cli create-admin ...` 再跑本用例；"
            "用户记录断言不受影响（见 test_audit_user_record_has_integer_actor_and_real_ip）"
        )

    for item in system_items:
        assert "actor_id" in item, (
            "系统记录的 actor_id 键必须存在（字段必填、值可为 null），"
            f"实际缺键：{item!r}"
        )
        assert item["actor_id"] is None, (
            f"系统记录 actor_id 应为 null（CHG-035），实际 {item['actor_id']!r}；item={item!r}"
        )
        assert item.get("ip") == "-", (
            '系统操作 ip 必须固定记 "-"（不是 null、不是空串，CHG-035），'
            f"实际 {item.get('ip')!r}；item={item!r}"
        )
        assert isinstance(item.get("action"), str) and item["action"].strip(), (
            f"系统记录缺 action：{item!r}"
        )
        assert item.get("created_at"), f"系统记录缺 created_at：{item!r}"


def test_audit_user_record_has_integer_actor_and_real_ip(admin, teacher) -> None:
    """CHG-035：用户记录 `actor_id` 为整数、`ip` 为非空字符串且不是 ``"-"``。

    以教师建题（``question.create``）为触发点，按 ``actor_id`` 过滤后核对同一 target_id，
    避免误取其他 run 的记录。
    """
    payload = {
        "type": "single",
        "stem": unique("qa-sec-audit-v103-stem"),
        "options": [{"key": "A", "text": "A"}, {"key": "B", "text": "B"}],
        "answer": ["A"],
        "explanation": "CHG-035 审计用例解析",
        "difficulty": 1,
        "tags": ["qa-sec"],
    }
    created = assert_json(
        teacher.post("/questions", json_body=payload),
        201,
        label="POST /questions（CHG-035 前置用户写）",
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
        label="GET /audit-logs?action=question.create（CHG-035）",
    )
    matching = [
        item
        for item in logs.get("items") or []
        if str(item.get("target_id")) == str(created["id"])
    ]
    assert matching, (
        f"审计日志未记录本次 question.create（target_id={created['id']}）"
    )
    item = matching[0]
    actor_id = item.get("actor_id")
    assert isinstance(actor_id, int) and not isinstance(actor_id, bool), (
        f"用户记录的 actor_id 必须是整数（CHG-035），实际 {actor_id!r}；item={item!r}"
    )
    assert actor_id == teacher.user["id"], (
        f"用户记录的 actor_id 应为触发者 {teacher.user['id']}，实际 {actor_id!r}"
    )
    ip = item.get("ip")
    assert isinstance(ip, str) and ip.strip(), (
        f"用户记录的 ip 必须是非空字符串（AC-22 / CHG-035），实际 {ip!r}；item={item!r}"
    )
    assert ip != "-", (
        '用户记录不得使用系统记录的占位 ip "-"（CHG-035：只有系统操作记 "-"），'
        f"实际 {ip!r}"
    )
