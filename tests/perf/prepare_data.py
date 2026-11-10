#!/usr/bin/env python3
"""TASK-QA-003 性能冒烟：数据准备（幂等）。

本脚本只通过**公开 HTTP API** 造数，不导入 `server/` 代码、不直连数据库，因此
SQLite（本地 uvicorn）与 PostgreSQL（Compose staging 同构栈）使用同一份逻辑。

产出
----
1. 1 名教师 + 100 名学生（统一口令，来自环境变量，不落盘）；
2. 100 道单选题（每题 1 分）+ 1 份 100 分试卷（题号 1..100）；
3. 1 场“进行中”的已发布考试（`start_at=now-5m`、`end_at=now+3h`、
   `duration_min=120`，覆盖一次 3 分钟压测）；
4. `tests/perf/data/students.json`（默认）——学生账号清单，供
   `tests/perf/k6/exam_flow.js` 的 init 上下文读取；同时复制一份到
   `reports/perf/<run_id>/prepare.json` 作为数据准备证据。

幂等与“轮换”语义（重要）
------------------------
- 账号 / 题目：按 `perf-<run_id>-...` 用户名、`[<run_id>] ...` 题干复用，不重复创建；
- 试卷：优先复用“草稿 + 题号集合正确 + 总分 100”的同名试卷，否则新建；
- 考试：仅当同名考试仍为 `published`、时间窗有效、且 **0 个会话**时复用。
  上一轮压测一旦产生会话（默认 20% 已交卷），服务端没有“重置考试”接口，脚本会
  自动新建 **新试卷 + 新考试**（旧试卷已冻结，不能复用），账号与题目继续复用。
  因此重复执行不会重复建账号/题目，也不会污染上一轮成绩；代价是专用性能库里会
  留下历史考试（无删除考试 API，见 README“清理”）。

前置
----
- API 已启动且可访问（本地 uvicorn 或独立 Compose 栈）；
- 已存在 admin 账号（runbook §3.5：`python -m app.cli create-admin ...`）；
- 环境变量：
  - `EXAM_ADMIN_PASSWORD`（必填）：admin 口令；
  - `EXAM_QA_PASSWORD`（必填，8–128 位）：100 个测试账号的统一口令；
  - 可选：`EXAM_API_URL`、`EXAM_RUN_ID`、`EXAM_ADMIN_USERNAME`。

退出码：0 成功；1 数据/接口问题；2 用法错误（argparse）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_API_URL = "http://127.0.0.1:8000/api"
DEFAULT_RUN_ID = "perf-local"
DEFAULT_OUTPUT = REPO_ROOT / "tests" / "perf" / "data" / "students.json"
PASSWORD_ENV = "EXAM_QA_PASSWORD"
ADMIN_PASSWORD_ENV = "EXAM_ADMIN_PASSWORD"
DEFAULT_ADMIN_USERNAME = "admin"

STUDENT_COUNT = 100
QUESTION_COUNT = 100
EXAM_DURATION_MIN = 120
EXAM_WINDOW_MIN = 180
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")

TEACHER_ROLE = "teacher"
STUDENT_ROLE = "student"
PAPER_TITLE_BASE = "100 题 100 分卷"
EXAM_TITLE_BASE = "性能考试"

PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128


# --------------------------------------------------------------------------- #
# 日志 / 错误 / 时间
# --------------------------------------------------------------------------- #
def log(message: str) -> None:
    """进度日志一律走 stderr，保证 stdout 只有一份合法 JSON。"""
    print(f"[prepare] {message}", file=sys.stderr)


class PrepareError(RuntimeError):
    """可预期的数据准备失败（消息中不含口令）。"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso_utc(moment: datetime) -> str:
    return (
        moment.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def parse_iso(raw: Any) -> datetime:
    if not isinstance(raw, str) or not raw.strip():
        raise PrepareError(f"时间字段格式非法：{raw!r}")
    value = raw.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(value)
    except ValueError as exc:
        raise PrepareError(f"时间字段无法解析：{raw!r}") from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def env_value(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def require_env(name: str) -> str:
    value = env_value(name)
    if value is None:
        raise PrepareError(
            f"环境变量 {name} 未设置或为空。口令只从环境变量读取（禁止写进命令行/仓库）。"
        )
    return value


def validate_password(value: str, env_name: str) -> None:
    if len(value) < PASSWORD_MIN_LENGTH:
        raise PrepareError(
            f"环境变量 {env_name} 的口令长度不足 {PASSWORD_MIN_LENGTH} 位"
            "（服务端 UserCreate 要求 8–128 位）"
        )
    if len(value) > PASSWORD_MAX_LENGTH:
        raise PrepareError(
            f"环境变量 {env_name} 的口令超过 {PASSWORD_MAX_LENGTH} 位"
        )


def validate_run_id(raw: str) -> str:
    run_id = raw.strip()
    if not RUN_ID_PATTERN.match(run_id):
        raise PrepareError(
            "run_id 只允许 1–40 位 [A-Za-z0-9._-]，且必须以字母或数字开头；"
            f"当前为 {raw!r}"
        )
    return run_id


# --------------------------------------------------------------------------- #
# HTTP 客户端
# --------------------------------------------------------------------------- #
def error_summary(response: httpx.Response) -> str:
    """只提取稳定错误字段，避免把响应体中的无关内容带进日志。"""
    try:
        body = response.json()
    except ValueError:
        return response.text[:200].replace("\n", " ")
    if isinstance(body, dict):
        return (
            f"code={body.get('code')!r} "
            f"message={str(body.get('message'))[:200]!r}"
        )
    return str(body)[:200]


class ApiClient:
    """极简 API 客户端：httpx Cookie jar + CSRF 头注入。"""

    def __init__(self, api_url: str, timeout: float = 30.0) -> None:
        self.api_url = api_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout, follow_redirects=False)
        self.csrf_token: str | None = None

    def close(self) -> None:
        self._client.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        expected: tuple[int, ...] = (200,),
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        request_headers = dict(headers or {})
        if method.upper() not in {"GET", "HEAD", "OPTIONS"}:
            if self.csrf_token:
                request_headers.setdefault("X-CSRF-Token", self.csrf_token)
            if json_body is not None:
                request_headers.setdefault("Content-Type", "application/json")
        try:
            response = self._client.request(
                method,
                f"{self.api_url}{path}",
                params=params,
                json=json_body,
                headers=request_headers,
            )
        except httpx.HTTPError as exc:
            raise PrepareError(
                f"{method} {path} 网络请求失败：{type(exc).__name__}"
            ) from exc
        if response.status_code not in expected:
            raise PrepareError(
                f"{method} {path} 返回 HTTP {response.status_code}"
                f"（期望 {sorted(expected)}）：{error_summary(response)}"
            )
        return response

    def login(self, username: str, password: str) -> dict[str, Any]:
        response = self.request(
            "POST",
            "/auth/login",
            json_body={"username": username, "password": password},
            expected=(200,),
        )
        try:
            body = response.json()
        except ValueError as exc:
            raise PrepareError(f"登录 {username} 返回非 JSON 响应") from exc
        token = body.get("csrf_token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not token:
            raise PrepareError(f"登录 {username} 成功但响应缺少 csrf_token")
        self.csrf_token = token
        # 立刻验证 Cookie 回传：纯 HTTP 的 staging 若忘关 SESSION_COOKIE_SECURE，
        # 浏览器/httpx 都不会回传 Secure Cookie，表现为“登录 200，随后 401”。
        try:
            self.request("GET", "/auth/me", expected=(200,))
        except PrepareError as exc:
            raise PrepareError(
                f"登录 {username} 后 Cookie 未被服务端接受（{exc}）。"
                "若压测走纯 HTTP 的 Compose staging，请确认 SESSION_COOKIE_SECURE=false。"
            ) from exc
        return body


def iter_pages(
    client: ApiClient,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    page_size: int = 100,
    max_pages: int = 100,
) -> Iterator[dict[str, Any]]:
    """遍历分页列表（契约统一 page/page_size/items/total）。"""
    page = 1
    while page <= max_pages:
        query = dict(params or {})
        query["page"] = page
        query["page_size"] = page_size
        response = client.request("GET", path, params=query, expected=(200,))
        try:
            body = response.json()
        except ValueError as exc:
            raise PrepareError(f"GET {path} 返回非 JSON 响应") from exc
        if not isinstance(body, dict):
            raise PrepareError(f"GET {path} 响应体不是 JSON 对象")
        items = body.get("items")
        if not isinstance(items, list):
            raise PrepareError(f"GET {path} 响应缺少 items 数组")
        for item in items:
            if isinstance(item, dict):
                yield item
        total = int(body.get("total") or 0)
        if not items or page * page_size >= total:
            return
        page += 1
    raise PrepareError(f"GET {path} 分页超过 {max_pages} 页，疑似 total 异常")


# --------------------------------------------------------------------------- #
# 账号
# --------------------------------------------------------------------------- #
def fetch_users(admin: ApiClient) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for user in iter_pages(admin, "/users"):
        username = user.get("username")
        if isinstance(username, str):
            index[username] = user
    return index


def ensure_account(
    admin: ApiClient,
    index: dict[str, dict[str, Any]],
    *,
    username: str,
    display_name: str,
    role: str,
    password: str,
    reset_password: bool,
) -> int:
    """创建或复用账号；复用时可选择重置口令/恢复启用。返回 user_id。"""
    existing = index.get(username)
    if existing is not None:
        if existing.get("role") != role:
            raise PrepareError(
                f"账号 {username} 已存在但角色为 {existing.get('role')!r}"
                f"（期望 {role!r}）；请人工确认后清理，脚本不会改角色。"
            )
        patch: dict[str, Any] = {}
        if existing.get("is_active") is False:
            patch["is_active"] = True
        if reset_password:
            patch["password"] = password
        if patch:
            admin.request(
                "PATCH",
                f"/users/{existing['id']}",
                json_body=patch,
                expected=(200,),
            )
        return int(existing["id"])

    response = admin.request(
        "POST",
        "/users",
        json_body={
            "username": username,
            "display_name": display_name,
            "role": role,
            "password": password,
        },
        expected=(201, 409),
    )
    if response.status_code == 201:
        user = response.json()
        index[username] = user
        return int(user["id"])

    # 409：索引过期或并发创建；重新拉取一次列表定位
    for user in iter_pages(admin, "/users"):
        if user.get("username") == username:
            if user.get("role") != role:
                raise PrepareError(
                    f"账号 {username} 创建冲突（409），且已有账号角色为"
                    f" {user.get('role')!r}（期望 {role!r}）"
                )
            index[username] = user
            return int(user["id"])
    raise PrepareError(f"账号 {username} 创建冲突（409），但用户列表里找不到该账号")


# --------------------------------------------------------------------------- #
# 题目 / 试卷
# --------------------------------------------------------------------------- #
def question_stem(run_id: str, number: int) -> str:
    return f"[{run_id}] 性能题 {number:03d}：{number} + {number} 等于多少？"


def question_payload(run_id: str, number: int) -> dict[str, Any]:
    total = number + number
    return {
        "type": "single",
        "stem": question_stem(run_id, number),
        "options": [
            {"key": "A", "text": str(total)},
            {"key": "B", "text": str(total + 1)},
            {"key": "C", "text": str(total + 2)},
            {"key": "D", "text": str(total + 3)},
        ],
        "answer": ["A"],
        "explanation": "性能数据：固定答案 A，仅用于压测，不参与教学内容。",
        "difficulty": 1,
        "tags": [run_id, "perf"],
    }


def ensure_questions(teacher: ApiClient, run_id: str) -> list[int]:
    """复用/创建 100 道题，按 1..100 顺序返回 question_id。"""
    by_stem: dict[str, int] = {}
    for question in iter_pages(teacher, "/questions", params={"keyword": run_id}):
        stem = question.get("stem")
        if isinstance(stem, str) and question.get("id") is not None:
            by_stem[stem] = int(question["id"])

    question_ids: list[int] = []
    created = 0
    for number in range(1, QUESTION_COUNT + 1):
        stem = question_stem(run_id, number)
        existing = by_stem.get(stem)
        if existing is not None:
            question_ids.append(existing)
            continue
        response = teacher.request(
            "POST",
            "/questions",
            json_body=question_payload(run_id, number),
            expected=(201,),
        )
        question_id = int(response.json()["id"])
        by_stem[stem] = question_id
        question_ids.append(question_id)
        created += 1
    log(f"题目：复用 {len(question_ids) - created} 道 / 新建 {created} 道（共 {QUESTION_COUNT} 道）")
    return question_ids


def paper_base_title(run_id: str) -> str:
    return f"[{run_id}] {PAPER_TITLE_BASE}"


def paper_revision(title: str, base: str) -> int | None:
    if title == base:
        return 1
    prefix = base + " v"
    if title.startswith(prefix):
        suffix = title[len(prefix) :]
        if suffix.isdigit() and int(suffix) >= 2:
            return int(suffix)
    return None


def choose_or_create_paper(
    teacher: ApiClient,
    run_id: str,
    question_ids: list[int],
) -> tuple[int, str]:
    base = paper_base_title(run_id)
    wanted = set(question_ids)
    candidates: list[tuple[int, dict[str, Any]]] = []
    for paper in iter_pages(teacher, "/papers"):
        title = paper.get("title")
        if not isinstance(title, str):
            continue
        revision = paper_revision(title, base)
        if revision is None:
            continue
        candidates.append((revision, paper))
    candidates.sort(key=lambda item: item[0])

    for _revision, paper in candidates:
        if paper.get("status") != "draft":
            continue
        questions = paper.get("questions") or []
        ids = {
            int(question["question_id"])
            for question in questions
            if isinstance(question, dict) and question.get("question_id") is not None
        }
        total_points = float(paper.get("total_points") or 0)
        if (
            len(questions) == QUESTION_COUNT
            and ids == wanted
            and abs(total_points - float(QUESTION_COUNT)) < 0.001
        ):
            log(
                f"试卷：复用草稿试卷 id={paper.get('id')} title={paper.get('title')!r}"
            )
            return int(paper["id"]), str(paper["title"])

    next_revision = max((revision for revision, _ in candidates), default=0) + 1
    title = base if next_revision == 1 else f"{base} v{next_revision}"
    payload = {
        "title": title,
        "questions": [
            {"question_id": question_id, "order_no": order_no, "points": 1}
            for order_no, question_id in enumerate(question_ids, start=1)
        ],
    }
    response = teacher.request("POST", "/papers", json_body=payload, expected=(201,))
    paper = response.json()
    log(
        f"试卷：新建 {title!r}（id={paper.get('id')}，"
        f"{len(question_ids)} 题 × 1 分 = {len(question_ids)} 分）"
    )
    return int(paper["id"]), title


# --------------------------------------------------------------------------- #
# 考试
# --------------------------------------------------------------------------- #
def exam_title_prefix(run_id: str) -> str:
    return f"[{run_id}] {EXAM_TITLE_BASE}"


def exam_is_reusable(teacher: ApiClient, exam: dict[str, Any], now: datetime) -> bool:
    if exam.get("status") != "published":
        return False
    start_at = parse_iso(exam.get("start_at"))
    end_at = parse_iso(exam.get("end_at"))
    if start_at > now or end_at <= now + timedelta(minutes=5):
        return False

    paper_id = exam.get("paper_id")
    if not isinstance(paper_id, int):
        return False
    paper = teacher.request("GET", f"/papers/{paper_id}", expected=(200,)).json()
    questions = paper.get("questions") or []
    if len(questions) != QUESTION_COUNT:
        return False
    if abs(float(paper.get("total_points") or 0) - float(QUESTION_COUNT)) > 0.001:
        return False

    summary = teacher.request(
        "GET", f"/exams/{exam['id']}/results", expected=(200,)
    ).json()
    if int(summary.get("total_sessions") or 0) != 0:
        return False
    return True


def find_reusable_exam(
    teacher: ApiClient,
    run_id: str,
    now: datetime,
) -> dict[str, Any] | None:
    prefix = exam_title_prefix(run_id)
    candidates: list[dict[str, Any]] = []
    for exam in iter_pages(teacher, "/exams", params={"status": "published"}):
        title = exam.get("title")
        if isinstance(title, str) and title.startswith(prefix):
            candidates.append(exam)
    candidates.sort(key=lambda item: int(item.get("id") or 0), reverse=True)
    for exam in candidates:
        if exam_is_reusable(teacher, exam, now):
            return exam
    return None


def create_exam(
    teacher: ApiClient,
    paper_id: int,
    run_id: str,
    now: datetime,
) -> dict[str, Any]:
    title = f"{exam_title_prefix(run_id)} {now.strftime('%Y%m%dT%H%M%SZ')}"
    payload = {
        "paper_id": paper_id,
        "title": title,
        "start_at": iso_utc(now - timedelta(minutes=5)),
        "end_at": iso_utc(now + timedelta(minutes=EXAM_WINDOW_MIN)),
        "duration_min": EXAM_DURATION_MIN,
        "shuffle_questions": False,
        "shuffle_options": False,
        "publish": True,
    }
    response = teacher.request("POST", "/exams", json_body=payload, expected=(201,))
    exam = response.json()
    log(
        f"考试：新建并发布 id={exam.get('id')} title={title!r}"
        f"（start_at=now-5m, end_at=now+{EXAM_WINDOW_MIN}m,"
        f" duration_min={EXAM_DURATION_MIN}）"
    )
    return exam


# --------------------------------------------------------------------------- #
# 校验 / 输出
# --------------------------------------------------------------------------- #
def verify_setup(
    api_url: str,
    *,
    students: list[dict[str, Any]],
    teacher_username: str,
    exam_id: int,
    qa_password: str,
) -> None:
    """登录首/末两名学生核对 /me/exams，再登录教师核对考试详情。"""
    for student in (students[0], students[-1]):
        client = ApiClient(api_url)
        try:
            try:
                client.login(student["username"], qa_password)
            except PrepareError as exc:
                raise PrepareError(
                    f"学生 {student['username']} 登录失败：{exc}。"
                    "若口令已变更，请用 --reset-passwords 重置测试账号口令后重跑。"
                ) from exc
            found = None
            for item in iter_pages(client, "/me/exams"):
                if int(item.get("exam_id") or 0) == exam_id:
                    found = item
                    break
            if found is None:
                raise PrepareError(
                    f"学生 {student['username']} 的 /me/exams 看不到考试 {exam_id}"
                    "（检查考试是否 published / 时间窗是否覆盖当前）"
                )
            session_status = found.get("session_status")
            if session_status != "not_started":
                raise PrepareError(
                    f"考试 {exam_id} 对学生 {student['username']} 已是"
                    f" {session_status!r}，不是全新状态；请重跑 prepare_data.py"
                    "（会自动轮换考试）"
                )
        finally:
            client.close()

    teacher = ApiClient(api_url)
    try:
        teacher.login(teacher_username, qa_password)
        teacher.request("GET", f"/exams/{exam_id}", expected=(200,))
    finally:
        teacher.close()
    log("校验通过：首/末名学生可见考试且 session_status=not_started；教师可读考试详情")


def build_payload(
    *,
    run_id: str,
    api_url: str,
    exam: dict[str, Any],
    paper_id: int,
    paper_title: str,
    teacher_id: int,
    teacher_username: str,
    students: list[dict[str, Any]],
    password_env: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "task": "TASK-QA-003",
        "run_id": run_id,
        "api_url": api_url,
        "generated_at": iso_utc(utcnow()),
        "password_env": password_env,
        "exam": {
            "exam_id": int(exam["id"]),
            "title": exam["title"],
            "paper_id": paper_id,
            "paper_title": paper_title,
            "start_at": exam["start_at"],
            "end_at": exam["end_at"],
            "duration_min": exam["duration_min"],
            "status": exam["status"],
        },
        "teacher": {"user_id": teacher_id, "username": teacher_username},
        "students": students,
        "notes": (
            "口令不落盘：k6 从 EXAM_QA_PASSWORD 读取，必须与 prepare_data.py 创建账号时一致。"
            "每个 exam 只适合一轮压测；重跑 prepare_data.py 会在上一轮产生会话后自动轮换试卷+考试。"
        ),
    }


def write_payload(payload: dict[str, Any], out_path: Path) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")

    # k6 的 handleSummary 不会自动创建目录；这里提前建好 reports/perf/<run_id>/
    report_dir = REPO_ROOT / "reports" / "perf" / payload["run_id"]
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "prepare.json").write_text(text, encoding="utf-8")

    # stdout 只输出 JSON（学生账号清单 + 考试信息），供 `> students.json` 或人工核对
    print(text, end="")


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def run(args: argparse.Namespace) -> None:
    run_id = validate_run_id(args.run_id)
    api_url = args.api_url.rstrip("/")
    if not api_url.startswith(("http://", "https://")):
        raise PrepareError(f"--api-url 必须是 http(s) 地址，当前为 {args.api_url!r}")

    qa_password = require_env(args.password_env)
    validate_password(qa_password, args.password_env)
    admin_password = require_env(args.admin_password_env)
    validate_password(admin_password, args.admin_password_env)

    out_path = Path(args.out).expanduser()
    if not str(args.out).strip():
        raise PrepareError("--out 不能为空")
    log(f"API={api_url} run_id={run_id} 输出={out_path}")
    log(
        "注意：本脚本应在**独立性能库**执行；禁止对共享 staging 直接压满"
        "（TASK-QA-003 forbidden）。"
    )

    teacher_username = f"perf-{run_id}-teacher"
    student_names = [
        f"perf-{run_id}-stu-{number:03d}" for number in range(1, STUDENT_COUNT + 1)
    ]

    # ---- 账号（admin） ---------------------------------------------------- #
    admin = ApiClient(api_url)
    try:
        try:
            admin.login(args.admin_username, admin_password)
        except PrepareError as exc:
            raise PrepareError(
                f"admin {args.admin_username!r} 登录失败：{exc}。"
                f"请确认 {args.admin_password_env} 正确，且已按 runbook §3.5 创建首个 admin。"
            ) from exc
        log(f"admin 登录成功：{args.admin_username}")
        user_index = fetch_users(admin)
        log(f"用户列表：已有 {len(user_index)} 个账号（分页拉取）")

        teacher_id = ensure_account(
            admin,
            user_index,
            username=teacher_username,
            display_name=f"性能教师 {run_id}",
            role=TEACHER_ROLE,
            password=qa_password,
            reset_password=args.reset_passwords,
        )
        students: list[dict[str, Any]] = []
        created = 0
        for number, username in enumerate(student_names, start=1):
            existed = username in user_index
            user_id = ensure_account(
                admin,
                user_index,
                username=username,
                display_name=f"性能学生 {number:03d}",
                role=STUDENT_ROLE,
                password=qa_password,
                reset_password=args.reset_passwords,
            )
            if not existed:
                created += 1
            students.append(
                {
                    "user_id": user_id,
                    "username": username,
                    "display_name": f"性能学生 {number:03d}",
                }
            )
        log(
            f"账号：教师 1 名（id={teacher_id}），学生 {len(students)} 名"
            f"（新建 {created}，复用 {len(students) - created}）"
        )
    finally:
        admin.close()

    # ---- 题目 / 试卷 / 考试（教师） -------------------------------------- #
    teacher = ApiClient(api_url)
    try:
        try:
            teacher.login(teacher_username, qa_password)
        except PrepareError as exc:
            raise PrepareError(
                f"教师 {teacher_username!r} 登录失败：{exc}。"
                "若该 run_id 的账号是上一轮用别的口令创建的，请加 --reset-passwords 重跑。"
            ) from exc
        log(f"教师登录成功：{teacher_username}")

        question_ids = ensure_questions(teacher, run_id)

        exam_now = utcnow()
        exam = find_reusable_exam(teacher, run_id, exam_now)
        if exam is not None:
            paper_id = int(exam["paper_id"])
            paper_detail = teacher.request(
                "GET", f"/papers/{paper_id}", expected=(200,)
            ).json()
            paper_title = str(paper_detail.get("title") or "")
            log(
                f"考试：复用进行中考试 id={exam.get('id')}"
                f" title={exam.get('title')!r}（0 会话，时间窗有效）"
            )
        else:
            paper_id, paper_title = choose_or_create_paper(
                teacher, run_id, question_ids
            )
            exam = create_exam(teacher, paper_id, run_id, exam_now)
    finally:
        teacher.close()

    if not args.skip_verify:
        verify_setup(
            api_url,
            students=students,
            teacher_username=teacher_username,
            exam_id=int(exam["id"]),
            qa_password=qa_password,
        )

    payload = build_payload(
        run_id=run_id,
        api_url=api_url,
        exam=exam,
        paper_id=paper_id,
        paper_title=paper_title,
        teacher_id=teacher_id,
        teacher_username=teacher_username,
        students=students,
        password_env=args.password_env,
    )
    write_payload(payload, out_path)
    log(f"完成：考试 id={payload['exam']['exam_id']}，学生数={len(students)}")
    log(
        "下一步：`k6 run tests/perf/k6/exam_flow.js`"
        f"（EXAM_RUN_ID={run_id} EXAM_QA_PASSWORD=*** EXAM_API_URL={api_url}）"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prepare_data.py",
        description="TASK-QA-003 性能冒烟数据准备（幂等；只走公开 API）",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--api-url",
        default=env_value("EXAM_API_URL") or DEFAULT_API_URL,
        help=f"API 根地址（含 /api 前缀），默认 {DEFAULT_API_URL}",
    )
    parser.add_argument(
        "--run-id",
        default=env_value("EXAM_RUN_ID") or DEFAULT_RUN_ID,
        help=f"数据命名空间（1–40 位 [A-Za-z0-9._-]），默认 {DEFAULT_RUN_ID}；"
        "同一 run_id 重跑会复用账号/题目，并在上一轮有会话时轮换考试",
    )
    parser.add_argument(
        "--admin-username",
        default=env_value("EXAM_ADMIN_USERNAME") or DEFAULT_ADMIN_USERNAME,
        help=f"admin 用户名，默认 {DEFAULT_ADMIN_USERNAME}",
    )
    parser.add_argument(
        "--admin-password-env",
        default=ADMIN_PASSWORD_ENV,
        metavar="VAR",
        help=f"admin 口令所在环境变量名，默认 {ADMIN_PASSWORD_ENV}",
    )
    parser.add_argument(
        "--password-env",
        default=PASSWORD_ENV,
        metavar="VAR",
        help=f"测试账号统一口令所在环境变量名，默认 {PASSWORD_ENV}",
    )
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUTPUT),
        help=f"学生账号清单输出路径，默认 {DEFAULT_OUTPUT}",
    )
    parser.add_argument(
        "--reset-passwords",
        action="store_true",
        help="对已存在的测试账号 PATCH 重置为当前口令（换口令后复用旧账号时使用；"
        "会吊销这些账号的既有会话）",
    )
    parser.add_argument(
        "--skip-verify",
        action="store_true",
        help="跳过末端的登录/可见性校验（仅排障时使用）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        run(args)
    except PrepareError as exc:
        log(f"失败：{exc}")
        return 1
    except httpx.HTTPError as exc:  # 兜底：不在日志里输出连接串/口令
        log(f"失败：HTTP 客户端错误（{type(exc).__name__}）")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
