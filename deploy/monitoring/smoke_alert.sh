#!/usr/bin/env bash
# deploy/monitoring/smoke_alert.sh — 告警链路自测（TASK-REL-003 验收）
#
# 目的：在没有真实故障的情况下，**真实触发**三类告警并验证：
#   - 告警文件按 reason=... 落盘；
#   - ALERT_CMD 钩子被调用（用临时记录器验证，不连真实 webhook）；
#   - 恢复/清理路径（health_probe --reset）与 dry-run 不产生副作用。
#
# 全部写入临时目录，不碰 staging 的告警文件；证据写入
# deploy/evidence/monitoring/smoke-alert-<UTC>.log（自动生成）。
#
# 用法（仓库根执行；需要 bash/curl/awk/df）：
#   bash deploy/monitoring/smoke_alert.sh
#   bash deploy/monitoring/smoke_alert.sh --keep    # 保留临时目录便于查看
#
# 退出码：0=全部通过；1=有失败；2=环境/参数错误。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=deploy/monitoring/lib.sh
. "${SCRIPT_DIR}/lib.sh"

# 自测必须与真实配置隔离：禁用 monitoring.env，清掉可能继承的告警命令。
export MONITORING_ENV_FILE=""
unset ALERT_CMD 2>/dev/null || true
export BACKUP_ALERT_CMD=""

EVID_DIR="${EVID_DIR:-${REPO_ROOT}/deploy/evidence/monitoring}"
KEEP=0

if [ "$#" -gt 1 ]; then
    printf 'ERROR: 参数过多（只接受 --keep）\n' >&2
    exit 2
fi

case "${1:-}" in
    --keep) KEEP=1 ;;
    -h|--help)
        cat <<'EOF'
用法：bash deploy/monitoring/smoke_alert.sh [--keep]

  --keep   保留临时目录（失败样本、告警文件、hook 记录），便于排查
EOF
        exit 0
        ;;
    '') ;;
    *)
        printf 'ERROR: 未知参数：%s（--help 查看用法）\n' "$1" >&2
        exit 2
        ;;
esac

mon_require_cmd mktemp
mon_require_cmd env
mon_require_cmd grep
mon_require_cmd awk

mkdir -p "$EVID_DIR" 2>/dev/null || mon_die "无法创建证据目录：${EVID_DIR}"
EVID="${EVID_DIR}/smoke-alert-$(date -u +%Y%m%dT%H%M%SZ).log"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/monitor_smoke.XXXXXX" 2>/dev/null || true)"
[ -n "$TMP" ] || mon_die "无法创建临时目录（检查 TMPDIR）"

cleanup() {
    if [ "$KEEP" = "1" ]; then
        printf '保留临时目录：%s\n' "$TMP"
    else
        rm -rf "$TMP" 2>/dev/null || true
    fi
}
trap cleanup EXIT

# stdout/stderr 同时落证据（与 deploy/scripts/smoke.sh 同风格）
exec > >(tee -a "$EVID") 2>&1

printf '# REL-003 告警链路自测\n'
printf '# 时间(UTC): %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf '# git: %s\n' "$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
printf '# 临时目录: %s\n\n' "$TMP"

passed=0
failed=0
skipped=0

record_pass() {
    passed=$((passed + 1))
    printf 'PASS  %s\n' "$1"
}

record_fail() {
    failed=$((failed + 1))
    printf 'FAIL  %s\n' "$1"
}

record_skip() {
    skipped=$((skipped + 1))
    printf 'SKIP  %s\n' "$1"
}

assert_file() {
    if [ -f "$2" ]; then
        record_pass "$1"
    else
        record_fail "$1（缺少文件：$2）"
    fi
}

assert_missing() {
    if [ ! -e "$2" ]; then
        record_pass "$1"
    else
        record_fail "$1（文件仍存在：$2）"
    fi
}

assert_grep() {
    if grep -qE "$2" "$3" 2>/dev/null; then
        record_pass "$1"
    else
        record_fail "$1（在 $3 中未匹配 /$2/）"
    fi
}

