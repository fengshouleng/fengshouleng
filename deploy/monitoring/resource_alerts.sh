#!/usr/bin/env bash
# deploy/monitoring/resource_alerts.sh — 资源与备份告警（TASK-REL-003）
#
# 三类检查（每项独立告警文件，恢复后自动清除）：
#   1) 磁盘：任一 DISK_PATHS 挂载点使用率 > DISK_THRESHOLD_PCT（默认 85）→ disk_high
#   2) 备份新鲜度：复用 deploy/backup/freshness_check.sh（>26h 无成功备份 → backup_stale）
#      auto 模式：BACKUP_DIR 在宿主可读且已有 .last_backup_ok 时本地执行，
#                 否则进 backup 容器执行（容器挂了也会因 exec 失败而告警）
#   3) 容器：EXPECTED_SERVICES 不在 running / health=unhealthy / 退出码非 0 → container_unhealthy
#
# 退出码：
#   0  全部检查通过（此前告警会被清除）
#   1  至少一类检查产生告警（已写告警文件并调用 ALERT_CMD，若配置）
#   2  参数/配置/依赖错误（如缺 docker、缺 ENV_FILE、阈值非法）——先修配置
#
# 用法（仓库根执行）：
#   bash deploy/monitoring/resource_alerts.sh
#   bash deploy/monitoring/resource_alerts.sh --dry-run
#   bash deploy/monitoring/resource_alerts.sh --skip-containers     # 无 docker 的宿主/演练
#   bash deploy/monitoring/resource_alerts.sh --skip-freshness --skip-containers  # 只查磁盘
#   bash deploy/monitoring/resource_alerts.sh --disk-path / --disk-path /var
#
# 环境变量（默认值见下；可写入 deploy/monitoring/monitoring.env）：
#   DISK_PATHS                  "/"（空格分隔多个挂载点/目录）
#   DISK_THRESHOLD_PCT          85
#   FRESHNESS_MODE              auto | local | container | skip
#   BACKUP_DIR                  空（local 模式必填；auto 模式用它判断）
#   BACKUP_FRESHNESS_MAX_HOURS  26（透传给 freshness_check.sh）
#   COMPOSE_FILE                <repo>/deploy/docker-compose.yml
#   ENV_FILE                    <repo>/deploy/.env.staging（只读取 COMPOSE_PROJECT_NAME，不读密码）
#   COMPOSE_PROJECT_NAME        空（缺省时从 ENV_FILE 读取）
#   EXPECTED_SERVICES           "postgres api nginx backup"
#   CONTAINER_RESTART_WARN      5（仅打印，不单独触发告警）
#   STATE_DIR / ALERT_DIR       <repo>/deploy/evidence/monitoring
#   RESOURCE_ALERT_REPEAT_SECONDS 1800（同一告警钩子冷却）
#   ALERT_CMD                   空（接口见 deploy/monitoring/lib.sh）
#   MONITORING_ENV_FILE         默认 deploy/monitoring/monitoring.env（空串禁用）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=deploy/monitoring/lib.sh
. "${SCRIPT_DIR}/lib.sh"
mon_load_env

DISK_PATHS="${DISK_PATHS:-/}"
DISK_THRESHOLD_PCT="${DISK_THRESHOLD_PCT:-85}"
FRESHNESS_MODE="${FRESHNESS_MODE:-auto}"
BACKUP_FRESHNESS_MAX_HOURS="${BACKUP_FRESHNESS_MAX_HOURS:-26}"
COMPOSE_FILE="${COMPOSE_FILE:-${REPO_ROOT}/deploy/docker-compose.yml}"
ENV_FILE="${ENV_FILE:-${REPO_ROOT}/deploy/.env.staging}"
EXPECTED_SERVICES="${EXPECTED_SERVICES:-postgres api nginx backup}"
CONTAINER_RESTART_WARN="${CONTAINER_RESTART_WARN:-5}"
STATE_DIR="${STATE_DIR:-${REPO_ROOT}/deploy/evidence/monitoring}"
ALERT_DIR="${ALERT_DIR:-${STATE_DIR}}"
RESOURCE_ALERT_REPEAT_SECONDS="${RESOURCE_ALERT_REPEAT_SECONDS:-1800}"

ALERT_SOURCE="resource_alerts"
DRY_RUN=0
SKIP_FRESHNESS=0
SKIP_CONTAINERS=0

