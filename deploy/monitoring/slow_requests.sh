#!/usr/bin/env bash
# deploy/monitoring/slow_requests.sh — 慢请求日报（TASK-REL-003）
#
# 从运行中的容器日志（api + nginx）统计：
#   - latency_ms > SLOW_LATENCY_MS（默认 1000）的请求数；
#   - 按路径聚合的 Top N（默认 10：次数 / 平均耗时 / 最大耗时 / 路径）；
#   - 同窗口的 5xx 计数（辅助判断“慢”是否伴随错误）。
#
# 支持的日志行（详见 deploy/monitoring/slow_requests.awk）：
#   1) api JSON 行：request_id/method/path/status/latency_ms/actor_id（目标格式）
#   2) nginx JSON 行：request + status + request_time（秒，自动换算成毫秒）
#   3) nginx 文本行（需自定义 log_format 带 rt= 或 request_time=）
#   默认 combined 格式没有耗时字段，只能计入 unknown_latency —— 见 docs/72-monitoring.md §5.3
#   的 nginx 待应用片段（本卡不改 deploy/nginx.conf）。
#
# 退出码：
#   0  报告生成成功（慢请求数量本身不改变退出码）
#   1  读取容器日志失败（docker 不可用 / compose 项目未运行等）
#   2  参数或配置错误
#
# 用法（仓库根执行）：
#   bash deploy/monitoring/slow_requests.sh                       # 最近 24h
#   bash deploy/monitoring/slow_requests.sh --since 2h --top 10
#   bash deploy/monitoring/slow_requests.sh --file /path/access.log  # 离线分析/取证
#   bash deploy/monitoring/slow_requests.sh --dry-run
#
# 环境变量（默认值见下；可写入 deploy/monitoring/monitoring.env）：
#   SLOW_LATENCY_MS     1000
#   SLOW_TOP_N          10
#   SLOW_LOG_SINCE      24h（docker compose logs --since 的值）
#   LOG_FILE            空（设置后读该文件，不再调用 docker）
#   COMPOSE_FILE        <repo>/deploy/docker-compose.yml
#   ENV_FILE            <repo>/deploy/.env.staging（只读取 COMPOSE_PROJECT_NAME，不读密码）
#   COMPOSE_PROJECT_NAME 空（缺省时从 ENV_FILE 读取）
#   REPORT_DIR          <repo>/deploy/evidence/monitoring
#   MONITORING_ENV_FILE 默认 deploy/monitoring/monitoring.env（空串禁用）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=deploy/monitoring/lib.sh
. "${SCRIPT_DIR}/lib.sh"
mon_load_env

SLOW_LATENCY_MS="${SLOW_LATENCY_MS:-1000}"
SLOW_TOP_N="${SLOW_TOP_N:-10}"
SLOW_LOG_SINCE="${SLOW_LOG_SINCE:-24h}"
COMPOSE_FILE="${COMPOSE_FILE:-${REPO_ROOT}/deploy/docker-compose.yml}"
ENV_FILE="${ENV_FILE:-${REPO_ROOT}/deploy/.env.staging}"
REPORT_DIR="${REPORT_DIR:-${REPO_ROOT}/deploy/evidence/monitoring}"
LOG_FILE="${LOG_FILE:-}"

ALERT_SOURCE="slow_requests"
DRY_RUN=0

usage() {
    cat <<'EOF'
用法：bash deploy/monitoring/slow_requests.sh [选项]

  --dry-run            只打印数据源与阈值，不读取日志、不写报告
  --file PATH          分析指定日志文件（离线/取证），不调用 docker
  --since VALUE        传给 docker compose logs --since（默认 24h，如 2h/30m）
  --latency-ms N       慢请求阈值（默认 1000 毫秒）
  --top N              Top 路径数量（默认 10）
  -h, --help           显示本帮助

退出码：0=报告成功；1=读取日志失败；2=参数/配置错误。
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --file)
            shift
            [ "$#" -gt 0 ] || mon_die "--file 缺少路径参数"
            LOG_FILE="$1"
            ;;
        --since)
            shift
            [ "$#" -gt 0 ] || mon_die "--since 缺少时间参数（如 2h）"
            SLOW_LOG_SINCE="$1"
            ;;
        --latency-ms)
            shift
            [ "$#" -gt 0 ] || mon_die "--latency-ms 缺少数字参数"
            SLOW_LATENCY_MS="$1"
            ;;
        --top)
            shift
            [ "$#" -gt 0 ] || mon_die "--top 缺少数字参数"
            SLOW_TOP_N="$1"
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

