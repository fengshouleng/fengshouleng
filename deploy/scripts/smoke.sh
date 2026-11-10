#!/usr/bin/env bash
# deploy/scripts/smoke.sh — staging 冒烟（TASK-REL-001，证据留档）
#
# 覆盖：健康检查 → 登录（Cookie+CSRF）→ /auth/me → 建题 → 列表回读 → 清理。
# 任一断言失败立即以非 0 退出，并把现场追加到证据文件。
#
# 用法（仓库根执行）：
#   export ADMIN_USER=admin ADMIN_PASSWORD='...'
#   bash deploy/scripts/smoke.sh
#
# 可覆盖的环境变量：
#   BASE        默认 http://127.0.0.1:8080   （compose 的 NGINX_HTTP_PORT）
#   ENV_FILE    默认 deploy/.env.staging（仅用于校验存在性与证据文件命名）
#   EVID_DIR    默认 deploy/evidence
#
# 依赖：curl、jq（缺失时脚本直接报错，不降级猜测）
set -euo pipefail

BASE="${BASE:-http://127.0.0.1:8080}"
ENV_FILE="${ENV_FILE:-deploy/.env.staging}"
EVID_DIR="${EVID_DIR:-deploy/evidence}"

: "${ADMIN_USER:?请 export ADMIN_USER（§3.5 创建的管理员用户名）}"
: "${ADMIN_PASSWORD:?请 export ADMIN_PASSWORD（不要写进 .env 或提交）}"

command -v curl >/dev/null 2>&1 || { echo "ERROR: 缺少 curl" >&2; exit 2; }
command -v jq >/dev/null 2>&1 || { echo "ERROR: 缺少 jq（apt install jq / brew install jq）" >&2; exit 2; }
[ -f "$ENV_FILE" ] || { echo "ERROR: 环境文件不存在：$ENV_FILE" >&2; exit 2; }

mkdir -p "$EVID_DIR"
ENV_NAME="${ENV_FILE##*/}"
ENV_NAME="${ENV_NAME#.env.}"
EVID="${EVID_DIR}/rel-001-${ENV_NAME}-$(date -u +%Y%m%dT%H%M%SZ).log"
COOKIE="$(mktemp)"
trap 'rm -f "$COOKIE"' EXIT

# stdout/stderr 同时落盘（证据）
exec > >(tee -a "$EVID") 2>&1

echo "# REL-001 staging 冒烟"
echo "# 时间(UTC): $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "# BASE=${BASE} ENV_FILE=${ENV_FILE}"
echo "# git: $(git rev-parse --short HEAD 2>/dev/null || echo 'unknown')"
echo

echo "== [1/6] 健康检查 =="
curl -fsS "${BASE}/api/healthz"
echo

echo "== [2/6] 登录（Cookie + CSRF）=="
LOGIN_JSON="$(curl -fsS -c "$COOKIE" -X POST "${BASE}/api/auth/login" \
  -H 'Content-Type: application/json' \
  --data "$(jq -nc --arg u "$ADMIN_USER" --arg p "$ADMIN_PASSWORD" '{username:$u,password:$p}')")"
printf '%s' "$LOGIN_JSON" | jq -c '{user: .user.username, role: .user.role, csrf_len: (.csrf_token | length)}'
CSRF="$(printf '%s' "$LOGIN_JSON" | jq -r '.csrf_token')"
[ -n "$CSRF" ] && [ "$CSRF" != "null" ] || { echo "FAIL: 登录响应缺少 csrf_token" >&2; exit 1; }

echo "== [3/6] 当前用户（Cookie 生效）=="
curl -fsS -b "$COOKIE" "${BASE}/api/auth/me" | jq -c '{username: .user.username, role: .user.role}'

echo "== [4/6] 建题（写接口需 X-CSRF-Token）=="
Q_JSON="$(curl -fsS -b "$COOKIE" -X POST "${BASE}/api/questions" \
  -H 'Content-Type: application/json' -H "X-CSRF-Token: ${CSRF}" --data @- <<'JSON'
{"type":"single","stem":"REL-001 冒烟题：1+1=?","options":[{"key":"A","text":"2"},{"key":"B","text":"3"}],"answer":["A"],"explanation":"基础算术","difficulty":1,"tags":["smoke"]}
JSON
)"
printf '%s' "$Q_JSON" | jq -c '{id, type, stem}'
QID="$(printf '%s' "$Q_JSON" | jq -r '.id')"
case "$QID" in
  ''|null) echo "FAIL: 建题响应缺少 id" >&2; exit 1 ;;
esac

echo "== [5/6] 列表回读（落库与分页）=="
curl -fsS -b "$COOKIE" "${BASE}/api/questions?page=1&page_size=5" \
  | jq -c '{total, ids: [.items[].id]}'

echo "== [6/6] 清理冒烟数据（软删除）=="
HTTP_CODE="$(curl -sS -o /dev/null -w '%{http_code}' -b "$COOKIE" \
  -X DELETE "${BASE}/api/questions/${QID}" -H "X-CSRF-Token: ${CSRF}")"
echo "delete status=${HTTP_CODE}"
case "$HTTP_CODE" in
  200|204) ;;
  *) echo "FAIL: 删除冒烟题失败（status=${HTTP_CODE}）" >&2; exit 1 ;;
esac

echo
echo "== 冒烟全部通过 =="
echo "证据文件：${EVID}"