usage() {
    cat <<'EOF'
用法：bash deploy/monitoring/resource_alerts.sh [选项]

  --dry-run               只打印将要执行的检查与阈值，不写告警文件、不调用 ALERT_CMD
  --skip-freshness        跳过备份新鲜度检查
  --skip-containers       跳过容器状态检查（无 docker 的宿主或演练）
  --disk-path PATH        覆盖要检查的磁盘路径（可重复，空格分隔多个）
  -h, --help              显示本帮助

退出码：0=全部通过；1=有告警；2=配置/依赖错误。
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --skip-freshness) SKIP_FRESHNESS=1 ;;
        --skip-containers) SKIP_CONTAINERS=1 ;;
        --disk-path)
            shift
            [ "$#" -gt 0 ] || mon_die "--disk-path 缺少路径参数"
            DISK_PATHS="$1"
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            usage >&2
            mon_die "未知参数：$1"
            ;;
    esac
    shift
done

mon_require_uint "DISK_THRESHOLD_PCT" "$DISK_THRESHOLD_PCT"
[ "$DISK_THRESHOLD_PCT" -le 100 ] || mon_die "DISK_THRESHOLD_PCT 必须 <= 100"
mon_require_posint "BACKUP_FRESHNESS_MAX_HOURS" "$BACKUP_FRESHNESS_MAX_HOURS"
mon_require_uint "CONTAINER_RESTART_WARN" "$CONTAINER_RESTART_WARN"
mon_require_uint "RESOURCE_ALERT_REPEAT_SECONDS" "$RESOURCE_ALERT_REPEAT_SECONDS"
case "$FRESHNESS_MODE" in
    auto|local|container|skip) ;;
    *) mon_die "FRESHNESS_MODE=${FRESHNESS_MODE} 非法（auto|local|container|skip）" ;;
esac

# 只从 ENV_FILE 读取项目名，绝不读取/回显密码行。
PROJECT="${COMPOSE_PROJECT_NAME:-}"
if [ -z "$PROJECT" ] && [ -f "$ENV_FILE" ]; then
    PROJECT="$(grep -E '^COMPOSE_PROJECT_NAME=' "$ENV_FILE" 2>/dev/null | tail -n 1 | cut -d= -f2- | tr -d '\r' || true)"
fi
COMPOSE=()
if [ -n "$PROJECT" ]; then
    COMPOSE=(docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" -p "$PROJECT")
fi

# auto 模式下实际会走哪种新鲜度检查（仅用于打印计划/日志）。
effective_freshness="$FRESHNESS_MODE"
if [ "$effective_freshness" = "auto" ]; then
    if [ -n "${BACKUP_DIR:-}" ] && [ -f "${BACKUP_DIR}/.last_backup_ok" ]; then
        effective_freshness="local"
    else
        effective_freshness="container"
    fi
fi

if [ "$DRY_RUN" = "1" ]; then
    mon_log "[dry-run] 不写告警文件、不调用 ALERT_CMD"
    printf '  disk_paths=%s threshold=%s%%\n' "$DISK_PATHS" "$DISK_THRESHOLD_PCT"
    printf '  freshness_mode=%s（auto 解析为 %s），backup_dir=%s，max_hours=%s\n' \
        "$FRESHNESS_MODE" "$effective_freshness" "${BACKUP_DIR:-（未设置）}" "$BACKUP_FRESHNESS_MAX_HOURS"
    printf '  expected_services=%s restart_warn=%s\n' "$EXPECTED_SERVICES" "$CONTAINER_RESTART_WARN"
    printf '  compose_project=%s compose_file=%s\n' "${PROJECT:-（未解析：需要 ENV_FILE 或 COMPOSE_PROJECT_NAME）}" "$COMPOSE_FILE"
    printf '  skip_freshness=%s skip_containers=%s\n' "$SKIP_FRESHNESS" "$SKIP_CONTAINERS"
    printf '  alert_dir=%s repeat_seconds=%s\n' "$ALERT_DIR" "$RESOURCE_ALERT_REPEAT_SECONDS"
    if [ -n "${ALERT_CMD:-}" ]; then
        printf '  alert_cmd=已配置（命令内容不打印，避免泄露 webhook 令牌）\n'
    else
        printf '  alert_cmd=未配置（仅落告警文件）\n'
    fi
    exit 0
fi

mon_require_cmd df
mon_require_cmd awk
mon_require_cmd date

# ---------------------------------------------------------------------------
# 1) 磁盘
# ---------------------------------------------------------------------------
check_disk() {
    _bad=""
    _ok=""
    for _path in $DISK_PATHS; do
        if [ ! -e "$_path" ]; then
            mon_warn "跳过不存在的磁盘路径：${_path}"
            continue
        fi
        _df_line="$(df -P -k "$_path" 2>/dev/null | awk 'NR==2 {print $5 "|" $6}' || true)"
        _pct="${_df_line%%|*}"
        _mount="${_df_line#*|}"
        _pct="${_pct%\%}"
        case "${_pct:-}" in
            ''|*[!0-9]*)
                mon_warn "无法解析磁盘使用率：path=${_path} df=${_df_line:-<空>}"
                continue
                ;;
        esac
        if [ "$_pct" -gt "$DISK_THRESHOLD_PCT" ]; then
            _bad="${_bad}${_mount}(${_path})=${_pct}%; "
        else
            _ok="${_ok}${_mount}=${_pct}%; "
        fi
    done
    if [ -n "$_ok" ]; then
        mon_log "OK 磁盘使用率（阈值 >${DISK_THRESHOLD_PCT}%）：${_ok}"
    fi
    if [ -n "$_bad" ]; then
        mon_alert "disk_high" \
            "磁盘使用率超过 ${DISK_THRESHOLD_PCT}%：${_bad}" \
            "${ALERT_DIR}/alert_disk_high.log" "$RESOURCE_ALERT_REPEAT_SECONDS"
        return 1
    fi
    if [ -n "$_ok" ]; then
        mon_clear_alert "${ALERT_DIR}/alert_disk_high.log"
    fi
    return 0
}