mon_require_posint "SLOW_LATENCY_MS" "$SLOW_LATENCY_MS"
mon_require_posint "SLOW_TOP_N" "$SLOW_TOP_N"
[ -n "$SLOW_LOG_SINCE" ] || mon_die "SLOW_LOG_SINCE 不能为空"
[ -f "${SCRIPT_DIR}/slow_requests.awk" ] || mon_die "缺少解析器：${SCRIPT_DIR}/slow_requests.awk"

# 只从 ENV_FILE 读取项目名，绝不读取/回显密码行。
PROJECT="${COMPOSE_PROJECT_NAME:-}"
if [ -z "$PROJECT" ] && [ -f "$ENV_FILE" ]; then
    PROJECT="$(grep -E '^COMPOSE_PROJECT_NAME=' "$ENV_FILE" 2>/dev/null | tail -n 1 | cut -d= -f2- | tr -d '\r' || true)"
fi
COMPOSE=()
if [ -n "$PROJECT" ]; then
    COMPOSE=(docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" -p "$PROJECT")
fi

if [ "$DRY_RUN" = "1" ]; then
    mon_log "[dry-run] 不读取日志、不写报告"
    printf '  latency_threshold_ms=%s top_n=%s\n' "$SLOW_LATENCY_MS" "$SLOW_TOP_N"
    if [ -n "$LOG_FILE" ]; then
        printf '  source=file:%s\n' "$LOG_FILE"
        [ -r "$LOG_FILE" ] || printf '  WARN 文件当前不可读（执行前请确认路径）\n'
    else
        printf '  source=compose project=%s services=api,nginx since=%s\n' \
            "${PROJECT:-（未解析：需要 ENV_FILE 或 COMPOSE_PROJECT_NAME）}" "$SLOW_LOG_SINCE"
        printf '  compose_file=%s\n' "$COMPOSE_FILE"
    fi
    printf '  report_dir=%s\n' "$REPORT_DIR"
    exit 0
fi

mon_require_cmd awk
mon_require_cmd sort
mon_require_cmd head
mon_require_cmd date

_tmp_log=""
_records=""
_top_file=""
cleanup() {
    if [ -n "${_tmp_log}" ]; then
        rm -f "${_tmp_log}" "${_tmp_log}.err" 2>/dev/null || true
    fi
    if [ -n "${_records}" ]; then
        rm -f "${_records}" 2>/dev/null || true
    fi
    if [ -n "${_top_file}" ]; then
        rm -f "${_top_file}" "${_top_file}.sorted" 2>/dev/null || true
    fi
}
trap cleanup EXIT

if [ -n "$LOG_FILE" ]; then
    [ -r "$LOG_FILE" ] || mon_die "日志文件不可读：${LOG_FILE}"
    _source="$LOG_FILE"
    _source_desc="file:${LOG_FILE}"
else
    mon_require_cmd docker
    if [ "${#COMPOSE[@]}" -eq 0 ]; then
        mon_die "需要 COMPOSE_PROJECT_NAME（或可读的 ENV_FILE=${ENV_FILE}），或用 --file 指定离线日志"
    fi
    [ -f "$ENV_FILE" ] || mon_die "ENV_FILE 不存在：${ENV_FILE}"
    [ -f "$COMPOSE_FILE" ] || mon_die "COMPOSE_FILE 不存在：${COMPOSE_FILE}"
    _tmp_log="$(mktemp "${TMPDIR:-/tmp}/slow_requests.XXXXXX" 2>/dev/null || true)"
    [ -n "$_tmp_log" ] || mon_die "无法创建临时文件（检查 TMPDIR）"

    if "${COMPOSE[@]}" logs --no-color --since "$SLOW_LOG_SINCE" api nginx >"$_tmp_log" 2>"${_tmp_log}.err"; then
        :
    else
        _err_line="$(tail -n 1 "${_tmp_log}.err" 2>/dev/null || true)"
        mon_warn "docker compose logs 失败：${_err_line:-见 docker compose ps}"
        exit 1
    fi
    _source="$_tmp_log"
    _source_desc="compose:${PROJECT} services=api,nginx since=${SLOW_LOG_SINCE}"
fi

_records="$(mktemp "${TMPDIR:-/tmp}/slow_requests_records.XXXXXX" 2>/dev/null || true)"
[ -n "$_records" ] || mon_die "无法创建临时文件（检查 TMPDIR）"

_awk_rc=0
_stats_raw="$(awk -v threshold="$SLOW_LATENCY_MS" -v records="$_records" \
    -f "${SCRIPT_DIR}/slow_requests.awk" "$_source")" || _awk_rc=$?
[ "$_awk_rc" -eq 0 ] || mon_die "日志解析失败（awk exit=${_awk_rc}）"

_stats_line="$(printf '%s\n' "$_stats_raw" | grep '^STATS' | head -n 1 || true)"
[ -n "$_stats_line" ] || mon_die "未得到统计结果（日志为空或解析器输出异常）"

IFS=$'\t' read -r _tag _total _access _parsed _slow _unknown _http5xx <<< "$_stats_line"
_total="${_total:-0}"
_access="${_access:-0}"
_parsed="${_parsed:-0}"
_slow="${_slow:-0}"
_unknown="${_unknown:-0}"
_http5xx="${_http5xx:-0}"

_top=""
if [ -s "$_records" ]; then
    _top_file="${_records}.top"
    awk -F'\t' '{
        count[$2]++
        sum[$2] += $1
        if ($1 + 0 > max[$2]) { max[$2] = $1 + 0 }
    }
    END {
        for (p in count) {
            printf "%d\t%.0f\t%.0f\t%s\n", count[p], sum[p] / count[p], max[p], p
        }
    }' "$_records" >"${_top_file}"
    LC_ALL=C sort -t $'\t' -k1,1nr -k4,4 "${_top_file}" >"${_top_file}.sorted"
    _top="$(head -n "$SLOW_TOP_N" "${_top_file}.sorted")"
