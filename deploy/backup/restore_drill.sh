#!/bin/sh
# deploy/backup/restore_drill.sh — 恢复演练（TASK-REL-002 · G4 准入门控）
#
# 做什么（全程**不碰生产库数据**）：
#   1. 选一份备份（--dump 指定，或 ${BACKUP_DIR} 下最新 *.dump），强制校验 sha256 + pg_restore --list
#   2. 建临时库 ${DRILL_DB_PREFIX}_<UTC时间戳> 并 pg_restore 到临时库
#      （源库只执行 SELECT count(*)，任何写入都发生在临时库）
#   3. 比对 5 张关键表行数（users / questions / papers / exams / exam_sessions）
#      - restored == source：一致
#      - restored <  source：允许（备份时间点之后有写入），打印差额，需在记录里说明
#      - restored >  source 或 source>0 而 restored=0：判定失败（不可能/数据缺失）
#   4. 输出恢复耗时、总耗时与结论；恢复耗时 > DRILL_RTO_LIMIT_SECONDS（默认 1800s = 30min）
#      即 **G4 不通过**（docs/71 §4.1）
#   5. 默认删除临时库；--keep 保留（或用例失败时自动保留，便于排查）
#
# 用法（在 backup 容器内执行，该容器有 pg_dump/pg_restore/psql 与同版本客户端）：
#   docker compose ... exec -T backup /bin/sh /backup/restore_drill.sh
#   docker compose ... exec -T backup /bin/sh /backup/restore_drill.sh --dump /backups/exam_20261109T020000Z.dump
#   docker compose ... exec -T backup /bin/sh /backup/restore_drill.sh --keep
#
# 退出码：0 = 演练通过且 G4 达标；1 = 演练失败/G4 不达标；2 = 参数或前置条件不满足
#
# 环境变量：
#   PGHOST PGPORT PGUSER PGPASSWORD    连接（必填）
#   PGDATABASE                         源库名（只读；必填）
#   BACKUP_DIR=/backups                备份目录
#   DRILL_DB_PREFIX=exam_restore_drill 临时库前缀（只允许 [a-z0-9_]）
#   DRILL_KEY_TABLES="users questions papers exams exam_sessions"
#   DRILL_RTO_LIMIT_SECONDS=1800        G4 判据（30 分钟）
#   DRILL_LOG_DIR=${BACKUP_DIR}/drill-logs   演练日志目录
#   DRILL_KEEP=no                      等价 --keep
#   BACKUP_ALERT_CMD=                  失败告警命令（REL-003 接口）
set -eu
if (set -o pipefail) 2>/dev/null; then set -o pipefail; fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=deploy/backup/lib.sh
. "${BACKUP_LIB:-${SCRIPT_DIR}/lib.sh}"

: "${PGHOST:?PGHOST 未设置}"
: "${PGUSER:?PGUSER 未设置}"
: "${PGPASSWORD:?PGPASSWORD 未设置}"
: "${PGDATABASE:?PGDATABASE 未设置（源库名，仅用于只读行数比对）}"

PGPORT="${PGPORT:-5432}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
DRILL_DB_PREFIX="${DRILL_DB_PREFIX:-exam_restore_drill}"
DRILL_KEY_TABLES="${DRILL_KEY_TABLES:-users questions papers exams exam_sessions}"
DRILL_RTO_LIMIT_SECONDS="${DRILL_RTO_LIMIT_SECONDS:-1800}"
DRILL_LOG_DIR="${DRILL_LOG_DIR:-${BACKUP_DIR}/drill-logs}"
DRILL_LOG_FILE="$(backup_log_path "$DRILL_LOG_DIR")"

DUMP=""
KEEP="${DRILL_KEEP:-no}"

usage() {
    cat <<EOF
用法：restore_drill.sh [--dump <file>] [--keep]

  --dump <file>  指定备份文件（默认取 ${BACKUP_DIR} 下最新的 *.dump）
  --keep         保留临时库（默认演练结束后删除）
  -h, --help     显示本帮助

说明：演练只写临时库，源库仅执行 SELECT count(*)；不支持 --force，
     生产库恢复请用 restore.sh（见 docs/71 §3.2）。
EOF
}

