#!/bin/sh
# deploy/backup/loop.sh — backup 容器主进程（TASK-REL-001 骨架 / TASK-REL-002 扩展）
#
# 镜像没有 cron（postgres:alpine），用最小的 sleep 循环代替：
#   启动后立即执行一次备份（首次部署立刻验证 pg_dump 通路），之后每
#   BACKUP_INTERVAL_SECONDS 秒执行一次；每轮备份后再跑一次新鲜度检查，
#   把“>26h 无成功备份”变成明确的 ERROR 日志 + 告警文件（不再只靠人肉看日志）。
#
# 失败不会终止循环（否则一次瞬时故障会永久停掉备份），但每次失败都会打印 ERROR
# 且由 backup.sh 以非 0 退出；告警细节（写文件 / 调 BACKUP_ALERT_CMD）在 backup.sh
# 与 freshness_check.sh 内完成，接口见 deploy/backup/lib.sh。
#
# 退出码语义（仅用于日志判断，循环本身长期运行）：
#   backup.sh      0=成功 1=本地失败 2=配置错误 3=本地成功但异地副本失败
#   freshness      0=新鲜 1=过期/从未成功 2=配置错误
set -u
if (set -o pipefail) 2>/dev/null; then set -o pipefail; fi

INTERVAL="${BACKUP_INTERVAL_SECONDS:-86400}"

log() {
    printf '[backup-loop] %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

run_once() {
    /bin/sh /backup/backup.sh
    rc=$?
    case "$rc" in
        0) log "本轮备份成功" ;;
        3) log "ERROR 本轮本地备份成功，但异地副本失败（exit=3）；${INTERVAL}s 后重试" ;;
        *) log "ERROR 本轮备份失败（exit=${rc}）；${INTERVAL}s 后重试" ;;
    esac
    return "$rc"
}

check_freshness() {
    /bin/sh /backup/freshness_check.sh
    rc=$?
    if [ "$rc" -ne 0 ]; then
        log "ERROR 备份新鲜度检查未通过（exit=${rc}），详见日志与 ${BACKUP_DIR:-/backups}/.alert_stale_backup"
    fi
    return "$rc"
}

log "备份循环启动：interval=${INTERVAL}s（首轮立即执行，随后每轮做新鲜度自检）"
run_once || true
check_freshness || true

while :; do
    sleep "$INTERVAL"
    run_once || true
    check_freshness || true
done