fi

mkdir -p "$REPORT_DIR" 2>/dev/null || mon_die "无法创建报告目录：${REPORT_DIR}"
REPORT_FILE="${REPORT_DIR}/slow-requests-$(date -u +%Y%m%d).log"

{
    printf '== 慢请求日报（threshold>%sms）==\n' "$SLOW_LATENCY_MS"
    printf 'time=%s\n' "$(mon_now_iso)"
    printf 'source=%s\n' "$_source_desc"
    printf 'summary: total=%s access=%s parsed=%s slow=%s unknown_latency=%s http_5xx=%s\n' \
        "$_total" "$_access" "$_parsed" "$_slow" "$_unknown" "$_http5xx"
    printf -- '-- 慢请求 Top %s（次数 / 平均ms / 最大ms / 路径）--\n' "$SLOW_TOP_N"
    if [ -n "$_top" ]; then
        printf '  次数\t平均ms\t最大ms\t路径\n'
        printf '%s\n' "$_top" | awk -F'\t' '{ printf "  %s\t%s\t%s\t%s\n", $1, $2, $3, $4 }'
    else
        printf '  （无 latency>%sms 的记录）\n' "$SLOW_LATENCY_MS"
    fi
    printf -- '-- 说明 --\n'
    printf '  - api 行需含 latency_ms；nginx JSON 用 request_time（秒），文本需 rt= 或 request_time=。\n'
    printf '  - unknown_latency>0 表示访问日志缺少耗时字段（默认 nginx combined 格式即如此）。\n'
    printf '  - 报告按 UTC 日期追加：%s\n' "$REPORT_FILE"
} | tee -a "$REPORT_FILE"

exit 0
