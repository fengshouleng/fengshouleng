#!/usr/bin/env bash
# deploy/release/rollback.sh — 回滚脚本（TASK-REL-004）
#
# 目标：把服务切回上一已验证镜像，并在 15 分钟内完成「切换 → 健康 → 冒烟」。
#
# 迁移回滚策略（默认最保守，符合“向前兼容优先”）：
#   A. 数据库版本 == 目标版本            → 不做任何 DB 操作
#   B. 版本不同且未给 --allow-downgrade  → **不执行** alembic downgrade，数据库保持新版本
#                                          （假设本次迁移向前兼容），并打印恢复预案
#   C. 版本不同且给了 --allow-downgrade  → 先 `alembic downgrade <目标> --sql` 预检；
#                                          预检失败 = 不可机械回滚 → 拒绝执行（exit 3）并给恢复预案；
#                                          预检通过才停 api → 真实 downgrade → 切镜像
#   不可回滚/被拒绝时，恢复预案 = docs/71 §3.2 从发布前备份整库恢复（RTO < 30min）
#
# 用法（仓库根执行）：
#   bash deploy/release/rollback.sh --env staging
#   bash deploy/release/rollback.sh --env production --confirm-production \
#       --to-tag <上一镜像 tag> --reason "P1 发布后 502"
#   bash deploy/release/rollback.sh --env staging --allow-downgrade   # 需事前评审
#
# 默认目标：
#   --to-tag          优先；缺省取 state 的 PREV_IMAGE_TAG（上一次成功发布的 tag）
#   --migration-before 优先；缺省取 state 的 PREV_MIGRATION_VERSION
#
# 退出码：
#   0 回滚完成且健康检查 + 冒烟通过（且未超过 15 分钟，或未启用 --strict-time）
#   1 已切换但验证失败/不完整/超时（需人工复核；服务通常已恢复）
#   2 用法/配置错误
#   3 拒绝执行（无回滚目标 / 目标镜像不存在 / 迁移不可机械回滚）
#   4 生产确认缺失（未提供 --confirm-production）
#   5 前置条件不满足（环境文件缺失等）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/release/lib.sh
. "${SCRIPT_DIR}/lib.sh"

EXIT_OK=0
EXIT_FAIL=1
EXIT_USAGE=2
EXIT_REFUSED=3
EXIT_CONFIRM=4
EXIT_PREFLIGHT=5

REL_ENV=""
ENV_FILE_ARG=""
PROJECT_ARG=""
BASE_URL_ARG=""
TO_TAG=""
FROM_TAG=""
MIGRATION_BEFORE=""
REASON=""
AUTO=0
CONFIRM_PRODUCTION=0
ALLOW_DOWNGRADE=0
STRICT_TIME=0
SKIP_SMOKE=0

REL_HEALTH_RETRIES="${REL_HEALTH_RETRIES:-30}"
REL_HEALTH_INTERVAL="${REL_HEALTH_INTERVAL:-2}"
ROLLBACK_LIMIT_SECONDS="${ROLLBACK_LIMIT_SECONDS:-900}"

REL_BASE_URL=""
START_EPOCH=0
CURRENT_STEP="init"
STATE_FILE=""
CURRENT_TAG=""
CURRENT_COMMIT="unknown"
TARGET_TAG=""
TARGET_COMMIT="unknown"
CURRENT_DB="unknown"
TARGET_MIG=""
DB_AFTER="unknown"
DB_ACTION="none"
ELAPSED=0
WITHIN_TARGET="no"
VERIFICATION="unknown"
FINISHED=0

