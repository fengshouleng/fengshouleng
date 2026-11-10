#!/bin/sh
# deploy/backup/backup.sh — 单次逻辑备份（TASK-REL-002：完整性校验 + 7 日备/4 周备 + 可选异地副本 + 告警）
#
# 一次执行依次完成：
#   1. pg_dump -Fc 写 <db>_<UTC时间戳>.dump.partial，成功后原子改名（半截文件永远不会被当有效备份）
#   2. 完整性校验（全部通过才保留产物）：
#        a) 体积下限（拦截 0 字节/写盘失败）
#        b) pg_restore --list：归档可解析且 TOC 非空
#        c) sha256sum 生成 .sha256 并**立即回验**（恢复/演练前还会再校验一次）
#   3. 写成功标记 .last_backup_ok（+ .last_backup_ok.epoch 供新鲜度检查做整数运算）
#   4. 保留策略：日备 7 份 + 周备 4 份
#        - 周备**不做完整复制**：周日（BACKUP_WEEKLY_WEEKDAY=7）成功备份后写标记文件
#          ${BACKUP_DIR}/.weekly/<base>；按天清理时跳过被标记文件。
#        - 标记数超过 4 时，删除最旧的标记及其 .dump/.sha256（保证周备滚存 4 周）。
#   5. 异地副本（可选开关，二选一）：
#        - BACKUP_REMOTE_DIR：目录副本（要求挂载到不同物理盘/NAS，脚本内做 sha256 回验）
#        - BACKUP_REMOTE_CMD：对象存储命令模板（占位接口，见 docs/71 §2.5）
#        两者都为空 → 只打 NOTICE，不影响本地备份结论。
#   6. 任何失败：日志写清晰原因 + 非 0 退出 + 写告警文件（.alert_backup_failed / .alert_remote_copy_failed）
#
# 退出码：0 成功；1 本地备份或其校验失败；2 参数/环境错误；3 本地成功但异地副本失败
#
# 环境变量（全部可配，默认值即 ADR-008 口径）：
#   PGHOST PGPORT PGDATABASE PGUSER PGPASSWORD   连接（必填；PGPASSWORD 不落盘不打印）
#   BACKUP_DIR=/backups                          产物目录
#   BACKUP_RETENTION_DAYS=7                      日备保留天数（0=不按天清理）
#   BACKUP_RETENTION_WEEKS=4                     周备保留份数（0=不再打周标记）
#   BACKUP_WEEKLY_WEEKDAY=7                      ISO 周几打周标记（1=周一 … 7=周日）
#   BACKUP_MIN_BYTES=1024                        文件体积下限
#   BACKUP_REMOTE_DIR=                           异地目录（未挂载则保持空）
#   BACKUP_REMOTE_CMD=                           对象存储命令模板（见 docs/71 §2.5）
#   BACKUP_ALERT_CMD=                            告警命令（REL-003 接口，见 lib.sh）
#   BACKUP_LOG_DIR=${BACKUP_DIR}/logs            备份日志目录（按 UTC 日期滚动）
#
# 手工执行（宿主，容器内会读到挂载卷）：
#   docker compose -f deploy/docker-compose.yml --env-file deploy/.env.staging -p exam-staging \
#     exec -T backup /bin/sh /backup/backup.sh
set -eu
# 等价于 set -euo pipefail；对没有 pipefail 的 POSIX shell（dash）降级，避免脚本无法启动。
if (set -o pipefail) 2>/dev/null; then set -o pipefail; fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=deploy/backup/lib.sh
. "${BACKUP_LIB:-${SCRIPT_DIR}/lib.sh}"

: "${PGHOST:?PGHOST 未设置}"
: "${PGDATABASE:?PGDATABASE 未设置}"
: "${PGUSER:?PGUSER 未设置}"
: "${PGPASSWORD:?PGPASSWORD 未设置（不要写进脚本或日志；由 compose 注入）}"

