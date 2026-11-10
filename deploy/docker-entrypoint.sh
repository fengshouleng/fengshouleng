#!/bin/sh
# deploy/docker-entrypoint.sh — API 容器入口（TASK-REL-001）
#
# 职责：
#   1. RUN_MIGRATIONS=true（默认）时执行 `alembic upgrade head`；失败即退出，容器不会带病启动
#   2. 无参数时以 uvicorn 启动（参数来自环境变量，便于 staging/prod 同镜像）
#   3. 有参数时 exec 该参数（如 `docker compose run --rm api sh`）
#
# 以非 root（uid 10001）运行，脚本本身只读执行，不写容器内任何路径。
set -eu

log() {
    printf '[entrypoint] %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
    if [ -n "${DATABASE_URL:-}" ]; then
        log "alembic upgrade head（DATABASE_URL 已注入）"
    else
        log "alembic upgrade head（DATABASE_URL 未注入，将使用应用默认值 sqlite）"
    fi
    alembic upgrade head
    log "迁移完成"
else
    log "RUN_MIGRATIONS=${RUN_MIGRATIONS}，跳过迁移"
fi

if [ "$#" -eq 0 ]; then
    # WEB_CONCURRENCY：uvicorn worker 进程数（默认 1）。
    # 单进程只能吃满一个核：本机实测 100+100 VU 时 session_read p95 596ms、
    # answer_save p95 20.9s（含开场 200 并发建会话），扩到 4 worker 后
    # session_read p95 82ms / save p95 589ms（证据见 docs/61 §10.2）。
    # 调大 worker 时务必同步缩小每进程连接池（DB_POOL_SIZE/DB_MAX_OVERFLOW），
    # 保证 workers × (pool_size + max_overflow) < postgres 的 max_connections。
    set -- uvicorn app.main:app \
        --host 0.0.0.0 \
        --port "${PORT:-8000}" \
        --workers "${WEB_CONCURRENCY:-1}" \
        --proxy-headers \
        --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}"
    log "启动：$*"
fi

exec "$@"
