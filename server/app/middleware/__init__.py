"""ASGI 中间件包：结构化访问日志（TASK-BE-008）。

只放纯 ASGI 中间件（不经 Starlette `BaseHTTPMiddleware`），按需在
`app/main.py` 的 `create_app()` 里用 `add_middleware()` 注册。
"""

from __future__ import annotations

__all__: list[str] = []
