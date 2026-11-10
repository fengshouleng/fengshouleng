#!/bin/sh
# deploy/backup/lib.sh — backup / freshness_check / restore_drill 共用的函数（被 source，不单独执行）
#
# 兼容性：镜像 postgres:16.4-alpine 无 bash，只有 busybox ash；本文件只用 POSIX sh
# 与 busybox 自带工具（date/grep/sed/wc/find/sha256sum）。宿主脚本（deploy/cron/*）另用 bash。
#
# 统一告警接口（供 TASK-REL-003 接入，不改脚本即可换通道）：
#   设置 BACKUP_ALERT_CMD 后，每次告警会执行 `sh -c "$BACKUP_ALERT_CMD"`，并在环境里提供：
#     BACKUP_ALERT_REASON   backup_failed | stale_backup | remote_copy_failed | drill_failed
#     BACKUP_ALERT_MESSAGE  人类可读摘要（不含密码）
#     BACKUP_ALERT_FILE     告警落盘文件路径（容器内，默认 ${BACKUP_DIR}/.alert_<reason>）
#     BACKUP_DB_NAME        库名
#   告警通道本身失败只打 WARN，不改变备份/演练的结论（否则会掩盖真实原因）。
#
# 约定：调用方在 source 本文件前完成 set -eu（以及可用的 pipefail），并先设置 BACKUP_DIR。

backup_now_iso() {
    date -u +%Y-%m-%dT%H:%M:%SZ
}

backup_now_epoch() {
    date -u +%s
}

# 生成日志文件路径（按 UTC 日期滚动）；目录不可写时返回空串，调用方降级为只输出 stderr。
# 用法：BACKUP_LOG_FILE="$(backup_log_path "$BACKUP_LOG_DIR")"
backup_log_path() {
    _dir="${1:-}"
    [ -n "$_dir" ] || { printf ''; return 0; }
    if mkdir -p "$_dir" 2>/dev/null && [ -w "$_dir" ]; then
        printf '%s' "${_dir}/backup-$(date -u +%Y%m%d).log"
    else
        printf ''
    fi
}

# 日志：stdout（容器日志） + 可选日志文件（宿主/挂载卷留档）
blog() {
    printf '[backup] %s %s\n' "$(backup_now_iso)" "$*"
    if [ -n "${BACKUP_LOG_FILE:-}" ]; then
        printf '[%s] %s\n' "$(backup_now_iso)" "$*" >>"$BACKUP_LOG_FILE" 2>/dev/null || true
    fi
}

# 告警：写告警文件 + 调用 BACKUP_ALERT_CMD（若配置）
# 用法：backup_alert <reason> <message> [alert-file]
backup_alert() {
    _reason="$1"
    _message="$2"
    _file="${3:-${BACKUP_DIR:-/backups}/.alert_${_reason}}"

    blog "ALERT ${_reason}: ${_message}（alert_file=${_file}）"

    {
        printf 'reason=%s\n' "$_reason"
        printf 'time=%s\n' "$(backup_now_iso)"
        printf 'host=%s\n' "$(hostname 2>/dev/null || echo unknown)"
        printf 'database=%s\n' "${PGDATABASE:-unknown}"
        printf 'message=%s\n' "$_message"
    } >"$_file" 2>/dev/null || blog "WARN 无法写入告警文件：$_file"

    if [ -n "${BACKUP_ALERT_CMD:-}" ]; then
        BACKUP_ALERT_REASON="$_reason" \
        BACKUP_ALERT_MESSAGE="$_message" \
        BACKUP_ALERT_FILE="$_file" \
        BACKUP_DB_NAME="${PGDATABASE:-unknown}" \
        sh -c "$BACKUP_ALERT_CMD" >/dev/null 2>&1 \
            || blog "WARN 告警通道执行失败（BACKUP_ALERT_CMD），不影响本次结论"
    fi
}

# 校验归档：pg_restore --list 必须成功且 TOC 中至少有一个条目。
# 成功时把条目数写入全局 VERIFY_TOC_ENTRIES，返回 0；失败打印原因并返回 1。
verify_dump_list() {
    _file="$1"
    VERIFY_TOC_ENTRIES=0

    if [ ! -f "$_file" ]; then
        printf '备份文件不存在：%s\n' "$_file" >&2
        return 1
    fi
    if [ ! -s "$_file" ]; then
        printf '备份文件为空：%s\n' "$_file" >&2
        return 1
    fi

    _tmp="${TMPDIR:-/tmp}/.toc.$$"
    if ! pg_restore --list "$_file" >"$_tmp" 2>"${_tmp}.err"; then
        printf 'pg_restore --list 失败（归档损坏或非 custom 格式）：%s\n' "$_file" >&2
        sed -n '1,3p' "${_tmp}.err" >&2 2>/dev/null || true
        rm -f "$_tmp" "${_tmp}.err"
        return 1
    fi
    if grep -qi 'error' "${_tmp}.err" 2>/dev/null; then
        printf 'pg_restore --list 输出中包含 error：%s\n' "$_file" >&2
        sed -n '1,3p' "${_tmp}.err" >&2 2>/dev/null || true
        rm -f "$_tmp" "${_tmp}.err"
        return 1
    fi

    # TOC 有效行形如：`215; 1259 16384 TABLE public users exam`
    _entries="$(grep -cE '^[0-9]+;' "$_tmp" 2>/dev/null || true)"
    rm -f "$_tmp" "${_tmp}.err"
    case "${_entries:-}" in
        ''|*[!0-9]*) _entries=0 ;;
    esac
    if [ "$_entries" -lt 1 ]; then
        printf '归档内没有任何对象（空库或导出失败）：%s\n' "$_file" >&2
        return 1
    fi

    VERIFY_TOC_ENTRIES="$_entries"
    return 0
}

# 校验 sha256：要求同名 .sha256 存在且匹配（缺失即失败，不允许“静默跳过”）
# 用法：verify_dump_sha <dump>
verify_dump_sha() {
    _dump="$1"
    _sha="${_dump}.sha256"

    if [ ! -f "$_sha" ]; then
        printf '缺少校验文件：%s（备份时未能生成，禁止用于恢复/演练）\n' "$_sha" >&2
        return 1
    fi
    if ! command -v sha256sum >/dev/null 2>&1; then
        printf '当前环境没有 sha256sum，无法校验：%s\n' "$_dump" >&2
        return 1
    fi
    if ! ( cd "$(dirname "$_dump")" && sha256sum -c "$(basename "$_sha")" >/dev/null 2>&1 ); then
        printf 'sha256 不匹配，备份文件可能损坏或被篡改：%s\n' "$_dump" >&2
        return 1
    fi
    return 0
}

# 文件体积（字节）；getconf 缺失时退回 wc -c
backup_file_size() {
    _f="$1"
    if command -v stat >/dev/null 2>&1; then
        stat -c %s "$_f" 2>/dev/null || wc -c <"$_f" | tr -d ' '
    else
        wc -c <"$_f" | tr -d ' '
    fi
}
