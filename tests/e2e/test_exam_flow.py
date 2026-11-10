"""tests/e2e/test_exam_flow.py — E2E 关键链路骨架（TASK-QA-000 §5）

链路（tests/README.md 的 G4 必过路径）：
    TC-E2E-01 教师登录 → 建 3 题
    TC-E2E-02 组卷（合计 100）→ 发布考试（试卷冻结）
    TC-E2E-03 学生登录 → 开始/恢复会话 → 首次保存
    TC-E2E-04 断网 30s → 恢复 → 重放旧 seq → 继续保存
    TC-E2E-05 交卷 → 判分 → 成绩/错题本/教师汇总 → 重复交卷幂等

状态：**骨架（skeleton）**。依赖 TASK-BE-003（组卷）、TASK-BE-004（考试会话/自动保存/交卷）、
TASK-BE-005（判分与成绩），以及可运行的服务实例。这些卡尚未建卡/实现，因此默认整模块 skip：

    # 默认（CI 契约阶段）
    python -m pytest tests/e2e -q            # -> 5 skipped

    # G4 阶段（服务已起，base url 指向 nginx 的 /api）
    EXAM_E2E=1 EXAM_BASE_URL=http://127.0.0.1:8080/api python -m pytest tests/e2e -q -v

已知契约缺陷影响：TC-E2E-04 的“已保存答案可回读”依赖缺陷 C-01（契约无答案回读字段），
在 R2 决策前，本骨架只断言 saved_seq 单调与“重放不覆盖”，不断言服务端答案回读。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

# --------------------------------------------------------------------------- #
# 依赖与开关（必须在 import 其它模块前完成 sys.path 修补）
# --------------------------------------------------------------------------- #
_TESTS_DIR = Path(__file__).resolve().parents[1]
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

httpx = pytest.importorskip("httpx", reason="缺少 httpx：pip install -r tests/requirements.txt")
factories = pytest.importorskip("factories", reason="tests/factories.py 不可导入")

BASE_URL = os.environ.get("EXAM_BASE_URL", "http://127.0.0.1:8000/api").rstrip("/")
OFFLINE_SECONDS = int(os.environ.get("EXAM_OFFLINE_SECONDS", "30"))  # 断网时长（仅记录用途）

# 账号必须来自**已存在的 seed 数据**（seed-demo / create-admin），不能用 factory 随机生成：
# `build_exam_scenario()` 只生成请求体，不会去 API 建账号。2026-11-11 之前这里直接用
# `scenario.teacher`（随机用户名）→ 永远 401，导致 5 条骨架用例永远只能 skip。
TEACHER_USERNAME = os.environ.get("EXAM_TEACHER_USERNAME", "demo-teacher")
STUDENT_USERNAME = os.environ.get("EXAM_STUDENT_USERNAME", "demo-student-1")
TEACHER_PASSWORD = os.environ.get("EXAM_TEACHER_PASSWORD") or factories.DEFAULT_PASSWORD
STUDENT_PASSWORD = os.environ.get("EXAM_STUDENT_PASSWORD") or factories.DEFAULT_PASSWORD

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("EXAM_E2E") != "1",
        reason="E2E skeleton：依赖 TASK-BE-003/004/005 与可运行服务；设置 EXAM_E2E=1 启用",
    ),
    pytest.mark.skipif(
        os.environ.get("EXAM_E2E_API") != "1",
        reason=(
            "旧版 API 级 E2E 骨架：TASK-QA-001 起主链路由 "
            "tests/e2e/test_exam_ui_flow.py（Playwright）覆盖；需 EXAM_E2E_API=1 才执行"
        ),
    ),
]


# --------------------------------------------------------------------------- #
# 极简 API 客户端（真实执行时用；不引入生产代码）
# --------------------------------------------------------------------------- #
class Api:
    """基于 httpx 的会话客户端：自动带 Cookie，写操作自动带 X-CSRF-Token。"""

    def __init__(self, base_url: str = BASE_URL) -> None:
        self.base_url = base_url
        self.client = httpx.Client(base_url=base_url, timeout=15.0, follow_redirects=False)
        self.csrf: Optional[str] = None
        self.user: Optional[Dict[str, Any]] = None

    # --- 基础 ------------------------------------------------------------- #
    def close(self) -> None:
        self.client.close()

    def _headers(self, write: bool) -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        if write:
            assert self.csrf, "未取得 CSRF token：请先 login() 或 me()"
            headers["X-CSRF-Token"] = self.csrf
        return headers

    def request(self, method: str, path: str, *, write: Optional[bool] = None, **kwargs: Any):
        if write is None:
            write = method.upper() != "GET"
        return self.client.request(method, path, headers=self._headers(write), **kwargs)

    def get(self, path: str, **kwargs: Any):
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any):
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any):
        return self.request("PUT", path, **kwargs)

    def patch(self, path: str, **kwargs: Any):
        return self.request("PATCH", path, **kwargs)

    # --- 会话 ------------------------------------------------------------- #
    def login(self, account: Any) -> Dict[str, Any]:
        resp = self.post("/auth/login", json=account.login_payload(), write=False)
        assert resp.status_code == 200, f"登录失败 {resp.status_code}: {resp.text[:500]}"
        body = resp.json()
        assert "csrf_token" in body and "user" in body
        self.csrf = body["csrf_token"]
        self.user = body["user"]
        return body

    def me(self) -> Dict[str, Any]:
        resp = self.get("/auth/me")
        assert resp.status_code == 200, f"/auth/me {resp.status_code}"
        self.csrf = resp.json().get("csrf_token", self.csrf)
        return resp.json()


def expect(resp: Any, status: int, hint: str = "") -> Any:
    assert resp.status_code == status, (
        f"{hint} 期望 {status}，实际 {resp.status_code}：{resp.text[:500]}"
    )
    if resp.content:
        return resp.json()
    return None


@pytest.fixture(scope="module")
def scenario() -> Any:
    built = factories.build_exam_scenario(seed=42)
    # 用环境里真实存在的账号覆盖随机账号（见模块头部说明）
    built.teacher = factories.Account(
        username=TEACHER_USERNAME,
        display_name="QA教师",
        role="teacher",
        password=TEACHER_PASSWORD,
    )
    built.student = factories.Account(
        username=STUDENT_USERNAME,
        display_name="QA学生",
        role="student",
        password=STUDENT_PASSWORD,
    )
    return built


@pytest.fixture(scope="module")
def ctx() -> Any:
    """链路上下文（模块内顺序执行，共享已创建资源 ID），模块结束统一收尾。"""
    context: Dict[str, Any] = {}
    yield context
    student = context.get("student")
    if isinstance(student, Api):
        student.close()


@pytest.fixture(scope="module")
def teacher(scenario: Any) -> Any:
    api = Api()
    api.login(scenario.teacher)
    yield api
    api.close()


# --------------------------------------------------------------------------- #
# TC-E2E-01 教师登录 + 建题
# --------------------------------------------------------------------------- #
def test_tc_e2e_01_teacher_login_and_create_questions(
    teacher: Api, scenario: Any, ctx: Dict[str, Any]
) -> None:
    """TC-E2E-01 | AC-01/AC-05：登录 → 建 3 题（单选/多选/判断，含解析）→ 列表字段完整。"""
    assert teacher.user and teacher.user["role"] == "teacher"

    created_ids = []
    for payload in scenario.questions[:3]:
        payload = {k: v for k, v in payload.items() if not k.startswith("_")}
        resp = teacher.post("/questions", json=payload)
        body = expect(resp, 201, "POST /questions")
        assert body["id"] and body["stem"] == payload["stem"]
        assert body["explanation"] == payload["explanation"], "解析未持久化（缺陷 C-10）"
        created_ids.append(body["id"])
    assert len(set(created_ids)) == 3
    ctx["question_ids"] = created_ids

    listing = expect(teacher.get("/questions", params={"page": 1, "page_size": 100}), 200)
    ids = {item["id"] for item in listing["items"]}
    assert set(created_ids) <= ids, "新建的题未出现在列表"


# --------------------------------------------------------------------------- #
# TC-E2E-02 组卷 100 分 → 发布考试
# --------------------------------------------------------------------------- #
def test_tc_e2e_02_build_paper_and_publish_exam(
    teacher: Api, scenario: Any, ctx: Dict[str, Any]
) -> None:
    """TC-E2E-02 | AC-09/AC-10：组卷合计 100 → total_points=100 → 发布后试卷冻结。"""
    question_ids = ctx["question_ids"]
    paper_payload = factories.PaperFactory.with_real_ids(
        factories.PaperFactory(seed=42, run_id=scenario.run_id).build(
            question_count=len(question_ids)
        ),
        question_ids,
    )
    paper = expect(teacher.post("/papers", json=paper_payload), 201, "POST /papers")
    assert float(paper["total_points"]) == 100.0, f"总分应为 100，实际 {paper['total_points']}"
    ctx["paper_id"] = paper["id"]

    exam_payload = dict(scenario.exam_live)
    exam_payload["paper_id"] = paper["id"]
    exam = expect(teacher.post("/exams", json=exam_payload), 201, "POST /exams")
    assert exam["status"] == "published"
    ctx["exam_id"] = exam["id"]

    frozen = expect(teacher.get(f"/papers/{paper['id']}"), 200)
    assert frozen["status"] == "frozen", "发布考试后试卷应冻结"

    # 冻结后再改 → 409（AC-10）
    patched = dict(paper_payload, title="试图修改已冻结试卷")
    assert teacher.put(f"/papers/{paper['id']}", json=patched).status_code == 409


# --------------------------------------------------------------------------- #
# TC-E2E-03 学生开始考试 + 首次保存
# --------------------------------------------------------------------------- #
def test_tc_e2e_03_student_starts_and_saves(
    scenario: Any, ctx: Dict[str, Any]
) -> None:
    """TC-E2E-03 | AC-01/AC-12：开始会话（201 新建）→ 取题 → 保存 client_seq=1。"""
    student = Api()
    student.login(scenario.student)
    ctx["student"] = student

    started = student.post(f"/exams/{ctx['exam_id']}/sessions")
    assert started.status_code in (200, 201), f"开始会话失败 {started.status_code}: {started.text}"
    state = started.json()
    assert state["status"] in ("in_progress", "submitted", "expired")
    ctx["session_id"] = state["id"]
    assert state["questions"], "会话未返回题目"
    ctx["session_questions"] = state["questions"]

    # 幂等：再次开始应 200 恢复同一会话（契约缺陷 C-22：需确认 200/409 口径）
    again = student.post(f"/exams/{ctx['exam_id']}/sessions")
    assert again.status_code == 200 and again.json()["id"] == state["id"]

    script = scenario.answers
    first = script.monotonic(steps=3)[0]
    first["answers"] = [
        {**item, "question_id": q["question_id"]}
        for item, q in zip(first["answers"], state["questions"])
    ]
    saved = expect(student.put(f"/sessions/{state['id']}/answers", json=first), 200, "首次保存")
    assert saved["saved_seq"] == first["client_seq"] == 1
    ctx["last_answers"] = first


# --------------------------------------------------------------------------- #
# TC-E2E-04 断网 30s → 恢复 → 旧 seq 不覆盖
# --------------------------------------------------------------------------- #
def test_tc_e2e_04_offline_recovery_and_stale_seq(
    scenario: Any, ctx: Dict[str, Any]
) -> None:
    """TC-E2E-04 | AC-12/AC-15：断网期间本地继续作答；恢复后旧 seq 不得覆盖新答案。"""
    student: Api = ctx["student"]
    session_id = ctx["session_id"]

    # 1) 本地继续作答（不发送）——模拟断网
    later = scenario.answers.monotonic(steps=3)[-1]
    later["answers"] = [
        {**item, "question_id": q["question_id"]}
        for item, q in zip(later["answers"], state_questions(ctx))
    ]
    ctx["pending"] = later

    # 2) 恢复：新 seq 先到达
    applied = expect(student.put(f"/sessions/{session_id}/answers", json=later), 200, "恢复后保存")
    assert applied["saved_seq"] == later["client_seq"] >= 2

    # 3) 旧 seq 重放（网络乱序的迟到包）→ 不得覆盖
    stale = scenario.answers.stale_replay(newest_seq=later["client_seq"])
    stale["answers"] = [
        {**item, "question_id": q["question_id"]}
        for item, q in zip(stale["answers"], state_questions(ctx))
    ]
    after = expect(student.put(f"/sessions/{session_id}/answers", json=stale), 200, "旧 seq 重放")
    assert after["saved_seq"] == later["client_seq"], "旧 client_seq 覆盖了新答案（AC-15 失败）"

    # 4) 断网不影响交卷能力：会话仍可读且状态为进行中
    state = expect(student.get(f"/sessions/{session_id}"), 200)
    assert state["status"] == "in_progress"
    assert state["deadline_at"], "缺少 deadline_at，前端无法做倒计时/自动交卷"


def state_questions(ctx: Dict[str, Any]) -> list:
    return ctx.get("session_questions", [])


# --------------------------------------------------------------------------- #
# TC-E2E-05 交卷 → 判分 → 成绩/错题本/汇总 → 幂等
# --------------------------------------------------------------------------- #
def test_tc_e2e_05_scoring_results_and_idempotent_resubmit(
    teacher: Api, scenario: Any, ctx: Dict[str, Any]
) -> None:
    """TC-E2E-05 | AC-13/16/17/19/20：交卷判分 → 成绩 → 错题本 → 汇总 → 重复交卷同分。"""
    student: Api = ctx["student"]
    session_id = ctx["session_id"]

    submitted = expect(student.post(f"/sessions/{session_id}/submit"), 200, "交卷")
    score = float(submitted["score"])
    assert submitted["status"] in ("submitted", "expired")
    assert score >= 0

    result = expect(student.get(f"/sessions/{session_id}/result"), 200, "逐题结果")
    assert result["session_id"] == session_id
    assert result["items"], "成绩明细为空"
    for item in result["items"]:
        assert {"is_correct", "earned_points", "points"} <= set(item), "缺判分字段"
        earned, full = float(item["earned_points"]), float(item["points"])
        if item["is_correct"]:
            assert abs(earned - full) < 1e-9, "答对却未得满分"
        else:
            assert 0.0 <= earned <= full, "得分越界"
            if item["type"] != "multiple":
                assert earned == 0.0, "单选/判断答错必须 0 分（AC-16）"
            assert round(earned, 2) == earned, "得分未保留 2 位小数（AC-17）"

    my_results = expect(student.get("/me/results"), 200)
    assert any(row["exam_id"] == ctx["exam_id"] for row in my_results["items"])

    wrong_book = expect(student.get("/me/wrong-questions"), 200)
    assert "items" in wrong_book and isinstance(wrong_book["total"], int)

    summary = expect(teacher.get(f"/exams/{ctx['exam_id']}/results"), 200)
    assert summary["exam_id"] == ctx["exam_id"]
    assert summary["submitted"] >= 1

    # 幂等：重复交卷返回同一成绩，不新增会话/成绩（AC-13）
    again = expect(student.post(f"/sessions/{session_id}/submit"), 200, "重复交卷应幂等 200")
    assert float(again["score"]) == score, "重复交卷产生了不同成绩"
    summary2 = expect(teacher.get(f"/exams/{ctx['exam_id']}/results"), 200)
    assert summary2["total_sessions"] == summary["total_sessions"], "重复交卷新增了会话"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
