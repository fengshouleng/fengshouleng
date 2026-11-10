#!/usr/bin/env bash
# deploy/release/staging_drill.sh — staging 回滚演练（TASK-REL-004 · G4 证据）
#
# 做什么（一条命令，按顺序，全部实测计时）：
#   0. 基线检查：state 中无可用回滚基线时，先做一次 baseline_deploy（tag=drill-<ts>-base）
#   1. deploy            ：真实上线流程（deploy.sh --skip-smoke --no-auto-rollback）
#   2. smoke_after_deploy：部署后冒烟（deploy/scripts/smoke.sh）
#   3. rollback          ：deploy/release/rollback.sh（默认切回 state.PREV_IMAGE_TAG）
#   4. smoke_after_rollback：回滚后冒烟
#
# 输出：
#   - reports/release-drill-<UTC时间戳>.json（机器可读，字段见 docs/73 §2）
#   - deploy/evidence/release-drill-<env>-<UTC时间戳>.log（完整过程输出）
#   - 退出码 0 = 四步全 PASS 且 rollback_seconds <= 900（15 分钟）；否则 1
#
# 只允许 staging：production 演练 = 真实故障，必须走 docs/74 的人工流程。
#
# 用法（仓库根执行）：
#   export ADMIN_USER=admin ADMIN_PASSWORD='...'
#   bash deploy/release/staging_drill.sh --env staging --g4-report-id G4-20261109-01
#
# 退出码：0 通过；1 失败；2 用法/前置不满足（含缺少 ADMIN_*、目标为 production）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/release/lib.sh
. "${SCRIPT_DIR}/lib.sh"

EXIT_OK=0
EXIT_FAIL=1
EXIT_USAGE=2

REL_ENV="staging"
ENV_FILE_ARG=""
PROJECT_ARG=""
BASE_URL_ARG=""
G4_ID="pending"
OUT_ARG=""
REASON=""
CLEANUP_ON_FAIL=1

REL_HEALTH_RETRIES="${REL_HEALTH_RETRIES:-30}"
REL_HEALTH_INTERVAL="${REL_HEALTH_INTERVAL:-2}"
ROLLBACK_LIMIT="${ROLLBACK_LIMIT_SECONDS:-900}"

REL_BASE_URL=""
STATE_FILE=""
OUT=""
START_EPOCH=0
START_ISO=""
TS=""
FINALIZED=0
DRILL_RESULT="FAIL"
DEPLOY_OK=0
LAST_STEP_RC=0
LAST_STEP_RESULT=""
LAST_STEP_SECONDS=0

STEP_NAMES=()
STEP_SECONDS=()
STEP_RESULTS=()
STEP_CODES=()

usage() {
    cat <<'EOF'
用法：bash deploy/release/staging_drill.sh [--env staging] [选项]

  --env staging            只接受 staging（默认 staging；production 一律拒绝）
  --g4-report-id <ID>      G4 报告编号；写入 JSON 的 g4 字段（默认 pending）
  --out <path>             JSON 输出路径（默认 reports/release-drill-<UTC>.json）
  --reason "..."           演练原因/批次说明
  --base-url <URL>         入口 URL（默认按 env 文件的 NGINX_HTTP_PORT 推导）
  --no-cleanup-on-fail     失败时不自动回滚清理 staging（默认会清理）
  --health-retries <n>     健康检查重试次数（默认 30）
  --health-interval <s>    健康检查间隔秒（默认 2）
  --project <name> / --env-file <path>   覆盖 compose 项目名 / 环境文件
  -h, --help               显示本帮助

前置：Docker 环境可用；已 export ADMIN_USER / ADMIN_PASSWORD（用于两次冒烟）。
输出：reports/release-drill-<UTC>.json + deploy/evidence/release-drill-<env>-<UTC>.log
EOF
}

