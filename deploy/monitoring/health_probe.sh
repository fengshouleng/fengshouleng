#!/usr/bin/env bash
# deploy/monitoring/health_probe.sh — 外部健康探测（TASK-REL-003）
#
# 每轮探测两个端点（带超时）：
#   1) ${BASE_URL}${HEALTH_PATH}       默认 /api/healthz  —— 必须 200 且 status=ok、db=ok
#   2) ${BASE_URL}${NGINX_HEALTH_PATH} 默认 /nginx-health —— 必须 200 且响应体为 ok
#
# 与 compose healthcheck 的分工：
#   - compose 的 healthcheck 决定“容器是否 ready / 是否重启”；
#   - 本脚本从**外部**（经 nginx）验证整条链路，并把“连续失败”转成告警文件 + ALERT_CMD。
#   cron 建议每分钟执行一次（片段见 docs/72-monitoring.md §7，不在本卡写作用域）。
#
# 连续失败计数（跨多次运行，存在 STATE_DIR/health_probe.state）：
#   - 本次采样成功：计数归零、删除告警文件（若已恢复）；
#   - 本次采样失败：计数 +1；达到 HEALTH_FAIL_THRESHOLD（默认 3）→ 写告警文件并调用 ALERT_CMD。
#
# 退出码（供 cron/人工判断）：
#   0  两个端点均正常（若此前告警，本轮已清除）
#   1  本次采样失败，但连续失败次数 < 阈值（只计数，不告警）
#   2  参数/配置/依赖错误（不写告警，先修配置）
#   3  连续失败达到阈值：已写告警文件并调用 ALERT_CMD（若配置）
#
# 用法：
#   bash deploy/monitoring/health_probe.sh                  # 正常探测
#   bash deploy/monitoring/health_probe.sh --dry-run        # 只打印计划，不联网/不写文件
#   bash deploy/monitoring/health_probe.sh --base-url http://127.0.0.1:8080
#   bash deploy/monitoring/health_probe.sh --reset          # 人工清除计数与告警（复盘/演练后）
#   bash deploy/monitoring/health_probe.sh --help
#
# 环境变量（默认值见下；可写入 deploy/monitoring/monitoring.env）：
#   BASE_URL                  http://127.0.0.1:${NGINX_HTTP_PORT:-8080}
#   HEALTH_PATH               /api/healthz
#   NGINX_HEALTH_PATH         /nginx-health
#   PROBE_CONNECT_TIMEOUT     3     秒
#   PROBE_MAX_TIME            5     秒（单次请求总超时）
#   HEALTH_FAIL_THRESHOLD     3     连续失败多少次告警
#   HEALTH_ALERT_REPEAT_SECONDS 3600 同一告警的钩子冷却（秒）
#   STATE_DIR                 <repo>/deploy/evidence/monitoring
#   ALERT_CMD                 空（配置后按 deploy/monitoring/lib.sh 的接口调用）
#   MONITORING_ENV_FILE       默认 deploy/monitoring/monitoring.env（空串禁用）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=deploy/monitoring/lib.sh
. "${SCRIPT_DIR}/lib.sh"
mon_load_env

BASE_URL="${BASE_URL:-http://127.0.0.1:${NGINX_HTTP_PORT:-8080}}"
HEALTH_PATH="${HEALTH_PATH:-/api/healthz}"
NGINX_HEALTH_PATH="${NGINX_HEALTH_PATH:-/nginx-health}"
PROBE_CONNECT_TIMEOUT="${PROBE_CONNECT_TIMEOUT:-3}"
PROBE_MAX_TIME="${PROBE_MAX_TIME:-5}"
HEALTH_FAIL_THRESHOLD="${HEALTH_FAIL_THRESHOLD:-3}"
HEALTH_ALERT_REPEAT_SECONDS="${HEALTH_ALERT_REPEAT_SECONDS:-3600}"
STATE_DIR="${STATE_DIR:-${REPO_ROOT}/deploy/evidence/monitoring}"

STATE_FILE="${STATE_DIR}/health_probe.state"
ALERT_FILE="${STATE_DIR}/alert_health_probe.log"
ALERT_SOURCE="health_probe"
DRY_RUN=0
RESET=0

