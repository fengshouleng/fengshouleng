# 15 · 首次 CI 运行清单（人类运维步骤）

> 目的：把「本机 shell 不可用（RISK-08）」下的静态结论，转成 CI 的真实证据。
> 这是 G3 从 PARTIAL 升级为 PASS、G4 开始计数之前的唯一路径。
> 相关：`.github/workflows/ci.yml`、`.github/workflows/README.md`、`docs/61-gate-report.md`
>
> **2026-11-10 更新**：本机已恢复（Docker 29.9.3 + Git 2.56 + Node/Python venv），
> 仓库已 `git init -b main` 并提交 6 次；`actionlint 1.7.7` 对 `ci.yml` 校验 **exit 0**；
> backend/contract/frontend 的 CI 等价命令已在本地真实跑绿。**当前阻塞只剩：创建 GitHub 远端并推送。**

## 1. 前置条件

- [ ] GitHub 仓库已创建（空仓库或已有 main 分支均可）
- [ ] 仓库 Settings → Actions 已启用
- [ ] 你有推送权限；如果需要，先建 `main` 并设为默认分支

## 2. 首次推送（按顺序执行）

```bash
cd "选择题答题系统"

# 仓库已初始化（main 分支，含 .gitattributes：sh/py/yml=LF、ps1/cmd=CRLF）
git remote add origin <你的仓库地址>
git push -u origin main
```

> 本地已有 6 次提交，`git status` 干净；`.env.staging`、`reports/`、`deploy/evidence/`、
> `deploy/release/.state/` 均被忽略。
> 注意：`web/pnpm-lock.yaml` 若仍缺失（P-004），CI 会自动退化 `--no-frozen-lockfile` 并打 notice。

## 3. 分支保护（P-005）

- Settings → Branches → Add branch protection rule
- 分支：`main`
- 勾选：Require status checks to pass before merging
- 必需检查：**`CI 汇总门`**（workflow job `ci-gate`）
- 可选：Require branches to be up to date before merging

## 4. 第一次运行预期结果

| Job | 命令 | 预期 |
|---|---|---|
| `changes` | git diff 变更集 | 首次 push 无 before sha → 全量兜底，三个 job 都跑 |
| `backend` | `pip install -e ".[dev]"` → ruff → mypy → pytest | 通过（本地已 164 passed） |
| `frontend` | `pnpm install` → lint → tsc → test → build | 通过（本地已 91 tests passed） |
| `contract` | `openapi-spec-validator` → `pytest tests/contract` | 通过（本地已 55 passed / 0 xfail） |
| `perf`（手动 Run workflow 才跑） | compose 起 postgres+api → 造数 → k6 100+100 VU | 目标 `answer_save`/`session_read` p95 <300ms；真实 ubuntu/SSD 上应通过（本机容器读 14ms/保存 200ms，仅开场 burst 428ms 是宿主存储 fsync 所致） |
| `ci-gate` | 汇总 | 全绿才显示 success（`perf` 在 push/PR 中 skipped 属预期） |

## 5. 高概率首跑失败与处置（按概率排序）

| 现象 | 可能原因 | 处置 |
|---|---|---|
| `contract` 失败于 openapi-spec-validator | 本地无法执行的 YAML/结构错误 | 把报错行号发回 R2，按 CHG 流程修订 v1.0.3 |
| `backend` ruff 失败 | 中文注释全角标点、行宽 | 已配置 RUF001-003 忽略；若仍失败看具体规则 |
| `backend` pytest 失败 | 测试与实现细节偏差（如 InputNumber 类前端问题不在此） | 只看第一个失败，逐个修；不要批量改测试迁就实现 |
| `frontend` tsc 失败 | antd/React 类型、`data-*` 透传不影响 tsc | 按报错修；必要时 FE 新开卡 |
| `frontend` vitest 失败 | `data-testid` 透传（FE-003 已知风险点） | 改断言到组件外层容器 |
| `pnpm install` 失败 | 无锁文件 + 依赖解析 | 在可执行环境执行 `pnpm install` 生成并提交 `web/pnpm-lock.yaml`（P-004） |

## 6. 运行后必须回填的文档

- [ ] [docs/61-gate-report.md](61-gate-report.md)：填入 run URL、命令、passed/failed、覆盖率
- [ ] [docs/decision-log.md](decision-log.md)：G3 状态由 PARTIAL → PASS（附 run 链接）
- [ ] 任务卡索引 `contracts/tasks/README.md`：把「运行时待 CI」统一改为「CI run #N 通过」
- [ ] `docs/00-prd.md` 的 NFR 与 `docs/60-test-plan.md` 的证据路径：改为真实 run 编号

## 7. 不能自动化、仍需人工的项

- [ ] `ci-gate` 分支保护设置（P-005）
- [x] staging 部署→回滚演练（2026-11-10 **PASS**：deploy 17s / rollback 11s，`reports/release-drill-20261110T052821Z.json`）
- [x] 备份恢复演练（DR-20261110-01，PASS，见 `docs/71`）
- [ ] G4 后的生产上线审批（DSH approval = ask，脚本不自批）
