"""FastAPI 应用工厂与全局中间件 / 异常处理。

- `create_app()`：应用工厂（uvicorn 入口用模块级 `app`）
- X-Request-ID：入站有则沿用，无则生成 uuid4，并回写响应头
- 访问日志：每个请求一行 JSON 写 stdout（TASK-BE-008；字段见
  `app/middleware/access_log.py`，可被 deploy/monitoring/slow_requests.sh 解析）
- 统一错误体：`{code, message, request_id}`（未知路由、参数校验、AppError、
  未捕获异常均遵守；见 contracts/openapi.yaml#/components/schemas/Error）
- 路由：健康检查同时暴露 `/healthz`（探针）与 `/api/healthz`（契约路径），
  其余业务路由统一挂在 `/api` 前缀下
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import errors
from app.api import api_router
from app.api.health import router as health_router
from app.errors import AppError
from app.middleware.access_log import AccessLogMiddleware
from app.settings import Settings, get_settings


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """启动预热（docs/61 §10.2.11）：把连接池 / 冻结快照 / 题面视图的冷启动成本
    挪到开始接流量之前。预热失败只记日志，绝不让服务起不来。

    放在线程里执行：预热是同步 DB 调用，不能阻塞事件循环；uvicorn 会等 lifespan
    startup 完成后才开始 serve，因此不会出现「半个进程热、半个冷」的竞态。
    """
    resolved = getattr(application.state, "settings", None)
    if isinstance(resolved, Settings) and resolved.warmup_enabled:
        from app.warmup import warm_up

        # warm_up 内部已逐步兜底；这里只防「连兜底都意外抛」把启动搞挂
        with contextlib.suppress(Exception):
            await asyncio.to_thread(warm_up, resolved)
    yield


# HTTP 状态 -> 错误码（contracts/openapi.yaml 的 400/401/403/404/409/422/500 分支）
HTTP_STATUS_TO_CODE: dict[int, str] = {
    400: errors.VALIDATION_ERROR,
    401: errors.AUTH_REQUIRED,
    403: errors.FORBIDDEN,
    404: errors.NOT_FOUND,
    405: errors.METHOD_NOT_ALLOWED,
    409: errors.CONFLICT,
    422: errors.VALIDATION_ERROR,
    500: errors.INTERNAL_ERROR,
}


def _request_id(request: Request) -> str:
    existing = getattr(request.state, "request_id", None)
    if isinstance(existing, str) and existing:
        return existing
    return str(uuid.uuid4())


def _request_id_header(request: Request) -> str:
    settings = getattr(request.app.state, "settings", None)
    if isinstance(settings, Settings):
        return settings.request_id_header
    return "X-Request-ID"


def error_response(request: Request, http_status: int, code: str, message: str) -> JSONResponse:
    """构造统一错误响应（同时回写 request_id 响应头）。"""
    request_id = _request_id(request)
    return JSONResponse(
        status_code=http_status,
        content={"code": code, "message": message, "request_id": request_id},
        headers={_request_id_header(request): request_id},
    )


def _format_validation_error(exc: RequestValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error["loc"])
        message = str(error["msg"])
        parts.append(f"{location}: {message}" if location else message)
    return "; ".join(parts) or "请求参数校验失败"


def create_app(settings: Settings | None = None) -> FastAPI:
    """应用工厂；测试可传入自定义 Settings（临时 SQLite 等）。"""
    resolved = settings or get_settings()
    application = FastAPI(
        title="选择题答题系统 API",
        version=resolved.app_version,
        docs_url=f"{resolved.api_prefix}/docs",
        redoc_url=None,
        openapi_url=f"{resolved.api_prefix}/openapi.json",
        lifespan=lifespan,
    )
    application.state.settings = resolved

    @application.middleware("http")
    async def request_id_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        header = resolved.request_id_header
        request_id = request.headers.get(header) or str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[header] = request_id
        return response

    # 访问日志：注册在 request_id 之后（Starlette 后注册者更靠外层），
    # 写日志阶段读取 request.state.request_id，与响应头/错误体保持同一个 id。
    application.add_middleware(AccessLogMiddleware)

    @application.exception_handler(AppError)
    def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return error_response(request, exc.http_status, exc.code, exc.message)

    @application.exception_handler(RequestValidationError)
    def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # 契约中参数校验失败为 422（components/responses/ValidationError）
        return error_response(request, 422, errors.VALIDATION_ERROR, _format_validation_error(exc))

    @application.exception_handler(StarletteHTTPException)
    def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = HTTP_STATUS_TO_CODE.get(exc.status_code, errors.HTTP_ERROR)
        message = exc.detail if isinstance(exc.detail, str) else "请求失败"
        return error_response(request, exc.status_code, code, message)

    @application.exception_handler(Exception)
    def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        # 不向客户端泄漏内部细节；堆栈由 uvicorn/日志系统记录
        return error_response(request, 500, errors.INTERNAL_ERROR, "服务器内部错误")

    application.include_router(api_router, prefix=resolved.api_prefix)
    application.include_router(health_router, include_in_schema=False)
    return application


# uvicorn 入口：`uvicorn app.main:app`
app = create_app()
