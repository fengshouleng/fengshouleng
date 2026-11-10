"""tests/security/conftest.py — TASK-QA-002 安全负例共享夹具。

设计约束
--------
1. **黑盒**：只通过 HTTP 访问运行中的 API，不 import ``server/`` 的任何代码，
   也不依赖 pytest 插件之外的安装（pytest + httpx）。
2. **默认不跑**：``EXAM_API_URL`` 未设置时，各测试模块用 ``pytestmark`` 整模块 skip；
   避免 CI/本地在没有起栈时出现“假红”。
3. **脱敏**：断言失败只输出 method/url/status + 截断后的响应体，密码、CSRF token、
   Cookie 值在输出前统一替换为 ``***``；见 :func:`redact`。
4. **账号**：默认取 ``python -m app.cli seed-demo`` 生成的 ``demo-teacher`` /
   ``demo-student-1`` / ``demo-student-2``；管理员账号需用
   ``python -m app.cli create-admin`` 单独创建（seed-demo 不创建 admin）。
   所有默认值**仅限本地开发**，staging/prod 必须用环境变量显式覆盖。
5. **逐用例可运行**：需要前置数据的用例通过 session 级 fixture 按需构造
   （例如 ``victim_scenario``），没有前置数据的用例不会被拖垮。

环境变量
--------
``EXAM_API_URL``
    运行中的 API 根（含 ``/api``），例如 ``http://127.0.0.1:8000/api``。
    **未设置 → 整目录 skip**；默认值仅用于显式设置后的回退。
``EXAM_QA_PASSWORD``
    测试账号统一密码；默认 ``dev-demo-password-123``（仅本地开发）。
``EXAM_TEACHER_USERNAME`` / ``EXAM_STUDENT_A_USERNAME`` /
``EXAM_STUDENT_B_USERNAME`` / ``EXAM_ADMIN_USERNAME``
    账号覆盖；默认 ``demo-teacher`` / ``demo-student-1`` / ``demo-student-2`` / ``qa-admin``。
``EXAM_TEACHER_PASSWORD`` / ``EXAM_STUDENT_A_PASSWORD`` / ``EXAM_STUDENT_B_PASSWORD`` /
``EXAM_ADMIN_PASSWORD``
    按角色覆盖口令；未设置时回退到 ``EXAM_QA_PASSWORD``。
"""

from __future__ import annotations

import os
import re
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    # 让测试模块可以稳定地 `from conftest import ...`（与 tests/e2e 的做法一致）
    sys.path.insert(0, str(_HERE))

try:  # 缺 httpx 时不要在 collection 阶段直接 ImportError（skip 场景应仍可收集）
    import httpx
except ImportError:  # pragma: no cover - 环境缺依赖
    httpx = None  # type: ignore[assignment]


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
DEFAULT_API_URL = "http://127.0.0.1:8000/api"
DEFAULT_DEV_PASSWORD = "dev-demo-password-123"
SESSION_COOKIE = "session_id"
CSRF_HEADER = "X-CSRF-Token"


def _env(*names: str, default: str) -> str:
    """取第一个非空环境变量（去首尾空白），否则返回默认值。"""
    for name in names:
        value = os.environ.get(name)
        if value is not None and value.strip():
            return value.strip()
    return default


API_URL = _env("EXAM_API_URL", default=DEFAULT_API_URL)
QA_PASSWORD = _env("EXAM_QA_PASSWORD", default=DEFAULT_DEV_PASSWORD)

TEACHER_USERNAME = _env("EXAM_TEACHER_USERNAME", default="demo-teacher")
STUDENT_A_USERNAME = _env(
    "EXAM_STUDENT_A_USERNAME", "EXAM_STUDENT_USERNAME", default="demo-student-1"
)
STUDENT_B_USERNAME = _env("EXAM_STUDENT_B_USERNAME", default="demo-student-2")
ADMIN_USERNAME = _env("EXAM_ADMIN_USERNAME", default="qa-admin")

TEACHER_PASSWORD = _env("EXAM_TEACHER_PASSWORD", default=QA_PASSWORD)
STUDENT_A_PASSWORD = _env(
    "EXAM_STUDENT_A_PASSWORD", "EXAM_STUDENT_PASSWORD", default=QA_PASSWORD
)
STUDENT_B_PASSWORD = _env("EXAM_STUDENT_B_PASSWORD", default=QA_PASSWORD)
ADMIN_PASSWORD = _env("EXAM_ADMIN_PASSWORD", default=QA_PASSWORD)


