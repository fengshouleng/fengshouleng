"""启动预热：把「首个请求才会付」的冷启动成本挪到启动阶段。

动机（docs/61 §10.2.11）：2026-11-11 的 CI 性能日志（run 42859094367）按 5s 时间桶拆开后
发现同一规模的 100 并发保存，t≈20s 那波服务端 p50=766ms，而 t≈170s 那波 p50=99ms——
**约 600ms 的尾部来自冷启动**，不是稳态容量问题。冷启动源：

1. 连接池空池：首批并发请求各自新建到 PostgreSQL 的连接（4 worker × 24 = 96 条），
   PG 每建一条连接都要 fork 后端进程；
2. 冻结快照 / 题面视图缓存为空：每 worker 第一次读都要查 `papers` 大 JSON 列并解析、
   构建 100 个响应模型；
3. pydantic 模型首次校验/序列化路径的惰性编译。

预热只做只读操作（`SELECT 1`、读已冻结试卷），失败一律降级为日志：**预热不了也不能
阻塞启动**（否则一次配置错误会让整个服务起不来）。

多 worker 注意：每个 uvicorn worker 各自预热，连接数合计 = `WEB_CONCURRENCY ×
(DB_POOL_SIZE + DB_MAX_OVERFLOW)`，必须仍小于 PostgreSQL 的 `max_connections`
（见 deploy/.env.example 的容量口径）。
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.engine import Engine, make_url

from app.db import get_engine, get_sessionmaker
from app.models.exam import Exam, ExamStatus
from app.models.paper import Paper
from app.settings import Settings, get_settings

log = logging.getLogger(__name__)

#: 预热时最多装载多少份已冻结试卷 / 多少场进行中的考试（防止大库上启动变慢）
MAX_PRELOAD_PAPERS = 64
MAX_PRELOAD_EXAMS = 64


def warm_connection_pool(engine: Engine, target: int) -> int:
    """同时开 `target` 条连接再全部归还，把 QueuePool 填满（含 overflow）。

    返回实际预热成功的连接数；SQLite（测试/本地默认）没有真实连接池开销，直接跳过。
    """
    if engine.dialect.name != "postgresql" or target <= 0:
        return 0
    held: list[Any] = []
    try:
        for _ in range(target):
            connection = engine.connect()
            connection.execute(text("SELECT 1"))
            held.append(connection)
    except Exception as exc:  # pragma: no cover - 取决于部署容量，失败只降级
        log.warning("连接池预热中断（已建 %s 条）：%s", len(held), exc)
    finally:
        for connection in held:
            connection.close()
    return len(held)


def warm_business_caches() -> dict[str, int]:
    """预热冻结快照与题面视图缓存（只读）。返回装载数量统计。"""
    from app.scoring import service as scoring
    from app.sessions import service as sessions

    summary = {"papers": 0, "questions": 0, "exams": 0}
    session_factory = get_sessionmaker()
    with session_factory() as db:
        paper_ids = db.scalars(
            select(Paper.id)
            .where(Paper.frozen_snapshot.is_not(None))
            .order_by(Paper.id.desc())
            .limit(MAX_PRELOAD_PAPERS)
        ).all()
        for paper_id in paper_ids:
            paper = db.get(Paper, paper_id)
            if paper is None:
                continue
            try:
                summary["papers"] += 1
                scoring.preload_snapshot(db, paper)
            except Exception as exc:  # pragma: no cover - 单份坏快照不影响其它预热
                summary["papers"] -= 1
                log.warning("试卷 %s 快照预热失败：%s", paper_id, exc)

        # 题面视图只对「已发布/已关闭」的考试有意义（draft 无学生、试卷可能未冻结）；
        # 乱序考试按会话洗牌，不缓存
        exam_rows = db.scalars(
            select(Exam)
            .where(Exam.status.in_((ExamStatus.PUBLISHED.value, ExamStatus.CLOSED.value)))
            .order_by(Exam.id.desc())
            .limit(MAX_PRELOAD_EXAMS)
        ).all()
        for exam in exam_rows:
            try:
                loaded = sessions.warm_questions_view(db, exam)
            except Exception as exc:  # pragma: no cover - 单场考试不影响其它预热
                log.warning("考试 %s 题面预热失败：%s", exam.id, exc)
                continue
            if loaded:
                summary["exams"] += 1
                summary["questions"] += loaded
    return summary


def _emit(record: dict[str, Any]) -> None:
    """把预热结果写一行 JSON 到 stdout。

    与访问日志同口径（docs/72 §5.1：结构化 JSON 单行、可被监控脚本解析），
    这样 `docker compose logs api | grep startup.warmup` 就能确认「这次启动到底预热了什么」。
    不依赖 logging 配置：uvicorn 默认只配置自己的 logger，应用 logger 的 INFO 会被丢掉。
    """
    line = {
        "level": "info",
        "logger": "app.warmup",
        "event": "startup.warmup",
        **record,
    }
    try:
        sys.stdout.write(json.dumps(line, ensure_ascii=False, separators=(",", ":")) + "\n")
        sys.stdout.flush()
    except (AttributeError, OSError, ValueError):  # pragma: no cover - 输出失败不影响启动
        pass


def warm_up(settings: Settings | None = None) -> dict[str, Any]:
    """执行全部预热步骤；任何一步失败都只记日志，不抛异常。"""
    resolved = settings or get_settings()
    if not resolved.warmup_enabled:
        return {"enabled": False}
    if resolved.is_sqlite:
        return {"enabled": True, "skipped": "sqlite"}
    engine = get_engine()
    # 注意：`str(engine.url)` 会把口令打码成 ***，不能直接与 DATABASE_URL 比对；
    # 这里比「驱动 + 主机 + 端口 + 库名」，足以识别「拿着 A 库 engine 预热 B 库」。
    target_url = make_url(resolved.database_url)
    if (engine.url.drivername, engine.url.host, engine.url.port, engine.url.database) != (
        target_url.drivername,
        target_url.host,
        target_url.port,
        target_url.database,
    ):
        return {"enabled": True, "skipped": "settings-mismatch"}

    summary: dict[str, Any] = {"enabled": True}
    target = resolved.db_pool_size + resolved.db_max_overflow
    try:
        summary["pool_connections"] = warm_connection_pool(engine, target)
    except Exception as exc:  # pragma: no cover - 启动阶段尽力而为
        log.warning("连接池预热失败：%s", exc)
        summary["pool_connections"] = 0
    try:
        summary.update(warm_business_caches())
    except Exception as exc:  # pragma: no cover - 启动阶段尽力而为
        log.warning("业务缓存预热失败：%s", exc)
    _emit(summary)
    return summary