for arg in "$@"; do
    case "$arg" in
        --dump)
            shift_next_dump="yes"
            ;;
        --keep)
            KEEP="yes"
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        --force)
            printf '[drill] ERROR 演练脚本永不支持 --force：生产恢复请用 /backup/restore.sh\n' >&2
            exit 2
            ;;
        -*)
            printf '[drill] ERROR 未知参数：%s\n' "$arg" >&2
            usage >&2
            exit 2
            ;;
        *)
            if [ -n "${shift_next_dump:-}" ] && [ -z "$DUMP" ]; then
                DUMP="$arg"
                shift_next_dump=""
            else
                printf '[drill] ERROR 只接受一个备份文件参数（多余：%s）\n' "$arg" >&2
                exit 2
            fi
            ;;
    esac
done
if [ -n "${shift_next_dump:-}" ] && [ -z "$DUMP" ]; then
    printf '[drill] ERROR --dump 缺少文件参数\n' >&2
    exit 2
fi

say() {
    printf '%s\n' "$*"
    if [ -n "$DRILL_LOG_FILE" ]; then
        printf '%s\n' "$*" >>"$DRILL_LOG_FILE" 2>/dev/null || true
    fi
}

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

is_uint "$DRILL_RTO_LIMIT_SECONDS" || {
    printf '[drill] ERROR DRILL_RTO_LIMIT_SECONDS=%s 不是正整数\n' "$DRILL_RTO_LIMIT_SECONDS" >&2
    exit 2
}

case "$DRILL_DB_PREFIX" in
    ''|*[!a-z0-9_]*)
        printf '[drill] ERROR DRILL_DB_PREFIX=%s 只允许小写字母/数字/下划线\n' "$DRILL_DB_PREFIX" >&2
        exit 2
        ;;
esac

command -v psql >/dev/null 2>&1 || { printf '[drill] ERROR 缺少 psql\n' >&2; exit 2; }
command -v pg_restore >/dev/null 2>&1 || { printf '[drill] ERROR 缺少 pg_restore\n' >&2; exit 2; }
command -v createdb >/dev/null 2>&1 || { printf '[drill] ERROR 缺少 createdb\n' >&2; exit 2; }
command -v dropdb >/dev/null 2>&1 || { printf '[drill] ERROR 缺少 dropdb\n' >&2; exit 2; }

DRILL_START="$(backup_now_epoch)"
RESTORE_SECONDS=0
TOTAL_SECONDS=0
RESULT="FAIL"
G4="fail"
ROW_OK="yes"
ROW_SUMMARY=""
TMPDB=""
CREATED="no"
DRILL_FAILED="no"
DUMP_TS="unknown"
BACKUP_AGE_HOURS="unknown"

print_summary() {
    say "================ 恢复演练结论（restore drill）================"
    say "dump=${DUMP:-（未选定）}"
    say "backup_utc=${DUMP_TS}  backup_age_hours=${BACKUP_AGE_HOURS}"
    say "source_db=${PGDATABASE}（只读）  temp_db=${TMPDB:-（未创建）}"
    say "restore_seconds=${RESTORE_SECONDS}  total_seconds=${TOTAL_SECONDS}  g4_limit_seconds=${DRILL_RTO_LIMIT_SECONDS}"
    say "row_check=${ROW_SUMMARY:-（未执行）}"
    say "result=${RESULT}  g4=${G4}"
    say "drill_log=${DRILL_LOG_FILE:-（未落盘：${DRILL_LOG_DIR} 不可写）}"
    say "回填模板：docs/71-backup-restore.md §4.2（日期/备份点/耗时/行数比对/结论/执行人）"
    say "=============================================================="
}