PGPORT="${PGPORT:-5432}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"
BACKUP_RETENTION_WEEKS="${BACKUP_RETENTION_WEEKS:-4}"
BACKUP_WEEKLY_WEEKDAY="${BACKUP_WEEKLY_WEEKDAY:-7}"
BACKUP_MIN_BYTES="${BACKUP_MIN_BYTES:-1024}"
BACKUP_LOG_DIR="${BACKUP_LOG_DIR:-${BACKUP_DIR}/logs}"
BACKUP_WEEKLY_DIR="${BACKUP_WEEKLY_DIR:-${BACKUP_DIR}/.weekly}"
BACKUP_REMOTE_DIR="${BACKUP_REMOTE_DIR:-}"
BACKUP_REMOTE_CMD="${BACKUP_REMOTE_CMD:-}"

is_uint() {
    case "${1:-}" in
        ''|*[!0-9]*) return 1 ;;
    esac
    # 拒绝前导 0：$((08)) 会被 POSIX shell 当八进制而报错
    case "$1" in
        0) return 0 ;;
        0*) return 1 ;;
    esac
    return 0
}

# 参数校验放在最前面：配置错误必须立刻失败，不能等到备份完才发现保留策略是空值
is_uint "$BACKUP_RETENTION_DAYS" || {
    printf '[backup] ERROR BACKUP_RETENTION_DAYS=%s 不是非负整数\n' "$BACKUP_RETENTION_DAYS" >&2
    exit 2
}
is_uint "$BACKUP_RETENTION_WEEKS" || {
    printf '[backup] ERROR BACKUP_RETENTION_WEEKS=%s 不是非负整数\n' "$BACKUP_RETENTION_WEEKS" >&2
    exit 2
}
case "$BACKUP_WEEKLY_WEEKDAY" in
    1|2|3|4|5|6|7) : ;;
    *) printf '[backup] ERROR BACKUP_WEEKLY_WEEKDAY=%s 必须是 1..7（ISO 周几）\n' \
        "$BACKUP_WEEKLY_WEEKDAY" >&2
       exit 2 ;;
esac

mkdir -p "$BACKUP_DIR" 2>/dev/null || {
    printf '[backup] ERROR 无法创建备份目录 %s（卷未挂载或只读）\n' "$BACKUP_DIR" >&2
    exit 1
}
BACKUP_LOG_FILE="$(backup_log_path "$BACKUP_LOG_DIR")"
[ -n "$BACKUP_LOG_FILE" ] || blog "WARN 日志目录不可写（${BACKUP_LOG_DIR}），仅输出到容器日志"

if ! command -v sha256sum >/dev/null 2>&1; then
    blog "ERROR 镜像内缺少 sha256sum：无法生成/回验校验文件（校验为强制项，禁止降级跳过）"
    exit 2
fi

blog "===== 备份开始 db=${PGDATABASE} host=${PGHOST}:${PGPORT} 目录=${BACKUP_DIR} ====="

# 清理上次异常中断遗留的 .partial（超过 1 天），避免占满卷
find "$BACKUP_DIR" -maxdepth 1 -type f -name '*.partial' -mtime +0 -exec rm -f {} \; 2>/dev/null || true

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
base="${PGDATABASE}_${timestamp}"
target="${BACKUP_DIR}/${base}.dump"
partial="${target}.partial"

cleanup_partial() {
    [ -f "$partial" ] && rm -f "$partial" 2>/dev/null || true
}
trap 'cleanup_partial' EXIT
trap 'cleanup_partial; exit 130' INT TERM

blog "导出 -> ${target}"

set +e
pg_dump \
    --format=custom \
    --no-owner \
    --no-privileges \
    --host="$PGHOST" \
    --port="$PGPORT" \
    --username="$PGUSER" \
    --dbname="$PGDATABASE" \
    --file="$partial"
dump_rc=$?
set -e