run_expect() {
    _expected="$1"
    _label="$2"
    shift 2
    printf '\n### %s\n' "$_label" >>"$TMP/commands.log"
    _rc=0
    "$@" >>"$TMP/commands.log" 2>&1 || _rc=$?
    if [ "$_rc" -eq "$_expected" ]; then
        record_pass "${_label}（exit=${_rc}）"
    else
        record_fail "${_label}（期望 exit=${_expected}，实际 ${_rc}）"
        printf -- '-- 该用例输出（最后 20 行）--\n'
        tail -n 20 "$TMP/commands.log"
        printf -- '--\n'
    fi
}

HOOK_LOG="$TMP/hook.log"
: >"$HOOK_LOG"
HOOK_CMD='printf "%s\n" "$ALERT_REASON" >>"$SMOKE_HOOK_LOG"'

# ---------------------------------------------------------------------------
# 1) 健康探测：连续失败达到阈值 → 告警文件 + ALERT_CMD
# ---------------------------------------------------------------------------
if command -v curl >/dev/null 2>&1; then
    run_expect 3 "health_probe 连续失败触发告警（127.0.0.1:1）" \
        env MONITORING_ENV_FILE= ALERT_CMD="$HOOK_CMD" SMOKE_HOOK_LOG="$HOOK_LOG" \
        STATE_DIR="$TMP/health" BASE_URL="http://127.0.0.1:1" \
        HEALTH_FAIL_THRESHOLD=1 HEALTH_ALERT_REPEAT_SECONDS=0 \
        PROBE_CONNECT_TIMEOUT=2 PROBE_MAX_TIME=3 \
        bash "$SCRIPT_DIR/health_probe.sh"

    assert_file "health alert 文件已落盘" "$TMP/health/alert_health_probe.log"
    assert_grep "health alert reason=health_probe_failed" \
        '^reason=health_probe_failed$' "$TMP/health/alert_health_probe.log"
    assert_grep "ALERT_CMD 收到 health_probe_failed" '^health_probe_failed$' "$HOOK_LOG"

    run_expect 0 "health_probe --reset 清除状态与告警" \
        env MONITORING_ENV_FILE= ALERT_CMD= \
        STATE_DIR="$TMP/health" \
        bash "$SCRIPT_DIR/health_probe.sh" --reset
    assert_missing "health alert 文件已清除" "$TMP/health/alert_health_probe.log"

    run_expect 0 "health_probe --dry-run 无副作用" \
        env MONITORING_ENV_FILE= ALERT_CMD= STATE_DIR="$TMP/health_dry" \
        bash "$SCRIPT_DIR/health_probe.sh" --dry-run
    assert_missing "dry-run 不创建状态目录" "$TMP/health_dry"
else
    record_skip "curl 缺失：跳过真实健康探测（其余用例继续）"
fi

# ---------------------------------------------------------------------------
# 2) 资源告警：磁盘阈值人为设为 0% → 必然触发
# ---------------------------------------------------------------------------
if command -v df >/dev/null 2>&1; then
    run_expect 1 "resource_alerts 磁盘阈值触发" \
        env MONITORING_ENV_FILE= ALERT_CMD="$HOOK_CMD" SMOKE_HOOK_LOG="$HOOK_LOG" \
        STATE_DIR="$TMP/resource" ALERT_DIR="$TMP/resource" \
        DISK_THRESHOLD_PCT=0 DISK_PATHS="/" \
        bash "$SCRIPT_DIR/resource_alerts.sh" --skip-freshness --skip-containers

    assert_file "disk alert 文件已落盘" "$TMP/resource/alert_disk_high.log"
    assert_grep "disk alert reason=disk_high" '^reason=disk_high$' "$TMP/resource/alert_disk_high.log"
    assert_grep "ALERT_CMD 收到 disk_high" '^disk_high$' "$HOOK_LOG"
else
    record_skip "df 缺失：跳过磁盘告警用例"
fi

# ---------------------------------------------------------------------------
# 3) 备份新鲜度：伪造一个 1970 年的成功备份 → freshness_check.sh 判定过期
# ---------------------------------------------------------------------------
FAKE_BACKUP="$TMP/backups"
mkdir -p "$FAKE_BACKUP"
printf '2000-01-01T00:00:00Z exam_20000101T000000Z.dump 1024 bytes\n' >"$FAKE_BACKUP/.last_backup_ok"
printf '0\n' >"$FAKE_BACKUP/.last_backup_ok.epoch"

