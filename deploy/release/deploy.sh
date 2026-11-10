#!/usr/bin/env bash
# deploy/release/deploy.sh — 上线脚本（TASK-REL-004 · G4 通过后使用）
#
# 做一件可审计的事（顺序固定，任一步失败即停）：
#   0. 门控：production 必须 --confirm-production + G4 报告（编号 + `G4: PASS` 行）
#   1. 记录基线：当前 IMAGE_TAG / commit / 迁移版本（来自 deploy/release/.state/<env>.env）
#   2. 构建镜像：exam-api:<tag> / exam-web:<tag>（tag 默认 git short SHA）
#   3. 备份确认：手工备份 → freshness_check → 距现在 <= BACKUP_MAX_AGE_MINUTES
#      （首次部署例外：空库先迁移建 schema，再做“迁移后基线备份”，否则 dump 无对象）
#   4. 迁移 dry-run：alembic upgrade <cur>:head --sql 落盘（参考件，生产须人工审阅 docs/73 §3）
#   5. 迁移：在 api 容器内 `alembic upgrade head`，随后校验 current == heads
#   6. 滚动重启：api → nginx（--no-deps，不动 postgres/backup）
#   7. 健康检查：/api/healthz（status=ok 且 db=ok）与 /nginx-health，带重试
#   8. 冒烟：复用 deploy/scripts/smoke.sh（登录/建题/回读/清理）
#   9. 写状态文件（下次部署的回滚点）与发布记录
#
# 失败处理：任一步失败 → 自动调用 deploy/release/rollback.sh 切回上一 IMAGE_TAG，
#           然后以非 0 退出。首次部署（无回滚基线）不自动回滚，只告警并给出恢复预案。
#
# 用法（仓库根执行）：
#   bash deploy/release/deploy.sh --env staging
#   bash deploy/release/deploy.sh --env production --confirm-production \
#       --g4-report-id G4-20261109-01 --g4-report docs/61-gate-report.md \
#       --base-url https://exam.example.com --approved-by "张三（人类 PO）"
#
# 必填环境变量（冒烟用，禁止写进仓库/证据）：
#   ADMIN_USER、ADMIN_PASSWORD
#
# 退出码：
#   0 成功（部署 + 健康 + 冒烟通过）
#   1 执行失败（已尝试自动回滚；或首次部署失败无回滚基线）
#   2 用法/配置错误（缺参数、环境文件缺失、compose 配置非法）
#   3 拒绝执行（G4 未通过 / G4 报告缺机器判据）
#   4 生产确认缺失（未提供 --confirm-production）
#   5 前置条件不满足（备份缺失/不新鲜等，尚未改动运行中的服务）
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
IMAGE_TAG_ARG=""
IMAGE_TAG_EXPLICIT=0
BASE_URL_ARG=""
G4_REPORT=""
G4_REPORT_ID=""
APPROVED_BY=""
GIT_PULL=0
SKIP_BACKUP=0
SKIP_SMOKE=0
SKIP_MIGRATION_DRY_RUN=0
NO_AUTO_ROLLBACK=0
CONFIRM_PRODUCTION=0

REL_HEALTH_RETRIES="${REL_HEALTH_RETRIES:-30}"
REL_HEALTH_INTERVAL="${REL_HEALTH_INTERVAL:-2}"
BACKUP_MAX_AGE_MINUTES="${BACKUP_MAX_AGE_MINUTES:-60}"

REL_IMAGE_TAG=""
REL_BASE_URL=""
START_EPOCH=0
CURRENT_STEP="init"
CHANGES_STARTED=0
DEPLOY_VERIFIED=0
COMMIT="unknown"
MIG_BEFORE="none"
MIG_AFTER="none"
BACKUP_DUMP="none"
ROLLBACK_TAG=""
ROLLBACK_COMMIT=""
ROLLBACK_MIGRATION=""
STATE_FILE=""