usage() {
    cat <<'EOF'
用法：bash deploy/monitoring/health_probe.sh [选项]

  --dry-run              只打印探测计划与阈值，不联网、不写状态/告警文件
  --reset                清除连续失败计数与告警文件（恢复/演练后使用）
  --base-url URL         覆盖 BASE_URL（默认 http://127.0.0.1:8080）
  -h, --help             显示本帮助

退出码：0=健康；1=失败但未达阈值；2=配置错误；3=已达阈值并告警。
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --reset) RESET=1 ;;
        --base-url)
            shift
            [ "$#" -gt 0 ] || mon_die "--base-url 缺少 URL 参数"
            BASE_URL="$1"
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

BASE_URL="${BASE_URL%/}"
case "$BASE_URL" in
    http://*|https://*) ;;
    *) mon_die "BASE_URL 必须以 http:// 或 https:// 开头：${BASE_URL}" ;;
esac
case "$HEALTH_PATH" in
    /*) ;;
    *) mon_die "HEALTH_PATH 必须以 / 开头：${HEALTH_PATH}" ;;
esac
case "$NGINX_HEALTH_PATH" in
    /*) ;;
    *) mon_die "NGINX_HEALTH_PATH 必须以 / 开头：${NGINX_HEALTH_PATH}" ;;
esac
mon_require_posint "PROBE_CONNECT_TIMEOUT" "$PROBE_CONNECT_TIMEOUT"
mon_require_posint "PROBE_MAX_TIME" "$PROBE_MAX_TIME"
mon_require_posint "HEALTH_FAIL_THRESHOLD" "$HEALTH_FAIL_THRESHOLD"
mon_require_uint "HEALTH_ALERT_REPEAT_SECONDS" "$HEALTH_ALERT_REPEAT_SECONDS"

if [ "$RESET" = "1" ]; then
    if [ "$DRY_RUN" = "1" ]; then
        mon_log "[dry-run] 将清除状态文件 ${STATE_FILE} 与告警文件 ${ALERT_FILE}"
        exit 0
    fi
    rm -f "$STATE_FILE" "$ALERT_FILE" "$(_mon_cooldown_path "$ALERT_FILE")" 2>/dev/null || true
    mon_log "已清除健康探测状态与告警：${STATE_FILE} ${ALERT_FILE}"
    exit 0
fi

if [ "$DRY_RUN" = "1" ]; then
    mon_log "[dry-run] 不发起网络请求、不写状态/告警文件、不调用 ALERT_CMD"
    printf '  base_url=%s\n' "$BASE_URL"
    printf '  endpoints=%s%s , %s%s\n' "$BASE_URL" "$HEALTH_PATH" "$BASE_URL" "$NGINX_HEALTH_PATH"
    printf '  connect_timeout=%ss max_time=%ss\n' "$PROBE_CONNECT_TIMEOUT" "$PROBE_MAX_TIME"
    printf '  fail_threshold=%s（连续失败达到该值才告警）\n' "$HEALTH_FAIL_THRESHOLD"
    printf '  alert_repeat_seconds=%s\n' "$HEALTH_ALERT_REPEAT_SECONDS"
    printf '  state_dir=%s\n' "$STATE_DIR"
    printf '  alert_file=%s\n' "$ALERT_FILE"
    if [ -n "${ALERT_CMD:-}" ]; then
        printf '  alert_cmd=已配置（命令内容不打印，避免泄露 webhook 令牌）\n'
    else
        printf '  alert_cmd=未配置（仅落告警文件）\n'
    fi
    exit 0
fi

mon_require_cmd curl
mon_require_cmd date
mkdir -p "$STATE_DIR" 2>/dev/null || mon_die "无法创建状态目录：${STATE_DIR}"

# 读取上一次的连续失败次数（缺失/损坏一律按 0 处理）。
prev_fail=0
if [ -f "$STATE_FILE" ]; then
    prev_fail="$(sed -n 's/^fail_count=//p' "$STATE_FILE" 2>/dev/null | head -n 1 || true)"
    case "${prev_fail:-}" in
        ''|*[!0-9]*) prev_fail=0 ;;
    esac
fi

# 探测一个端点；成功返回 0，失败返回 1，并把单行原因写入全局 PROBE_DETAIL。
PROBE_DETAIL=""
probe_one() {
    _kind="$1"
    _url="$2"
    PROBE_DETAIL=""

    _body="$(mktemp "${TMPDIR:-/tmp}/health_probe_body.XXXXXX" 2>/dev/null || true)"
    _err="$(mktemp "${TMPDIR:-/tmp}/health_probe_err.XXXXXX" 2>/dev/null || true)"
    if [ -z "$_body" ] || [ -z "$_err" ]; then
        PROBE_DETAIL="${_kind}:mktemp_failed"
        if [ -n "$_body" ]; then rm -f "$_body"; fi
        if [ -n "$_err" ]; then rm -f "$_err"; fi
        return 1
    fi

    _code="$(curl -sS -o "$_body" -w '%{http_code}' \
        --connect-timeout "$PROBE_CONNECT_TIMEOUT" \
        --max-time "$PROBE_MAX_TIME" \
        "$_url" 2>"$_err")" || _code=""

    if [ -z "$_code" ] || [ "$_code" = "000" ]; then
        _why="$(head -n 1 "$_err" 2>/dev/null | tr -d '\r' || true)"
        PROBE_DETAIL="${_kind}:curl_error(${_why:-no response})"
        rm -f "$_body" "$_err"
        return 1
    fi

    if [ "$_code" != "200" ]; then
        PROBE_DETAIL="${_kind}:http_${_code}"
        rm -f "$_body" "$_err"
        return 1
    fi

    if [ "$_kind" = "api" ]; then
        if ! grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"' "$_body"; then
            PROBE_DETAIL="${_kind}:status_not_ok"
            rm -f "$_body" "$_err"
            return 1
        fi
        # 契约/TASK-BE-007：DB 不可用时 /healthz 应返回 503；这里再校验 db:"ok" 防止代理缓存假 200
        if ! grep -Eq '"db"[[:space:]]*:[[:space:]]*"ok"' "$_body"; then
            PROBE_DETAIL="${_kind}:db_not_ok"
            rm -f "$_body" "$_err"
            return 1
        fi
    else
        # nginx-health 的契约是纯文本 ok（deploy/nginx.conf）
        if ! grep -Eq '^[[:space:]]*ok[[:space:]]*$' "$_body"; then
            PROBE_DETAIL="${_kind}:body_not_ok"
            rm -f "$_body" "$_err"
            return 1
        fi
    fi

    mon_log "OK ${_kind} ${_url} status=${_code}"
    rm -f "$_body" "$_err"
    PROBE_DETAIL=""
    return 0
}

fail_detail=""
if ! probe_one "api" "${BASE_URL}${HEALTH_PATH}"; then
    fail_detail="${fail_detail}${PROBE_DETAIL}; "
fi
if ! probe_one "nginx" "${BASE_URL}${NGINX_HEALTH_PATH}"; then
    fail_detail="${fail_detail}${PROBE_DETAIL}; "
fi

write_state() {
    _fail_count="$1"
    _result="$2"
    _detail="$3"
    {
        printf 'fail_count=%s\n' "$_fail_count"
        printf 'last_sample=%s\n' "$(mon_now_iso)"
        printf 'last_result=%s\n' "$_result"
        printf 'last_detail=%s\n' "$_detail"
        printf 'base_url=%s\n' "$BASE_URL"
    } >"${STATE_FILE}.tmp" 2>/dev/null \
        && mv "${STATE_FILE}.tmp" "$STATE_FILE" 2>/dev/null \
        || mon_die "无法写入状态文件：${STATE_FILE}"
}

if [ -z "$fail_detail" ]; then
    if [ "$prev_fail" -gt 0 ]; then
        mon_log "RECOVERED 连续失败计数由 ${prev_fail} 归零"
    fi
    write_state 0 "ok" "-"
    mon_clear_alert "$ALERT_FILE"
    exit 0
fi

fail_detail="${fail_detail% }"
fail_detail="${fail_detail%;}"
fail_count=$((prev_fail + 1))
write_state "$fail_count" "fail" "$fail_detail"

if [ "$fail_count" -lt "$HEALTH_FAIL_THRESHOLD" ]; then
    mon_warn "探测失败 ${fail_count}/${HEALTH_FAIL_THRESHOLD}（未达告警阈值）：${fail_detail}"
    exit 1
fi

mon_alert "health_probe_failed" \
    "健康探测连续 ${fail_count} 次失败（阈值 ${HEALTH_FAIL_THRESHOLD}）：${fail_detail}；base=${BASE_URL}" \
    "$ALERT_FILE" "$HEALTH_ALERT_REPEAT_SECONDS"

mon_warn "连续失败 ${fail_count} 次，已告警：${ALERT_FILE}"
exit 3