if [ "$dump_rc" -ne 0 ]; then
    rm -f "$partial"
    backup_alert backup_failed "pg_dump 失败（exit=${dump_rc}），db=${PGDATABASE}"
    blog "ERROR pg_dump 失败（exit=${dump_rc}），已删除不完整文件"
    exit 1
fi

if [ ! -s "$partial" ]; then
    rm -f "$partial"
    backup_alert backup_failed "pg_dump 产物为空（db=${PGDATABASE}）"
    blog "ERROR pg_dump 产物为空：${partial}"
    exit 1
fi

size_bytes="$(backup_file_size "$partial")"
case "${size_bytes:-}" in
    ''|*[!0-9]*) size_bytes=0 ;;
esac
if [ "$size_bytes" -lt "$BACKUP_MIN_BYTES" ]; then
    rm -f "$partial"
    backup_alert backup_failed "备份文件过小（${size_bytes} bytes < ${BACKUP_MIN_BYTES}），疑似空库或写盘失败"
    blog "ERROR 备份文件过小（${size_bytes} bytes < ${BACKUP_MIN_BYTES}），已删除：${partial}"
    exit 1
fi

# 校验 1/2：pg_restore --list（对 .partial 做，损坏就不必再改名）
if ! verify_dump_list "$partial"; then
    rm -f "$partial"
    backup_alert backup_failed "备份归档无法通过 pg_restore --list 校验（db=${PGDATABASE}）"
    blog "ERROR pg_restore --list 校验失败，已删除不完整文件：${partial}"
    exit 1
fi
blog "pg_restore --list 校验通过：TOC 条目 ${VERIFY_TOC_ENTRIES} 个"

mv "$partial" "$target" || {
    rm -f "$partial"
    backup_alert backup_failed "重命名备份文件失败：${target}"
    blog "ERROR 重命名备份文件失败：${target}"
    exit 1
}
trap - EXIT
trap - INT TERM

# 校验 2/2：sha256 生成 + 立即回验
if ! ( cd "$BACKUP_DIR" && sha256sum "${base}.dump" >"${base}.dump.sha256" ); then
    rm -f "$target" "${target}.sha256"
    backup_alert backup_failed "生成 sha256 校验文件失败（已删除未通过校验的产物）：${base}.dump"
    blog "ERROR 生成 sha256 校验文件失败，已删除 ${target}"
    exit 1
fi
if ! ( cd "$BACKUP_DIR" && sha256sum -c "${base}.dump.sha256" >/dev/null 2>&1 ); then
    rm -f "$target" "${target}.sha256"
    backup_alert backup_failed "sha256 回验失败（产物已删除）：${base}.dump"
    blog "ERROR sha256 回验失败，已删除 ${target}"
    exit 1
fi
blog "sha256 校验通过：${base}.dump (${size_bytes} bytes, TOC ${VERIFY_TOC_ENTRIES})"

# 成功标记（先写临时文件再改名，避免读到半行）
ok_ts="$(backup_now_iso)"
mark_ok="yes"
printf '%s %s %s\n' "$ok_ts" "$base" "$size_bytes" >"${BACKUP_DIR}/.last_backup_ok.partial" 2>/dev/null || mark_ok="no"
printf '%s\n' "$(backup_now_epoch)" >"${BACKUP_DIR}/.last_backup_ok.epoch.partial" 2>/dev/null || mark_ok="no"
if [ "$mark_ok" != "yes" ]; then
    rm -f "${BACKUP_DIR}/.last_backup_ok.partial" "${BACKUP_DIR}/.last_backup_ok.epoch.partial" 2>/dev/null || true
    backup_alert backup_failed "写入成功标记失败：${BACKUP_DIR}/.last_backup_ok"
    blog "ERROR 写入 ${BACKUP_DIR}/.last_backup_ok 失败"
    exit 1
