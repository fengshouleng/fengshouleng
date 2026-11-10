#!/bin/sh
# deploy/backup/restore.sh — 从逻辑备份恢复（TASK-REL-001 骨架 / TASK-REL-002 演练执行）
#
# 安全约束（默认拒绝，防止误操作）：
#   1. 必须显式传 --yes 或设置 RESTORE_CONFIRM=yes
#   2. 必须先停止 api（避免恢复期间写入）；脚本无法验证，靠流程保证
#   3. 恢复前校验 .sha256（存在时）并用 pg_restore --list 验证归档可读
#   4. 禁止恢复到维护库名 postgres / template*
#
# 用法（在 backup 容器内执行，该容器已有 pg_restore 且与服务器同版本）：
#   docker compose --env-file .env.staging -p exam-staging stop api
#   docker compose --env-file .env.staging -p exam-staging exec -T \
#     -e RESTORE_CONFIRM=yes backup \
#     /bin/sh /backup/restore.sh /backups/exam_20261109T020000Z.dump
#
# 恢复完成后：启动 api（入口会 alembic upgrade head），再按 docs/70-runbook.md §7 冒烟。
set -eu

: "${PGHOST:?PGHOST 未设置}"
: "${PGDATABASE:?PGDATABASE 未设置}"
: "${PGUSER:?PGUSER 未设置}"
: "${PGPASSWORD:?PGPASSWORD 未设置}"

PGPORT="${PGPORT:-5432}"

log() {
    printf '[restore] %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

fail() {
    log "ERROR $*"
    exit 1
}

confirm="no"
dump=""

for arg in "$@"; do
    case "$arg" in
        --yes) confirm="yes" ;;
        -h|--help)
            printf 'usage: restore.sh <dump-file> [--yes]\n'
            exit 0
            ;;
        -*)
            fail "未知参数：$arg（用法：restore.sh <dump-file> [--yes]）"
            ;;
        *)
            [ -z "$dump" ] || fail "只接受一个备份文件参数（多余：$arg）"
            dump="$arg"
            ;;
    esac
done

[ -n "$dump" ] || fail "缺少备份文件参数（用法：restore.sh <dump-file> [--yes]）"
[ "${RESTORE_CONFIRM:-no}" = "yes" ] && confirm="yes"
[ "$confirm" = "yes" ] || fail "拒绝执行：请显式加 --yes 或设置 RESTORE_CONFIRM=yes（会覆盖当前库数据）"

case "$PGDATABASE" in
    postgres|template0|template1)
        fail "拒绝恢复到维护库 ${PGDATABASE}；请把 PGDATABASE 指向业务库"
        ;;
esac

[ -f "$dump" ] || fail "备份文件不存在：$dump"
[ -s "$dump" ] || fail "备份文件为空：$dump"

if [ -f "${dump}.sha256" ]; then
    log "校验 sha256：${dump}.sha256"
    ( cd "$(dirname "$dump")" && sha256sum -c "$(basename "$dump").sha256" ) \
        || fail "sha256 校验失败，备份文件可能损坏或被篡改"
else
    log "WARN 未找到 ${dump}.sha256，跳过完整性校验（不建议用于生产恢复）"
fi

log "验证归档可读：pg_restore --list"
pg_restore --list "$dump" >/dev/null || fail "备份归档无法解析（文件损坏或非 custom 格式）"

log "开始恢复 db=${PGDATABASE} host=${PGHOST}:${PGPORT} from ${dump}"
log "注意：恢复会 DROP/重建归档内的对象（--clean --if-exists）"

set +e
pg_restore \
    --clean \
    --if-exists \
    --no-owner \
    --no-privileges \
    --exit-on-error \
    --host="$PGHOST" \
    --port="$PGPORT" \
    --username="$PGUSER" \
    --dbname="$PGDATABASE" \
    "$dump"
restore_rc=$?
set -e

if [ "$restore_rc" -ne 0 ]; then
    fail "pg_restore 失败（exit=${restore_rc}）；库可能处于部分恢复状态，请评估是否需要重跑或从更早备份恢复"
fi

log "恢复完成。下一步：启动 api（自动 alembic upgrade head）并执行冒烟（docs/70-runbook.md §7）"
exit 0