fail_drill() {
    RESULT="FAIL"
    DRILL_FAILED="yes"
    say "ERROR $*"
    TOTAL_SECONDS=$(( $(backup_now_epoch) - DRILL_START ))
    print_summary
    exit 1
}

cleanup() {
    _rc=$?
    if [ "$CREATED" = "yes" ]; then
        if [ "$KEEP" = "yes" ] || [ "$DRILL_FAILED" = "yes" ]; then
            say "临时库保留：${TMPDB}（清理：先 export PGPASSWORD=<口令>，再 dropdb -h ${PGHOST} -p ${PGPORT} -U ${PGUSER} ${TMPDB}）"
        else
            if dropdb -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" "$TMPDB" >/dev/null 2>&1; then
                say "已删除临时库：${TMPDB}"
            else
                say "WARN 删除临时库失败：${TMPDB}（不影响演练结论，请手工清理）"
            fi
        fi
    fi
    exit "$_rc"
}
trap cleanup EXIT INT TERM

say "===== 恢复演练开始（源库 ${PGDATABASE} 只执行只读查询）====="

# --- 0. 安全护栏：源库不能是维护库，临时库名不能等于源库 ------------------------
case "$PGDATABASE" in
    postgres|template0|template1)
        fail_drill "PGDATABASE=${PGDATABASE} 是维护库，演练源必须是业务库"
        ;;
esac

# --- 1. 选备份 ---------------------------------------------------------------
if [ -z "$DUMP" ]; then
    DUMP="$(ls -1t "${BACKUP_DIR}"/*.dump 2>/dev/null | head -n 1 || true)"
fi
if [ -z "$DUMP" ]; then
    fail_drill "在 ${BACKUP_DIR} 未找到任何 *.dump；先执行 /bin/sh /backup/backup.sh"
fi
say "选定备份：${DUMP}"

[ -f "$DUMP" ] || fail_drill "备份文件不存在：${DUMP}"
[ -s "$DUMP" ] || fail_drill "备份文件为空：${DUMP}"

DUMP_TS="$(date -u -r "$DUMP" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown)"
if [ "$DUMP_TS" != "unknown" ]; then
    _dump_epoch="$(date -u -r "$DUMP" +%s 2>/dev/null || true)"
    case "${_dump_epoch:-}" in
        ''|*[!0-9]*) : ;;
        *) BACKUP_AGE_HOURS=$(( ($(backup_now_epoch) - _dump_epoch) / 3600 )) ;;
    esac
fi
say "备份时间（文件 mtime，UTC）：${DUMP_TS}（约 ${BACKUP_AGE_HOURS}h 前）"

# --- 2. 完整性校验（sha256 + pg_restore --list）--------------------------------
say "校验 sha256：${DUMP}.sha256"
verify_dump_sha "$DUMP" || fail_drill "sha256 校验失败（或缺校验文件）：${DUMP}"

say "校验归档可解析：pg_restore --list"
verify_dump_list "$DUMP" || fail_drill "pg_restore --list 校验失败：${DUMP}"
say "归档可解析：TOC 条目 ${VERIFY_TOC_ENTRIES} 个"

# --- 3. 建临时库 --------------------------------------------------------------
TMPDB="${DRILL_DB_PREFIX}_$(date -u +%Y%m%dT%H%M%S)"
[ "$TMPDB" != "$PGDATABASE" ] || fail_drill "临时库名与源库相同（${TMPDB}），拒绝执行"
say "创建临时库：${TMPDB}"
if ! createdb -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" "$TMPDB"; then
    fail_drill "创建临时库失败：${TMPDB}（是否已存在同名库？或 ${PGUSER} 无 CREATEDB 权限）"
fi
CREATED="yes"
say "临时库已创建（源库 ${PGDATABASE} 不会被写入）"

# --- 4. 恢复到临时库（计时；--single-transaction 保证要么全成要么全不成）--------
say "开始 pg_restore -> ${TMPDB}"
restore_start="$(backup_now_epoch)"
set +e
pg_restore \
    --single-transaction \
    --no-owner \
    --no-privileges \
    --exit-on-error \
    --host="$PGHOST" \
    --port="$PGPORT" \
    --username="$PGUSER" \
    --dbname="$TMPDB" \
    "$DUMP"
