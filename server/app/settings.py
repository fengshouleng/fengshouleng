"""应用配置：全部来自环境变量（pydantic-settings）。

环境变量名与字段名大小写不敏感，例如 `APP_ENV` 映射到 `app_env`、
`DATABASE_URL` 映射到 `database_url`、`SESSION_TTL_HOURS` 映射到 `session_ttl_hours`。
本地可复制 `.env.example` 为 `.env`。
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """运行时配置（均有默认值，便于本地直接启动）。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = "local"
    app_version: str = "1.0.0"

    # 本地/测试用 SQLite；生产为 postgresql+psycopg://user:pass@host:5432/dbname
    database_url: str = "sqlite:///./app.db"

    # 与契约一致：服务端会话 8h 过期
    session_ttl_hours: int = 8

    # 请求追踪头；未携带时服务端生成 uuid4 并回写
    request_id_header: str = "X-Request-ID"

    # 所有业务接口统一挂载在 /api 前缀（见 contracts/openapi.yaml servers）
    api_prefix: str = "/api"

    # PostgreSQL 连接池（每个 worker 独立）。默认值面向「单 worker + 40 线程」；
    # 多 worker 部署时按 PG max_connections 摊分，例如 4 worker 用 10/10。
    db_pool_size: int = 20
    db_max_overflow: int = 30
    db_pool_timeout: int = 10
    db_pool_recycle: int = 1800

    @property
    def is_sqlite(self) -> bool:
        """SQLite 需要 `check_same_thread=False` 才能配合多线程测试客户端。"""
        return self.database_url.startswith("sqlite")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程内单例配置；测试可直接构造 Settings 传给 create_app()。"""
    return Settings()