need_value() {
    if [ "$#" -lt 2 ] || [ -z "${2:-}" ]; then
        rel_die "参数 $1 缺少取值" "$EXIT_USAGE"
    fi
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --env)
            need_value "$1" "${2:-}"
            REL_ENV="$2"
            shift 2
            ;;
        --g4-report-id)
            need_value "$1" "${2:-}"
            G4_ID="$2"
            shift 2
            ;;
        --out)
            need_value "$1" "${2:-}"
            OUT_ARG="$2"
            shift 2
            ;;
        --reason)
            need_value "$1" "${2:-}"
            REASON="$2"
            shift 2
            ;;
        --base-url)
            need_value "$1" "${2:-}"
            BASE_URL_ARG="$2"
            shift 2
            ;;
        --project)
            need_value "$1" "${2:-}"
            PROJECT_ARG="$2"
            shift 2
            ;;
        --env-file)
            need_value "$1" "${2:-}"
            ENV_FILE_ARG="$2"
            shift 2
            ;;
        --health-retries)
            need_value "$1" "${2:-}"
            REL_HEALTH_RETRIES="$2"
            shift 2
            ;;
        --health-interval)
            need_value "$1" "${2:-}"
            REL_HEALTH_INTERVAL="$2"
            shift 2
            ;;
        --no-cleanup-on-fail) CLEANUP_ON_FAIL=0; shift ;;
        -h|--help) usage; exit "$EXIT_OK" ;;
        *) rel_die "未知参数：$1（--help 查看用法）" "$EXIT_USAGE" ;;
    esac
done

preflight() {
    local _port=""

    if [ "$REL_ENV" != "staging" ]; then
        rel_die "演练只允许 --env staging（production 演练=真实故障；人工流程见 docs/74）" "$EXIT_USAGE"
    fi
    rel_require_cmds docker curl git jq tee
    rel_init_env "$REL_ENV" "$ENV_FILE_ARG" "$PROJECT_ARG"
    rel_require_posint "HEALTH_RETRIES" "$REL_HEALTH_RETRIES"
    rel_require_posint "HEALTH_INTERVAL" "$REL_HEALTH_INTERVAL"
    rel_require_posint "ROLLBACK_LIMIT_SECONDS" "$ROLLBACK_LIMIT"

    if [ -z "${ADMIN_USER:-}" ] || [ -z "${ADMIN_PASSWORD:-}" ]; then
        rel_die "演练需要 export ADMIN_USER 与 ADMIN_PASSWORD（两次冒烟用；口令不落盘）" "$EXIT_USAGE"
    fi
    if ! rel_compose config -q; then
        rel_die "compose 配置自检失败（见 docs/70 §2）" "$EXIT_USAGE"
    fi
    if [ ! -f "${SCRIPT_DIR}/deploy.sh" ] || [ ! -f "${SCRIPT_DIR}/rollback.sh" ]; then
        rel_die "缺少 deploy.sh / rollback.sh（发布脚本不完整）" "$EXIT_USAGE"
    fi

    if [ -n "$BASE_URL_ARG" ]; then
        REL_BASE_URL="$BASE_URL_ARG"
    else
        _port="$(sed -n 's/^NGINX_HTTP_PORT=//p' "$REL_ENV_FILE_RESOLVED" | tail -n 1 | tr -d '\r')"
        REL_BASE_URL="http://127.0.0.1:${_port:-8080}"
    fi
    REL_BASE_URL="${REL_BASE_URL%/}"
    case "$REL_BASE_URL" in
        http://*|https://*) : ;;
        *) rel_die "非法 --base-url：${REL_BASE_URL}" "$EXIT_USAGE" ;;
    esac

    STATE_FILE="${REL_REPO}/deploy/release/.state/${REL_ENV}.env"
}

# ---------------------------------------------------------------------------
# 步骤执行（计时 + 结果累积）
# ---------------------------------------------------------------------------
run_step() {
    local _name="$1"
    shift
    local _t0 _t1 _secs _rc=0 _result

    rel_log "===== [step] ${_name} 开始 ====="
    _t0="$(rel_now_epoch)"
    if "$@"; then
        _rc=0
        _result="PASS"
    else
        _rc=$?
        _result="FAIL"
    fi
    _t1="$(rel_now_epoch)"
    _secs=$((_t1 - _t0))

    STEP_NAMES+=("$_name")
    STEP_SECONDS+=("$_secs")
    STEP_RESULTS+=("$_result")
    STEP_CODES+=("$_rc")
    LAST_STEP_RC="$_rc"
    LAST_STEP_RESULT="$_result"
    LAST_STEP_SECONDS="$_secs"

    rel_log "===== [step] ${_name} 结束：result=${_result} seconds=${_secs} exit=${_rc} ====="
    if [ "$_result" = "PASS" ]; then
        return 0
    fi
    return 1
}