# ---------------------------------------------------------------------------
# 2) 备份新鲜度（复用 deploy/backup/freshness_check.sh）
# ---------------------------------------------------------------------------
check_freshness() {
    if [ "$SKIP_FRESHNESS" = "1" ] || [ "$FRESHNESS_MODE" = "skip" ]; then
        mon_log "SKIP 备份新鲜度检查（--skip-freshness 或 FRESHNESS_MODE=skip）"
        return 0
    fi

    _mode="$effective_freshness"
    _rc=0
    _out=""
    if [ "$_mode" = "local" ]; then
        [ -n "${BACKUP_DIR:-}" ] || mon_die "FRESHNESS_MODE=local 需要设置 BACKUP_DIR（宿主上可读的备份目录）"
        [ -f "${REPO_ROOT}/deploy/backup/freshness_check.sh" ] \
            || mon_die "缺少脚本：${REPO_ROOT}/deploy/backup/freshness_check.sh"
        _out="$(BACKUP_DIR="$BACKUP_DIR" \
            BACKUP_ALERT_CMD= \
            BACKUP_FRESHNESS_MAX_HOURS="$BACKUP_FRESHNESS_MAX_HOURS" \
            sh "${REPO_ROOT}/deploy/backup/freshness_check.sh" 2>&1)" || _rc=$?
    elif [ "$_mode" = "container" ]; then
        if [ "${#COMPOSE[@]}" -eq 0 ]; then
            mon_die "容器模式需要 COMPOSE_PROJECT_NAME（或可读的 ENV_FILE=${ENV_FILE}）"
        fi
        [ -f "$ENV_FILE" ] || mon_die "ENV_FILE 不存在：${ENV_FILE}"
        [ -f "$COMPOSE_FILE" ] || mon_die "COMPOSE_FILE 不存在：${COMPOSE_FILE}"
        _out="$("${COMPOSE[@]}" exec -T \
            -e "BACKUP_FRESHNESS_MAX_HOURS=${BACKUP_FRESHNESS_MAX_HOURS}" \
            backup /bin/sh /backup/freshness_check.sh 2>&1)" || _rc=$?
    else
        mon_die "内部错误：未知新鲜度模式 ${_mode}"
    fi

    printf '%s\n' "$_out" | sed 's/^/    /'
    _last_line="$(printf '%s\n' "$_out" | tail -n 1 | tr -d '\r' || true)"

    case "$_rc" in
        0)
            mon_clear_alert "${ALERT_DIR}/alert_backup_stale.log"
            mon_log "OK 备份新鲜度检查通过（mode=${_mode}）"
            return 0
            ;;
        1)
            mon_alert "backup_stale" \
                "备份不新鲜或从未成功（mode=${_mode}，freshness_check.sh exit=1）：${_last_line:-见 docker compose logs backup}" \
                "${ALERT_DIR}/alert_backup_stale.log" "$RESOURCE_ALERT_REPEAT_SECONDS"
            return 1
            ;;
        2)
            mon_die "备份新鲜度阈值配置非法（freshness_check.sh exit=2）：${_last_line:-见上方输出}"
            ;;
        *)
            mon_alert "backup_stale" \
                "新鲜度检查异常退出（mode=${_mode}，exit=${_rc}）：${_last_line:-见 docker compose logs backup}" \
                "${ALERT_DIR}/alert_backup_stale.log" "$RESOURCE_ALERT_REPEAT_SECONDS"
            return 1
            ;;
    esac
}

