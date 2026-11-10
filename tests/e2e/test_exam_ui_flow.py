"""tests/e2e/test_exam_ui_flow.py — 关键链路 E2E（TASK-QA-001）

覆盖 4 条链路（每条独立用例、独立 BrowserContext、独立数据，失败互不影响）：

- ``T-E2E-01`` 教师：登录 → 建 3 题（单选/多选/判断）→ 组卷（40+30+30=100）→ 发布考试
  （时间窗覆盖当前）。用例 ID 对齐 ``docs/60-test-plan.md`` 的 ``TC-E2E-01/02``。
- ``T-E2E-02`` 学生：登录 → 我的考试 → 开始 → 作答 → **刷新回填** → 交卷 → 成绩
  （对齐 ``TC-E2E-03/05``）。
- ``T-E2E-03`` 幂等：重复开始会话（同一 ``session_id``）、重复交卷（同分、``replayed=true``、
  不新增会话）（对齐 ``TC-E2E-05``）。
- ``T-E2E-04`` 到期：``04a`` 倒计时归零自动交卷（Playwright Clock 快进，不真实等待）；
  ``04b`` 服务端结算后保存 409 ``EXAM_EXPIRED`` → 前端跳成绩页；``04c`` 真实等待
  deadline（默认 xfail，``EXAM_E2E_WAIT_EXPIRY=1`` 才执行）。

默认整模块 skip；``EXAM_E2E=1`` 才执行（见 ``tests/e2e/README.md``）：

    EXAM_E2E=1 python -m pytest tests/e2e -q
    EXAM_E2E=1 python -m pytest tests/e2e -q -k t_e2e_02
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterator, Sequence

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

pytest.importorskip("httpx", reason="缺少 httpx：pip install -r tests/requirements.txt")
_sync_api = pytest.importorskip(
    "playwright.sync_api",
    reason="缺少 playwright：pip install -r tests/requirements.txt && python -m playwright install chromium",
)
_expect = _sync_api.expect

import e2e_support as s  # noqa: E402

pytestmark = pytest.mark.skipif(
    os.environ.get("EXAM_E2E") != "1",
    reason="E2E（Playwright）：需要真实栈；设置 EXAM_E2E=1 启用（见 tests/e2e/README.md）",
)


# --------------------------------------------------------------------------- #
# 夹具：账号与前置数据（全部经契约 API 构造，与 UI 创建路径解耦）
# --------------------------------------------------------------------------- #
@pytest.fixture()
def teacher_api() -> Iterator[s.Api]:
    api = s.Api()
    api.login(s.TEACHER_USERNAME, s.TEACHER_PASSWORD)
    try:
        yield api
    finally:
        api.close()


@pytest.fixture()
def student_api() -> Iterator[s.Api]:
    api = s.Api()
    api.login(s.STUDENT_USERNAME, s.STUDENT_PASSWORD)
    try:
        yield api
    finally:
        api.close()


@pytest.fixture()
def make_exam(teacher_api: s.Api, run_id: str):
    """每次调用创建一套独立数据：3 题 + 100 分试卷 + 已发布考试。"""

    def _make(
        *,
        suffix: str,
        duration_min: int = 30,
        start_offset_min: int = -1,
        end_offset_min: int = 120,
    ) -> dict[str, Any]:
        tag = s.unique_tag(run_id, suffix)
        questions = s.create_questions(teacher_api, tag)
        paper = s.create_paper(
            teacher_api, f"[e2e-{tag}] 三题组卷（100 分）", questions
        )
        exam = s.create_exam(
            teacher_api,
            paper["id"],
            f"[e2e-{tag}] 考试",
            duration_min=duration_min,
            start_offset_min=start_offset_min,
            end_offset_min=end_offset_min,
        )
        s.assert_window_covers_now(exam)
        return {"tag": tag, "questions": questions, "paper": paper, "exam": exam}

    return _make


# --------------------------------------------------------------------------- #
# T-E2E-01 教师：登录 → 建 3 题 → 组卷 100 → 发布考试
# --------------------------------------------------------------------------- #
def test_t_e2e_01_teacher_creates_questions_builds_paper_and_publishes_exam(
    page: Any,
    teacher_api: s.Api,
    run_id: str,
    test_artifact_dir: Path,
) -> None:
    """T-E2E-01 | AC-01/05/09/10：教师 UI 建题 + 组卷（100）→ 发布考试并冻结试卷。

    说明（写入 open_questions）：教师端「考试管理」目前是占位页（``/exams`` →
    ``page-exams``，代码注释「待 TASK-FE-003 接入」），没有 ``exams-publish`` 等
    data-testid，故「发布考试」一步通过契约 ``POST /exams`` 完成；建题与组卷全部走 UI。
    """
    tag = s.unique_tag(run_id, "01")
    s.login_ui(page, s.TEACHER_USERNAME, s.TEACHER_PASSWORD, "/questions")
    _expect(page.locator('[data-testid="page-questions"]')).to_be_visible()

    # 1) UI 新建 3 题（单选 / 多选 / 判断，均含解析）
    for index, spec in enumerate(s.UI_QUESTION_SPECS, start=1):
        s.create_question_via_ui(page, spec, tag, index)
    s.screenshot(page, test_artifact_dir, "01-questions-created.png")

    questions = s.list_questions(teacher_api, tag)
    assert len(questions) == 3, f"期望按关键词 {tag} 找到 3 题，实际 {len(questions)}"
    by_type: dict[str, dict[str, Any]] = {}
    for spec in s.UI_QUESTION_SPECS:
        matches = [item for item in questions if item["type"] == spec["type"]]
        assert len(matches) == 1, f"题型 {spec['type']} 应有且仅有 1 题，实际 {len(matches)}"
        question = matches[0]
        by_type[spec["type"]] = question
        assert [option["key"] for option in question["options"]] == [
            chr(ord("A") + offset) for offset in range(len(spec["options"]))
        ], "选项 key 必须为 A–D 连续编号（契约 Option.pattern）"
        assert question["answer"] == s.answer_keys(spec), "UI 保存的答案与勾选不一致"
        assert question["explanation"].strip(), "解析未持久化（AC-05 / C-10）"

    # 2) UI 组卷：3 题 × (40/30/30) = 100，保存后回到列表
    ordered: Sequence[dict[str, Any]] = [
        by_type["single"],
        by_type["multiple"],
        by_type["judge"],
    ]
    paper_title = f"[e2e-{tag}] 三题组卷（100 分）"
    s.create_paper_via_ui(
        page, paper_title, tag, [question["id"] for question in ordered]
    )
    paper = s.find_paper_by_title(teacher_api, paper_title)
    assert float(paper["total_points"]) == 100.0, (
        f"组卷总分应为 100，实际 {paper['total_points']}"
    )
    assert paper["status"] == "draft"
    _expect(page.locator(f'[data-testid="papers-row-{paper["id"]}"]')).to_be_visible()
    _expect(page.locator(f'[data-testid="papers-total-{paper["id"]}"]')).to_contain_text("100")
    s.screenshot(page, test_artifact_dir, "02-paper-saved.png")

    # 3) 发布考试（时间窗覆盖当前）——契约接口，原因见 docstring
    now = s.utc_now()
    exam = s.create_exam(
        teacher_api,
        paper["id"],
        f"[e2e-{tag}] 考试",
        duration_min=30,
        start_offset_min=-1,
        end_offset_min=120,
    )
    start = s.parse_iso(exam["start_at"])
    end = s.parse_iso(exam["end_at"])
    assert start <= now <= end, f"考试时间窗未覆盖当前：{start} ~ {end}，now={now}"
    assert exam["status"] == "published"
    assert int(exam.get("snapshot_version") or 0) >= 1, "发布后缺少 snapshot_version（C-12）"

    # 4) 发布即冻结（AC-10 / CHG-027）：frozen_at 必返回，再改 → 409 PAPER_FROZEN
    frozen = s.expect_json(teacher_api.get(f"/papers/{paper['id']}"), 200, "GET /papers/{id}")
    assert frozen["status"] == "frozen", "发布考试后试卷应为 frozen"
    assert frozen.get("frozen_at"), "frozen 状态必须返回 frozen_at（CHG-027）"
    payload = {
        "title": paper_title,
        "questions": [
            {
                "question_id": question["id"],
                "order_no": index + 1,
                "points": point,
            }
            for index, (question, point) in enumerate(zip(ordered, s.PAPER_POINTS))
        ],
    }
    conflict = teacher_api.put(f"/papers/{paper['id']}", json=payload)
    assert conflict.status_code == 409, (
        f"冻结后修改应 409，实际 {conflict.status_code}: {conflict.text[:500]}"
    )
    assert conflict.json().get("code") == "PAPER_FROZEN"
    s.screenshot(page, test_artifact_dir, "03-exam-published.png")


# --------------------------------------------------------------------------- #
# T-E2E-02 学生：开始 → 作答 → 刷新回填 → 交卷 → 成绩
# --------------------------------------------------------------------------- #
def test_t_e2e_02_student_answers_refresh_restores_and_submits(
    page: Any,
    student_api: s.Api,
    make_exam: Any,
    test_artifact_dir: Path,
) -> None:
    """T-E2E-02 | AC-01/12/13/16/17：作答 → 刷新回填（C-01）→ 交卷 → 成绩 100。"""
    scenario = make_exam(suffix="02", duration_min=30)
    exam = scenario["exam"]
    questions = scenario["questions"]

    s.login_ui(page, s.STUDENT_USERNAME, s.STUDENT_PASSWORD, "/me/exams")
    s.open_exam_from_my_exams(page, exam["id"])
    _expect(page.locator('[data-testid="exam-save-status"]')).to_be_visible()

    # 全部答对（100 分），作答后切题触发强制保存（不必等 30s debounce）
    for question in questions:
        s.apply_answer(page, question)
    s.force_save(page, questions[0]["id"])
    s.screenshot(page, test_artifact_dir, "01-answers-saved.png")

    # 刷新页面：服务端 answers 回填（C-01），刷新后仍应处于作答页且选项已选中
    page.reload(wait_until="domcontentloaded")
    _expect(page.locator('[data-testid="exam-page"]')).to_be_visible()
    _expect(page.locator('[data-testid="exam-countdown"]')).to_be_visible()
    for question in questions:
        for key in question["answer"]:
            _expect(
                page.locator(f'[data-testid="answer-{question["id"]}-{key}"]')
            ).to_be_checked()
    s.screenshot(page, test_artifact_dir, "02-answers-restored.png")

    # 交卷 → 成绩页
    page.locator('[data-testid="exam-submit"]').click()
    _expect(page.locator('[data-testid="exam-confirm-submit"]')).to_be_visible()
    page.locator('[data-testid="exam-confirm-submit"]').click()
    s.wait_for_result(page)

    score = s.read_score(page)
    assert score == 100.0, f"全对成绩应为 100，实际 {score}"
    for question in questions:
        _expect(page.locator(f'[data-testid="result-item-{question["id"]}"]')).to_be_visible()
        _expect(
            page.locator(f'[data-testid="result-correct-{question["id"]}"]')
        ).to_contain_text("回答正确")

    # 旁证：我的成绩列表出现该 session（C-06 带 session_id）
    session_id = s.session_id_from_url(page.url)
    results = s.expect_json(student_api.get("/me/results"), 200, "GET /me/results")
    rows = [row for row in results["items"] if row["session_id"] == session_id]
    assert rows, "我的成绩列表未出现刚交卷的 session（C-06）"
    assert float(rows[0]["score"]) == 100.0
    s.screenshot(page, test_artifact_dir, "03-result.png")


# --------------------------------------------------------------------------- #
# T-E2E-03 幂等：重复开始会话 + 重复交卷
# --------------------------------------------------------------------------- #
def test_t_e2e_03_idempotent_start_and_submit(
    page: Any,
    teacher_api: s.Api,
    make_exam: Any,
    test_artifact_dir: Path,
) -> None:
    """T-E2E-03 | AC-13/15：重复开始复用同一 session；重复交卷同分、replayed=true。"""
    scenario = make_exam(suffix="03", duration_min=30)
    exam = scenario["exam"]
    questions = scenario["questions"]

    s.login_ui(page, s.STUDENT_USERNAME, s.STUDENT_PASSWORD, "/me/exams")
    s.open_exam_from_my_exams(page, exam["id"])
    s.apply_answer(page, questions[0])
    s.force_save(page, questions[0]["id"])

    page.locator('[data-testid="exam-submit"]').click()
    _expect(page.locator('[data-testid="exam-confirm-submit"]')).to_be_visible()
    page.locator('[data-testid="exam-confirm-submit"]').click()
    s.wait_for_result(page)

    session_id = s.session_id_from_url(page.url)
    first_score = s.read_score(page)
    s.screenshot(page, test_artifact_dir, "01-first-submit.png")

    # 1) 重复开始：再次进入考试应 200 恢复同一会话 → 直接跳成绩页（同一 session_id）
    page.goto(f"{s.BASE_URL}/exams/{exam['id']}", wait_until="domcontentloaded")
    page.wait_for_url(re.compile(rf".*/results/{session_id}$"), timeout=25_000)
    _expect(page.locator('[data-testid="result-score"]')).to_be_visible()
    s.wait_toast(page, "已交卷")
    assert s.read_score(page) == first_score, "重复开始会话后成绩发生变化"

    # 2) 重复交卷：契约 C-15 → 恒 200、replayed=true、成绩不变
    duplicate = s.page_api(page, "POST", f"/api/sessions/{session_id}/submit")
    assert duplicate["status"] == 200, f"重复交卷应 200，实际 {duplicate}"
    body = duplicate["body"]
    assert body is not None, "重复交卷响应体为空"
    assert body["session_id"] == session_id
    assert body["status"] == "submitted"
    assert body["replayed"] is True, "重复交卷必须回 replayed=true（C-15 / CHG-028）"
    assert float(body["score"]) == first_score, "重复交卷产生了不同成绩（AC-13）"

    # 3) 不新增会话/成绩：教师汇总 total_sessions 仍为 1
    summary = s.expect_json(
        teacher_api.get(f"/exams/{exam['id']}/results"), 200, "GET /exams/{id}/results"
    )
    assert summary["total_sessions"] == 1, (
        f"重复开始/交卷新增了会话：total_sessions={summary['total_sessions']}"
    )
    assert summary["submitted"] == 1
    s.screenshot(page, test_artifact_dir, "02-idempotent.png")


# --------------------------------------------------------------------------- #
# T-E2E-04 到期：自动交卷 / 409 后跳成绩页
# --------------------------------------------------------------------------- #
def test_t_e2e_04a_countdown_auto_submit(
    page: Any,
    student_api: s.Api,
    make_exam: Any,
    test_artifact_dir: Path,
) -> None:
    """T-E2E-04a | AC-13/14：倒计时归零自动交卷（Playwright Clock 快进，不真实等待）。

    公开 API 无法构造 <10 分钟的 deadline（``duration_min ≥ 10`` 且不超过时间窗），
    因此用浏览器时钟快进触发前端 ``deadline_at`` 归零；服务端随后按 200 幂等交卷，
    用例断言“没有任何人工点击交卷”也到达成绩页。
    """
    clock = getattr(page, "clock", None)
    if clock is None:  # pragma: no cover - 依赖 Playwright 版本
        pytest.xfail("当前 Playwright 无 Clock API（需 ≥1.45），无法快进倒计时")
    clock.install()

    scenario = make_exam(suffix="04a", duration_min=10)
    exam = scenario["exam"]
    questions = scenario["questions"]

    s.login_ui(page, s.STUDENT_USERNAME, s.STUDENT_PASSWORD, "/me/exams")
    s.open_exam_from_my_exams(page, exam["id"])
    s.apply_answer(page, questions[0])
    s.force_save(page, questions[1]["id"])
    s.screenshot(page, test_artifact_dir, "01-before-deadline.png")

    # 快进 11 分钟：1s 心跳把剩余时间推到 0 → useExamSession 自动 submit({auto:true})
    clock.fast_forward(11 * 60 * 1000)
    s.wait_for_result(page, timeout=30_000)

    session_id = s.session_id_from_url(page.url)
    state = s.expect_json(
        student_api.get(f"/sessions/{session_id}"), 200, "GET /sessions/{id}"
    )
    assert state["status"] in ("submitted", "expired"), (
        f"自动交卷后会话状态异常：{state['status']}"
    )
    assert state.get("submitted_at"), "自动交卷后缺少 submitted_at（CHG-028）"
    assert state.get("score") is not None, "自动交卷后缺少 score（C-13）"
    assert float(state["score"]) == s.read_score(page)
    s.screenshot(page, test_artifact_dir, "02-auto-submitted.png")


def test_t_e2e_04b_server_settlement_409_then_result_page(
    page: Any,
    teacher_api: s.Api,
    student_api: s.Api,
    make_exam: Any,
    test_artifact_dir: Path,
) -> None:
    """T-E2E-04b | AC-14：服务端结算后保存 → 409 ``EXAM_EXPIRED`` → 前端跳成绩页。

    以“教师关闭考试（``published → closed``）”触发服务端的到期结算代码路径
    （``_is_settled`` 同时覆盖 ``now ≥ deadline_at`` 与 ``exam.status=closed``）。
    已保存的 Q1（40 分）必须保留：到期自动判分后成绩 = 40。
    """
    scenario = make_exam(suffix="04b", duration_min=10)
    exam = scenario["exam"]
    questions = scenario["questions"]

    s.login_ui(page, s.STUDENT_USERNAME, s.STUDENT_PASSWORD, "/me/exams")
    s.open_exam_from_my_exams(page, exam["id"])

    # 先保存 Q1（40 分），再让考试结束
    s.apply_answer(page, questions[0])
    s.force_save(page, questions[1]["id"])

    closed = s.expect_json(
        teacher_api.patch(f"/exams/{exam['id']}", json={"status": "closed"}),
        200,
        "PATCH /exams/{id}",
    )
    assert closed["status"] == "closed"

    # 本地继续作答 Q2（未落库），随后切题触发保存 → 409 EXAM_EXPIRED → 刷新会话 → 跳成绩页
    s.apply_answer(page, questions[1])
    page.locator(f'[data-testid="exam-card-{questions[0]["id"]}"]').click()
    s.wait_for_result(page, timeout=30_000)
    s.wait_toast(page, "自动交卷")

    session_id = s.session_id_from_url(page.url)
    assert s.read_score(page) == 40.0, "到期结算应保留已保存的 Q1 得分（40）但丢弃未保存作答"

    # 契约断言：到期后保存 → 409 + EXAM_EXPIRED；会话 200 + expired + score
    rejected = student_api.put(
        f"/sessions/{session_id}/answers",
        json={
            "client_seq": 99,
            "answers": [{"question_id": questions[1]["id"], "selected": ["A"]}],
        },
    )
    assert rejected.status_code == 409, (
        f"到期后保存应 409，实际 {rejected.status_code}: {rejected.text[:500]}"
    )
    assert rejected.json().get("code") == "EXAM_EXPIRED", "到期保存错误码必须为 EXAM_EXPIRED"
    state = s.expect_json(
        student_api.get(f"/sessions/{session_id}"), 200, "GET /sessions/{id}"
    )
    assert state["status"] == "expired", f"到期会话状态应为 expired，实际 {state['status']}"
    assert state.get("score") is not None and state.get("submitted_at")
    s.screenshot(page, test_artifact_dir, "01-expired-result.png")


def test_t_e2e_04c_real_deadline_expiry(
    request: pytest.FixtureRequest,
    student_api: s.Api,
    make_exam: Any,
) -> None:
    """T-E2E-04c | AC-14：真实等待 deadline 到期后自动交卷（默认 xfail）。

    契约下限 ``duration_min ≥ 10``，真实等待约 10 分钟；仅在
    ``EXAM_E2E_WAIT_EXPIRY=1`` 时执行，避免拖慢 PR 门。默认 xfail 并写明原因，
    到期行为由 ``T-E2E-04a/04b`` 覆盖。
    """
    if os.environ.get("EXAM_E2E_WAIT_EXPIRY") != "1":
        pytest.xfail(
            "公开 API 无法构造 <10 分钟的 deadline（duration_min ≥ 10 且 duration ≤ 时间窗）；"
            "真实到期需等待约 10 分钟，默认不执行（T-E2E-04a/04b 已覆盖自动交卷与 409 跳转）"
        )

    scenario = make_exam(suffix="04c", duration_min=10)
    exam = scenario["exam"]
    started = student_api.post(f"/exams/{exam['id']}/sessions")
    assert started.status_code in (200, 201), started.text
    session = started.json()
    deadline = s.parse_iso(session["deadline_at"])
    wait_seconds = max(0.0, (deadline - s.utc_now()).total_seconds()) + 5
    time.sleep(min(wait_seconds, 700))

    state = s.expect_json(
        student_api.get(f"/sessions/{session['id']}"), 200, "GET /sessions/{id}"
    )
    assert state["status"] == "expired", f"到期后应为 expired，实际 {state['status']}"
    assert state.get("score") is not None and state.get("submitted_at")
    rejected = student_api.put(
        f"/sessions/{session['id']}/answers",
        json={
            "client_seq": 999,
            "answers": [{"question_id": scenario["questions"][0]["id"], "selected": ["A"]}],
        },
    )
    assert rejected.status_code == 409
    assert rejected.json().get("code") == "EXAM_EXPIRED"
