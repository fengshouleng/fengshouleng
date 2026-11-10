"""tests/e2e/conftest.py — Playwright E2E 夹具与证据收集（TASK-QA-001）

证据（每次运行）落盘到 ``reports/e2e/{run_id}/{test_name}/``：

- ``trace.zip``：Playwright trace（screenshots + snapshots，可 `playwright show-trace`）；
- ``video/*.webm``：浏览器录像（context 关闭时落盘）；
- ``failure.png`` / ``failure.html`` / ``failure.txt``：失败时的截图、DOM 快照与现场信息；
- 关键步骤截图：由用例通过 ``e2e_support.screenshot()`` 主动保存。

失败时终端会额外打印服务端日志位置（``EXAM_SERVER_LOG``，默认
``reports/e2e/{run_id}/server.log``）。E2E 不负责启动/收集服务端日志，
起栈命令中的重定向见 ``tests/e2e/README.md``。
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORTS_ROOT = REPO_ROOT / "reports" / "e2e"

BASE_URL = os.environ.get("EXAM_BASE_URL", "http://127.0.0.1:5173").rstrip("/")
API_URL = os.environ.get("EXAM_API_URL", "http://127.0.0.1:8000/api").rstrip("/")
RUN_ID = os.environ.get("EXAM_E2E_RUN_ID") or datetime.now().strftime("%Y%m%d-%H%M%S")
E2E_ENABLED = os.environ.get("EXAM_E2E") == "1"
DEFAULT_TIMEOUT_MS = int(os.environ.get("EXAM_E2E_TIMEOUT_MS", "20000") or 20000)
SERVER_LOG_HINT = os.environ.get("EXAM_SERVER_LOG") or str(REPORTS_ROOT / RUN_ID / "server.log")


def _safe_name(raw: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip("-")
    return cleaned[:120] or "test"


@pytest.fixture(scope="session")
def run_id() -> str:
    """本次 E2E 运行 ID；同时用于数据前缀与证据目录。"""
    return RUN_ID


@pytest.fixture(scope="session")
def artifacts_dir() -> Path:
    """``reports/e2e/{run_id}/``（会话级）。"""
    path = REPORTS_ROOT / RUN_ID
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture()
def test_artifact_dir(artifacts_dir: Path, request: pytest.FixtureRequest) -> Path:
    """单条用例的独立证据目录，失败互不覆盖。"""
    path = artifacts_dir / _safe_name(request.node.name)
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture(scope="session")
def playwright_instance() -> Iterator[Any]:
    """启动 Playwright；缺少依赖时给出可执行的修复命令。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - 环境问题
        pytest.fail(
            "缺少 playwright：pip install -r tests/requirements.txt && "
            f"python -m playwright install chromium（{exc}）"
        )
    manager = sync_playwright().start()
    try:
        yield manager
    finally:
        manager.stop()


@pytest.fixture(scope="session")
def browser(playwright_instance: Any) -> Iterator[Any]:
    """Chromium 浏览器；``EXAM_E2E_HEADED=1`` 可本地可视化调试。"""
    browser_name = os.environ.get("EXAM_E2E_BROWSER", "chromium")
    browser_type = getattr(playwright_instance, browser_name, None)
    if browser_type is None:
        pytest.fail(f"不支持的浏览器 {browser_name!r}（可选 chromium / firefox / webkit）")
    headless = os.environ.get("EXAM_E2E_HEADED") != "1"
    slow_mo = int(os.environ.get("EXAM_E2E_SLOWMO_MS", "0") or 0)
    args = [item for item in os.environ.get("EXAM_E2E_BROWSER_ARGS", "").split(",") if item]
    try:
        instance = browser_type.launch(headless=headless, slow_mo=slow_mo, args=args)
    except Exception as exc:  # pragma: no cover - 环境问题
        pytest.fail(
            f"启动浏览器失败：{exc}\n"
            "提示：先执行 `python -m playwright install chromium`（CI 可加 --with-deps）"
        )
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture()
def context(browser: Any, test_artifact_dir: Path) -> Iterator[Any]:
    """每个用例独立 BrowserContext：独立 Cookie、trace、video（失败隔离）。"""
    video_dir = test_artifact_dir / "video"
    video_dir.mkdir(parents=True, exist_ok=True)
    ctx = browser.new_context(
        base_url=BASE_URL,
        viewport={"width": 1440, "height": 900},
        record_video_dir=str(video_dir),
        record_video_size={"width": 1280, "height": 720},
        locale="zh-CN",
    )
    ctx.set_default_timeout(DEFAULT_TIMEOUT_MS)
    ctx.set_default_navigation_timeout(60_000)
    ctx.tracing.start(
        screenshots=True,
        snapshots=True,
        sources=False,
        title=test_artifact_dir.name,
    )
    try:
        yield ctx
    finally:
        try:
            ctx.tracing.stop(path=str(test_artifact_dir / "trace.zip"))
        except Exception as exc:  # pragma: no cover - 证据失败不掩盖测试结果
            print(f"[e2e] trace 保存失败：{exc}")
        try:
            ctx.close()
        except Exception as exc:  # pragma: no cover
            print(f"[e2e] context 关闭失败：{exc}")