step_seconds_of() {
    local _name="$1" _i
    if [ "${#STEP_NAMES[@]}" -eq 0 ]; then
        printf '0'
        return 0
    fi
    for _i in "${!STEP_NAMES[@]}"; do
        if [ "${STEP_NAMES[$_i]}" = "$_name" ]; then
            printf '%s' "${STEP_SECONDS[$_i]}"
            return 0
        fi
    done
    printf '0'
}

# 单行化并去掉会破坏 JSON 的双引号（只用于 note/reason/path 等自由文本）
json_safe() {
    printf '%s' "$1" | tr -d '\r\n' | tr '"' "'"
}

# ---------------------------------------------------------------------------
# 被演练的三条命令
# ---------------------------------------------------------------------------
deploy_cmd() {
    bash "${SCRIPT_DIR}/deploy.sh" --env "$REL_ENV" --skip-smoke --no-auto-rollback \
        --base-url "$REL_BASE_URL" "$@"
}

smoke_cmd() {
    if [ -z "${ADMIN_USER:-}" ] || [ -z "${ADMIN_PASSWORD:-}" ]; then
        rel_err "冒烟缺少 ADMIN_USER / ADMIN_PASSWORD"
        return 1
    fi
    BASE="$REL_BASE_URL" \
    ENV_FILE="$REL_ENV_FILE_RESOLVED" \
    EVID_DIR="$REL_EVID_DIR" \
        bash "${REL_REPO}/deploy/scripts/smoke.sh"
}

rollback_cmd() {
    bash "${SCRIPT_DIR}/rollback.sh" --env "$REL_ENV" --auto \
        --base-url "$REL_BASE_URL" --reason "staging drill ${TS}" "$@"
}

# 失败清理：把 staging 恢复到“最近一次成功状态”
cleanup_rollback_cmd() {
    local _to="" _mig="" _from="drill-${TS}-new"
    if [ "$DEPLOY_OK" = "1" ]; then
        _to="$(rel_state_read "$STATE_FILE" PREV_IMAGE_TAG)"
        _mig="$(rel_state_read "$STATE_FILE" PREV_MIGRATION_VERSION)"
    else
        _to="$(rel_state_read "$STATE_FILE" IMAGE_TAG)"
        _mig="$(rel_state_read "$STATE_FILE" MIGRATION_VERSION)"
    fi
    case "$_to" in
        ''|none)
            rel_warn "清理回滚：state 中没有可用目标镜像，跳过"
            return 0
            ;;
    esac
    case "$_mig" in
        none) _mig="" ;;
    esac
    local -a _args=(--env "$REL_ENV" --auto --to-tag "$_to" --base-url "$REL_BASE_URL" \
        --reason "drill cleanup: ${TS}")
    if [ -n "$_mig" ]; then
        _args+=(--migration-before "$_mig")
    fi
    # 部署失败时运行中的容器可能还是旧镜像，但数据库可能已被新镜像升级：
    # 用本次构建的 tag 读 alembic 版本，避免“旧镜像找不到新 revision”导致误判。
    if [ -n "$_from" ] && rel_image_exists "$(rel_api_image "$_from")"; then
        _args+=(--from-tag "$_from")
    fi
    bash "${SCRIPT_DIR}/rollback.sh" "${_args[@]}"
}

