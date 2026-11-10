"""tests/e2e/e2e_support.py — E2E 支撑层（TASK-QA-001）

只通过两样东西与被测系统交互：

1. **HTTP 契约**（``contracts/openapi.yaml`` v1.0.3）——用于构造前置数据与做旁证断言；
2. **页面 ``data-testid``**——用于真实浏览器操作（Playwright Python）。

刻意不导入 ``server/`` 或 ``web/`` 的任何生产代码，避免测试与实现耦合。

约定
----
- 账号一律来自环境变量；默认值 ``demo-teacher`` / ``demo-student-1`` 与开发默认密码
  ``dev-demo-password-123`` **仅限本地开发**（与 ``server/app/cli.py`` 的
  ``DEFAULT_DEV_PASSWORD`` 一致）。staging/prod 必须显式提供强密码。
- 所有测试数据带 ``[e2e-<tag>]`` 前缀；``tag`` 由 ``run_id + 用例后缀 + 随机串`` 组成，
  保证跨 run / 同 run 多用例不撞题干唯一键 ``(owner_id, type, md5(stem))``。
- 考试时间窗全部相对 ``now`` 构造，禁止固定日期。
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

import httpx

# 仅开发默认值：与 server/app/cli.py 的 DEFAULT_DEV_PASSWORD 对齐，禁止用于 staging/prod
DEFAULT_DEV_PASSWORD = "dev-demo-password-123"

BASE_URL = os.environ.get("EXAM_BASE_URL", "http://127.0.0.1:5173").rstrip("/")
API_URL = os.environ.get("EXAM_API_URL", "http://127.0.0.1:8000/api").rstrip("/")

TEACHER_USERNAME = os.environ.get("EXAM_TEACHER_USERNAME", "demo-teacher")
STUDENT_USERNAME = os.environ.get("EXAM_STUDENT_USERNAME", "demo-student-1")
TEACHER_PASSWORD = (
    os.environ.get("EXAM_TEACHER_PASSWORD")
    or os.environ.get("EXAM_DEMO_PASSWORD")
    or DEFAULT_DEV_PASSWORD
)
STUDENT_PASSWORD = (
    os.environ.get("EXAM_STUDENT_PASSWORD")
    or os.environ.get("EXAM_DEMO_PASSWORD")
    or DEFAULT_DEV_PASSWORD
)

#: 与前端表单/契约一致的 3 题组合（单选 / 多选 / 判断），分值 40 + 30 + 30 = 100。
UI_QUESTION_SPECS: tuple[dict[str, Any], ...] = (
    {
        "type": "single",
        "label": "单选题",
        "options": ["服务器内部错误", "请求成功", "资源未找到", "永久重定向"],
        "answer_indexes": [1],  # B
    },
    {
        "type": "multiple",
        "label": "多选题",
        "options": ["GET", "POST", "HEAD", "DELETE"],
        "answer_indexes": [0, 2],  # A、C
    },
    {
        "type": "judge",
        "label": "判断题",
        "options": ["正确", "错误"],
        "answer_indexes": [0],  # A
    },
)

PAPER_POINTS: tuple[float, ...] = (40.0, 30.0, 30.0)


# --------------------------------------------------------------------------- #
# 通用工具
# --------------------------------------------------------------------------- #
def utc_now() -> datetime:
    """秒级 UTC 时间（测试计划 §3.2：允许 ±5s 漂移）。"""
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(value: datetime) -> str:
    """ISO 8601 UTC（契约要求）；统一用 ``Z`` 结尾。"""
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    """解析契约返回的 ISO 8601 时间（兼容 ``Z`` 与 ``+00:00``）。"""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def unique_tag(run_id: str, suffix: str = "") -> str:
    """``run_id`` + 用例后缀 + 随机串：题干/标题唯一，且可按 run_id 检索。"""
    tail = uuid.uuid4().hex[:6]
    return f"{run_id}-{suffix}-{tail}" if suffix else f"{run_id}-{tail}"


def answer_keys(spec: dict[str, Any]) -> list[str]:
    """表单下标 → 契约选项 key（A/B/C/D...）。"""
    return [chr(ord("A") + index) for index in spec["answer_indexes"]]


def screenshot(page: Any, directory: Any, name: str) -> None:
    """保存关键步骤截图（失败证据由 conftest 的 hook 额外兜底）。"""
    try:
        page.screenshot(path=str(directory / name), full_page=True)
    except Exception as exc:  # pragma: no cover - 截图失败不应掩盖主断言
        print(f"[e2e] 截图失败 {name}: {exc}")


# --------------------------------------------------------------------------- #
# HTTP 客户端（契约层）
# --------------------------------------------------------------------------- #
class Api:
    """httpx 会话客户端：自动带 Cookie，写操作自动带 ``X-CSRF-Token``。"""

    def __init__(self, base_url: str = API_URL) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(
            base_url=self.base_url, timeout=20.0, follow_redirects=False
        )
        self.csrf: str | None = None
        self.user: dict[str, Any] | None = None

    # --- 生命周期 --------------------------------------------------------- #
    def close(self) -> None:
        self.client.close()

    def login(self, username: str, password: str) -> dict[str, Any]:
        """登录并保存 CSRF token；失败信息带响应片段，便于定位环境问题。"""
        response = self.client.post(
            "/auth/login",
            json={"username": username, "password": password},
            headers={"Accept": "application/json"},
        )
        assert response.status_code == 200, (
            f"登录失败（{username}）{response.status_code}: {response.text[:500]}\n"
            "提示：先执行 `python -m app.cli seed-demo` 生成演示账号"
        )
        body = response.json()
        assert "csrf_token" in body and "user" in body, f"登录响应缺字段: {body}"
        self.csrf = body["csrf_token"]
        self.user = body["user"]
        return body

    # --- 请求 ------------------------------------------------------------- #
    def _headers(self, write: bool) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if write:
            assert self.csrf, "未取得 CSRF token：请先 login()"
            headers["X-CSRF-Token"] = self.csrf
        return headers

    def request(self, method: str, path: str, *, write: bool | None = None, **kwargs: Any):
        if write is None:
            write = method.upper() not in ("GET", "HEAD", "OPTIONS")
        return self.client.request(method, path, headers=self._headers(write), **kwargs)

    def get(self, path: str, **kwargs: Any):
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any):
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any):
        return self.request("PUT", path, **kwargs)

    def patch(self, path: str, **kwargs: Any):
        return self.request("PATCH", path, **kwargs)


def expect_json(response: Any, status: int, hint: str = "") -> Any:
    """断言状态码并返回 JSON（空响应返回 ``None``）。"""
    assert response.status_code == status, (
        f"{hint} 期望 {status}，实际 {response.status_code}: {response.text[:800]}"
    )
    if not response.content:
        return None
    return response.json()


# --------------------------------------------------------------------------- #
# 数据构造（全部经 API，契约 v1.0.3；v1.0.3 未改请求模型，构造逻辑不变）
# --------------------------------------------------------------------------- #
def create_question(api: Api, tag: str, spec: dict[str, Any], index: int) -> dict[str, Any]:
    """新建 1 题；``explanation`` 必填（C-10），答案用 key 数组。"""
    payload = {
        "type": spec["type"],
        "stem": f"[e2e-{tag}] {spec['label']}第 {index} 题",
        "options": [
            {"key": chr(ord("A") + offset), "text": text}
            for offset, text in enumerate(spec["options"])
        ],
        "answer": answer_keys(spec),
        "explanation": f"[e2e-{tag}] {spec['label']}解析：HTTP 语义。",
        "difficulty": 2,
        "tags": ["e2e"],
    }
    body = expect_json(api.post("/questions", json=payload), 201, "POST /questions")
    assert body["type"] == spec["type"] and body["stem"] == payload["stem"]
    assert body["explanation"] == payload["explanation"], "解析未持久化（AC-05 / C-10）"
    assert body["answer"] == payload["answer"]
    return body


def create_questions(api: Api, tag: str) -> list[dict[str, Any]]:
    """按 ``UI_QUESTION_SPECS`` 建 3 题；返回含 id/answer 的契约对象。"""
    unique = uuid.uuid4().hex[:6]
    return [
        create_question(api, f"{tag}-{unique}", spec, index)
        for index, spec in enumerate(UI_QUESTION_SPECS, start=1)
    ]


def create_paper(
    api: Api,
    title: str,
    questions: Sequence[dict[str, Any]],
    points: Sequence[float] = PAPER_POINTS,
) -> dict[str, Any]:
    """组卷：请求体只含 title + questions（C-08 / CHG-026），断言合计 100。"""
    assert len(questions) == len(points)
    payload = {
        "title": title,
        "questions": [
            {
                "question_id": question["id"],
                "order_no": index + 1,
                "points": float(point),
                "partial_rule": "all_or_nothing",
            }
            for index, (question, point) in enumerate(zip(questions, points))
        ],
    }
    body = expect_json(api.post("/papers", json=payload), 201, "POST /papers")
    assert float(body["total_points"]) == sum(float(p) for p in points), (
        f"total_points 应为 100，实际 {body.get('total_points')}"
    )
    assert body["status"] == "draft"
    return body


def create_exam(
    api: Api,
    paper_id: int,
    title: str,
    *,
    duration_min: int = 30,
    start_offset_min: int = -1,
    end_offset_min: int = 120,
    publish: bool = True,
) -> dict[str, Any]:
    """发布考试（时间窗相对 now，覆盖当前）；发布即冻结试卷并生成快照。"""
    now = utc_now()
    payload = {
        "paper_id": paper_id,
        "title": title,
        "start_at": iso(now + timedelta(minutes=start_offset_min)),
        "end_at": iso(now + timedelta(minutes=end_offset_min)),
        "duration_min": duration_min,
        "publish": publish,
    }
    body = expect_json(api.post("/exams", json=payload), 201, "POST /exams")
    assert body["status"] == ("published" if publish else "draft")
    if publish:
        # CHG-027：published 时 snapshot_version 必返回（≥ 1）
        assert int(body.get("snapshot_version") or 0) >= 1, "发布后缺少 snapshot_version（C-12）"
    return body


def list_questions(api: Api, keyword: str, page_size: int = 100) -> list[dict[str, Any]]:
    """按题干关键词检索题库（用于 UI 创建后取回 id）。"""
    body = expect_json(
        api.get("/questions", params={"keyword": keyword, "page": 1, "page_size": page_size}),
        200,
        "GET /questions",
    )
    return list(body["items"])


def find_paper_by_title(api: Api, title: str) -> dict[str, Any]:
    """在试卷列表中按标题精确查找（UI 保存后取回 id）。"""
    body = expect_json(
        api.get("/papers", params={"page": 1, "page_size": 100}), 200, "GET /papers"
    )
    matches = [item for item in body["items"] if item["title"] == title]
    assert matches, f"试卷列表未找到标题《{title}》（共 {body['total']} 张）"
    return max(matches, key=lambda item: item["id"])


def assert_window_covers_now(exam: dict[str, Any]) -> None:
    """考试时间窗必须覆盖当前时刻（T-E2E-01 / T-E2E-02 前提）。"""
    start = parse_iso(exam["start_at"])
    end = parse_iso(exam["end_at"])
    now = utc_now()
    assert start <= now <= end, f"考试时间窗未覆盖当前：{start} ~ {end}，now={now}"


# --------------------------------------------------------------------------- #
# UI 操作（只依赖 data-testid）
# --------------------------------------------------------------------------- #
def login_ui(page: Any, username: str, password: str, expected_path: str) -> None:
    """登录并等待落到角色默认首页（教师 /questions，学生 /me/exams）。"""
    page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
    page.locator('[data-testid="login-username"]').fill(username)
    page.locator('[data-testid="login-password"]').fill(password)
    page.locator('[data-testid="login-submit"]').click()
    try:
        page.wait_for_url(f"**{expected_path}", timeout=30_000)
    except Exception:
        error = page.locator('[data-testid="login-error"]')
        detail = error.inner_text() if error.count() else f"当前地址 {page.url}"
        raise AssertionError(f"登录失败（{username}）：{detail}") from None
    page.locator('[data-testid="app-layout"]').wait_for(state="visible", timeout=15_000)


def open_exam_from_my_exams(page: Any, exam_id: int) -> None:
    """从「我的考试」点击进入考试，等待作答页加载。"""
    page.goto(f"{BASE_URL}/me/exams", wait_until="domcontentloaded")
    page.locator('[data-testid="my-exams-page"]').wait_for(state="visible", timeout=20_000)
    row = page.locator(f'[data-testid="my-exams-row-{exam_id}"]')
    row.wait_for(state="visible", timeout=20_000)
    page.locator(
        f'[data-testid="exam-start-{exam_id}"] [data-testid="exam-start"]'
    ).click()
    page.wait_for_url(f"**/exams/{exam_id}", timeout=20_000)
    page.locator('[data-testid="exam-page"]').wait_for(state="visible", timeout=20_000)
    # 会话就绪（倒计时渲染）后再作答
    page.locator('[data-testid="exam-countdown"]').wait_for(state="visible", timeout=20_000)


def apply_answer(page: Any, question: dict[str, Any], keys: Sequence[str] | None = None) -> None:
    """按契约答案勾选（多选逐个勾；单选/判断点选替换）。"""
    for key in keys if keys is not None else question["answer"]:
        page.locator(f'[data-testid="answer-{question["id"]}-{key}"]').check()


def force_save(page: Any, target_question_id: int) -> None:
    """切题会触发 ``forceSave``（useExamSession.goToQuestion），避免等 30s debounce。"""
    page.locator(f'[data-testid="exam-card-{target_question_id}"]').click()
    wait_saved(page)


def wait_saved(page: Any, timeout: int = 20_000) -> None:
    """等待 ``exam-save-status`` 出现“答案已保存”（含时间戳）。"""
    page.locator('[data-testid="exam-save-status"]').filter(
        has_text=re.compile("答案已保存")
    ).wait_for(state="visible", timeout=timeout)


def wait_for_result(page: Any, timeout: int = 25_000) -> None:
    """等待交卷后跳转成绩页并渲染 ``result-score``。"""
    page.wait_for_url(re.compile(r".*/results/\d+$"), timeout=timeout)
    page.locator('[data-testid="result-score"]').wait_for(state="visible", timeout=15_000)


def wait_toast(page: Any, text: str, timeout: int = 8_000) -> None:
    """等待 antd 全局提示出现（用于“已交卷/自动交卷”等用户可见提示）。"""
    page.locator(".ant-message").filter(has_text=text).first.wait_for(
        state="visible", timeout=timeout
    )


def session_id_from_url(url: str) -> int:
    match = re.search(r"/results/(\d+)(?:$|[?#])", url)
    assert match, f"当前地址不是成绩页: {url}"
    return int(match.group(1))


def read_score(page: Any) -> float:
    """从 ``result-score`` 读取成绩（文本形如 ``100 / 100 分``）。"""
    text = page.locator('[data-testid="result-score"]').inner_text()
    match = re.search(r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", text)
    assert match, f"无法从成绩卡片解析分数: {text!r}"
    return float(match.group(1))


def page_api(page: Any, method: str, path: str, body: Any = None) -> dict[str, Any]:
    """在浏览器上下文内调用 ``/api``（走 Vite/nginx 同源代理，复用 Cookie 与 CSRF）。

    仅用于 E2E 中“UI 无法表达”的旁证（如重复交卷幂等断言），
    避免另起 HTTP 会话导致 CSRF/Cookie 口径不一致。
    """
    script = """
    async ({ method, path, body }) => {
      const headers = { Accept: 'application/json' };
      if (body !== null && body !== undefined) headers['Content-Type'] = 'application/json';
      if (method !== 'GET' && method !== 'HEAD') {
        const meResp = await fetch('/api/auth/me', { credentials: 'include' });
        if (meResp.ok) {
          const me = await meResp.json();
          if (me && me.csrf_token) headers['X-CSRF-Token'] = me.csrf_token;
        }
      }
      const resp = await fetch(path, {
        method,
        credentials: 'include',
        headers,
        body: body === null || body === undefined ? undefined : JSON.stringify(body),
      });
      const text = await resp.text();
      let data = null;
      try { data = text ? JSON.parse(text) : null; } catch { data = text; }
      return { status: resp.status, body: data };
    }
    """
    return page.evaluate(script, {"method": method, "path": path, "body": body})


def create_question_via_ui(page: Any, spec: dict[str, Any], tag: str, index: int) -> None:
    """题库页新建 1 题（选题型 → 题干 → 选项 → 答案 → 解析 → 保存）。

    选项/答案控件的可访问名称随题干变化，故用 antd 稳定的 wrapper class 定位；
    其余全部走 ``data-testid``。
    """
    page.locator('[data-testid="questions-create"]').click()
    modal = page.locator(".ant-modal-content").filter(
        has=page.locator('[data-testid="questions-stem"]')
    ).last
    modal.wait_for(state="visible", timeout=15_000)

    if spec["type"] != "single":
        modal.locator("label.ant-radio-button-wrapper").filter(
            has_text=spec["label"]
        ).first.click()

    modal.locator('[data-testid="questions-stem"]').fill(
        f"[e2e-{tag}] {spec['label']}第 {index} 题"
    )

    if spec["type"] != "judge":
        for offset, text in enumerate(spec["options"]):
            modal.locator(f'[data-testid="questions-option-text-{offset}"]').fill(text)

    answer_area = modal.locator('[data-testid="questions-create-answer-text"]')
    if spec["type"] == "judge":
        # 等判断题的选项从 4 个空选项切换为固定的 A/B 两项
        answer_area.locator("label.ant-radio-wrapper").nth(1).wait_for(
            state="visible", timeout=10_000
        )
    elif spec["type"] == "multiple":
        answer_area.locator("label.ant-checkbox-wrapper").nth(1).wait_for(
            state="visible", timeout=10_000
        )
    for answer_index in spec["answer_indexes"]:
        if spec["type"] == "multiple":
            answer_area.locator("label.ant-checkbox-wrapper").nth(answer_index).click()
        else:
            answer_area.locator("label.ant-radio-wrapper").nth(answer_index).click()

    modal.locator('[data-testid="questions-explanation"]').fill(
        f"[e2e-{tag}] {spec['label']}解析：HTTP 语义。"
    )
    modal.locator('[data-testid="questions-submit"]').click()

    try:
        modal.wait_for(state="hidden", timeout=20_000)
    except Exception:
        detail = modal.inner_text()[:1200]
        raise AssertionError(f"{spec['label']}保存失败，弹窗仍打开：{detail}") from None


def create_paper_via_ui(
    page: Any,
    title: str,
    keyword: str,
    question_ids: Sequence[int],
    points: Sequence[float] = PAPER_POINTS,
) -> None:
    """组卷页：标题 + 关键词筛选 + 逐个加入 + 设分，断言实时总分 100。"""
    page.goto(f"{BASE_URL}/papers", wait_until="domcontentloaded")
    page.locator('[data-testid="papers-create"]').click()
    page.wait_for_url("**/papers/new", timeout=20_000)
    page.locator('[data-testid="page-paper-editor"]').wait_for(state="visible", timeout=20_000)

    page.locator('[data-testid="papers-title"]').fill(title)
    page.locator('[data-testid="papers-keyword"]').fill(keyword)
    page.locator('[data-testid="papers-search"]').click()

    for question_id in question_ids:
        page.locator(f'[data-testid="papers-add-{question_id}"]').click()

    for question_id, point in zip(question_ids, points):
        field = page.locator(f'[data-testid="papers-points-{question_id}"]')
        if field.count() == 0:
            # 兜底：antd InputNumber 未透传 data-testid 时，从已选行内定位输入框
            field = page.locator(
                f'[data-testid="papers-selected-{question_id}"] input.ant-input-number-input'
            )
        elif field.evaluate("element => element.tagName") != "INPUT":
            field = field.locator("input")
        field.fill(str(point))
        field.press("Enter")

    total = page.locator('[data-testid="papers-total-points"]')
    total.filter(has_text=re.compile(r"^100(\s|$)")).wait_for(state="visible", timeout=10_000)
    assert total.inner_text().strip().startswith("100"), (
        f"实时总分应为 100，实际 {total.inner_text()!r}"
    )

    page.locator('[data-testid="papers-save"]').click()
    page.wait_for_url("**/papers", timeout=20_000)
    page.locator('[data-testid="page-papers"]').wait_for(state="visible", timeout=20_000)
