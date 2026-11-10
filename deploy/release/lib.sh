#!/usr/bin/env bash
# deploy/release/lib.sh — 发布/回滚/演练共用函数（TASK-REL-004 · R6 Release）
#
# 本文件只被 source，不单独执行。调用方必须在 source 之前 `set -euo pipefail`
# （`deploy/release/*.sh` 均已这样做；下面再显式设置一次，保证被单独 source 时行为一致）。
# 本文件不打印、不落盘任何口令。
#
# 约定：
#   - 所有路径以仓库根为基准解析（本文件位于 <repo>/deploy/release/）；
#   - compose 一律带 -f/--env-file/-p，禁止隐式读取当前目录的 .env；
#   - 生产（production）的确认与 G4 门控由调用脚本负责，本文件只提供判据函数；
#   - 证据/状态文件权限 640（umask 027），状态目录 deploy/release/.state/（运行期产物，不入库）。

umask 027
set -euo pipefail

REL_LIB_VERSION="1.0"

# 15 分钟 = G4 回滚判据（docs/61-gate-report.md §2、docs/73 §5）
REL_ROLLBACK_LIMIT_SECONDS="${REL_ROLLBACK_LIMIT_SECONDS:-900}"

rel_now_iso() { date -u +%Y-%m-%dT%H:%M:%SZ; }
rel_now_epoch() { date -u +%s; }

rel_log() { printf '[release] %s %s\n' "$(rel_now_iso)" "$*"; }
rel_warn() { printf '[release] %s WARN %s\n' "$(rel_now_iso)" "$*" >&2; }
rel_err() { printf '[release] %s ERROR %s\n' "$(rel_now_iso)" "$*" >&2; }

# rel_die <message> [exit_code]，默认 2（用法/配置错误）
rel_die() {
    rel_err "$1"
    exit "${2:-2}"
}

rel_repo_root() {
    local _here
    _here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    (cd "${_here}/../.." && pwd)
}

rel_operator() { id -un 2>/dev/null || whoami 2>/dev/null || printf 'unknown'; }
rel_hostname() { hostname 2>/dev/null || printf 'unknown'; }

rel_require_cmds() {
    local _c
    for _c in "$@"; do
        command -v "$_c" >/dev/null 2>&1 || rel_die "缺少命令：${_c}（安装后重试；cron 环境注意 PATH）" 2
    done
}

rel_require_uint() {
    case "${2:-}" in
        ''|*[!0-9]*) rel_die "$1=${2:-} 不是非负整数" 2 ;;
    esac
}

rel_require_posint() {
    rel_require_uint "$1" "${2:-}"
    if [ "${2:-0}" -lt 1 ]; then
        rel_die "$1=${2:-} 必须 >= 1" 2
    fi
}

# 计算 sha256（缺失工具时返回 unavailable，不阻塞）
rel_sha256() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    else
        printf 'unavailable'
    fi
}

