#!/usr/bin/env bash
# deploy/monitoring/lib.sh — 监控脚本共用函数（TASK-REL-003；被 source，不单独执行）
#
# 设计约束（与 deploy/backup/lib.sh 的告警接口保持同风格，便于运维只学一套）：
#   - 调用方先 `set -euo pipefail`，再 `. lib.sh`，然后 `mon_load_env`；
#   - 只使用 bash + coreutils + curl/docker，不引入 Prometheus/Grafana/agent；
#   - 不读取、不打印任何密码/令牌；ALERT_CMD 的值只以“已配置/未配置”出现；
#   - DRY_RUN=1 时：不写状态/告警文件、不调用告警命令、不发起网络探测；
#   - umask 027：告警/状态文件默认 640，避免同机其他用户读到运维信息。
#
# 配置优先级（重要）：
#   monitoring.env（若存在） > 进程环境变量 > 脚本内默认值。
#   临时覆盖配置文件里的同名变量时，用 `MONITORING_ENV_FILE= BASE_URL=... bash ...`
#   显式禁用配置文件（空串 = 不加载）。
#
# 告警通道（ALERT_CMD）：
#   调用 `sh -c "$ALERT_CMD"`，并在环境里提供：
#     ALERT_REASON    health_probe_failed | disk_high | backup_stale | container_unhealthy
#     ALERT_MESSAGE   人类可读摘要（不含密码/令牌/用户答案）
#     ALERT_FILE      告警落盘文件（宿主路径）
#     ALERT_SOURCE    health_probe | resource_alerts | slow_requests
#   通道失败只打 WARN，不改变本次判定（与 backup_alert 一致）。

umask 027

MON_LIB_VERSION="1.0"

mon_now_iso() {
    date -u +%Y-%m-%dT%H:%M:%SZ
}

mon_now_epoch() {
    date -u +%s
}

mon_log() {
    printf '[monitor] %s %s\n' "$(mon_now_iso)" "$*"
}

mon_warn() {
    printf '[monitor] %s WARN %s\n' "$(mon_now_iso)" "$*" >&2
}

mon_die() {
    printf '[monitor] %s ERROR %s\n' "$(mon_now_iso)" "$*" >&2
    exit 2
}

mon_require_cmd() {
    command -v "$1" >/dev/null 2>&1 || mon_die "缺少命令：$1（安装后重试，或检查 cron 的 PATH）"
}

mon_require_uint() {
    case "${2:-}" in
        ''|*[!0-9]*) mon_die "$1=${2:-} 不是非负整数" ;;
    esac
}

mon_require_posint() {
    mon_require_uint "$1" "${2:-}"
    [ "${2:-0}" -gt 0 ] || mon_die "$1=${2:-} 必须 >= 1"
}

# 可选配置文件：默认 deploy/monitoring/monitoring.env；MONITORING_ENV_FILE 为空串则禁用。
# 文件是普通 KEY=VALUE 赋值（不是脚本逻辑），权限建议 600，且禁止提交真实密钥。
mon_load_env() {
    _mon_env_file="${MONITORING_ENV_FILE-${REPO_ROOT:-.}/deploy/monitoring/monitoring.env}"
    if [ -n "${_mon_env_file}" ] && [ -f "${_mon_env_file}" ]; then
        # shellcheck disable=SC1090
        . "${_mon_env_file}"
        mon_log "已加载配置：${_mon_env_file}"
    fi
}

# 冷却标记路径：与告警文件同目录，命名稳定，便于人工清理。
_mon_cooldown_path() {
    printf '%s/.cooldown_%s' "$(dirname "$1")" "$(basename "$1")"
}

# 写告警文件 + 调用 ALERT_CMD。
# 用法：mon_alert <reason> <message> <alert_file> [repeat_seconds]
#   - 告警文件覆盖写（key=value），代表“当前状态”，不是历史堆积；
#   - repeat_seconds > 0 时，同一告警文件在该冷却窗口内只调用一次钩子，
#     但告警文件每次都会刷新时间（避免每分钟刷屏）；
#   - 钩子失败只 WARN，不改变调用方退出码。
mon_alert() {
    _reason="$1"
    _message="$2"
    _file="$3"
    _repeat="${4:-0}"

    if [ "${DRY_RUN:-0}" = "1" ]; then
        mon_log "[dry-run] 将写告警文件 ${_file}（reason=${_reason}）"
        mon_log "[dry-run] 告警摘要：${_message}"
        if [ -n "${ALERT_CMD:-}" ]; then
            mon_log "[dry-run] ALERT_CMD 已配置：不实际调用"
        else
            mon_log "[dry-run] ALERT_CMD 未配置：仅落告警文件"
        fi
        return 0
    fi

    _dir="$(dirname "$_file")"
    mkdir -p "$_dir" 2>/dev/null || mon_warn "无法创建告警目录：${_dir}"

    if printf 'reason=%s\ntime=%s\nhost=%s\nsource=%s\nmessage=%s\n' \
        "$_reason" "$(mon_now_iso)" "$(hostname 2>/dev/null || echo unknown)" \
        "${ALERT_SOURCE:-monitoring}" "$_message" >"$_file" 2>/dev/null; then
        :
    else
        mon_warn "无法写入告警文件：${_file}"
    fi

    mon_log "ALERT ${_reason}: ${_message}（alert_file=${_file}）"

    _call_hook=1
    _marker="$(_mon_cooldown_path "$_file")"
    if [ "${_repeat}" -gt 0 ] 2>/dev/null; then
        _now="$(mon_now_epoch)"
        _last=0
        if [ -f "$_marker" ]; then
            _last="$(tr -d '[:space:]' <"$_marker" 2>/dev/null || echo 0)"
            case "${_last:-}" in ''|*[!0-9]*) _last=0 ;; esac
        fi
        if [ "$_last" -gt 0 ] && [ $((_now - _last)) -lt "$_repeat" ]; then
            _call_hook=0
            mon_log "告警抑制：${_reason} 在 ${_repeat}s 冷却窗口内（仅刷新告警文件）"
        fi
    fi

    if [ "$_call_hook" = "1" ] && [ -n "${ALERT_CMD:-}" ]; then
        if ALERT_REASON="$_reason" ALERT_MESSAGE="$_message" ALERT_FILE="$_file" \
            ALERT_SOURCE="${ALERT_SOURCE:-monitoring}" \
            sh -c "$ALERT_CMD" >/dev/null 2>&1; then
            if [ "${_repeat}" -gt 0 ] 2>/dev/null; then
                mon_now_epoch >"$_marker" 2>/dev/null || true
            fi
        else
            mon_warn "告警通道执行失败（ALERT_CMD），不影响本次结论"
        fi
    fi
}

# 恢复正常：删除告警文件与冷却标记（不存在也返回 0）。
mon_clear_alert() {
    _file="$1"
    if [ "${DRY_RUN:-0}" = "1" ]; then
        mon_log "[dry-run] 将清除告警文件 ${_file}"
        return 0
    fi
    rm -f "$_file" "$(_mon_cooldown_path "$_file")" 2>/dev/null || true
}