restore_rc=$?
set -e
RESTORE_SECONDS=$(( $(backup_now_epoch) - restore_start ))
say "pg_restore 结束：exit=${restore_rc}，耗时 ${RESTORE_SECONDS}s"

if [ "$restore_rc" -ne 0 ]; then
    fail_drill "pg_restore 失败（exit=${restore_rc}）；见上方错误与保留的临时库"
fi

# --- 5. 关键表行数比对 --------------------------------------------------------
count_rows() {
    _db="$1"
    _table="$2"
    _out="$(psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$_db" -tA \
        -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM public.\"$_table\"" 2>&1)" || {
        printf 'ERR'
        return 0
    }
    printf '%s' "$_out" | tr -d '[:space:]'
}

say "关键表行数比对（source=${PGDATABASE} / restored=${TMPDB}）："
for table in $DRILL_KEY_TABLES; do
    src="$(count_rows "$PGDATABASE" "$table")"
    rst="$(count_rows "$TMPDB" "$table")"

    if ! is_uint "$src"; then
        ROW_OK="no"
        ROW_SUMMARY="${ROW_SUMMARY}${table}=源库查询失败; "
        say "  ${table}: 源库查询失败（表不存在或无权限）"
        continue
    fi
    if ! is_uint "$rst"; then
        ROW_OK="no"
        ROW_SUMMARY="${ROW_SUMMARY}${table}=恢复库查询失败; "
        say "  ${table}: 恢复库查询失败（表未恢复/结构异常）"
        continue
    fi

    if [ "$rst" -eq "$src" ]; then
        verdict="一致"
    elif [ "$rst" -lt "$src" ]; then
        verdict="备份点后有写入（差 $((src - rst)) 行）"
    else
        ROW_OK="no"
        verdict="异常：恢复结果多于源库（多 $((rst - src)) 行）"
    fi
    if [ "$src" -gt 0 ] && [ "$rst" -eq 0 ]; then
        ROW_OK="no"
        verdict="${verdict}；恢复后为空（数据缺失）"
    fi

    ROW_SUMMARY="${ROW_SUMMARY}${table}=${src}/${rst}(${verdict}); "
    say "  ${table}: source=${src} restored=${rst} -> ${verdict}"
done

# --- 6. 结论 ------------------------------------------------------------------
TOTAL_SECONDS=$(( $(backup_now_epoch) - DRILL_START ))

if [ "$ROW_OK" = "yes" ] && [ "$RESTORE_SECONDS" -le "$DRILL_RTO_LIMIT_SECONDS" ]; then
    RESULT="PASS"
    G4="pass"
    say "结论：通过（恢复 ${RESTORE_SECONDS}s < 阈值 ${DRILL_RTO_LIMIT_SECONDS}s；关键表行数校验通过）"
elif [ "$ROW_OK" = "yes" ]; then
    RESULT="FAIL"
    G4="fail"
    say "结论：不通过 —— 恢复耗时 ${RESTORE_SECONDS}s 超过 ${DRILL_RTO_LIMIT_SECONDS}s（30 分钟），G4 不通过（docs/71 §4.1）"
else
    RESULT="FAIL"
    G4="fail"
    say "结论：不通过 —— 关键表行数校验失败（见上方 row_check），G4 不通过"
fi

print_summary

if [ "$RESULT" = "PASS" ]; then
    say "下一步：把本次结论回填 docs/71 §4.2 台账，日志留存到 deploy/evidence/（命名 dr-<YYYYMMDD>-<NN>.log）"
    exit 0
fi

DRILL_FAILED="yes"
backup_alert drill_failed "恢复演练未通过：result=${RESULT} g4=${G4} restore=${RESTORE_SECONDS}s row_ok=${ROW_OK} dump=${DUMP}"
exit 1