# --------------------------------------------------------------------------- #
# 脱敏与通用工具
# --------------------------------------------------------------------------- #
_SECRETS: set[str] = set()


def remember_secret(value: str | None) -> None:
    """登记需要在日志/断言失败信息中脱敏的字符串（密码、CSRF token 等）。"""
    if value:
        _SECRETS.add(value)


def redact(text: str) -> str:
    """把已登记的秘密、Cookie 值、CSRF token 替换为 ``***``。"""
    cleaned = text
    for secret in sorted(_SECRETS, key=len, reverse=True):
        if secret:
            cleaned = cleaned.replace(secret, "***")
    cleaned = re.sub(r"(?i)(session_id=)[^;,\s\"']+", r"\1***", cleaned)
    cleaned = re.sub(
        r"(?i)(csrf[_-]?token[\"']?\s*[:=]\s*[\"']?)[^\"'\s,}]+",
        r"\1***",
        cleaned,
    )
    return cleaned


def utc_now() -> datetime:
    """秒级 UTC（安全用例允许 ±5s 漂移）。"""
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(value: datetime) -> str:
    """契约要求的 ISO 8601 UTC；统一 ``Z`` 结尾。"""
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def unique(prefix: str) -> str:
    """跨 run / 同 run 多用例唯一的名称前缀。"""
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