@pytest.fixture()
def page(context: Any, request: pytest.FixtureRequest, test_artifact_dir: Path) -> Iterator[Any]:
    """页面对象；失败 hook 通过 ``request.node`` 上的引用保存 DOM/截图。"""
    page = context.new_page()
    page.set_default_timeout(DEFAULT_TIMEOUT_MS)
    request.node._e2e_page = page  # type: ignore[attr-defined]
    request.node._e2e_artifact_dir = test_artifact_dir  # type: ignore[attr-defined]
    try:
        yield page
    finally:
        try:
            page.close()
        except Exception:  # pragma: no cover
            pass


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[Any]):
    """失败证据：截图 + DOM 快照 + 现场信息 + 服务端日志位置。"""
    outcome = yield
    report = outcome.get_result()
    setattr(item, f"rep_{report.when}", report)

    if report.when not in ("setup", "call") or not report.failed:
        return

    page = getattr(item, "_e2e_page", None)
    artifact_dir = getattr(item, "_e2e_artifact_dir", None)
    if page is None or artifact_dir is None:
        return

    try:
        page.screenshot(path=str(artifact_dir / "failure.png"), full_page=True)
    except Exception as exc:
        print(f"[e2e] 失败截图不可用：{exc}")

    try:
        (artifact_dir / "failure.html").write_text(page.content(), encoding="utf-8")
    except Exception as exc:
        print(f"[e2e] DOM 快照不可用：{exc}")

    try:
        (artifact_dir / "failure.txt").write_text(
            "\n".join(
                [
                    f"test: {item.nodeid}",
                    f"url: {page.url}",
                    f"base_url: {BASE_URL}",
                    f"api_url: {API_URL}",
                    f"server_log: {SERVER_LOG_HINT}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"[e2e] failure.txt 不可用：{exc}")

    print(f"\n[e2e] 失败证据目录：{artifact_dir}")
    print(f"[e2e] 服务端日志位置：{SERVER_LOG_HINT}")
    print(f"[e2e] 回放 trace：playwright show-trace {artifact_dir / 'trace.zip'}")


def pytest_report_header(config: pytest.Config) -> list[str]:
    if not E2E_ENABLED:
        return []
    return [
        "E2E(Playwright): EXAM_E2E=1",
        f"  base_url={BASE_URL}",
        f"  api_url={API_URL}",
        f"  run_id={RUN_ID}",
        f"  evidence={REPORTS_ROOT / RUN_ID}",
    ]


def pytest_terminal_summary(terminalreporter: Any, exitstatus: int, config: pytest.Config) -> None:
    if not E2E_ENABLED:
        return
    terminalreporter.write_sep("-", "E2E 证据")
    terminalreporter.write_line(f"证据目录: {REPORTS_ROOT / RUN_ID}")
    terminalreporter.write_line(f"服务端日志: {SERVER_LOG_HINT}")
    terminalreporter.write_line("回放失败 trace: playwright show-trace <run_dir>/<test>/trace.zip")


# 让 `from conftest import ...` 之外的同目录模块导入稳定（pytest prepend 模式已处理，此处兜底）
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