usage() {
    cat <<'EOF'
用法：bash deploy/release/deploy.sh --env <staging|production> [选项]

必填：
  --env staging|production   目标环境（决定 env 文件与 compose 项目名）

生产专用（缺一不可）：
  --confirm-production       人工审批后由执行人显式输入；脚本不自批、不代签
  --g4-report-id <ID>        G4 报告编号（例：G4-20261109-01）
  --g4-report <path>         G4 报告文件；必须含该编号，且有一行 `G4: PASS`
  --base-url <https://...>   生产入口 URL（不猜域名）

常用选项：
  --image-tag <tag>          镜像 tag（默认 git short SHA）
  --approved-by <姓名/角色>  人工审批人（写入发布记录；缺省时告警，需在 docs/73 补签）
  --git-pull                 构建前执行 git pull --ff-only（tag 未显式指定时随新 commit 更新）
  --project <name>           覆盖 compose 项目名（必须与 env 文件 COMPOSE_PROJECT_NAME 一致）
  --env-file <path>          覆盖环境文件（默认 staging=deploy/.env.staging，production=deploy/.env.prod）
  --health-retries <n>       健康检查重试次数（默认 30）
  --health-interval <s>      健康检查间隔秒（默认 2）
  --backup-max-age <min>     备份新鲜度阈值（默认 60 分钟）
  --skip-backup              跳过备份确认（仅 staging；production 禁止）
  --skip-smoke               跳过冒烟（仅 staging；production 禁止）
  --skip-migration-dry-run   跳过迁移 dry-run（仅建议 staging）
  --no-auto-rollback         失败时不自动回滚（仅演练/自动化接管时使用）
  -h, --help                 显示本帮助

退出码：0 成功；1 失败（已尝试回滚）；2 用法/配置；3 G4 拒绝；4 缺生产确认；5 前置不满足。
证据：deploy/evidence/release-<env>-<UTC>.log（含 commit/镜像 tag/迁移版本/回滚点/冒烟输出）
检查单：docs/73-release-checklist.md　回滚：deploy/release/rollback.sh　事故：docs/74
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
        --image-tag)
            need_value "$1" "${2:-}"
            IMAGE_TAG_ARG="$2"
            IMAGE_TAG_EXPLICIT=1
            shift 2
            ;;
        --base-url)
            need_value "$1" "${2:-}"
            BASE_URL_ARG="$2"
            shift 2
            ;;
        --g4-report-id)
            need_value "$1" "${2:-}"
            G4_REPORT_ID="$2"
            shift 2
            ;;
        --g4-report)
            need_value "$1" "${2:-}"
            G4_REPORT="$2"
            shift 2
            ;;
        --approved-by)
            need_value "$1" "${2:-}"
            APPROVED_BY="$2"
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
        --backup-max-age)
            need_value "$1" "${2:-}"
            BACKUP_MAX_AGE_MINUTES="$2"
            shift 2
            ;;
        --git-pull) GIT_PULL=1; shift ;;
        --skip-backup) SKIP_BACKUP=1; shift ;;
        --skip-smoke) SKIP_SMOKE=1; shift ;;
        --skip-migration-dry-run) SKIP_MIGRATION_DRY_RUN=1; shift ;;
        --no-auto-rollback) NO_AUTO_ROLLBACK=1; shift ;;
        --confirm-production) CONFIRM_PRODUCTION=1; shift ;;
        -h|--help) usage; exit "$EXIT_OK" ;;
        *) rel_die "未知参数：$1（--help 查看用法）" "$EXIT_USAGE" ;;
    esac
done