# ---------------------------------------------------------------------------
# 环境解析：env / env-file / compose project
# 用法：rel_init_env <staging|production> [env_file] [project]
# 结果写入全局：REL_ENV REL_REPO REL_COMPOSE_FILE REL_ENV_FILE_RESOLVED
#               REL_PROJECT REL_EVID_DIR（默认）
# ---------------------------------------------------------------------------
rel_init_env() {
    REL_ENV="$1"
    local _env_file_arg="${2:-}" _project_arg="${3:-}" _file_project=""

    REL_REPO="$(rel_repo_root)"
    REL_COMPOSE_FILE="${REL_REPO}/deploy/docker-compose.yml"

    case "$REL_ENV" in
        staging)
            REL_DEFAULT_ENV_FILE="deploy/.env.staging"
            REL_DEFAULT_PROJECT="exam-staging"
            ;;
        production)
            REL_DEFAULT_ENV_FILE="deploy/.env.prod"
            REL_DEFAULT_PROJECT="exam-prod"
            ;;
        *)
            rel_die "非法 --env：${REL_ENV}（只接受 staging|production）" 2
            ;;
    esac

    REL_ENV_FILE_RESOLVED="${_env_file_arg:-${REL_DEFAULT_ENV_FILE}}"
    case "$REL_ENV_FILE_RESOLVED" in
        /*) : ;;
        *) REL_ENV_FILE_RESOLVED="${REL_REPO}/${REL_ENV_FILE_RESOLVED}" ;;
    esac
    if [ ! -f "$REL_ENV_FILE_RESOLVED" ]; then
        rel_die "环境文件不存在：${REL_ENV_FILE_RESOLVED}（复制 deploy/.env.example 后填写，见 docs/70 §1）" 2
    fi

    REL_PROJECT="${_project_arg:-${REL_DEFAULT_PROJECT}}"
    _file_project="$(sed -n 's/^COMPOSE_PROJECT_NAME=//p' "$REL_ENV_FILE_RESOLVED" | tail -n 1 | tr -d '\r')"
    if [ -n "$_file_project" ] && [ "$_file_project" != "$REL_PROJECT" ]; then
        rel_die "项目名不一致：${REL_ENV_FILE_RESOLVED} 中 COMPOSE_PROJECT_NAME=${_file_project}，脚本将使用 ${REL_PROJECT}；请用 --project ${_file_project} 或修正环境文件" 2
    fi

    REL_EVID_DIR="${REL_REPO}/deploy/evidence"
}

# ---------------------------------------------------------------------------
# compose 封装：永不隐式读取 .env，项目名/环境文件必须显式
# ---------------------------------------------------------------------------
rel_compose() {
    docker compose -f "$REL_COMPOSE_FILE" --env-file "$REL_ENV_FILE_RESOLVED" -p "$REL_PROJECT" "$@"
}

# 指定镜像 tag 的 compose 调用（用于回滚时对“当前新镜像”执行 alembic 等一次性操作）
rel_compose_tag() {
    local _tag="$1"
    shift
    IMAGE_TAG="$_tag" docker compose -f "$REL_COMPOSE_FILE" \
        --env-file "$REL_ENV_FILE_RESOLVED" -p "$REL_PROJECT" "$@"
}

# 打开证据日志：stdout+stderr 同时落盘并回显
rel_open_log() {
    local _file="$1" _dir
    _dir="$(dirname "$_file")"
    mkdir -p "$_dir" || rel_die "无法创建证据目录：${_dir}" 2
    : >"$_file" || rel_die "无法写入证据文件：${_file}" 2
    REL_EVID_FILE="$_file"
    exec > >(tee -a "$_file") 2>&1
}

# ---------------------------------------------------------------------------
# git / 镜像
# ---------------------------------------------------------------------------
rel_git_commit() {
    git -C "$REL_REPO" rev-parse --short HEAD 2>/dev/null || printf 'unknown'
}

# 镜像名与 deploy/docker-compose.yml 的 `image:` 保持一致
# （api: exam-api:${IMAGE_TAG}；nginx: exam-web:${IMAGE_TAG}）
rel_api_image() { printf 'exam-api:%s' "$1"; }
rel_web_image() { printf 'exam-web:%s' "$1"; }

rel_image_exists() {
    docker image inspect "$1" >/dev/null 2>&1
}

# 当前运行中的 api 容器所用镜像（含 tag），取不到时输出空串
rel_running_image() {
    local _cid=""
    _cid="$(rel_compose ps -q "${1:-api}" 2>/dev/null | head -n 1 | tr -d '\r')"
    if [ -z "$_cid" ]; then
        return 0
    fi
    docker inspect --format '{{.Config.Image}}' "$_cid" 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# 状态文件（回滚基线）：key=value 每行一个，覆盖写且原子替换
# 默认路径 <repo>/deploy/release/.state/<env>.env
# 关键字段：IMAGE_TAG / PREV_IMAGE_TAG / COMMIT / MIGRATION_VERSION
#           PREV_MIGRATION_VERSION / BASE_URL / RESULT / UPDATED_AT
# ---------------------------------------------------------------------------
rel_state_read() {
    local _file="$1" _key="$2"
    if [ ! -f "$_file" ]; then
        return 0
    fi
    sed -n "s/^${_key}=//p" "$_file" | tail -n 1 | tr -d '\r'
}

rel_state_write() {
    local _file="$1"
    shift
    local _tmp _kv
    if ! mkdir -p "$(dirname "$_file")"; then
        rel_err "无法创建状态目录：$(dirname "$_file")"
        return 1
    fi
    _tmp="${_file}.tmp.$$"
    if ! {
        for _kv in "$@"; do
            printf '%s\n' "$_kv"
        done
    } >"$_tmp"; then
        rel_err "无法写入状态文件：$_tmp"
        rm -f "$_tmp" 2>/dev/null || true
        return 1
    fi
    if ! mv -f "$_tmp" "$_file"; then
        rel_err "无法替换状态文件：$_file"
        rm -f "$_tmp" 2>/dev/null || true
        return 1
    fi
    return 0
}

# ---------------------------------------------------------------------------
# 健康检查（带重试）：/nginx-health 与 /api/healthz
# 判据与 docs/70 §4、docs/72 §4 一致：healthz 必须 status=ok 且 db=ok
# ---------------------------------------------------------------------------
rel_healthz_ok() {
    local _body="$1"
    if command -v jq >/dev/null 2>&1; then
        if printf '%s' "$_body" | jq -e '.status == "ok" and .db == "ok"' >/dev/null 2>&1; then
            return 0
        fi
        return 1
    fi
    if ! printf '%s' "$_body" | grep -qE '"status"[[:space:]]*:[[:space:]]*"ok"'; then
        return 1
    fi
    printf '%s' "$_body" | grep -qE '"db"[[:space:]]*:[[:space:]]*"ok"'
}

rel_wait_nginx() {
    local _retries="${1:-${REL_HEALTH_RETRIES:-30}}"
    local _interval="${2:-${REL_HEALTH_INTERVAL:-2}}"
    local _attempt=0 _body=""

    rel_require_posint "HEALTH_RETRIES" "$_retries"
    rel_require_posint "HEALTH_INTERVAL" "$_interval"

    while [ "$_attempt" -lt "$_retries" ]; do
        _attempt=$((_attempt + 1))
        if _body="$(curl -fsS --connect-timeout 3 --max-time 8 "${REL_BASE_URL}/nginx-health" 2>/dev/null)"; then
            _body="$(printf '%s' "$_body" | tr -d '[:space:]')"
            if [ "$_body" = "ok" ]; then
                rel_log "健康检查通过：${REL_BASE_URL}/nginx-health -> ok（第 ${_attempt}/${_retries} 次）"
                return 0
            fi
            rel_warn "nginx-health 响应不是 ok：${_body}"
        fi
        rel_log "等待 nginx-health（${_attempt}/${_retries}）"
        if [ "$_attempt" -lt "$_retries" ]; then
            sleep "$_interval"
        fi
    done
    rel_warn "nginx-health 重试 ${_retries} 次仍失败：${REL_BASE_URL}/nginx-health"
    return 1
}

rel_wait_healthz() {
    local _retries="${1:-${REL_HEALTH_RETRIES:-30}}"
    local _interval="${2:-${REL_HEALTH_INTERVAL:-2}}"
    local _attempt=0 _body=""

    rel_require_posint "HEALTH_RETRIES" "$_retries"
    rel_require_posint "HEALTH_INTERVAL" "$_interval"

    while [ "$_attempt" -lt "$_retries" ]; do
        _attempt=$((_attempt + 1))
        if _body="$(curl -fsS --connect-timeout 3 --max-time 8 "${REL_BASE_URL}/api/healthz" 2>/dev/null)"; then
            if rel_healthz_ok "$_body"; then
                rel_log "健康检查通过：${REL_BASE_URL}/api/healthz -> $(printf '%s' "$_body" | tr -d '\n' | cut -c1-200)"
                return 0
            fi
            rel_warn "/api/healthz 响应未达判据（需 status=ok 且 db=ok）：$(printf '%s' "$_body" | cut -c1-200)"
        fi
        rel_log "等待 /api/healthz（${_attempt}/${_retries}）"
        if [ "$_attempt" -lt "$_retries" ]; then
            sleep "$_interval"
        fi
    done
    rel_warn "/api/healthz 重试 ${_retries} 次仍失败：${REL_BASE_URL}/api/healthz"
    return 1
}

rel_health_wait() {
    if ! rel_wait_nginx "$@"; then
        return 1
    fi
    rel_wait_healthz "$@"
}

# ---------------------------------------------------------------------------
# 冒烟：复用 deploy/scripts/smoke.sh（不改其行为，证据仍写 deploy/evidence/）
# 需要环境变量 ADMIN_USER / ADMIN_PASSWORD（只从环境读取，禁止写入仓库/证据）
# ---------------------------------------------------------------------------
rel_smoke() {
    if [ -z "${ADMIN_USER:-}" ] || [ -z "${ADMIN_PASSWORD:-}" ]; then
        rel_die "冒烟需要 export ADMIN_USER 与 ADMIN_PASSWORD（口令不落盘、不进证据）" 2
    fi
    BASE="$REL_BASE_URL" \
    ENV_FILE="$REL_ENV_FILE_RESOLVED" \
    EVID_DIR="${REL_EVID_DIR:-${REL_REPO}/deploy/evidence}" \
        bash "${REL_REPO}/deploy/scripts/smoke.sh"
}

# 读取最近一次成功备份的 epoch（失败/缺失时输出空串）
rel_last_backup_epoch() {
    rel_compose exec -T backup sh -c 'cat /backups/.last_backup_ok.epoch 2>/dev/null' | tr -d '\r\n '
}

# ---------------------------------------------------------------------------
# 备份确认（发布前强制）：有新备份就复用，否则手工触发 → 新鲜度检查 → 记录最新 dump
# 成功时设置全局 BACKUP_DUMP；失败以 exit 5 结束（尚未改动运行中的服务）
# ---------------------------------------------------------------------------
rel_backup_gate() {
    local _max_age_minutes="${1:-60}"
    local _epoch="" _now="" _age_min="" _dump="" _fresh=0

    rel_require_posint "BACKUP_MAX_AGE_MINUTES" "$_max_age_minutes"

    _epoch="$(rel_last_backup_epoch)"
    case "${_epoch:-}" in
        ''|*[!0-9]*) _epoch="" ;;
    esac
    if [ -n "$_epoch" ]; then
        _now="$(rel_now_epoch)"
        if [ "$_epoch" -gt "$_now" ]; then
            rel_die "备份时间戳在未来（last=${_epoch} > now=${_now}）：检查宿主时钟/时区" 5
        fi
        _age_min=$(( (_now - _epoch) / 60 ))
        if [ "$_age_min" -le "$_max_age_minutes" ]; then
            _fresh=1
            rel_log "复用现有新鲜备份：最近成功 ${_age_min} 分钟前（阈值 ${_max_age_minutes} 分钟），不重复触发"
        fi
    fi

    if [ "$_fresh" != "1" ]; then
        rel_log "手工触发一次备份（backup.sh：pg_dump -Fc + sha256/pg_restore 校验）"
        if ! rel_compose exec -T backup /bin/sh /backup/backup.sh; then
            rel_die "backup.sh 执行失败：禁止继续（见 docs/71 §2.4 与容器日志）" 5
        fi
        _epoch="$(rel_last_backup_epoch)"
        case "${_epoch:-}" in
            ''|*[!0-9]*) rel_die "无法读取 /backups/.last_backup_ok.epoch（备份成功标记缺失；禁止跳过备份直接迁移）" 5 ;;
        esac
        _now="$(rel_now_epoch)"
        _age_min=$(( (_now - _epoch) / 60 ))
        if [ "$_age_min" -gt "$_max_age_minutes" ]; then
            rel_die "备份不新鲜：最近成功 ${_age_min} 分钟前 > 阈值 ${_max_age_minutes} 分钟" 5
        fi
    fi

    rel_log "备份新鲜度检查（freshness_check.sh）"
    if ! rel_compose exec -T backup /bin/sh /backup/freshness_check.sh; then
        rel_die "备份新鲜度检查失败（freshness_check.sh 非 0）：禁止继续" 5
    fi

    _dump="$(rel_compose exec -T backup sh -c 'ls -1t /backups/*.dump 2>/dev/null | head -n 1' | tr -d '\r')"
    BACKUP_DUMP="${_dump:-none}"
    rel_log "备份确认通过：last_ok=${_epoch}（${_age_min} 分钟前）dump=${BACKUP_DUMP}"
}

# ---------------------------------------------------------------------------
# alembic（在 api 容器内执行；RUN_MIGRATIONS=false 避免入口重复迁移）
# 注意：一次性容器使用当前 shell 的 IMAGE_TAG（或 rel_compose_tag 指定的 tag）
# ---------------------------------------------------------------------------
rel_alembic_first_token() {
    # 过滤 alembic 的 INFO 日志行，取首列（revision id）
    awk 'NF > 0 && $1 !~ /^INFO/ && $1 !~ /^\[/ {print $1; exit}'
}

rel_alembic_current() {
    rel_compose run -T --rm --no-deps -e RUN_MIGRATIONS=false api alembic current 2>/dev/null \
        | tr -d '\r' | rel_alembic_first_token
}

rel_alembic_current_tag() {
    local _tag="$1"
    rel_compose_tag "$_tag" run -T --rm --no-deps -e RUN_MIGRATIONS=false api alembic current 2>/dev/null \
        | tr -d '\r' | rel_alembic_first_token
}

rel_alembic_heads() {
    rel_compose run -T --rm --no-deps -e RUN_MIGRATIONS=false api alembic heads 2>/dev/null \
        | tr -d '\r' | awk 'NF > 0 && $1 !~ /^INFO/ && $1 !~ /^\[/ {print $1}'
}

# ---------------------------------------------------------------------------
# G4 门控（仅 production 使用）
# 机器判据：报告中必须出现报告编号，且有一行 `G4: PASS`（见 docs/73 §1）
# 这一步是“脚本不自批”的技术兜底：没有人类签字的 G4 报告就无法通过
# ---------------------------------------------------------------------------
rel_require_g4() {
    local _file="$1" _id="$2"

    if [ -z "$_id" ]; then
        rel_die "生产上线必须提供 --g4-report-id（G4 报告编号，例：G4-20261109-01）" 3
    fi
    if [ -z "$_file" ]; then
        rel_die "生产上线必须提供 --g4-report（G4 报告文件路径）" 3
    fi
    if [ ! -f "$_file" ]; then
        rel_die "G4 报告不存在：${_file}（G4 未通过不得上线生产）" 3
    fi
    if ! grep -Fq -- "$_id" "$_file"; then
        rel_die "G4 报告 ${_file} 中未找到报告编号 ${_id}（编号必须与报告一致）" 3
    fi
    if ! grep -qiE '^[[:space:]]*G4:[[:space:]]*PASS[[:space:]]*$' "$_file"; then
        rel_die "G4 报告缺少机器判据行 'G4: PASS'（在 G4 区块末尾单起一行写入；见 docs/73 §1）" 3
    fi
    rel_log "G4 门控通过：report=${_file} id=${_id} sha256=$(rel_sha256 "$_file")"
}
