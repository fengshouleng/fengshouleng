"""API 层入口：路由聚合后由 `create_app()` 统一挂载到 `/api` 前缀。"""

from __future__ import annotations

from app.api.router import api_router

__all__ = ["api_router"]
