"""审计保留策略 CLI：删除超过保留期的审计记录（TASK-BE-006）。

运行方式（工作目录 `server/`）::

    python -m app.audit.cleanup --days 180
    python -m app.audit.cleanup --days 180 --dry-run

- 默认保留 180 天（docs/11-data-model.md#audit_logs）
- `--dry-run` 只统计将删除的条数，不写库
- 成功退出码 0；数据库失败退出码 1；参数错误退出码 2
- 定时执行示例（crontab，每天 03:00）::

      0 3 * * * cd /srv/quiz/server && .venv/bin/python -m app.audit.cleanup --days 180

清理动作本身不写 `audit_logs`：契约（openapi.yaml#/components/schemas/AuditLog）
明确系统任务不得写入审计表；删除的只是过期历史，不产生新的业务操作记录。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.audit.service import DEFAULT_RETENTION_DAYS
from app.db import get_sessionmaker
from app.models.audit_log import AuditLog

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2


def cleanup(
    db: Session,
    *,
    days: int = DEFAULT_RETENTION_DAYS,
    dry_run: bool = False,
    now: datetime | None = None,
) -> int:
    """删除 `created_at < now - days` 的记录；返回命中条数（dry-run 只统计）。

    调用方负责提供 Session；本函数在真实删除时提交事务。
    """
    if days < 1:
        raise ValueError("days 必须为正整数")

    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    cutoff = moment.astimezone(UTC) - timedelta(days=days)

    condition = AuditLog.created_at < cutoff
    if dry_run:
        return int(
            db.scalar(select(func.count()).select_from(AuditLog).where(condition)) or 0
        )

    result = db.execute(delete(AuditLog).where(condition))
    db.commit()
    # DML 返回 CursorResult：typeshed 把 Session.execute 标注为 Result（无 rowcount），
    # 用 getattr 读取既有运行时语义，避免对 SQLAlchemy 类型做不安全断言。
    return int(getattr(result, "rowcount", 0) or 0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.audit.cleanup",
        description="删除超过保留期的审计记录（默认 180 天）",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_RETENTION_DAYS,
        help=f"保留天数（默认 {DEFAULT_RETENTION_DAYS}）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只统计将删除的条数，不实际删除",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    session_factory: Callable[[], Session] | None = None,
) -> int:
    """CLI 入口；`session_factory` 仅供测试注入。"""
    args = build_parser().parse_args(argv)
    if args.days < 1:
        print("错误: --days 必须为正整数", file=sys.stderr)
        return EXIT_USAGE

    factory: Callable[[], Session] = session_factory or get_sessionmaker()
    try:
        with factory() as db:
            removed = cleanup(db, days=args.days, dry_run=args.dry_run)
    except SQLAlchemyError as exc:
        # 只打印异常类型，避免把连接串等内部信息写进终端/日志
        print(f"错误: 审计清理失败（{type(exc).__name__}）", file=sys.stderr)
        return EXIT_FAILURE

    if args.dry_run:
        print(
            f"dry-run：将删除 {removed} 条超过 {args.days} 天的审计记录（未实际删除）"
        )
    else:
        print(f"已删除 {removed} 条超过 {args.days} 天的审计记录")
    return EXIT_OK


__all__ = [
    "EXIT_FAILURE",
    "EXIT_OK",
    "EXIT_USAGE",
    "build_parser",
    "cleanup",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