fi
mv "${BACKUP_DIR}/.last_backup_ok.partial" "${BACKUP_DIR}/.last_backup_ok" || {
    backup_alert backup_failed "重命名成功标记失败：${BACKUP_DIR}/.last_backup_ok"
    blog "ERROR 重命名 ${BACKUP_DIR}/.last_backup_ok 失败"
    exit 1
}
mv "${BACKUP_DIR}/.last_backup_ok.epoch.partial" "${BACKUP_DIR}/.last_backup_ok.epoch" 2>/dev/null || blog "WARN 写入 .last_backup_ok.epoch 失败（新鲜度检查将回退到文件 mtime）"

# 本次成功 → 清掉上一次的失败告警文件（告警恢复由 REL-003 读取存在性）
rm -f "${BACKUP_DIR}/.alert_backup_failed" 2>/dev/null || true
rm -f "${BACKUP_DIR}/.alert_stale_backup" 2>/dev/null || true

# --- 周备标记（不做完整复制，只打标记）-----------------------------------------
weekly_marked="no"
if [ "$BACKUP_RETENTION_WEEKS" -gt 0 ]; then
    today_weekday="$(date -u +%u)"
    if [ "$today_weekday" = "$BACKUP_WEEKLY_WEEKDAY" ]; then
        if mkdir -p "$BACKUP_WEEKLY_DIR" 2>/dev/null \
            && : >"${BACKUP_WEEKLY_DIR}/${base}" 2>/dev/null; then
            weekly_marked="yes"
            blog "周备标记：${base}（${BACKUP_WEEKLY_DIR}/${base}；不做复制，按标记保留）"
        else
            blog "WARN 无法写入周备标记目录 ${BACKUP_WEEKLY_DIR}（本次按日备处理）"
        fi
    else
        blog "今日 UTC 周${today_weekday}，非周备日（周${BACKUP_WEEKLY_WEEKDAY}），不写周标记"
    fi
else
    blog "BACKUP_RETENTION_WEEKS=0：周备标记已禁用（只保留 ${BACKUP_RETENTION_DAYS} 天日备）"
fi

# --- 保留策略：日备 ------------------------------------------------------------
pruned=0
if [ "$BACKUP_RETENTION_DAYS" -gt 0 ]; then
    for old in $(find "$BACKUP_DIR" -maxdepth 1 -type f -name '*.dump' \
        -mtime "+${BACKUP_RETENTION_DAYS}" 2>/dev/null); do
        obase="$(basename "$old" .dump)"
        if [ "$BACKUP_RETENTION_WEEKS" -gt 0 ] && [ -f "${BACKUP_WEEKLY_DIR}/${obase}" ]; then
            blog "跳过周备（标记保留）：$(basename "$old")"
            continue
        fi
        rm -f "$old" "${old}.sha256" 2>/dev/null || true
        pruned=$((pruned + 1))
        blog "清理过期日备：$(basename "$old")"
    done
else
    blog "WARN BACKUP_RETENTION_DAYS=0：跳过按天清理（磁盘占用由运维负责）"
fi

# --- 保留策略：周备（滚存 RETENTION_WEEKS 份，删最旧标记及其文件） ---------------
if [ "$BACKUP_RETENTION_WEEKS" -gt 0 ] && [ -d "$BACKUP_WEEKLY_DIR" ]; then
    # 先清理“标记还在但 dump 已被人工删除”的孤儿标记
    for mark in $(ls -1 "$BACKUP_WEEKLY_DIR" 2>/dev/null); do
        if [ ! -f "${BACKUP_DIR}/${mark}.dump" ]; then
            rm -f "${BACKUP_WEEKLY_DIR}/${mark}" 2>/dev/null || true
            blog "清理孤儿周备标记：${mark}"
        fi
    done

    week_count="$(ls -1 "$BACKUP_WEEKLY_DIR" 2>/dev/null | wc -l | tr -d ' ')"
    case "${week_count:-}" in
        ''|*[!0-9]*) week_count=0 ;;
    esac
    if [ "$week_count" -gt "$BACKUP_RETENTION_WEEKS" ]; then
        excess=$((week_count - BACKUP_RETENTION_WEEKS))
        idx=0
        for mark in $(ls -1 "$BACKUP_WEEKLY_DIR" 2>/dev/null | sort); do
            if [ "$idx" -lt "$excess" ]; then
                rm -f "${BACKUP_WEEKLY_DIR}/${mark}" 2>/dev/null || true
                rm -f "${BACKUP_DIR}/${mark}.dump" "${BACKUP_DIR}/${mark}.dump.sha256" 2>/dev/null || true
                pruned=$((pruned + 1))
                blog "清理过期周备：${mark}（超过 ${BACKUP_RETENTION_WEEKS} 份）"
            fi
            idx=$((idx + 1))
        done
    fi
    blog "周备存量：${week_count} 份（上限 ${BACKUP_RETENTION_WEEKS}）"