# ---------------------------------------------------------------------------
# JSON 结果（字段见 docs/73 §2；reports/release-drill-<ts>.json）
# ---------------------------------------------------------------------------
finalize() {
    local _requested="$1" _note="${2:-}"
    local _i _all="PASS" _result="PASS" _total _rb_secs=0 _within="false"
    local _deploy_s _smoke_s _first=1

    if [ "$FINALIZED" = "1" ]; then
        return 0
    fi
    FINALIZED=1

    _total=$(( $(rel_now_epoch) - START_EPOCH ))
    if [ "${#STEP_NAMES[@]}" -gt 0 ]; then
        for _i in "${!STEP_NAMES[@]}"; do
            if [ "${STEP_RESULTS[$_i]}" != "PASS" ]; then
                _all="FAIL"
            fi
            if [ "${STEP_NAMES[$_i]}" = "rollback" ]; then
                _rb_secs="${STEP_SECONDS[$_i]}"
            fi
        done
    fi
    if [ "$_rb_secs" -gt 0 ] && [ "$_rb_secs" -le "$ROLLBACK_LIMIT" ]; then
        _within="true"
    fi
    if [ "$_requested" = "PASS" ] && [ "$_all" = "PASS" ] && [ "$_within" = "true" ]; then
        _result="PASS"
    fi

    _deploy_s="$(step_seconds_of deploy)"
    _smoke_s=$(( $(step_seconds_of smoke_after_deploy) + $(step_seconds_of smoke_after_rollback) ))

    mkdir -p "$(dirname "$OUT")" || rel_die "无法创建 JSON 输出目录：$(dirname "$OUT")" 2
    {
        printf '{\n'
        printf '  "schema": "release-drill/1",\n'
        printf '  "env": "%s",\n' "$REL_ENV"
        printf '  "g4": "%s",\n' "$(json_safe "$G4_ID")"
        printf '  "operator": "%s",\n' "$(json_safe "$(rel_operator)")"
        printf '  "host": "%s",\n' "$(json_safe "$(rel_hostname)")"
        printf '  "started_utc": "%s",\n' "$START_ISO"
        printf '  "finished_utc": "%s",\n' "$(rel_now_iso)"
        printf '  "steps": ['
        if [ "${#STEP_NAMES[@]}" -gt 0 ]; then
            for _i in "${!STEP_NAMES[@]}"; do
                if [ "$_first" = "1" ]; then
                    printf '\n'
                    _first=0
                else
                    printf ',\n'
                fi
                printf '    {"name": "%s", "seconds": %s, "result": "%s", "exit_code": %s}' \
                    "${STEP_NAMES[$_i]}" "${STEP_SECONDS[$_i]}" "${STEP_RESULTS[$_i]}" "${STEP_CODES[$_i]}"
            done
        fi
        if [ "$_first" = "0" ]; then
            printf '\n  '
        fi
        printf '],\n'
        printf '  "deploy_seconds": %s,\n' "$_deploy_s"
        printf '  "smoke_seconds_total": %s,\n' "$_smoke_s"
        printf '  "rollback_seconds": %s,\n' "$_rb_secs"
        printf '  "rollback_limit_seconds": %s,\n' "$ROLLBACK_LIMIT"
        printf '  "rollback_within_15min": %s,\n' "$_within"
        printf '  "total_seconds": %s,\n' "$_total"
        printf '  "result": "%s",\n' "$_result"
        printf '  "reason": "%s",\n' "$(json_safe "${REASON:-未提供}")"
        printf '  "note": "%s",\n' "$(json_safe "$_note")"
        printf '  "evidence_log": "%s"\n' "$(json_safe "${REL_EVID_FILE:-}")"
        printf '}\n'
    } >"$OUT"

    DRILL_RESULT="$_result"
    rel_log "===== 演练结论（release drill）====="
    rel_log "env=${REL_ENV} g4=${G4_ID} result=${_result}"
    rel_log "deploy_seconds=${_deploy_s} rollback_seconds=${_rb_secs} rollback_within_15min=${_within} total_seconds=${_total}"
    rel_log "smoke_seconds_total=${_smoke_s}"
    rel_log "json=${OUT}"
    rel_log "evidence=${REL_EVID_FILE:-unknown}"
    rel_log "回填：docs/61-gate-report.md §1 第 8/9 行 + §2「回滚耗时」；docs/73 §1/§5 引用本 JSON"
    rel_log "===================================="
}

