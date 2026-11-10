"""统一错误码与 `AppError`。

契约（contracts/openapi.yaml#/components/schemas/Error）：

    {"code": "STRING_CODE", "message": "人类可读", "request_id": "uuid"}

错误码与 HTTP 状态的对应关系见 docs/10-arch.md「错误码约定」。
所有业务错误都应抛出 `AppError`，由 `app.main` 的异常处理器统一渲染。
"""

from __future__ import annotations

from typing import Final

# 认证 / 授权
AUTH_REQUIRED: Final[str] = "AUTH_REQUIRED"
AUTH_INVALID_CREDENTIALS: Final[str] = "AUTH_INVALID_CREDENTIALS"
CSRF_INVALID: Final[str] = "CSRF_INVALID"
FORBIDDEN: Final[str] = "FORBIDDEN"

# 通用
NOT_FOUND: Final[str] = "NOT_FOUND"
VALIDATION_ERROR: Final[str] = "VALIDATION_ERROR"
CONFLICT: Final[str] = "CONFLICT"
METHOD_NOT_ALLOWED: Final[str] = "METHOD_NOT_ALLOWED"
INTERNAL_ERROR: Final[str] = "INTERNAL_ERROR"
HTTP_ERROR: Final[str] = "HTTP_ERROR"

# 业务状态冲突（docs/10-arch.md 错误码约定）
EXAM_NOT_STARTED: Final[str] = "EXAM_NOT_STARTED"
EXAM_EXPIRED: Final[str] = "EXAM_EXPIRED"
QUESTION_IN_USE: Final[str] = "QUESTION_IN_USE"
PAPER_FROZEN: Final[str] = "PAPER_FROZEN"
NOT_SUBMITTED: Final[str] = "NOT_SUBMITTED"
IMPORT_PARTIAL: Final[str] = "IMPORT_PARTIAL"


class AppError(Exception):
    """业务错误：`code` 稳定可供前端判断，`message` 面向用户。"""

    def __init__(self, code: str, message: str, http_status: int = 400) -> None:
        self.code = code
        self.message = message
        self.http_status = http_status
        super().__init__(message)

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


def auth_required(message: str = "未登录或会话已失效") -> AppError:
    return AppError(AUTH_REQUIRED, message, 401)


def forbidden(message: str = "无权访问该资源") -> AppError:
    return AppError(FORBIDDEN, message, 403)


def not_found(message: str = "资源不存在") -> AppError:
    return AppError(NOT_FOUND, message, 404)


def conflict(code: str = CONFLICT, message: str = "状态冲突") -> AppError:
    return AppError(code, message, 409)


def validation_error(message: str = "参数校验失败") -> AppError:
    # 契约中校验失败映射到 422（components/responses/ValidationError）
    return AppError(VALIDATION_ERROR, message, 422)