# ---------------------------------------------------------------------------
# 前置检查（不写证据文件；失败信息直接到终端）
# ---------------------------------------------------------------------------
preflight() {
    local _port=""

    if [ -z "$REL_ENV" ]; then
        rel_die "缺少 --env staging|production" "$EXIT_USAGE"
    fi

    rel_require_cmds docker curl git tee
    rel_init_env "$REL_ENV" "$ENV_FILE_ARG" "$PROJECT_ARG"
    rel_require_posint "HEALTH_RETRIES" "$REL_HEALTH_RETRIES"
    rel_require_posint "HEALTH_INTERVAL" "$REL_HEALTH_INTERVAL"
    rel_require_posint "BACKUP_MAX_AGE_MINUTES" "$BACKUP_MAX_AGE_MINUTES"

    if ! rel_compose config -q; then
        rel_die "compose 配置自检失败（见 docs/70 §2）" "$EXIT_USAGE"
    fi

    COMMIT="$(rel_git_commit)"
    if [ -z "$IMAGE_TAG_ARG" ]; then
        if [ "$COMMIT" = "unknown" ]; then
            rel_die "无法从 git 取得 commit；请显式 --image-tag" "$EXIT_USAGE"
        fi
        IMAGE_TAG_ARG="$COMMIT"
    fi
    case "$IMAGE_TAG_ARG" in
        *[!A-Za-z0-9._-]*) rel_die "非法 --image-tag：${IMAGE_TAG_ARG}（只允许字母/数字/._-）" "$EXIT_USAGE" ;;
    esac
    REL_IMAGE_TAG="$IMAGE_TAG_ARG"

    # G4 报告路径：相对路径按仓库根解析
    if [ -n "$G4_REPORT" ]; then
        case "$G4_REPORT" in
            /*) : ;;
            *) G4_REPORT="${REL_REPO}/${G4_REPORT}" ;;
        esac
    fi

    # 入口 URL：staging 可由 NGINX_HTTP_PORT 推导；production 必须显式给出
    if [ -n "$BASE_URL_ARG" ]; then
        REL_BASE_URL="$BASE_URL_ARG"
    elif [ "$REL_ENV" = "staging" ]; then
        _port="$(sed -n 's/^NGINX_HTTP_PORT=//p' "$REL_ENV_FILE_RESOLVED" | tail -n 1 | tr -d '\r')"
        REL_BASE_URL="http://127.0.0.1:${_port:-8080}"
    else
        rel_die "生产上线必须显式 --base-url https://<域名>（不猜域名）" "$EXIT_USAGE"
    fi
    REL_BASE_URL="${REL_BASE_URL%/}"
    case "$REL_BASE_URL" in
        http://*|https://*) : ;;
        *) rel_die "非法 --base-url：${REL_BASE_URL}（必须以 http:// 或 https:// 开头）" "$EXIT_USAGE" ;;
    esac

    if [ "$REL_ENV" = "production" ]; then
        if [ "$CONFIRM_PRODUCTION" != "1" ]; then
            rel_die "生产上线必须显式 --confirm-production（人工审批后由执行人输入；脚本不自批）" "$EXIT_CONFIRM"
        fi
        case "$REL_BASE_URL" in
            https://*) : ;;
            *) rel_die "生产入口必须是 https://（当前：${REL_BASE_URL}；TLS 见 docs/70 §8）" "$EXIT_USAGE" ;;
        esac
        rel_require_g4 "$G4_REPORT" "$G4_REPORT_ID"
        if [ "$SKIP_BACKUP" = "1" ]; then
            rel_die "生产禁止 --skip-backup（禁止跳过备份直接迁移）" "$EXIT_USAGE"
        fi
        if [ "$SKIP_SMOKE" = "1" ]; then
            rel_die "生产禁止 --skip-smoke（冒烟是发布记录的一部分）" "$EXIT_USAGE"
        fi
        if [ -z "$APPROVED_BY" ]; then
            rel_warn "未提供 --approved-by：审批人必须在 docs/73 §9 签字位补录（脚本不自批）"
        fi
    fi

    if [ "$SKIP_SMOKE" != "1" ]; then
        if [ -z "${ADMIN_USER:-}" ] || [ -z "${ADMIN_PASSWORD:-}" ]; then
            rel_die "发布冒烟需要 export ADMIN_USER 与 ADMIN_PASSWORD（口令不落盘、不进证据）" "$EXIT_USAGE"
        fi
    fi
}

# ---------------------------------------------------------------------------
# 发布记录（同时写入证据日志）
# ---------------------------------------------------------------------------
print_summary() {
    local _result="$1" _elapsed="$2"
    rel_log "===== 发布记录（release record）====="
    rel_log "result=${_result} env=${REL_ENV} elapsed_seconds=${_elapsed}"
    rel_log "commit=${COMMIT} image_tag=${REL_IMAGE_TAG}"
    rel_log "rollback_point commit=${ROLLBACK_COMMIT:-unknown} image_tag=${ROLLBACK_TAG:-none} migration=${ROLLBACK_MIGRATION:-unknown}"
    rel_log "migration_before=${MIG_BEFORE} migration_after=${MIG_AFTER}"
    rel_log "backup_dump=${BACKUP_DUMP:-none}"
    rel_log "base_url=${REL_BASE_URL} project=${REL_PROJECT} env_file=${REL_ENV_FILE_RESOLVED}"
    rel_log "g4_report_id=${G4_REPORT_ID:-none} approved_by=${APPROVED_BY:-未提供}"
    rel_log "operator=$(rel_operator) host=$(rel_hostname)"
    rel_log "evidence=${REL_EVID_FILE:-unknown}"
    rel_log "====================================="
}

# ---------------------------------------------------------------------------
# 失败处理（EXIT trap）：记录现场 → 必要时自动回滚到上一 IMAGE_TAG → 非 0 退出
# 用 EXIT trap 而不是 ERR trap：bash 的 ERR trap 默认不继承进函数（除非 set -E），
# 而本脚本的失败几乎都发生在函数内部；EXIT trap 一定能拿到退出码。
# ---------------------------------------------------------------------------
deploy_on_exit() {
    local _rc="$1" _elapsed _rb_rc=0
    trap - EXIT
    set +e

    if [ "$_rc" -eq 0 ]; then
        exit 0
    fi

    _elapsed=$(( $(rel_now_epoch) - START_EPOCH ))
    rel_warn "部署失败：rc=${_rc} step=${CURRENT_STEP} elapsed=${_elapsed}s"

    # 已上线且冒烟通过，只是收尾（状态/证据）失败：不要回滚健康服务
    if [ "$_rc" -eq 1 ] && [ "${CHANGES_STARTED:-0}" != "1" ] && [ "${DEPLOY_VERIFIED:-0}" = "1" ]; then
        rel_err "服务已上线且冒烟通过，但收尾（状态/证据写入）失败：**不要回滚**，请人工修复记录后按 docs/73 §8 观察"
        print_summary "DEGRADED(rc=1,deployed)" "$_elapsed"
        exit "$EXIT_FAIL"
    fi

    # 2/3/4/5 = 用法/门控/确认/前置失败：线上未被改动，不回滚
    # 1 且 CHANGES_STARTED=0 = 构建/启动依赖阶段失败：同样不回滚
    if [ "$_rc" -ne 1 ] || [ "${CHANGES_STARTED:-0}" != "1" ]; then
        print_summary "FAILED(rc=${_rc})" "$_elapsed"
        exit "$_rc"
    fi

    if [ "$NO_AUTO_ROLLBACK" = "1" ]; then
        rel_warn "--no-auto-rollback：跳过自动回滚（演练/人工接管）"
        print_summary "FAILED" "$_elapsed"
        exit "$EXIT_FAIL"
    fi

    if [ -z "$ROLLBACK_TAG" ]; then
        rel_err "无回滚基线（首次部署或状态文件缺失）：不会自动回滚；服务可能处于部分更新状态"
        rel_err "处置：按 docs/74 止损（先判断镜像回滚/恢复备份），确认后手工执行 rollback.sh 或 docs/71 §3.2"
        print_summary "FAILED_NO_ROLLBACK" "$_elapsed"
        exit "$EXIT_FAIL"
    fi

    local -a rb_args=(--env "$REL_ENV" --auto --to-tag "$ROLLBACK_TAG" \
        --from-tag "$REL_IMAGE_TAG" \
        --reason "deploy failed: step=${CURRENT_STEP} rc=${_rc}")
    if [ -n "$ROLLBACK_MIGRATION" ]; then
        rb_args+=(--migration-before "$ROLLBACK_MIGRATION")
    fi
    if [ -n "$ENV_FILE_ARG" ]; then
        rb_args+=(--env-file "$ENV_FILE_ARG")
    fi
    if [ -n "$PROJECT_ARG" ]; then
        rb_args+=(--project "$PROJECT_ARG")
    fi
    if [ -n "$BASE_URL_ARG" ]; then
        rb_args+=(--base-url "$BASE_URL_ARG")
    fi
    if [ "$REL_ENV" = "production" ]; then
        rb_args+=(--confirm-production)
    fi

    rel_warn "自动回滚：IMAGE_TAG ${REL_IMAGE_TAG} -> ${ROLLBACK_TAG}（保持数据库版本策略见 rollback.sh 日志）"
    RELEASE_AUTO_ROLLBACK=1 bash "${SCRIPT_DIR}/rollback.sh" "${rb_args[@]}"
    _rb_rc=$?

    case "$_rb_rc" in
        0) rel_warn "自动回滚完成且验证通过：已恢复 ${ROLLBACK_TAG}" ;;
        3) rel_err "自动回滚被拒绝（无目标镜像/迁移不可回滚）：立即人工介入，恢复预案 docs/71 §3.2、分级 docs/74" ;;
        *) rel_err "自动回滚未通过验证（exit=${_rb_rc}）：立即人工介入（docs/74）" ;;
    esac
    print_summary "FAILED_ROLLED_BACK(rc=${_rb_rc})" "$_elapsed"
    exit "$EXIT_FAIL"
}

# ---------------------------------------------------------------------------
# 基础设施就绪 + 迁移（首次部署与升级共用）
# ---------------------------------------------------------------------------
wait_postgres_ready() {
    local _retries=30 _i=0
    while [ "$_i" -lt "$_retries" ]; do
        _i=$((_i + 1))
        if rel_compose exec -T postgres sh -c 'pg_isready -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null 2>&1; then
            rel_log "postgres 就绪（第 ${_i}/${_retries} 次）"
            return 0
        fi
        rel_log "等待 postgres（${_i}/${_retries}）"
        sleep 2
    done
    rel_warn "postgres 在 ${_retries} 次重试后仍未就绪"
    return 1
}

run_migrations() {
    # 4. dry-run（参考件；失败只告警）
    CURRENT_STEP="migration_dry_run"
    MIG_BEFORE="$(rel_alembic_current)"
    MIG_BEFORE="${MIG_BEFORE:-none}"
    rel_log "alembic current（升级前）=${MIG_BEFORE}"
    MIG_DRY_SQL="${REL_EVID_DIR}/release-${REL_ENV}-${TS}-migration.sql"
    if [ "$SKIP_MIGRATION_DRY_RUN" = "1" ]; then
        rel_warn "跳过迁移 dry-run（--skip-migration-dry-run）；生产上线前必须人工审阅 docs/73 §3"
    else
        if [ "$MIG_BEFORE" = "none" ]; then
            MIG_DRY_RANGE="head"
        else
            MIG_DRY_RANGE="${MIG_BEFORE}:head"
        fi
        if rel_compose run -T --rm --no-deps -e RUN_MIGRATIONS=false api alembic upgrade "$MIG_DRY_RANGE" --sql \
            >"$MIG_DRY_SQL" 2>&1; then
            rel_log "迁移 dry-run SQL 已生成：${MIG_DRY_SQL}（range=${MIG_DRY_RANGE}，$(wc -l <"$MIG_DRY_SQL") 行）"
        else
            rel_warn "迁移 dry-run 未能生成 SQL（离线模式限制/数据迁移）：继续升级，但生产前必须人工审阅 docs/73 §3；详见 ${MIG_DRY_SQL}"
        fi
    fi

    # 5. 升级 + 校验 current == heads
    CURRENT_STEP="migrate"
    rel_log "执行 alembic upgrade head（升级前 ${MIG_BEFORE}）"
    rel_compose run -T --rm --no-deps -e RUN_MIGRATIONS=false api alembic upgrade head
    MIG_AFTER="$(rel_alembic_current)"
    MIG_AFTER="${MIG_AFTER:-none}"
    MIG_HEAD="$(rel_alembic_heads | head -n 1)"
    rel_log "alembic current（升级后）=${MIG_AFTER} heads=${MIG_HEAD:-unknown}"
    if [ -z "$MIG_HEAD" ]; then
        rel_warn "无法解析 alembic heads：请人工核对数据库迁移状态"
    elif [ "$MIG_AFTER" != "$MIG_HEAD" ]; then
        rel_warn "迁移未到 head（current=${MIG_AFTER} head=${MIG_HEAD}）"
        false # 触发非 0 退出 → EXIT trap 自动回滚
    fi
}

# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
main() {
    preflight

    TS="$(date -u +%Y%m%dT%H%M%SZ)"
    REL_EVID_DIR="${EVID_DIR:-${REL_REPO}/deploy/evidence}"
    rel_open_log "${REL_EVID_DIR}/release-${REL_ENV}-${TS}.log"

    START_EPOCH="$(rel_now_epoch)"
    STATE_FILE="${REL_REPO}/deploy/release/.state/${REL_ENV}.env"
    trap 'deploy_on_exit $?' EXIT

    ROLLBACK_TAG="$(rel_state_read "$STATE_FILE" IMAGE_TAG)"
    ROLLBACK_COMMIT="$(rel_state_read "$STATE_FILE" COMMIT)"
    ROLLBACK_MIGRATION="$(rel_state_read "$STATE_FILE" MIGRATION_VERSION)"
    case "$ROLLBACK_TAG" in
        none) ROLLBACK_TAG="" ;;
    esac
    case "$ROLLBACK_MIGRATION" in
        none) ROLLBACK_MIGRATION="" ;;
    esac

    rel_log "===== 上线开始（deploy）====="
    rel_log "env=${REL_ENV} project=${REL_PROJECT} env_file=${REL_ENV_FILE_RESOLVED}"
    rel_log "base_url=${REL_BASE_URL} image_tag=${REL_IMAGE_TAG} commit=${COMMIT}"
    rel_log "operator=$(rel_operator) host=$(rel_hostname)"
    rel_log "g4_report_id=${G4_REPORT_ID:-none} approved_by=${APPROVED_BY:-未提供}"
    rel_log "回滚基线：IMAGE_TAG=${ROLLBACK_TAG:-none} commit=${ROLLBACK_COMMIT:-unknown} migration=${ROLLBACK_MIGRATION:-unknown}"

    if [ -n "$(git -C "$REL_REPO" status --porcelain 2>/dev/null | head -n 1)" ]; then
        rel_warn "工作区存在未提交改动：image_tag=${REL_IMAGE_TAG} 不能完全代表该 commit，发布记录须注明"
    fi

    # --- 1. 可选：拉取代码 ---------------------------------------------------
    if [ "$GIT_PULL" = "1" ]; then
        CURRENT_STEP="git_pull"
        rel_log "git pull --ff-only"
        git -C "$REL_REPO" pull --ff-only
        COMMIT="$(rel_git_commit)"
        if [ "$IMAGE_TAG_EXPLICIT" = "0" ]; then
            REL_IMAGE_TAG="$COMMIT"
        fi
        rel_log "更新后 commit=${COMMIT} image_tag=${REL_IMAGE_TAG}"
    fi
    export IMAGE_TAG="$REL_IMAGE_TAG"

    # --- 2. 构建镜像 ---------------------------------------------------------
    CURRENT_STEP="build"
    rel_log "构建镜像：exam-api:${REL_IMAGE_TAG} / exam-web:${REL_IMAGE_TAG}"
    rel_compose build api nginx
    rel_log "镜像构建完成"

    # --- 3. 判断首次部署（空库没有可保护的数据）-----------------------------
    FIRST_DEPLOY=0
    if [ ! -f "$STATE_FILE" ] && [ -z "$(rel_compose ps -q api 2>/dev/null | tr -d '\r')" ]; then
        FIRST_DEPLOY=1
        rel_warn "首次部署（无 state、无运行中的 api）：空库没有需要保护的数据"
    fi

    # --- 4. 启动依赖服务（--no-deps，不重建 api/nginx）----------------------
    if [ "$FIRST_DEPLOY" = "1" ]; then
        CURRENT_STEP="bootstrap_postgres"
        rel_log "首次部署：先启动 postgres（迁移建 schema 后再做基线备份）"
        rel_compose up -d --no-deps --no-recreate postgres
        wait_postgres_ready
    else
        CURRENT_STEP="ensure_infra"
        rel_log "确保 postgres/backup 在运行（不重建 api/nginx；不重建 postgres，避免误重启数据库）"
        rel_compose up -d --no-deps --no-recreate postgres backup
        wait_postgres_ready
    fi

    # --- 5. 备份确认（升级场景：必须在迁移之前）-----------------------------
    if [ "$FIRST_DEPLOY" != "1" ]; then
        CURRENT_STEP="backup"
        if [ "$SKIP_BACKUP" = "1" ]; then
            rel_warn "跳过备份确认（--skip-backup）：仅 staging 允许，本次发布不得作为 G4/G5 证据"
        else
            # 备份失败发生在“改动运行中服务”之前：以 5 退出，不做无意义回滚
            if ! rel_backup_gate "$BACKUP_MAX_AGE_MINUTES"; then
                rel_die "备份确认失败：禁止跳过备份直接迁移（见 docs/71 §2.4）" "$EXIT_PREFLIGHT"
            fi
        fi
    fi

    # --- 6. 迁移（在 api 容器内；含 dry-run 与 head 校验）--------------------
    # 从这里开始会改动运行中的服务（数据库/容器），失败才触发自动回滚
    CHANGES_STARTED=1
    run_migrations

    # --- 7. 首次部署：迁移后做基线备份（此后发布才有“迁移前备份”）------------
    if [ "$FIRST_DEPLOY" = "1" ]; then
        CURRENT_STEP="backup"
        rel_compose up -d --no-deps --no-recreate backup
        if [ "$SKIP_BACKUP" = "1" ]; then
            rel_warn "跳过首次部署的基线备份（--skip-backup，仅 staging）"
        else
            rel_log "首次部署：迁移后基线备份（迁移前空库无数据可备）"
            # 等 backup 服务首轮备份写成功标记，避免与服务内 loop 并发写同一卷
            _wait_i=0
            while [ "$_wait_i" -lt 45 ]; do
                _wait_i=$((_wait_i + 1))
                if rel_compose exec -T backup sh -c 'test -s /backups/.last_backup_ok' >/dev/null 2>&1; then
                    rel_log "backup 首轮备份已就绪（第 ${_wait_i} 次探测）"
                    break
                fi
                sleep 2
            done
            if ! rel_backup_gate "$BACKUP_MAX_AGE_MINUTES"; then
                rel_die "首次部署基线备份失败：见 docs/71 §2.4" "$EXIT_PREFLIGHT"
            fi
        fi
    fi

    # --- 8. 滚动重启（不动 postgres/backup）---------------------------------
    CURRENT_STEP="restart"
    rel_log "滚动重启：api → nginx（--no-deps --no-build --force-recreate）"
    rel_compose up -d --no-deps --no-build --force-recreate api nginx
    if ! rel_compose ps; then
        rel_warn "compose ps 输出失败（不影响健康检查，继续）"
    fi

    # --- 9. 健康检查（带重试）-----------------------------------------------
    CURRENT_STEP="health"
    rel_health_wait "$REL_HEALTH_RETRIES" "$REL_HEALTH_INTERVAL"

    # --- 10. 冒烟 -----------------------------------------------------------
    CURRENT_STEP="smoke"
    if [ "$SKIP_SMOKE" = "1" ]; then
        rel_warn "跳过冒烟（--skip-smoke）：本次发布不得作为 G4/G5 证据"
    else
        rel_log "执行冒烟：deploy/scripts/smoke.sh（BASE=${REL_BASE_URL}）"
        rel_smoke
    fi

    # 部署与冒烟已通过：后续只做记录，收尾失败不应回滚健康的服务
    DEPLOY_VERIFIED=1
    CHANGES_STARTED=0

    # --- 11. 写状态（下次部署/回滚的基线）----------------------------------
    CURRENT_STEP="finalize"
    rel_compose images >"${REL_EVID_DIR}/release-${REL_ENV}-${TS}-images.txt" 2>&1 || true

    rel_state_write "$STATE_FILE" \
        "ENV=${REL_ENV}" \
        "IMAGE_TAG=${REL_IMAGE_TAG}" \
        "PREV_IMAGE_TAG=${ROLLBACK_TAG:-none}" \
        "COMMIT=${COMMIT}" \
        "PREV_COMMIT=${ROLLBACK_COMMIT:-unknown}" \
        "MIGRATION_VERSION=${MIG_AFTER}" \
        "PREV_MIGRATION_VERSION=${MIG_BEFORE}" \
        "BASE_URL=${REL_BASE_URL}" \
        "G4_REPORT_ID=${G4_REPORT_ID:-none}" \
        "APPROVED_BY=${APPROVED_BY:-未提供（见 docs/73 §9）}" \
        "UPDATED_AT=$(rel_now_iso)" \
        "RESULT=deployed" \
        || exit "$EXIT_FAIL"

    rel_log "状态已写入：${STATE_FILE}"
    print_summary "SUCCESS" "$(( $(rel_now_epoch) - START_EPOCH ))"
    rel_log "发布完成。下一步：按 docs/73 §8 执行 24h 观察（healthz/错误率/慢请求/备份新鲜度）"
    exit "$EXIT_OK"
}

main "$@"