drill_on_exit() {
    local _rc="$1"
    trap - EXIT
    set +e
    if [ "$_rc" -eq 0 ] || [ "${FINALIZED:-0}" = "1" ]; then
        exit "$_rc"
    fi
    rel_err "演练意外失败：rc=${_rc}"
    finalize "FAIL" "unexpected error rc=${_rc}"
    exit "$_rc"
}

# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
main() {
    preflight

    TS="$(date -u +%Y%m%dT%H%M%SZ)"
    REL_EVID_DIR="${EVID_DIR:-${REL_REPO}/deploy/evidence}"
    rel_open_log "${REL_EVID_DIR}/release-drill-${REL_ENV}-${TS}.log"
    START_EPOCH="$(rel_now_epoch)"
    START_ISO="$(rel_now_iso)"
    trap 'drill_on_exit $?' EXIT

    OUT="${OUT_ARG:-${REL_REPO}/reports/release-drill-${TS}.json}"
    case "$OUT" in
        /*) : ;;
        *) OUT="${REL_REPO}/${OUT}" ;;
    esac

    rel_log "===== staging 回滚演练开始（deploy → smoke → rollback → smoke）====="
    rel_log "env=${REL_ENV} g4=${G4_ID} base_url=${REL_BASE_URL} project=${REL_PROJECT}"
    rel_log "operator=$(rel_operator) host=$(rel_hostname) ts=${TS}"
    rel_log "rollback_limit=${ROLLBACK_LIMIT}s output=${OUT}"

    # --- 0. 基线：没有可用回滚目标时先部署一份基线 ---------------------------
    BASE_TAG="$(rel_state_read "$STATE_FILE" IMAGE_TAG)"
    if [ -z "$BASE_TAG" ] || [ "$BASE_TAG" = "none" ] \
        || ! rel_image_exists "$(rel_api_image "$BASE_TAG")" \
        || ! rel_image_exists "$(rel_web_image "$BASE_TAG")"; then
        rel_warn "无可用回滚基线（state IMAGE_TAG='${BASE_TAG:-空}' 或镜像缺失）：先做 baseline_deploy"
        if ! run_step "baseline_deploy" deploy_cmd --image-tag "drill-${TS}-base"; then
            finalize "FAIL" "baseline_deploy 失败：无法建立回滚基线"
            exit "$EXIT_FAIL"
        fi
    else
        rel_log "回滚基线已存在：IMAGE_TAG=${BASE_TAG}（跳过 baseline_deploy）"
    fi

    # --- 1. 部署 ------------------------------------------------------------
    if ! run_step "deploy" deploy_cmd --image-tag "drill-${TS}-new"; then
        rel_err "deploy 失败：按失败清理流程回滚"
        if [ "$CLEANUP_ON_FAIL" = "1" ]; then
            run_step "cleanup_rollback" cleanup_rollback_cmd || true
        fi
        finalize "FAIL" "deploy 失败"
        exit "$EXIT_FAIL"
    fi
    DEPLOY_OK=1

    # --- 2. 部署后冒烟 ------------------------------------------------------
    if ! run_step "smoke_after_deploy" smoke_cmd; then
        rel_err "部署后冒烟失败：按失败清理流程回滚"
        if [ "$CLEANUP_ON_FAIL" = "1" ]; then
            run_step "cleanup_rollback" cleanup_rollback_cmd || true
        fi
        finalize "FAIL" "smoke_after_deploy 失败"
        exit "$EXIT_FAIL"
    fi

    # --- 3. 回滚 ------------------------------------------------------------
    if ! run_step "rollback" rollback_cmd; then
        rel_err "rollback 失败：立即人工介入（docs/74）"
        finalize "FAIL" "rollback 失败"
        exit "$EXIT_FAIL"
    fi

    # --- 4. 回滚后冒烟 ------------------------------------------------------
    if ! run_step "smoke_after_rollback" smoke_cmd; then
        finalize "FAIL" "smoke_after_rollback 失败"
        exit "$EXIT_FAIL"
    fi

    finalize "PASS" "deploy → smoke → rollback → smoke 全部通过；rollback_seconds=$(step_seconds_of rollback)"
    if [ "$DRILL_RESULT" = "PASS" ]; then
        exit "$EXIT_OK"
    fi
    exit "$EXIT_FAIL"
}

main "$@"