run_expect 1 "resource_alerts 备份不新鲜触发" \
    env MONITORING_ENV_FILE= ALERT_CMD="$HOOK_CMD" SMOKE_HOOK_LOG="$HOOK_LOG" \
    BACKUP_ALERT_CMD= \
    STATE_DIR="$TMP/resource_backup" ALERT_DIR="$TMP/resource_backup" \
    FRESHNESS_MODE=local BACKUP_DIR="$FAKE_BACKUP" BACKUP_FRESHNESS_MAX_HOURS=26 \
    DISK_THRESHOLD_PCT=100 DISK_PATHS="/" \
    bash "$SCRIPT_DIR/resource_alerts.sh" --skip-containers

assert_file "backup alert 文件已落盘" "$TMP/resource_backup/alert_backup_stale.log"
assert_grep "backup alert reason=backup_stale" \
    '^reason=backup_stale$' "$TMP/resource_backup/alert_backup_stale.log"
assert_grep "ALERT_CMD 收到 backup_stale" '^backup_stale$' "$HOOK_LOG"
assert_file "freshness_check 自身也留下告警文件（容器卷口径）" \
    "$FAKE_BACKUP/.alert_stale_backup"

# ---------------------------------------------------------------------------
# 4) 慢请求：JSON 行 + nginx 文本/JSON 混合，已知 4 条慢请求
# ---------------------------------------------------------------------------
FIXTURE="$TMP/fixture.log"
cat >"$FIXTURE" <<'EOF'
2026-11-09T00:00:00Z api-1  | {"request_id":"r1","method":"GET","path":"/api/questions","status":200,"latency_ms":1500,"actor_id":"u1"}
2026-11-09T00:00:01Z api-1  | {"request_id":"r2","method":"POST","path":"/api/exams","status":500,"latency_ms":2500,"actor_id":null}
2026-11-09T00:00:02Z api-1  | {"request_id":"r3","method":"GET","path":"/api/healthz","status":200,"latency_ms":5,"actor_id":null}
172.18.0.1 - - [09/Nov/2026:00:00:03 +0000] "GET /api/slow-nginx HTTP/1.1" 504 0 "-" "curl/8.0" rt=2.300
{"time":"2026-11-09T00:00:04Z","request":"GET /api/nginx-json HTTP/1.1","status":200,"request_time":1.200,"remote_addr":"172.18.0.1"}
INFO:     Started server process [1]
EOF

run_expect 0 "slow_requests 解析 JSON+nginx 两种格式" \
    env MONITORING_ENV_FILE= REPORT_DIR="$TMP/report" \
    bash "$SCRIPT_DIR/slow_requests.sh" --file "$FIXTURE" --latency-ms 1000 --top 10

REPORT="$TMP/report/slow-requests-$(date -u +%Y%m%d).log"
assert_file "慢请求报告已生成" "$REPORT"
assert_grep "统计 slow=4" 'summary: .*slow=4' "$REPORT"
assert_grep "统计 http_5xx=2" 'http_5xx=2' "$REPORT"
assert_grep "Top 路径包含 /api/exams" '/api/exams' "$REPORT"

run_expect 0 "slow_requests --dry-run 不写报告" \
    env MONITORING_ENV_FILE= REPORT_DIR="$TMP/report_dry" \
    bash "$SCRIPT_DIR/slow_requests.sh" --file "$FIXTURE" --dry-run
assert_missing "dry-run 不创建报告目录" "$TMP/report_dry"

# ---------------------------------------------------------------------------
# 5) resource_alerts --dry-run 无副作用 + 非法参数退出码 2
# ---------------------------------------------------------------------------
run_expect 0 "resource_alerts --dry-run 无副作用" \
    env MONITORING_ENV_FILE= ALERT_CMD= STATE_DIR="$TMP/resource_dry" \
    bash "$SCRIPT_DIR/resource_alerts.sh" --dry-run
assert_missing "dry-run 不创建告警目录" "$TMP/resource_dry"

run_expect 2 "health_probe 非法参数退出码 2" \
    env MONITORING_ENV_FILE= bash "$SCRIPT_DIR/health_probe.sh" --not-a-real-flag

printf '\n== smoke 结果：passed=%s failed=%s skipped=%s ==\n' "$passed" "$failed" "$skipped"
printf '证据文件：%s\n' "$EVID"

if [ "$failed" -gt 0 ]; then
    exit 1
fi
exit 0