usage() {
    cat <<'EOF'
用法：bash deploy/release/rollback.sh --env <staging|production> [选项]

必填：
  --env staging|production      目标环境

生产专用：
  --confirm-production          人工审批后由执行人输入；脚本不自批

常用选项：
  --to-tag <tag>                回滚到哪个镜像 tag（默认 state 的 PREV_IMAGE_TAG）
                                若跨多个版本/手工指定 tag，请同时给 --migration-before，
                                否则默认用 state 的 PREV_MIGRATION_VERSION（可能不匹配）
  --from-tag <tag>              当前/待回滚镜像 tag（默认探测运行中的 api 容器；
                                部署失败自动回滚时由 deploy.sh 传入本次构建的 tag，
                                用于正确读取已升级数据库的 alembic 版本）
  --migration-before <rev>      目标迁移版本（默认 state 的 PREV_MIGRATION_VERSION）
  --reason "..."                回滚原因（写入状态/证据；建议关联事故编号）
  --base-url <URL>              入口 URL（默认 state 的 BASE_URL；staging 可推导）
  --allow-downgrade             允许执行 alembic downgrade（需事前评审；默认不执行）
  --strict-time                 耗时 > 15 分钟即非 0 退出（G4 判据；演练用）
  --no-smoke                    跳过冒烟（不推荐：退出码仍为 1，表示“未完整验证”）
  --auto                        由 deploy.sh 自动触发（仅影响日志措辞）
  --health-retries <n>          健康检查重试次数（默认 30）
  --health-interval <s>         健康检查间隔秒（默认 2）
  --project <name> / --env-file <path>  覆盖 compose 项目名 / 环境文件
  -h, --help                    显示本帮助

退出码：0 成功；1 已切换但验证失败/不完整/超时；2 用法；3 拒绝执行；4 缺生产确认；5 前置不满足。
证据：deploy/evidence/rollback-<env>-<UTC>.log
恢复预案（不可回滚时）：docs/71-backup-restore.md §3.2；分级与止损：docs/74；检查单：docs/73 §5
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
        --to-tag)
            need_value "$1" "${2:-}"
            TO_TAG="$2"
            shift 2
            ;;
        --from-tag)
            need_value "$1" "${2:-}"
            FROM_TAG="$2"
            shift 2
            ;;
        --migration-before)
            need_value "$1" "${2:-}"
            MIGRATION_BEFORE="$2"
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
        --allow-downgrade) ALLOW_DOWNGRADE=1; shift ;;
        --strict-time) STRICT_TIME=1; shift ;;
        --no-smoke) SKIP_SMOKE=1; shift ;;
        --auto) AUTO=1; shift ;;
        --confirm-production) CONFIRM_PRODUCTION=1; shift ;;
        -h|--help) usage; exit "$EXIT_OK" ;;
        *) rel_die "未知参数：$1（--help 查看用法）" "$EXIT_USAGE" ;;
    esac
done

# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
tag_of_image() {
    # "exam-api:abc123" -> "abc123"；无 tag（含 digest）时输出空串
    local _ref="$1"
    case "$_ref" in
        *:*) printf '%s' "${_ref##*:}" ;;
        *) printf '' ;;
    esac
}

print_restore_plan() {
    rel_warn "================ 恢复预案（数据库回退不可用/未授权时）================"
    rel_warn "1) 停 api：docker compose -f ${REL_COMPOSE_FILE} --env-file ${REL_ENV_FILE_RESOLVED} -p ${REL_PROJECT} stop api"
    rel_warn "2) 选备份：... exec -T backup ls -1t /backups/*.dump | head -n 5"
    rel_warn "3) 恢复：... exec -T -e RESTORE_CONFIRM=yes backup /bin/sh /backup/restore.sh /backups/<dump>"
    rel_warn "4) 启 api：... start api（入口自动升级到当前代码所需版本）"
    rel_warn "5) 验证：curl -fsS ${REL_BASE_URL}/api/healthz；bash deploy/scripts/smoke.sh"
    rel_warn "6) 记录 RTO/RPO：docs/71 §4.2；事故分级/复盘：docs/74"
    rel_warn "详细步骤：docs/71-backup-restore.md §3.2（含前置检查与 --clean 语义）"
    rel_warn "====================================================================="
}

print_summary() {
    local _result="$1"
    rel_log "===== 回滚记录（rollback record）====="
    rel_log "result=${_result} env=${REL_ENV} auto=${AUTO} elapsed_seconds=${ELAPSED}"
    rel_log "from=${CURRENT_TAG:-unknown}(${CURRENT_COMMIT}) -> to=${TARGET_TAG:-unknown}(${TARGET_COMMIT})"
    rel_log "db_action=${DB_ACTION} db_version=${CURRENT_DB} -> ${DB_AFTER}（target=${TARGET_MIG:-none}）"
    rel_log "within_15min=${WITHIN_TARGET} limit_seconds=${ROLLBACK_LIMIT_SECONDS} verification=${VERIFICATION}"
    rel_log "reason=${REASON:-未提供}"
    rel_log "base_url=${REL_BASE_URL} project=${REL_PROJECT}"
    rel_log "operator=$(rel_operator) host=$(rel_hostname) auto_rollback=${RELEASE_AUTO_ROLLBACK:-0}"
    rel_log "evidence=${REL_EVID_FILE:-unknown}"
    rel_log "======================================"
}

