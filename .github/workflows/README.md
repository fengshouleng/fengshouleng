# .github/workflows — CI 判据（TASK-REL-000）

本目录只有 `ci.yml`。因为本机 `pwsh` 不可用（exit `0xC0000142`，RISK-08），CI 是当前唯一的
**运行时**验证通道；所有门控证据（G2 契约机器门、G3 模块合并）都以这里的一次成功运行为准。

## 1. 触发与并发

| 项 | 值 |
|---|---|
| 触发 | `pull_request`（目标 `main`）+ `push`（`main`） |
| 并发 | group `CI-<ref>`，`cancel-in-progress: true` → 同分支/同 PR 只保留最新一次运行 |
| 权限 | `contents: read`；不写回仓库、不部署、不注入任何密钥 |

## 2. 四个 job 与判据

`changes` 先用 `git diff` 算出变更集（等价路径过滤），再把 `backend/frontend/contract`
三个布尔输出给下游 job 的 `if`。**路径 → job 映射**：

| 变更路径 | 触发 job |
|---|---|
| `server/**` | `backend` |
| `web/**` | `frontend` |
| `contracts/**`、`tests/**` | `contract` |
| `.github/workflows/**` | 三个 job 全跑 |
| 无法计算变更集 / 空变更集 | 三个 job 全跑（保守兜底，日志里有 `::notice`） |

| job | 环境 | 命令（工作目录） | 超时 |
|---|---|---|---|
| `backend` | Python 3.12，pip 缓存（`server/pyproject.toml`） | `server/`：`pip install -e ".[dev]"` → `ruff check .` → `mypy app` → `pytest -q` | 20 min |
| `frontend` | Node 20 + pnpm 9，pnpm store 缓存 | `web/`：`pnpm install` → `pnpm lint` → `pnpm tsc --noEmit` → `pnpm test` → `pnpm build` | 20 min |
| `contract` | Python 3.12，pip 缓存（`tests/requirements.txt`） | 仓库根：`openapi-spec-validator contracts/openapi.yaml` → `python -m pytest tests/contract -q` | 15 min |
| `ci-gate` | 汇总 | 任一 job `failure`/`cancelled` → 失败；`skipped`（路径未命中）不算失败 | 5 min |

**判据**：`ci-gate` 绿 = 本次变更相关的 lint / 类型 / 单测 / 契约 / 构建全绿。
被路径过滤跳过的 job 记 `skipped`，不降低门禁强度（未改动模块无需重跑）。

### 已知宽容项（都会在日志里给出 `::notice` / `::warning`）

1. **仓库暂无 `web/pnpm-lock.yaml`**：`pnpm install` 检测到锁文件缺失时退回
   `--no-frozen-lockfile` 并告警；一旦提交锁文件，自动切回 `--frozen-lockfile`。
   这是唯一允许的非可复现安装路径。→ 见 open_questions。
2. **`pnpm gen:types` 非阻断**：`web/src/api/schema.d.ts` 目前没有被源码 import，
   所以它不是 `lint`/`tsc` 的前置；该步放在 `build` 之后，失败只出 `::warning`。
3. **pytest 的 `xfail(strict=False)`**：`tests/contract` 里 7 条已知契约缺陷用例
   （`test_openapi_valid.py` 5 条 + `test_error_codes.py` 2 条，对应 C-01/03/05/08/09/10/11），
   XPASS 不会让 CI 变红（缺陷修复后应删除标记，见 `docs/60-test-plan.md` §8）。

### 失败证据

每个 job 的命令都用 `tee` 留档到 `$RUNNER_TEMP/ci-logs/*.log`，失败时（`if: failure()`）
以 `backend-logs-<run_id>-<attempt>` 等名字上传 artifact（保留 7 天，`if-no-files-found: ignore`）。
注意：口令/密钥不会进入日志 —— CI 从不读取任何 secret。

本 workflow **不需要在仓库根新增任何文件**：契约 job 直接复用已存在的 `tests/requirements.txt`
（不依赖根 `pyproject.toml`，故无需 `pip install -e .`）。

## 3. 本地复现（在没有 CI 的机器上照抄即可）

```bash
# 后端（在 server/ 下）
cd server
python3.12 -m venv .venv && . .venv/bin/activate
python -m pip install -e ".[dev]"
ruff check .
mypy app
pytest -q

# 前端（在 web/ 下）
cd web
pnpm install              # 有锁文件时改 --frozen-lockfile
pnpm lint
pnpm tsc --noEmit
pnpm test
pnpm build
pnpm gen:types            # 可选：重新生成 src/api/schema.d.ts

# 契约（仓库根）
python3.12 -m pip install -r tests/requirements.txt
openapi-spec-validator contracts/openapi.yaml
python -m pytest tests/contract -q
```

Windows 侧（本机 `pwsh` 恢复后）把 `python3.12`/`python -m` 换成 `py -3.12 -m`，
`pnpm` 需 `corepack enable`。当前本机不可执行（0xC0000142），故上述命令只是复现说明，
不作为已完成证据。

`docker compose` 不参与 CI：镜像构建属于 TASK-REL-001（部署骨架），
本文件刻意不引入额外本地依赖。

### 与 QA 门控脚本的关系

`docs/60-test-plan.md` §6.1 的 G4 门控命令带 `--junitxml=reports/...` 并串行执行
（含 `tests/security`、`tests/e2e`、性能与 Compose 校验），**不属于本 workflow**：
本文件是每个 PR 的最小快速门（lint/type/单测/契约/构建，4 个 job 并行），
G4 的串行门控脚本应在 TASK-REL-002 及之后以独立 workflow 落地（届时复用同一套缓存/日志约定）。
本 workflow 用 `tee` 归档原始日志而不是 junitxml，是为了不引入 `reports/` 目录预处理。

## 4. open_questions（交 Lead 决定，本 Worker 未越界创建）

1. **`web/pnpm-lock.yaml`**：需要前端在可执行环境执行一次 `pnpm install` 并提交锁文件；
   否则 CI 长期跑在 `--no-frozen-lockfile` 上（依赖可漂移）。`web/.gitignore` 并未忽略锁文件，提交即可生效。
2. **根 `README.md` 的「CI 判据」小节**：根 README 不在本任务写作用域（仅 `.github/workflows/**`），
   建议 Lead 把上文第 2 节压缩成 5–8 行放进根 README，或直接链接本文件。
3. **`tests/requirements.txt` 含 `playwright`**：契约 job 只用到 `pytest`/`PyYAML`/
   `openapi-spec-validator`，装 playwright 仅增加安装体积；若要瘦身需 QA 改该文件（本任务禁改 `tests/**`）。
4. **分支保护**：需要人类在 GitHub 上把 `ci-gate` 设为 `main` 的必需检查，
   本 Worker 无 GitHub 权限，只能提供 workflow。
5. **Python 版本口径冲突（需 QA/Lead 改文档）**：`docs/60-test-plan.md` 第 23 行写 CI 用
   **Python 3.11**，而 `server/pyproject.toml` 的 `requires-python = ">=3.12"`、任务卡也写 3.12。
   本 workflow 取 **3.12**（否则后端装不上）；建议 QA 修正该行，避免后续按 3.11 复现失败。
