#!/usr/bin/env bash
# deploy/cron/exam-backup-cron.sh — 宿主机定时维护入口（TASK-REL-002）
#
# 为什么需要它：
#   - backup 容器内的 loop.sh 负责“每日备份 + 每次备份后自检”；
#     一旦容器整体挂掉/被误停，容器内的检查也不会运行 → 宿主机 cron 才会发现“备份停止”。
#   - 恢复演练（每月）与异地导出（每周）也放在宿主机，避免把演练权限常驻在容器里。
#
# 子命令：
#   freshness   新鲜度检查（>26h 无成功备份 → 写告警并退出 1）
#   backup      立即执行一次备份（手工/定点备份用）
#   drill       在 backup 容器内跑恢复演练（restore_drill.sh），结论写入容器 /backups/drill-logs
#   export      把容器内最新备份复制到宿主机/异地目录并校验 sha256（容器无出网时的替代方案）
#   status      打印最近成功备份、备份清单、告警文件（排障用）
#
# 用法（仓库根，或用绝对路径；cron 里务必写绝对路径）：
#   bash deploy/cron/exam-backup-cron.sh freshness
#   bash deploy/cron/exam-backup-cron.sh drill --keep
#   COMPOSE_PROJECT_NAME=exam-staging bash deploy/cron/exam-backup-cron.sh export
#
# 环境变量（均有默认值，不写死密钥）：
#   COMPOSE_FILE=deploy/docker-compose.yml
#   ENV_FILE=deploy/.env.staging            # 里面只放占位/真实密码，本脚本不读取密码
#   COMPOSE_PROJECT_NAME=（缺省时从 ENV_FILE 读）
#   LOG_DIR=deploy/evidence/cron            # 宿主日志与告警文件目录
#   BACKUP_REMOTE_DIR=                      # export 的目标目录（默认 deploy/evidence/offsite）
#   EXPORT_RETENTION_DAYS=7                 # export 目标目录按天清理
#   HOST_ALERT_CMD=                         # 告警命令（REL-003 接口；从 HOST_ALERT_REASON/MESSAGE/FILE 读上下文）
#   BACKUP_RETENTION_DAYS / BACKUP_RETENTION_WEEKS / BACKUP_WEEKLY_WEEKDAY /
#   BACKUP_REMOTE_DIR / BACKUP_REMOTE_CMD / BACKUP_ALERT_CMD /
#   BACKUP_FRESHNESS_MAX_HOURS / DRILL_*    # 设置后会透传给容器内的脚本（docker compose exec -e）
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-${REPO_ROOT}/deploy/docker-compose.yml}"
ENV_FILE="${ENV_FILE:-${REPO_ROOT}/deploy/.env.staging}"
LOG_DIR="${LOG_DIR:-${REPO_ROOT}/deploy/evidence/cron}"
EXPORT_RETENTION_DAYS="${EXPORT_RETENTION_DAYS:-7}"

[ -f "$COMPOSE_FILE" ] || { echo "ERROR: compose 文件不存在：$COMPOSE_FILE" >&2; exit 2; }
[ -f "$ENV_FILE" ] || { echo "ERROR: 环境文件不存在：$ENV_FILE（cp deploy/.env.example 后填写）" >&2; exit 2; }

mkdir -p "$LOG_DIR"
LOG_FILE="${LOG_DIR}/$(date -u +%Y%m%d).log"

say() {
    local line
    line="[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"
    printf '%s\n' "$line"
    printf '%s\n' "$line" >>"$LOG_FILE"
}

# 告警：写宿主告警文件 + 调用 HOST_ALERT_CMD（失败不改变结论）
host_alert() {
    local reason="$1" message="$2" file
    file="${LOG_DIR}/alert_${reason}.log"
    {
        printf 'reason=%s\n' "$reason"
        printf 'time=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        printf 'host=%s\n' "$(hostname 2>/dev/null || echo unknown)"
        printf 'message=%s\n' "$message"
        printf 'project=%s\n' "$PROJECT"
    } >>"$file"
    say "ALERT ${reason}: ${message}（file=${file}）"
    if [ -n "${HOST_ALERT_CMD:-}" ]; then
        # 接口约定（REL-003）：命令从环境读取，自行实现邮件/webhook/企业微信，例如：
        #   HOST_ALERT_CMD='curl -fsS -X POST -d "$HOST_ALERT_MESSAGE" https://alert.example/hook'
        if ! HOST_ALERT_REASON="$reason" HOST_ALERT_MESSAGE="$message" HOST_ALERT_FILE="$file" \
            sh -c "$HOST_ALERT_CMD" >>"$LOG_FILE" 2>&1; then
            say "WARN 告警命令执行失败（HOST_ALERT_CMD），不影响本次结论"
        fi
    fi
}

PROJECT="${COMPOSE_PROJECT_NAME:-}"
if [ -z "$PROJECT" ]; then
    PROJECT="$(grep -E '^COMPOSE_PROJECT_NAME=' "$ENV_FILE" | tail -n 1 | cut -d= -f2- | tr -d '\r' || true)"
fi
[ -n "$PROJECT" ] || { echo "ERROR: 无法确定 COMPOSE_PROJECT_NAME（$ENV_FILE 中缺该行）" >&2; exit 2; }