rollback_on_exit() {
    local _rc="$1"
    trap - EXIT
    set +e
    if [ "$_rc" -eq 0 ] || [ "${FINISHED:-0}" = "1" ]; then
        exit "$_rc"
    fi
    ELAPSED=$(( $(rel_now_epoch) - START_EPOCH ))
    if [ "$_rc" -eq 3 ]; then
        rel_err "回滚被拒绝（exit=3）：见上方原因与恢复预案"
    else
        rel_err "回滚过程失败：step=${CURRENT_STEP} rc=${_rc} elapsed=${ELAPSED}s"
    fi
    print_summary "FAILED"
    print_restore_plan
    exit "$_rc"
}

# ---------------------------------------------------------------------------
# 前置检查
# ---------------------------------------------------------------------------
preflight() {
    local _port="" _running_image="" _state_image="" _state_prev=""

    [ -n "$REL_ENV" ] || rel_die "缺少 --env staging|production" "$EXIT_USAGE"
    rel_require_cmds docker curl git tee
    rel_init_env "$REL_ENV" "$ENV_FILE_ARG" "$PROJECT_ARG"
    rel_require_posint "HEALTH_RETRIES" "$REL_HEALTH_RETRIES"
    rel_require_posint "HEALTH_INTERVAL" "$REL_HEALTH_INTERVAL"
    rel_require_posint "ROLLBACK_LIMIT_SECONDS" "$ROLLBACK_LIMIT_SECONDS"

    if ! rel_compose config -q; then
        rel_die "compose 配置自检失败（见 docs/70 §2）" "$EXIT_USAGE"
    fi

    STATE_FILE="${REL_REPO}/deploy/release/.state/${REL_ENV}.env"
    _state_image="$(rel_state_read "$STATE_FILE" IMAGE_TAG)"
    _state_prev="$(rel_state_read "$STATE_FILE" PREV_IMAGE_TAG)"

    # 当前镜像：显式 --from-tag > 运行中的 api 容器 > state
    _running_image="$(rel_running_image api)"
    if [ -n "$FROM_TAG" ]; then
        CURRENT_TAG="$FROM_TAG"
    elif [ -n "$_running_image" ]; then
        CURRENT_TAG="$(tag_of_image "$_running_image")"
    else
        CURRENT_TAG="$_state_image"
    fi
    case "$CURRENT_TAG" in
        none) CURRENT_TAG="" ;;
    esac

    # 目标镜像：显式 --to-tag > state PREV_IMAGE_TAG
    if [ -n "$TO_TAG" ]; then
        TARGET_TAG="$TO_TAG"
    else
        TARGET_TAG="$_state_prev"
    fi
    case "$TARGET_TAG" in
        none|'') TARGET_TAG="" ;;
    esac
    if [ -z "$TARGET_TAG" ]; then
        rel_err "无可回滚目标：state 中没有 PREV_IMAGE_TAG（首次发布/状态文件缺失），且未提供 --to-tag"
        print_restore_plan
        exit "$EXIT_REFUSED"
    fi

    if ! rel_image_exists "$(rel_api_image "$TARGET_TAG")" \
        || ! rel_image_exists "$(rel_web_image "$TARGET_TAG")"; then
        rel_err "回滚目标镜像不在本机：$(rel_api_image "$TARGET_TAG") / $(rel_web_image "$TARGET_TAG")"
        rel_err "重建方式（在目标 commit 上）：git checkout <commit> && IMAGE_TAG=${TARGET_TAG} docker compose -f deploy/docker-compose.yml --env-file ${REL_ENV_FILE_RESOLVED} -p ${REL_PROJECT} build api nginx"
        print_restore_plan
        exit "$EXIT_REFUSED"
    fi

    # 入口 URL：显式 > state.BASE_URL > staging 推导；production 必须有 https
    if [ -n "$BASE_URL_ARG" ]; then
        REL_BASE_URL="$BASE_URL_ARG"
    elif [ -n "$(rel_state_read "$STATE_FILE" BASE_URL)" ]; then
        REL_BASE_URL="$(rel_state_read "$STATE_FILE" BASE_URL)"
    elif [ "$REL_ENV" = "staging" ]; then
        _port="$(sed -n 's/^NGINX_HTTP_PORT=//p' "$REL_ENV_FILE_RESOLVED" | tail -n 1 | tr -d '\r')"
        REL_BASE_URL="http://127.0.0.1:${_port:-8080}"
    else
        rel_die "生产回滚必须能确定入口：提供 --base-url https://<域名>（或 state.BASE_URL）" "$EXIT_USAGE"
    fi
    REL_BASE_URL="${REL_BASE_URL%/}"
    case "$REL_BASE_URL" in
        http://*|https://*) : ;;
        *) rel_die "非法 --base-url：${REL_BASE_URL}" "$EXIT_USAGE" ;;
    esac

    if [ "$REL_ENV" = "production" ]; then
        if [ "$CONFIRM_PRODUCTION" != "1" ]; then
            rel_die "生产回滚必须显式 --confirm-production（人工审批后由执行人输入；脚本不自批）" "$EXIT_CONFIRM"
        fi
        case "$REL_BASE_URL" in
            https://*) : ;;
            *) rel_die "生产入口必须是 https://（当前：${REL_BASE_URL}）" "$EXIT_USAGE" ;;
        esac
    fi

    # 目标迁移版本：显式 > state PREV_MIGRATION_VERSION；忽略 none
    if [ -z "$MIGRATION_BEFORE" ]; then
        MIGRATION_BEFORE="$(rel_state_read "$STATE_FILE" PREV_MIGRATION_VERSION)"
    fi
    case "$MIGRATION_BEFORE" in
        none) MIGRATION_BEFORE="" ;;
    esac
    TARGET_MIG="$MIGRATION_BEFORE"

    # commit 归属（用于发布/回滚记录）
    if [ -n "$CURRENT_TAG" ] && [ "$CURRENT_TAG" = "$_state_image" ]; then
        CURRENT_COMMIT="$(rel_state_read "$STATE_FILE" COMMIT)"
        CURRENT_COMMIT="${CURRENT_COMMIT:-unknown}"
    fi
    if [ "$TARGET_TAG" = "$_state_image" ]; then
        TARGET_COMMIT="$(rel_state_read "$STATE_FILE" COMMIT)"
    elif [ "$TARGET_TAG" = "$_state_prev" ]; then
        TARGET_COMMIT="$(rel_state_read "$STATE_FILE" PREV_COMMIT)"
    fi
    TARGET_COMMIT="${TARGET_COMMIT:-unknown}"
}

# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
main() {
    preflight

    TS="$(date -u +%Y%m%dT%H%M%SZ)"
    REL_EVID_DIR="${EVID_DIR:-${REL_REPO}/deploy/evidence}"
    rel_open_log "${REL_EVID_DIR}/rollback-${REL_ENV}-${TS}.log"
    START_EPOCH="$(rel_now_epoch)"
    trap 'rollback_on_exit $?' EXIT

    rel_log "===== 回滚开始（rollback）====="
    rel_log "env=${REL_ENV} auto=${AUTO} project=${REL_PROJECT}"
    rel_log "current=${CURRENT_TAG:-unknown} target=${TARGET_TAG} reason=${REASON:-未提供}"
    rel_log "base_url=${REL_BASE_URL} limit=${ROLLBACK_LIMIT_SECONDS}s"
    rel_log "operator=$(rel_operator) host=$(rel_hostname) auto_rollback=${RELEASE_AUTO_ROLLBACK:-0}"
    if [ "$REASON" = "" ]; then
        rel_warn "未提供 --reason：事故/演练记录会缺少原因，建议补上（docs/74）"
    fi

    # --- 1. 数据库当前版本 ---------------------------------------------------
    CURRENT_STEP="inspect_db"
    if [ -n "$CURRENT_TAG" ] && rel_image_exists "$(rel_api_image "$CURRENT_TAG")"; then
        if CURRENT_DB="$(rel_alembic_current_tag "$CURRENT_TAG")"; then
            CURRENT_DB="${CURRENT_DB:-unknown}"
            rel_log "数据库当前迁移版本=${CURRENT_DB}（探测镜像 ${CURRENT_TAG}）"
        else
            CURRENT_DB="unknown"
            rel_warn "读取数据库迁移版本失败（镜像 ${CURRENT_TAG}）：继续回滚，但不会执行迁移降级"
        fi
    else
        rel_warn "无法确定当前镜像 tag，跳过数据库版本探测（将不做迁移回滚）"
    fi

    # --- 2. 迁移回滚策略 -----------------------------------------------------
    CURRENT_STEP="migration_rollback"
    if [ -z "$TARGET_MIG" ]; then
        DB_ACTION="skipped_no_target"
        DB_AFTER="$CURRENT_DB"
        rel_warn "无目标迁移版本（--migration-before / state PREV_MIGRATION_VERSION 均无）：不执行 alembic downgrade"
        print_restore_plan
    elif [ "$CURRENT_DB" = "unknown" ]; then
        DB_ACTION="skipped_unknown_current"
        DB_AFTER="$CURRENT_DB"
        rel_warn "当前数据库版本未知：不执行 alembic downgrade（避免误降级）"
    elif [ "$CURRENT_DB" = "$TARGET_MIG" ]; then
        DB_ACTION="none"
        DB_AFTER="$CURRENT_DB"
        rel_log "数据库版本已等于回滚目标（${TARGET_MIG}）：无需迁移回滚"
    elif [ "$ALLOW_DOWNGRADE" != "1" ]; then
        DB_ACTION="skipped_not_authorized"
        DB_AFTER="$CURRENT_DB"
        rel_warn "迁移版本不同（db=${CURRENT_DB} -> target=${TARGET_MIG}），但未提供 --allow-downgrade：不执行 downgrade"
        rel_warn "策略：数据库保持新版本（要求本次迁移向前兼容），仅回滚代码/镜像"
        print_restore_plan
    else
        DOWNGRADE_SQL="${REL_EVID_DIR}/rollback-${REL_ENV}-${TS}-downgrade.sql"
        rel_log "迁移回滚预检：alembic downgrade ${CURRENT_DB}:${TARGET_MIG} --sql"
        # 离线模式下降级必须显式给出 from:to 区间（alembic 的硬性要求）
        if ! rel_compose_tag "$CURRENT_TAG" run -T --rm --no-deps -e RUN_MIGRATIONS=false \
            api alembic downgrade "${CURRENT_DB}:${TARGET_MIG}" --sql >"$DOWNGRADE_SQL" 2>&1; then
            rel_err "迁移不可机械回滚（alembic downgrade --sql 失败）；拒绝执行 downgrade（详见 ${DOWNGRADE_SQL}）"
            print_restore_plan
            exit "$EXIT_REFUSED"
        fi
        rel_log "迁移可回滚（SQL 预检通过）：${DOWNGRADE_SQL}"
        rel_log "停止 api 后执行真实 downgrade"
        rel_compose stop api
        if ! rel_compose_tag "$CURRENT_TAG" run -T --rm --no-deps -e RUN_MIGRATIONS=false \
            api alembic downgrade "$TARGET_MIG"; then
            rel_err "alembic downgrade 失败：尝试用当前镜像恢复服务，然后按恢复预案处理"
            rel_compose_tag "$CURRENT_TAG" up -d --no-deps --no-build --force-recreate api || true
            print_restore_plan
            exit "$EXIT_FAIL"
        fi
        DB_ACTION="downgraded"
        DB_AFTER="$(rel_alembic_current_tag "$CURRENT_TAG")"
        DB_AFTER="${DB_AFTER:-unknown}"
        rel_log "downgrade 完成：db=${DB_AFTER}（目标 ${TARGET_MIG}）"
        if [ "$DB_AFTER" != "$TARGET_MIG" ]; then
            rel_warn "downgrade 后版本与目标不一致（db=${DB_AFTER} target=${TARGET_MIG}）：继续切镜像，但需人工核对"
        fi
    fi

    # --- 3. 切换镜像 ---------------------------------------------------------
    CURRENT_STEP="switch_image"
    if [ -n "$CURRENT_TAG" ] && [ "$CURRENT_TAG" = "$TARGET_TAG" ]; then
        rel_warn "当前运行镜像已是目标 tag（${TARGET_TAG}）：跳过切换，仅做验证"
        DB_ACTION="${DB_ACTION}:already_at_target"
    else
        rel_log "切换镜像：exam-api:${TARGET_TAG} / exam-web:${TARGET_TAG}"
        export IMAGE_TAG="$TARGET_TAG"
        rel_compose up -d --no-deps --no-build --force-recreate api nginx
    fi

    # --- 4. 健康检查 ---------------------------------------------------------
    CURRENT_STEP="health"
    rel_health_wait "$REL_HEALTH_RETRIES" "$REL_HEALTH_INTERVAL"

    # --- 5. 冒烟 ------------------------------------------------------------
    CURRENT_STEP="smoke"
    if [ "$SKIP_SMOKE" = "1" ]; then
        VERIFICATION="skipped_by_flag"
        rel_warn "跳过冒烟（--no-smoke）：回滚已执行但未完整验证，退出码为 1，需人工补验"
    elif [ -z "${ADMIN_USER:-}" ] || [ -z "${ADMIN_PASSWORD:-}" ]; then
        VERIFICATION="skipped_no_credentials"
        rel_warn "未提供 ADMIN_USER/ADMIN_PASSWORD：无法冒烟，退出码为 1，需人工补验"
    else
        rel_log "执行冒烟：deploy/scripts/smoke.sh（BASE=${REL_BASE_URL}）"
        rel_smoke
        VERIFICATION="smoke_passed"
    fi

    # --- 6. 计时与结论 -------------------------------------------------------
    CURRENT_STEP="finalize"
    ELAPSED=$(( $(rel_now_epoch) - START_EPOCH ))
    if [ "$ELAPSED" -le "$ROLLBACK_LIMIT_SECONDS" ]; then
        WITHIN_TARGET="yes"
    else
        WITHIN_TARGET="no"
        rel_warn "回滚耗时 ${ELAPSED}s 超过 ${ROLLBACK_LIMIT_SECONDS}s：G4 判据不达标（docs/73 §5），需在复盘登记并优化"
    fi

    if ! rel_state_write "$STATE_FILE" \
        "ENV=${REL_ENV}" \
        "IMAGE_TAG=${TARGET_TAG}" \
        "PREV_IMAGE_TAG=${CURRENT_TAG:-none}" \
        "COMMIT=${TARGET_COMMIT}" \
        "PREV_COMMIT=${CURRENT_COMMIT}" \
        "MIGRATION_VERSION=${DB_AFTER}" \
        "PREV_MIGRATION_VERSION=${CURRENT_DB}" \
        "BASE_URL=${REL_BASE_URL}" \
        "G4_REPORT_ID=$(rel_state_read "$STATE_FILE" G4_REPORT_ID)" \
        "UPDATED_AT=$(rel_now_iso)" \
        "RESULT=rolled_back" \
        "ROLLBACK_SECONDS=${ELAPSED}" \
        "ROLLBACK_REASON=${REASON:-未提供}"; then
        rel_warn "回滚已执行，但状态写入失败：下次回滚基线缺失，请手工修复 ${STATE_FILE}（docs/73 §5）"
        print_summary "SUCCESS_STATE_WRITE_FAILED"
        FINISHED=1
        exit "$EXIT_FAIL"
    fi

    rel_log "状态已写入：${STATE_FILE}"
    print_summary "SUCCESS"
    FINISHED=1

    if [ "$VERIFICATION" != "smoke_passed" ]; then
        rel_warn "回滚完成但未完整验证（verification=${VERIFICATION}）：退出码 1，需人工补跑冒烟"
        exit "$EXIT_FAIL"
    fi
    if [ "$WITHIN_TARGET" = "no" ] && [ "$STRICT_TIME" = "1" ]; then
        rel_err "耗时超限（--strict-time）：退出码 1"
        exit "$EXIT_FAIL"
    fi
    exit "$EXIT_OK"
}

main "$@"
