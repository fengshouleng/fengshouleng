#!/usr/bin/env bash
# 一键执行 V1 验收预检（G4 证据采集）—— Linux / macOS / Git Bash / WSL
#
# 用法:
#   ./scripts/verify_all.sh                      # 契约 + 后端 + 前端
#   ./scripts/verify_all.sh --with-security --api-url http://127.0.0.1:8000/api
#   ./scripts/verify_all.sh --with-e2e --base-url http://127.0.0.1:5173
#   ./scripts/verify_all.sh --with-perf
#   ./scripts/verify_all.sh --only contract
#   ./scripts/verify_all.sh --skip-install
#
# 注意: 开发会话中 shell 不可用（0xC0000142），本脚本仅静态审查，从未执行。
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG_DIR="$ROOT/reports/verify-$STAMP"
mkdir -p "$LOG_DIR"

ONLY="all"
WITH_SECURITY=0
WITH_E2E=0
WITH_PERF=0
SKIP_INSTALL=0
API_URL="http://127.0.0.1:8000/api"
BASE_URL="http://127.0.0.1:5173"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --only) ONLY="$2"; shift 2 ;;
    --with-security) WITH_SECURITY=1; shift ;;
    --with-e2e) WITH_E2E=1; shift ;;
    --with-perf) WITH_PERF=1; shift ;;
    --api-url) API_URL="$2"; shift 2 ;;
    --base-url) BASE_URL="$2"; shift 2 ;;
    --skip-install) SKIP_INSTALL=1; shift ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

RESULTS=()
FAILED=0
INDEX=0

run_step() {
  local name="$1" dir="$2" cmd="$3"
  INDEX=$((INDEX + 1))
  local safe log begin end
  safe="$(printf '%s' "$name" | tr -c '[:alnum:]_-' '_')"
  log="$LOG_DIR/$(printf '%02d' "$INDEX")-$safe.log"
  printf '\n=== [%d] %s ===\n' "$INDEX" "$name"
  printf '    dir: %s\n    cmd: %s\n' "$dir" "$cmd"
  begin=$(date +%s)
  ( cd "$dir" && bash -c "$cmd" ) 2>&1 | tee "$log"
  local code="${PIPESTATUS[0]}"
  end=$(date +%s)
  RESULTS+=("$(printf '%-40s exit=%-3s %ss  %s' "$name" "$code" "$((end - begin))" "$log")")
  if [[ "$code" -ne 0 ]]; then
    FAILED=$((FAILED + 1))
    printf '  -> FAIL (exit %s)\n' "$code" >&2
  fi
}

if [[ "$ONLY" == "all" || "$ONLY" == "contract" ]]; then
  if [[ "$SKIP_INSTALL" -eq 0 ]]; then
    run_step "安装契约测试依赖" "$ROOT" "python -m pip install -q -r tests/requirements.txt"
  fi
  run_step "OpenAPI 结构校验" "$ROOT" "openapi-spec-validator contracts/openapi.yaml"
  run_step "契约测试" "$ROOT" "python -m pytest tests/contract -q"
fi

if [[ "$ONLY" == "all" || "$ONLY" == "backend" ]]; then
  if [[ "$SKIP_INSTALL" -eq 0 ]]; then
    run_step "安装后端依赖" "$ROOT" 'python -m pip install -q -e "server[dev]"'
  fi
  run_step "后端 ruff" "$ROOT/server" "ruff check ."
  run_step "后端 mypy" "$ROOT/server" "mypy app"
  run_step "后端 pytest" "$ROOT/server" "python -m pytest -q"
fi

if [[ "$ONLY" == "all" || "$ONLY" == "frontend" ]]; then
  if [[ "$SKIP_INSTALL" -eq 0 ]]; then
    run_step "安装前端依赖" "$ROOT/web" "pnpm install --no-frozen-lockfile"
    run_step "生成契约类型" "$ROOT/web" "pnpm gen:types"
  fi
  run_step "前端 lint" "$ROOT/web" "pnpm lint"
  run_step "前端 tsc" "$ROOT/web" "pnpm tsc --noEmit"
  run_step "前端单测" "$ROOT/web" "pnpm test"
  run_step "前端构建" "$ROOT/web" "pnpm build"
fi

if [[ "$WITH_SECURITY" -eq 1 ]]; then
  run_step "安全负例（需运行中的 API）" "$ROOT" "EXAM_API_URL=$API_URL python -m pytest tests/security -q"
fi

if [[ "$WITH_E2E" -eq 1 ]]; then
  if [[ "$SKIP_INSTALL" -eq 0 ]]; then
    run_step "安装 Playwright 浏览器（chromium）" "$ROOT" "python -m playwright install chromium"
  fi
  run_step "E2E 关键链路（需前端与 seed 数据）" "$ROOT" "EXAM_E2E=1 EXAM_BASE_URL=$BASE_URL python -m pytest tests/e2e -q"
fi

if [[ "$WITH_PERF" -eq 1 ]]; then
  run_step "性能冒烟（k6，需独立环境）" "$ROOT" "k6 run tests/perf/k6/exam_flow.js"
fi

printf '\n================ 汇总 ================\n'
for row in "${RESULTS[@]:-}"; do
  [[ -n "$row" ]] && printf '%s\n' "$row"
done
printf '日志目录: %s\n' "$LOG_DIR"

if [[ "$FAILED" -gt 0 ]]; then
  printf '结果: FAILED（%d 步失败）\n' "$FAILED" >&2
  exit "$FAILED"
fi
printf '结果: PASS（全部步骤通过）\n'
exit 0