# --------------------------------------------------------------------------- #
# HTTP 客户端
# --------------------------------------------------------------------------- #
class Api:
    """同步 httpx 客户端：自动带 Cookie，写请求默认自动带 ``X-CSRF-Token``。"""

    def __init__(
        self,
        base_url: str = API_URL,
        label: str = "api",
        timeout: float = 20.0,
    ) -> None:
        if httpx is None:  # pragma: no cover - 环境缺依赖
            pytest.fail("缺少 httpx：请先执行 `pip install -r tests/requirements.txt`")
        self.base_url = base_url.rstrip("/")
        self.label = label
        self.client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            follow_redirects=False,
        )
        self.csrf: str | None = None
        self.user: dict[str, Any] | None = None

    # --- 生命周期 --------------------------------------------------------- #
    def close(self) -> None:
        self.client.close()

    def clear_cookies(self) -> None:
        self.client.cookies.clear()

    def session_token(self) -> str | None:
        """当前 ``session_id`` Cookie 的明文值（仅用于测试旧 Cookie 复用）。"""
        for cookie in self.client.cookies.jar:
            if cookie.name == SESSION_COOKIE:
                return cookie.value
        return None

    # --- 失败信息（脱敏） ------------------------------------------------- #
    def describe(self, response: Any) -> str:
        request = response.request
        return redact(
            f"[{self.label}] {request.method} {request.url} -> {response.status_code}; "
            f"body={response.text[:800]!r}"
        )

    # --- 请求 ------------------------------------------------------------- #
    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        data: Any = None,
        files: Any = None,
        params: Any = None,
        csrf: Any = "auto",
        headers: dict[str, str] | None = None,
        cookies: Any = None,
    ) -> Any:
        """发请求。

        ``csrf="auto"``：写请求自动带当前会话 token；``csrf=None``：故意不带；
        ``csrf="<value>"``：发送指定（通常是错误）token。
        """
        method = method.upper()
        merged_headers: dict[str, str] = {"Accept": "application/json"}
        if headers:
            merged_headers.update(headers)

        if csrf == "auto":
            if method not in ("GET", "HEAD", "OPTIONS") and self.csrf:
                merged_headers[CSRF_HEADER] = self.csrf
        elif isinstance(csrf, str):
            merged_headers[CSRF_HEADER] = csrf
        # csrf is None：刻意省略 X-CSRF-Token

        return self.client.request(
            method,
            path,
            json=json_body,
            data=data,
            files=files,
            params=params,
            headers=merged_headers,
            cookies=cookies,
        )

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> Any:
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> Any:
        return self.request("PUT", path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> Any:
        return self.request("PATCH", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Any:
        return self.request("DELETE", path, **kwargs)

    # --- 登录 ------------------------------------------------------------- #
    def attempt_login(self, username: str, password: str) -> Any:
        """登录请求本身（不断言成功）；用于 AC-02 的失败对比。"""
        remember_secret(password)
        return self.request(
            "POST",
            "/auth/login",
            json_body={"username": username, "password": password},
            csrf=None,
        )

    def login(self, username: str, password: str) -> dict[str, Any]:
        response = self.attempt_login(username, password)
        assert response.status_code == 200, f"登录失败：{self.describe(response)}"
        body = response.json()
        self.csrf = body.get("csrf_token")
        self.user = body.get("user")
        if self.csrf:
            remember_secret(self.csrf)
        return body


# --------------------------------------------------------------------------- #
# 断言工具
# --------------------------------------------------------------------------- #
def assert_error(
    response: Any,
    status: int,
    code: str,
    *,
    label: str = "",
) -> dict[str, Any]:
    """断言统一错误体：状态码 + code + 仅 ``code/message/request_id`` 三字段。"""
    request = response.request
    context = f"{label or 'error'} [{request.method} {request.url.path}]"
    assert response.status_code == status, (
        f"{context}: 期望 HTTP {status} / {code}，实际 {response.status_code}；"
        f"{redact(response.text[:800])}"
    )
    content_type = response.headers.get("content-type", "")
    assert "application/json" in content_type.lower(), (
        f"{context}: 错误响应必须是 application/json，实际 {content_type!r}"
    )
    try:
        body = response.json()
    except Exception as exc:  # pragma: no cover - 正常实现不会走到
        pytest.fail(
            f"{context}: 错误响应不是合法 JSON（{exc}）；{redact(response.text[:800])}"
        )
    assert isinstance(body, dict), f"{context}: 错误体必须是对象，实际 {type(body)!r}"
    assert set(body) == {"code", "message", "request_id"}, (
        f"{context}: 错误体字段必须恰为 code/message/request_id，实际 {sorted(body)}；"
        "403/404 不得夹带 items/score/selected/correct_answer 等业务字段"
    )
    assert body["code"] == code, (
        f"{context}: 期望 code={code}，实际 code={body['code']!r}，"
        f"message={body.get('message')!r}"
    )
    assert isinstance(body["message"], str) and body["message"].strip(), (
        f"{context}: message 必须是非空字符串"
    )
    try:
        uuid.UUID(str(body["request_id"]))
    except (ValueError, TypeError):
        pytest.fail(f"{context}: request_id 必须是 UUID，实际 {body['request_id']!r}")
    return body


def assert_json(response: Any, status: int = 200, *, label: str = "") -> Any:
    """断言状态码与 JSON Content-Type，返回解析后的 body。"""
    request = response.request
    context = f"{label or 'json'} [{request.method} {request.url.path}]"
    assert response.status_code == status, (
        f"{context}: 期望 HTTP {status}，实际 {response.status_code}；"
        f"{redact(response.text[:800])}"
    )
    content_type = response.headers.get("content-type", "")
    assert "application/json" in content_type.lower(), (
        f"{context}: 期望 application/json，实际 {content_type!r}"
    )
    return response.json()


def login_or_skip(
    api: Api,
    username: str,
    password: str,
    role: str,
) -> dict[str, Any]:
    """登录；账号不存在/口令不符（401）时 skip，并提示先 seed。

    401 既可能是“未 seed”也可能是“密码配错”，对安全套件都按环境前置缺失处理；
    5xx 或其它异常才是 fail。
    """
    response = api.attempt_login(username, password)
    if response.status_code == 401:
        pytest.skip(
            f"{role} 账号 {username!r} 登录返回 401。请先按 tests/security/README.md §2 "
            "执行 seed-demo / create-admin，或设置 EXAM_QA_PASSWORD（及按角色的覆盖变量）。"
        )
    if response.status_code != 200:
        pytest.fail(f"{role} 登录异常：{api.describe(response)}")
    body = response.json()
    api.csrf = body.get("csrf_token")
    api.user = body.get("user")
    if api.csrf:
        remember_secret(api.csrf)
    return body


# --------------------------------------------------------------------------- #
# 数据构造（全部经 API，符合 contracts/openapi.yaml v1.0.3；v1.0.3 未改请求模型）
# --------------------------------------------------------------------------- #
def _question_payload(
    *,
    stem: str | None = None,
    explanation: str | None = None,
) -> dict[str, Any]:
    tag = unique("qa-sec-question")
    return {
        "type": "single",
        "stem": stem or f"[{tag}] 安全负例题干",
        "options": [
            {"key": "A", "text": "选项 A"},
            {"key": "B", "text": "选项 B"},
            {"key": "C", "text": "选项 C"},
            {"key": "D", "text": "选项 D"},
        ],
        "answer": ["B"],
        "explanation": explanation or f"[{tag}] 安全负例解析",
        "difficulty": 1,
        "tags": ["qa-sec"],
    }


def _paper_payload(question_id: int, *, title: str | None = None) -> dict[str, Any]:
    return {
        "title": title or unique("qa-sec-paper"),
        "questions": [
            {
                "question_id": question_id,
                "order_no": 1,
                "points": 10.0,
                "partial_rule": "all_or_nothing",
            }
        ],
    }


def _exam_payload(
    paper_id: int,
    *,
    title: str | None = None,
    publish: bool,
) -> dict[str, Any]:
    now = utc_now()
    return {
        "paper_id": paper_id,
        "title": title or unique("qa-sec-exam"),
        "start_at": iso(now - timedelta(minutes=5)),
        "end_at": iso(now + timedelta(hours=2)),
        "duration_min": 10,
        "shuffle_questions": False,
        "shuffle_options": False,
        "publish": publish,
    }


def _create_question(teacher: Api, tag: str) -> dict[str, Any]:
    payload = _question_payload(
        stem=f"[{tag}] 安全负例题干",
        explanation=f"[{tag}] 安全负例解析",
    )
    response = teacher.post("/questions", json_body=payload)
    assert_json(response, 201, label="POST /questions（安全前置数据）")
    return response.json()


def _create_paper(teacher: Api, question_id: int, tag: str) -> dict[str, Any]:
    response = teacher.post(
        "/papers",
        json_body=_paper_payload(question_id, title=f"[{tag}] 安全负例试卷"),
    )
    assert_json(response, 201, label="POST /papers（安全前置数据）")
    return response.json()


def _create_exam(
    teacher: Api,
    paper_id: int,
    tag: str,
    *,
    publish: bool,
) -> dict[str, Any]:
    response = teacher.post(
        "/exams",
        json_body=_exam_payload(
            paper_id,
            title=f"[{tag}] 安全负例考试",
            publish=publish,
        ),
    )
    assert_json(response, 201, label="POST /exams（安全前置数据）")
    return response.json()


@dataclass(frozen=True)
class AccountHandle:
    """由 admin 通过 ``POST /users`` 创建并已登录的测试账号。"""

    api: Api
    username: str
    password: str
    user: dict[str, Any]


@dataclass(frozen=True)
class DisabledAccount:
    """已被停用的账号；``token`` 是停用前抓到的旧 Cookie。"""

    handle: AccountHandle
    token: str


@dataclass(frozen=True)
class VictimScenario:
    """IDOR / 枚举用例的受害者场景（教师 A + 学生 A）。"""

    question: dict[str, Any]
    paper: dict[str, Any]
    exam: dict[str, Any]
    session_id: int
    draft_exam: dict[str, Any]
    #: 教师 A 的 draft 试卷（其关联考试也是 draft）；用于 CHG-036 的「教师同角色他人
    #: draft 资源 → 403（不得 404）」负例，v1.0.3 新增。
    draft_paper: dict[str, Any]


def _create_account(
    admin: Api,
    api_url: str,
    *,
    role: str,
    label: str,
    password: str,
) -> AccountHandle:
    username = unique(f"qa-sec-{role}")
    payload = {
        "username": username,
        "display_name": f"QA-SEC {role}",
        "role": role,
        "password": password,
    }
    response = admin.post("/users", json_body=payload)
    assert response.status_code == 201, (
        f"admin 创建 {role} 账号失败：{admin.describe(response)}"
    )
    user = response.json()
    api = Api(api_url, label=label)
    login_or_skip(api, username, password, f"{role}/{label}")
    return AccountHandle(api=api, username=username, password=password, user=user)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def api_url() -> str:
    return API_URL


@pytest.fixture(scope="session")
def qa_password() -> str:
    return QA_PASSWORD


@pytest.fixture()
def client_factory(api_url: str) -> Iterator[Callable[..., Api]]:
    """按用例创建独立 httpx 会话；用例结束统一关闭。"""
    clients: list[Api] = []

    def make(label: str = "case") -> Api:
        client = Api(api_url, label=label)
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.close()


@pytest.fixture(scope="session")
def teacher(api_url: str) -> Iterator[Api]:
    api = Api(api_url, label="teacher")
    login_or_skip(api, TEACHER_USERNAME, TEACHER_PASSWORD, "teacher")
    yield api
    api.close()


@pytest.fixture(scope="session")
def student_a(api_url: str) -> Iterator[Api]:
    api = Api(api_url, label="student-a")
    login_or_skip(api, STUDENT_A_USERNAME, STUDENT_A_PASSWORD, "student A")
    yield api
    api.close()


@pytest.fixture(scope="session")
def student_b(api_url: str) -> Iterator[Api]:
    api = Api(api_url, label="student-b")
    login_or_skip(api, STUDENT_B_USERNAME, STUDENT_B_PASSWORD, "student B")
    yield api
    api.close()


@pytest.fixture(scope="session")
def admin(api_url: str) -> Iterator[Api]:
    api = Api(api_url, label="admin")
    login_or_skip(api, ADMIN_USERNAME, ADMIN_PASSWORD, "admin")
    yield api
    api.close()


@pytest.fixture(scope="session")
def teacher_b(api_url: str, admin: Api) -> Iterator[AccountHandle]:
    """第二个教师：用于“教师 B 改教师 A 的题目/试卷/考试”负例。"""
    handle = _create_account(
        admin,
        api_url,
        role="teacher",
        label="teacher-b",
        password=QA_PASSWORD,
    )
    yield handle
    handle.api.close()


@pytest.fixture(scope="session")
def disabled_student(api_url: str, admin: Api) -> Iterator[DisabledAccount]:
    """admin 创建并停用的学生；保留停用前的 Cookie 用于 AC-04。"""
    handle = _create_account(
        admin,
        api_url,
        role="student",
        label="disabled-student",
        password=QA_PASSWORD,
    )
    token = handle.api.session_token()
    assert token, "登录成功后未在 Cookie jar 中找到 session_id"
    response = admin.patch(
        f"/users/{handle.user['id']}",
        json_body={"is_active": False},
    )
    assert response.status_code == 200, (
        f"admin 停用账号失败：{admin.describe(response)}"
    )
    yield DisabledAccount(handle=handle, token=token)
    handle.api.close()


@pytest.fixture(scope="session")
def victim_scenario(teacher: Api, student_a: Api) -> VictimScenario:
    """构造：教师 A 出题/组卷/发布考试 + 学生 A 交卷；另建一场 draft 考试。"""
    tag = unique("qa-sec-victim")
    question = _create_question(teacher, tag)
    paper = _create_paper(teacher, int(question["id"]), tag)
    exam = _create_exam(teacher, int(paper["id"]), tag, publish=True)

    start = student_a.post(f"/exams/{exam['id']}/sessions")
    assert start.status_code in (200, 201), (
        f"学生 A 开始考试失败：{student_a.describe(start)}"
    )
    state = start.json()
    session_id = int(state["id"])

    save = student_a.put(
        f"/sessions/{session_id}/answers",
        json_body={
            "client_seq": 1,
            "answers": [
                {"question_id": int(question["id"]), "selected": ["B"]},
            ],
        },
    )
    assert_json(save, 200, label="PUT /sessions/{id}/answers（安全前置数据）")

    submit = student_a.post(f"/sessions/{session_id}/submit")
    submit_body = assert_json(
        submit, 200, label="POST /sessions/{id}/submit（安全前置数据）"
    )
    assert submit_body.get("status") == "submitted", (
        f"前置会话未进入 submitted：{submit_body!r}"
    )
    assert submit_body.get("replayed") is False, (
        f"首次交卷 replayed 应为 false：{submit_body!r}"
    )

    result = student_a.get(f"/sessions/{session_id}/result")
    assert_json(result, 200, label="GET /sessions/{id}/result（安全前置数据）")

    # matrix §2.1 #4/#13：学生创建 draft 考试的会话必须 404；该考试也不得出现在 /me/exams。
    # 试卷与考试都保持 draft（不发布 → 不冻结），用于 CHG-036 的教师侧 403 负例。
    draft_tag = f"{tag}-draft"
    draft_paper = _create_paper(teacher, int(question["id"]), draft_tag)
    assert draft_paper.get("status") == "draft", (
        f"draft 试卷前置数据状态不符：{draft_paper.get('status')!r}"
    )
    draft_exam = _create_exam(
        teacher,
        int(draft_paper["id"]),
        draft_tag,
        publish=False,
    )
    assert draft_exam.get("status") == "draft", (
        f"draft 考试前置数据状态不符：{draft_exam.get('status')!r}"
    )

    return VictimScenario(
        question=question,
        paper=paper,
        exam=exam,
        session_id=session_id,
        draft_exam=draft_exam,
        draft_paper=draft_paper,
    )
