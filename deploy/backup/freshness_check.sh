#!/bin/sh
# deploy/backup/freshness_check.sh — 备份新鲜度检查（TASK-REL-002）
#
# 判据：最近一次成功备份距今 > BACKUP_FRESHNESS_MAX_HOURS（默认 26h）即视为“不新鲜”。
# 26h = 24h 周期 + 2h 告警宽限（覆盖一次错过的日备后仍留有抢修时间）。
#
# 行为：
#   - 新鲜：打印 OK（含距今小时数），清除 .alert_stale_backup，退出 0
#   - 过期或从未成功备份：写告警文件 ${BACKUP_DIR}/.alert_stale_backup（含时间/库名/原因），
#     调用 BACKUP_ALERT_CMD（REL-003 接口），打印 ERROR，**退出 1**
#
# 由 backup/loop.sh 每轮调用，也建议宿主机 cron 每小时调用一次
# （容器若整体挂掉，容器内的 loop 与检查都不会跑；宿主 cron 见 deploy/cron/README.md）。
#
# 环境变量：
#   BACKUP_DIR=/backups                    产物目录（读 .last_backup_ok / .last_backup_ok.epoch）
#   BACKUP_FRESHNESS_MAX_HOURS=26          新鲜度阈值
#   BACKUP_ALERT_CMD=                      告警命令（REL-003 接口，见 lib.sh）
#   PGDATABASE                             仅用于告警信息（可缺省）
#
# 手工执行：
#   docker compose ... exec -T backup /bin/sh /backup/freshness_check.sh
set -eu
if (set -o pipefail) 2>/dev/null; then set -o pipefail; fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=deploy/backup/lib.sh
. "${BACKUP_LIB:-${SCRIPT_DIR}/lib.sh}"

BACKUP_DIR="${BACKUP_DIR:-/backups}"
BACKUP_FRESHNESS_MAX_HOURS="${BACKUP_FRESHNESS_MAX_HOURS:-26}"
BACKUP_LOG_FILE="$(backup_log_path "${BACKUP_LOG_DIR:-${BACKUP_DIR}/logs}")"

case "$BACKUP_FRESHNESS_MAX_HOURS" in
    ''|*[!0-9]*)
        blog "ERROR BACKUP_FRESHNESS_MAX_HOURS=${BACKUP_FRESHNESS_MAX_HOURS} 不是正整数"
        exit 2
        ;;
    0?*)
        blog "ERROR BACKUP_FRESHNESS_MAX_HOURS 不要以 0 开头（避免被当作八进制）"
        exit 2
        ;;
esac
if [ "$BACKUP_FRESHNESS_MAX_HOURS" -lt 1 ]; then
    blog "ERROR BACKUP_FRESHNESS_MAX_HOURS 必须 >= 1"
    exit 2
fi

last_file="${BACKUP_DIR}/.last_backup_ok"
stale_file="${BACKUP_DIR}/.alert_stale_backup"
max_seconds=$((BACKUP_FRESHNESS_MAX_HOURS * 3600))
now_epoch="$(backup_now_epoch)"

if [ ! -f "$last_file" ]; then
    backup_alert stale_backup "从未成功备份：缺少 ${last_file}（backup 服务未启动或首轮即失败）" "$stale_file"
    blog "ERROR 从未成功备份（${last_file} 不存在）"
    exit 1
fi

# 优先读整数 epoch（backup.sh 每次成功都会写）；缺失时退回文件 mtime
last_epoch=""
if [ -f "${last_file}.epoch" ]; then
    last_epoch="$(tr -d '[:space:]' <"${last_file}.epoch" 2>/dev/null || true)"
fi
case "${last_epoch:-}" in
    ''|*[!0-9]*) last_epoch="" ;;
esac
if [ -z "$last_epoch" ]; then
    last_epoch="$(date -u -r "$last_file" +%s 2>/dev/null || true)"
    case "${last_epoch:-}" in
        ''|*[!0-9]*) last_epoch="" ;;
    esac
fi
if [ -z "$last_epoch" ]; then
    backup_alert stale_backup "无法解析最近成功备份时间：${last_file}（内容/时间戳异常）" "$stale_file"
    blog "ERROR 无法解析最近成功备份时间：${last_file}"
    exit 1
fi

if [ "$last_epoch" -gt "$now_epoch" ]; then
    # 时钟回拨/宿主时间被修改：宁可报错也不要静默放过
    backup_alert stale_backup "备份时间戳在未来（last=${last_epoch} > now=${now_epoch}），检查宿主时钟/时区" "$stale_file"
    blog "ERROR 备份时间戳在未来（宿主时钟异常）"
    exit 1
fi

age_seconds=$((now_epoch - last_epoch))
age_hours=$((age_seconds / 3600))
last_ok_line="$(head -n 1 "$last_file" 2>/dev/null || true)"

if [ "$age_seconds" -gt "$max_seconds" ]; then
    backup_alert stale_backup \
        "备份不新鲜：最近成功 ${age_hours}h 前（阈值 ${BACKUP_FRESHNESS_MAX_HOURS}h），记录=${last_ok_line}" \
        "$stale_file"
    blog "ERROR 备份不新鲜：距今 ${age_hours}h > ${BACKUP_FRESHNESS_MAX_HOURS}h；最近成功：${last_ok_line}"
    exit 1
fi

rm -f "$stale_file" 2>/dev/null || true
blog "OK 备份新鲜：最近成功 ${age_hours}h 前（阈值 ${BACKUP_FRESHNESS_MAX_HOURS}h）；${last_ok_line}"
exit 0