COMPOSE=(docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" -p "$PROJECT")

# 需要从宿主透传到 backup 容器的变量（未设置的不传，容器内用脚本默认值）
PASS_VARS=(
    BACKUP_RETENTION_DAYS BACKUP_RETENTION_WEEKS BACKUP_WEEKLY_WEEKDAY
    BACKUP_REMOTE_DIR BACKUP_REMOTE_CMD BACKUP_ALERT_CMD
    BACKUP_FRESHNESS_MAX_HOURS
    DRILL_KEEP DRILL_RTO_LIMIT_SECONDS DRILL_DB_PREFIX DRILL_KEY_TABLES
)
compose_cid() {
    local cid
    cid="$("${COMPOSE[@]}" ps -q backup 2>/dev/null || true)"
    [ -n "$cid" ] || return 1
    printf '%s' "$cid"
}

exec_in_backup() {
    local cid v
    cid="$(compose_cid)" || return 1
    local -a cmd=(docker exec -i)
    for v in "${PASS_VARS[@]}"; do
        if [ -n "${!v:-}" ]; then
            cmd+=(-e "$v=${!v}")
        fi
    done
    cmd+=("$cid" /bin/sh "$@")
    "${cmd[@]}"
}

cmd_freshness() {
    say "== 备份新鲜度检查 =="
    if ! compose_cid >/dev/null; then
        host_alert stale_backup "backup 容器未运行：容器停止即意味着备份已中断"
        return 1
    fi
    local rc=0
    exec_in_backup /backup/freshness_check.sh || rc=$?
    if [ "$rc" -ne 0 ]; then
        host_alert stale_backup "容器内新鲜度检查失败（exit=${rc}）：备份过期、从未成功或时钟异常"
        return 1
    fi
}

cmd_backup() {
    say "== 手工触发备份 =="
    local rc=0
    exec_in_backup /backup/backup.sh || rc=$?
    if [ "$rc" -ne 0 ]; then
        host_alert backup_failed "手工触发备份失败（exit=${rc}）"
        return 1
    fi
    "${COMPOSE[@]}" exec -T backup cat /backups/.last_backup_ok
}

cmd_drill() {
    say "== 恢复演练（临时库，不碰生产）=="
    local rc=0
    exec_in_backup /backup/restore_drill.sh "$@" || rc=$?
    if [ "$rc" -ne 0 ]; then
        host_alert drill_failed "恢复演练未通过（exit=${rc}）；详见容器 /backups/drill-logs"
        return 1
    fi
    say "演练通过；请把结论回填 docs/71-backup-restore.md §4.2"
}

cmd_export() {
    local dest="${BACKUP_REMOTE_DIR:-${REPO_ROOT}/deploy/evidence/offsite}"
    mkdir -p "$dest"
    say "== 导出最新备份到 ${dest} =="

    local cid latest base
    cid="$(compose_cid)" || { host_alert export_failed "backup 容器未运行，无法导出"; return 1; }
    latest="$(docker exec "$cid" sh -c 'ls -1t /backups/*.dump 2>/dev/null | head -n 1' | tr -d '\r' || true)"
    [ -n "$latest" ] || { host_alert export_failed "容器内没有备份文件（/backups/*.dump 为空）"; return 1; }
    base="$(basename "$latest")"

    docker cp "${cid}:${latest}" "${dest}/${base}"
    docker cp "${cid}:${latest}.sha256" "${dest}/${base}.sha256"
    ( cd "$dest" && sha256sum -c "${base}.sha256" ) || {
        host_alert export_failed "导出后 sha256 校验失败：${dest}/${base}"
        return 1
    }
    say "导出完成并校验通过：${dest}/${base}"

    # 目标目录按天清理（只处理本脚本复制进来的 *.dump 及其 .sha256）
    local old
    while IFS= read -r old; do
        [ -n "$old" ] || continue
        rm -f "$old" "${old}.sha256"
        say "清理过期导出：$(basename "$old")"
    done < <(find "$dest" -maxdepth 1 -type f -name '*.dump' -mtime "+${EXPORT_RETENTION_DAYS}" 2>/dev/null || true)
}

cmd_status() {
    local cid
    say "== 备份状态（project=${PROJECT}）=="
    if ! cid="$(compose_cid)"; then
        say "ERROR backup 容器未运行"
        return 1
    fi
    docker exec "$cid" sh -c 'cat /backups/.last_backup_ok 2>/dev/null || echo "（无 .last_backup_ok：从未成功备份）"'
    docker exec "$cid" sh -c 'ls -1t /backups/*.dump 2>/dev/null | head -n 5 || echo "（无备份文件）"'
    docker exec "$cid" sh -c 'for f in /backups/.alert_*; do [ -f "$f" ] && { echo "--- $f"; cat "$f"; }; done' || true
    docker exec "$cid" sh -c '/bin/sh /backup/freshness_check.sh' || say "新鲜度检查未通过（见上方 ERROR）"
}

case "${1:-}" in
    freshness) shift; cmd_freshness "$@" ;;
    backup) shift; cmd_backup "$@" ;;
    drill) shift; cmd_drill "$@" ;;
    export) shift; cmd_export "$@" ;;
    status) shift; cmd_status "$@" ;;
    *)
        cat >&2 <<'EOF'
用法：exam-backup-cron.sh <freshness|backup|drill|export|status> [参数]

  freshness  新鲜度检查（>26h 无成功备份 → 非 0）
  backup     立即备份一次
  drill      恢复演练（可加 --keep 保留临时库）
  export     导出最新备份到 BACKUP_REMOTE_DIR 并校验
  status     查看最近成功备份/清单/告警
EOF
        exit 2
        ;;
esac