fi

# --- 异地副本（可选开关）-------------------------------------------------------
remote_state="skipped"
if [ -n "$BACKUP_REMOTE_DIR" ]; then
    remote_state="failed"
    if mkdir -p "$BACKUP_REMOTE_DIR" 2>/dev/null \
        && cp -p "$target" "${target}.sha256" "$BACKUP_REMOTE_DIR/" 2>/dev/null \
        && ( cd "$BACKUP_REMOTE_DIR" && sha256sum -c "${base}.dump.sha256" >/dev/null 2>&1 ); then
        remote_state="ok"
        blog "异地副本完成（并回验 sha256）：${BACKUP_REMOTE_DIR}/${base}.dump"
    else
        # 回验失败/拷贝中断：删掉目标目录里的坏副本，避免被误当有效异地备份
        rm -f "${BACKUP_REMOTE_DIR}/${base}.dump" "${BACKUP_REMOTE_DIR}/${base}.dump.sha256" 2>/dev/null || true
        backup_alert remote_copy_failed "异地目录副本失败：${BACKUP_REMOTE_DIR}（本地备份有效，但异地无副本）"
        blog "ERROR 异地目录副本失败：${BACKUP_REMOTE_DIR}（本地备份仍有效，见 .alert_remote_copy_failed）"
    fi
elif [ -n "$BACKUP_REMOTE_CMD" ]; then
    remote_state="failed"
    # 占位接口：命令里用 $BACKUP_FILE / $BACKUP_SHA256_FILE / $BACKUP_BASE 引用本次产物
    if BACKUP_FILE="$target" BACKUP_SHA256_FILE="${target}.sha256" BACKUP_BASE="$base" \
        sh -c "$BACKUP_REMOTE_CMD" >/dev/null 2>&1; then
        remote_state="ok"
        blog "异地副本命令执行成功（BACKUP_REMOTE_CMD）"
    else
        backup_alert remote_copy_failed "对象存储副本命令失败（BACKUP_REMOTE_CMD，本地备份有效）"
        blog "ERROR 异地副本命令执行失败（BACKUP_REMOTE_CMD；本地备份仍有效）"
    fi
else
    blog "NOTICE 未配置异地副本（BACKUP_REMOTE_DIR / BACKUP_REMOTE_CMD 均为空）：本次仅本地备份，不阻塞"
fi

# 异地副本成功（或本次未配置）才清除历史告警；失败时保留 .alert_remote_copy_failed 供监控读取
if [ "$remote_state" != "failed" ]; then
    rm -f "${BACKUP_DIR}/.alert_remote_copy_failed" 2>/dev/null || true
fi

blog "===== 备份完成：${base}.dump (${size_bytes} bytes) 清理 ${pruned} 个 | 日备保留 ${BACKUP_RETENTION_DAYS} 天 / 周备保留 ${BACKUP_RETENTION_WEEKS} 份 / 周标记 ${weekly_marked} / 异地 ${remote_state} ====="

if [ "$remote_state" = "failed" ]; then
    exit 3
fi
exit 0