# ---------------------------------------------------------------------------
# 3) 容器状态
# ---------------------------------------------------------------------------
check_containers() {
    if [ "$SKIP_CONTAINERS" = "1" ]; then
        mon_log "SKIP 容器状态检查（--skip-containers）"
        return 0
    fi

    mon_require_cmd docker
    if [ "${#COMPOSE[@]}" -eq 0 ]; then
        mon_die "容器检查需要 COMPOSE_PROJECT_NAME（或可读的 ENV_FILE=${ENV_FILE}）"
    fi
    [ -f "$ENV_FILE" ] || mon_die "ENV_FILE 不存在：${ENV_FILE}"
    [ -f "$COMPOSE_FILE" ] || mon_die "COMPOSE_FILE 不存在：${COMPOSE_FILE}"

    _bad=""
    _summary=""
    # EXPECTED_SERVICES 以空格分隔，故意不加引号做分词
    for _svc in $EXPECTED_SERVICES; do
        _cid="$("${COMPOSE[@]}" ps -q "$_svc" 2>/dev/null | head -n 1 || true)"
        if [ -z "$_cid" ]; then
            _bad="${_bad}${_svc}:not_running; "
            continue
        fi
        _info="$(docker inspect --format \
            '{{.State.Status}} {{.State.ExitCode}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} {{.RestartCount}}' \
            "$_cid" 2>/dev/null || true)"
        if [ -z "$_info" ]; then
            _bad="${_bad}${_svc}:inspect_failed; "
            continue
        fi
        read -r _status _exit_code _health _restarts <<< "$_info"
        _summary="${_summary}${_svc}=${_status}/${_health}/restarts=${_restarts}; "

        case "${_status:-}" in
            running) ;;
            *)
                _bad="${_bad}${_svc}:status=${_status:-unknown}(exit=${_exit_code:-?}); "
                continue
                ;;
        esac
        if [ "${_health:-none}" = "unhealthy" ]; then
            _bad="${_bad}${_svc}:unhealthy; "
            continue
        fi
        case "${_exit_code:-}" in
            ''|*[!0-9]*) ;;
            *)
                if [ "$_exit_code" -ne 0 ]; then
                    _bad="${_bad}${_svc}:exit=${_exit_code}; "
                    continue
                fi
                ;;
        esac
    done

    mon_log "容器状态：${_summary:-（无）}（restart_warn=${CONTAINER_RESTART_WARN}，仅供排查参考）"
    if [ -n "$_bad" ]; then
        mon_alert "container_unhealthy" \
            "容器异常：${_bad}" \
            "${ALERT_DIR}/alert_container_unhealthy.log" "$RESOURCE_ALERT_REPEAT_SECONDS"
        return 1
    fi
    mon_clear_alert "${ALERT_DIR}/alert_container_unhealthy.log"
    mon_log "OK 全部容器运行中且健康"
    return 0
}

alerts=0
if ! check_disk; then alerts=$((alerts + 1)); fi
if ! check_freshness; then alerts=$((alerts + 1)); fi
if ! check_containers; then alerts=$((alerts + 1)); fi

if [ "$alerts" -gt 0 ]; then
    mon_log "RESULT 有 ${alerts} 类告警，告警文件在：${ALERT_DIR}"
    exit 1
fi

mon_log "RESULT 全部检查通过"
exit 0
