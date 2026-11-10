# 61 · 发布门报告（模板）

> 每次门控（G2 / G3 / G4 / G5）复制本文件为一节，或用「追加」方式在文末新增区块。
> 规则（`tests/README.md`）：**每条命令必须记录命令、退出码、通过/失败数、耗时、日志路径**；
> 缺陷必须以缺陷单 ID 出现；必须有回滚点 commit。缺任一项 → 该门判 `FAIL`。

---

## 0. 报告元信息

| 字段 | 值 |
|---|---|
| 门控 | `G2 契约冻结 / G3 模块合并 / G4 发布候选 / G5 上线验收`（选一） |
| 报告版本 | `v1.0` |
| 日期（UTC+8） | `YYYY-MM-DD HH:mm` |
| 执行环境 | `CI（GitHub Actions）/ staging（Compose）/ 人工` |
| 被测版本 | commit `__________`　镜像 `__________` |
| 回滚点 | commit `__________`（上一个已知良好版本） |
| 执行人 | R5 QA（`qa-verify`） |
| 结论 | `PASS / FAIL / CONDITIONAL_PASS` |

## 1. 命令执行记录（逐条命令一行，禁止合并）

| # | 命令 | 工作目录 | 退出码 | passed | failed | skipped | xfail | 耗时 | 日志 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `python -m pytest tests/contract -q --junitxml=reports/gate-g4/contract.xml` | 仓库根 |  |  |  |  |  |  | `reports/gate-g4/contract.log` |
| 2 | `cd server && python -m pytest -q --junitxml=../reports/gate-g4/server.xml` | `server/` |  |  |  |  |  |  | `reports/gate-g4/server.log` |
| 3 | `cd web && pnpm test -- --run` | `web/` |  |  |  |  |  |  | `reports/gate-g4/web.log` |
| 4 | `python -m pytest tests/security -q` | 仓库根 |  |  |  |  |  |  | `reports/gate-g4/security.log` |
| 5 | `EXAM_E2E=1 EXAM_BASE_URL=http://127.0.0.1:8080/api python -m pytest tests/e2e -q` | 仓库根 |  |  |  |  |  |  | `reports/gate-g4/e2e.log` |
| 6 | `python -m pytest tests/perf -q`（或 `k6 run tests/perf/save_read_100.js`） | 仓库根 |  |  |  |  |  |  | `reports/gate-g4/perf.log` |
| 7 | `docker compose -f deploy/docker-compose.yml config` | `deploy/` |  |  |  |  |  |  | `reports/gate-g4/compose.log` |
| 8 | 备份恢复演练（T4.3 runbook） | staging |  | — | — | — | — |  | `reports/gate-g4/backup-restore.md` |
| 9 | 回滚演练（`docs/70-runbook.md`） | staging |  | — | — | — | — |  | `reports/gate-g4/rollback.md` |

**环境快照**（必须粘 CI job 头部）：

```
python --version           #     3.11.x
pytest --version           #     pytest 8.x
openapi-spec-validator     #     >=0.7.1
node --version             #     20.x
git rev-parse --short HEAD #     
docker compose version     #     
```

## 2. AC 覆盖摘要

| 指标 | 期望 | 实测 | 证据 |
|---|---|---|---|
| AC 映射率 | 22/22（100%） |  | `tests/contract/test_ac_matrix.py` |
| 契约用例（TC-CT-*） | 55 个（v1.0.3 同步后，0 xfail） |  | `reports/gate-g4/contract.log` |
| 接口用例 | P0 全绿 |  | `server/tests` |
| E2E 链路 | 5/5，连续 2 次 |  | `reports/gate-g4/e2e/` |
| 安全负例失败数 | 0 |  | `reports/gate-g4/security.log` |
| 性能 P95（100 并发保存/读取） | < 300ms |  | `reports/gate-g4/perf.log` |
| 备份恢复耗时 | < 30min |  | `backup-restore.md` |
| 回滚耗时 | < 15min |  | `rollback.md` |

## 3. 缺陷单

| 缺陷 ID | 关联 AC | 严重级 | 标题 | 状态 | 复现/证据 | 负责人 |
|---|---|---|---|---|---|---|
| `DEF-001` | G4/性能 | `P1` | 单 worker 部署容量不足 + 日志锁遇慢 stdout 放大（PERF-001） | `fixed`（配置层，Linux 销项待 CI） | `docs/61 §10.2`；`reports/perf/*` | R0/R6 |
| `DEF-002` | G4/发布 | `P1` | `staging_drill.sh` 失败时 JSON/终端仍打印 `result=PASS`（finalize 缺 FAIL 分支 + 回滚 0s 判失败） | `fixed` | 2026-11-10 演练日志 `reports/release/20261110-1300/staging-drill3.log`；修复 `deploy/release/staging_drill.sh` | R0 |
| `DEF-003` | G4/发布 | `P2` | `smoke.sh` 冒烟题干固定，二次执行因题库唯一约束返回 409，破坏演练可重复性 | `fixed` | `reports/release/20261110-1300/staging-drill4.log`（step 4/6 409）；修复为带时间戳后缀 | R0 |
| `DEF-004` | 环境 | `P2` | Windows Git-bash 下 `/bin/sh`、`/backup/*.sh` 参数被 MSYS 路径转换，容器 `exec` 报 `C:/Program Files/Git/usr/bin/sh` 不存在 | `fixed`（运行约定） | `reports/release/20261110-1300/staging-drill.log`；执行时设 `MSYS2_ARG_CONV_EXCL="/bin/sh;/backup"`（已写入 `docs/70 §14.2`） | R0 |
| `DEF-005` | G4/CI | `P1` | `changes` job 在「首次 push / `workflow_dispatch`」时用 `HEAD~1` 兜底，只 diff 最后一个提交 → backend/frontend/contract 全部 `skipped`，而 `ci-gate` 仍判 **success**（假绿） | `fixed` | 首次 CI run `42841073643`（push：三 job skipped、ci-gate success）；修复后由 push `f99195e` 起全量运行 | R0 |
| `DEF-006` | G4/CI | `P1` | `.gitignore` 的裸 `reports/` 把 `server/app/reports/`、`server/tests/reports/` 一并忽略 → CI clone 后 mypy 报 `app.reports` import-not-found（本地因文件在磁盘上而"假绿"） | `fixed` | CI run `42841208142` mypy 2 errors；改根锚定 `/reports/` 后 backend 三 job 全绿（run `42848561341`） | R0 |
| `DEF-007` | G4/CI | `P2` | 前端 `admin.test.tsx` 在 CI 上元素 1381ms 才渲染，`findByTestId` 默认 1s 超时 → 偶发失败 | `fixed` | CI run `42841208142` frontend 失败；`web/tests/setup.ts` 加 `asyncUtilTimeout: 5000` | R0 |
| `DEF-008` | G4/CI | `P2` | perf job 未创建 `PERF_REPORT_DIR`，k6 无法写 `summary.json`，artifact 只剩日志 | `fixed` | CI perf run `42847410502` 日志 `could not open reports/perf/ci/summary.json`；k6 前加 `mkdir -p` | R0 |

**放行规则**：P0/P1 缺陷数必须为 0；P2 允许存在但需写明规避方案与修复版本。

## 4. 契约缺陷（QA 只报不修，处置权在 R2）

来源：`docs/60-test-plan.md` §8；下列状态由 R2 回填。

| 缺陷 ID | 一句话 | 关联 AC | 状态 | R2 处置 / ADR |
|---|---|---|---|---|
| C-01 | 会话无答案回读通道，断线恢复不可证 | AC-12 | `open` |  |
| C-02 | 学生无考试发现接口 | 主链路 | `open` |  |
| C-03 | `Error.code` 未枚举，错误码无法断言 | AC-02/07/11/13/14/15/19/21 | `open` |  |
| C-04 | `Exam` 缺 `created_by` 归属字段 | AC-10/RBAC | `open` |  |
| C-05 | `AuditLog.actor_id/ip` 可选，缺 180 天口径 | AC-21/22 | `open` |  |
| C-06 | `MyResultItem` 缺 `session_id` | 成绩导航 | `open` |  |
| C-07 | 登录失败语义未固化 | AC-02 | `open` |  |
| C-08 | 分值下界/合计 100 口径缺失 | AC-09 | `open` |  |
| C-09 | CSRF 失败无 403/`CSRF_INVALID` | AC-03/22 | `open` |  |
| C-10 | `QuestionCreate` 未要求 `explanation` | AC-05 | `open` |  |
| C-11 | `ImportResult` 无去重计数、`total` 口径未定义 | AC-08 | `open` |  |
| C-12 | 试卷快照不可见（无 `frozen_at/snapshot_version`） | AC-10 | `open` |  |
| C-13 | `EXAM_EXPIRED` 自动交卷结果不可见 | AC-14 | `open` |  |
| C-14 | `partial_rule` 非必填/无默认、分数无 `multipleOf: 0.01` | AC-17 | `open` |  |
| C-15 | submit 同时 200/409，幂等语义矛盾 | AC-13 | `open` |  |
| C-16 | 旧 seq 被忽略无 `applied` 标志 | AC-15 | `open` |  |
| C-17 | 403 vs 404 防枚举口径未定 | AC-03/18 | `open` |  |
| C-18 | 考试状态机迁移未约束 | 状态机 | `open` |  |
| C-19 | 错误响应不一致（PATCH 缺 422、healthz 无 503） | 全链路 | `open` |  |
| C-20 | `SessionState`/`SubmitResult`/汇总 status 枚举不一致 | AC-14 | `open` |  |
| C-21 | `additionalProperties` 全开，无输出白名单 | NFR | `open` |  |
| C-22 | 会话接口 200/201/409 未区分状态 | AC-11/13/14 | `open` |  |

**更新（2026-11-09）**：上表 22 条已**全部处置**——v1.0.1（CHG-001..023，ADR-009）、v1.0.2（CHG-024..032，ADR-010）、v1.0.3（CHG-033..036，ADR-011）；`tests/contract` 现 **0 个 xfail**。
上表「状态」列保留为首次审查快照；最新口径以 [docs/13-contract-changes.md](13-contract-changes.md) 与 `contracts/openapi.yaml` v1.0.3 为准。

## 5. 自检（2026-11-09 会话结束时状态，已被 §10 更新）

> ⚠️ 本节为历史快照：2026-11-10 已在本机完成首次真实执行，结果见 **§10**。下方「未验证」清单中 contract/backend/frontend 已转绿。

| 项 | 值 |
|---|---|
| 执行环境 | 本机（会话全程） |
| shell 状态 | **不可用**：`pwsh` 退出码 `0xC0000142`（DLL 初始化失败，RISK-08 已发生） |
| 已执行命令 | 无（按任务卡要求，**禁止**尝试运行 pytest 等命令） |
| 结论 | `verified = false`：所有“通过/失败”数字留空，由 CI 首次运行回填 |
| 已验证（只读静态核对） | 契约 v1.0.3 与 55 个契约用例逐条核对；安全套件新增 10 例（draft 403 硬断言）；E2E 4 条主链路与 testid 对齐；性能 k6 脚本与阈值；实现侧 404/403 判定链、healthz `db`、审计 `ip="-"` 静态核对一致 |
| 未验证（必须 CI 补） | `openapi-spec-validator` 对 v1.0.3 的判定；`pytest tests/contract`、`server/tests`、`tests/security`；`pnpm lint/tsc/test/build`；Playwright E2E；k6 性能；`docker compose config`；备份恢复演练；staging 上线回滚演练 |

**回填后必须满足**：`tests/contract` → `0 failed, 0 xfail`；`tests/security` → `0 failed`（仅允许 1 个已知缺口 xfail：越权尝试未审计）；E2E 4/4；性能 P95 < 300ms；恢复 < 30min；回滚 < 15min。

## 6. 回滚点与回滚验证

| 项 | 值 |
|---|---|
| 回滚 commit | `fdb3a64`（演练时工作区 HEAD；演练镜像 tag `drill-20261110T052626Z-base`） |
| 回滚方式 | `docs/70-runbook.md` §14.4：镜像 tag 回退 + 健康检查 + 冒烟（本次演练无 SQL 迁移，`db_action=none`，DB 版本 0006→0006） |
| 数据兼容 | 本次是否含破坏性迁移：**否**；回滚不需要恢复备份（演练 `db_action=none`） |
| 回滚实测耗时 | **11s** ≈ **0.2 min**（判据 < 15min ✅；来源 `reports/release-drill-20261110T052821Z.json`） |
| 回滚后冒烟 | ✅ 六步冒烟（healthz / 登录 / me / 建题 / 回读 / 软删）全过；`smoke_after_rollback result=PASS` |

## 7. 签字

| 角色 | 姓名 | 结论 | 时间 |
|---|---|---|---|
| R5 QA |  |  |  |
| R0 Lead |  |  |  |
| 人类 PO（G4/G5） |  |  |  |

## 8. 附录：证据清单

- `reports/gate-g4/contract.xml`、`server.xml`、`security.xml`、`e2e.xml`（junit，含 passed/failed/skipped）
- `reports/gate-g4/e2e-01..05/`：截图 + `trace.zip`（失败时 `--tracing retain-on-failure`）
- `reports/gate-g4/perf.log`：并发数、P50/P95/P99、错误率
- `reports/gate-g4/backup-restore.md`：备份文件大小、恢复起止时间、校验查询
- 缺陷单链接：`__________`
- 复跑命令：见 §1；复跑须同 commit、同镜像 tag，否则结果不可比

## 9. G4 预检清单（待执行，按顺序）

> 一键执行：`./scripts/verify_all.ps1`（Windows）或 `./scripts/verify_all.sh`（Linux/macOS），
> 覆盖第 1–8 步并输出 `reports/verify-<timestamp>/NN-*.log`；第 9–11 步（备份恢复、staging 演练、compose config）仍需单独执行。
> 全部命令必须在**同一 commit + 同一镜像 tag** 下执行并留日志；任一项失败即 G4 = FAIL，不进 G5。

| # | 命令/动作 | 通过判据 | 失败处置 |
|---|---|---|---|
| 1 | `openapi-spec-validator contracts/openapi.yaml` | 退出码 0 | 回 R2 按 CHG 修，升版本 |
| 2 | `python -m pytest tests/contract -q` | 0 failed / 0 xfail | 断言未同步找 QA；契约问题找 R2 |
| 3 | `cd server && pytest -q`（含 ruff/mypy） | 全绿 | 按失败用例回对应 BE 卡 |
| 4 | `cd web && pnpm lint && pnpm tsc --noEmit && pnpm test && pnpm build` | 全绿 | 按失败文件回对应 FE 卡 |
| 5 | 起栈（compose 或 uvicorn+vite）+ `python -m app.cli seed-demo` + `create-admin` | 账号可用、管理员存在 | 查 env/迁移 |
| 6 | `EXAM_API_URL=... python -m pytest tests/security -q` | 0 failed（1 个已知 xfail 允许） | 安全缺陷按 P0/P1 处理 |
| 7 | `EXAM_E2E=1 ... python -m pytest tests/e2e -q` | 4/4 链路通过（04c 条件 xfail 允许） | 前端/后端联调缺陷 |
| 8 | `k6 run tests/perf/k6/exam_flow.js`（100 VU） | P95 < 300ms、错误率 < 0.1% | 定位瓶颈后再决定是否优化 |
| 9 | `bash deploy/backup/restore_drill.sh` | 恢复 < 30min 且数据比对一致 | 修备份脚本后重跑 |
| 10 | `bash deploy/release/staging_drill.sh --env staging` | 部署→冒烟→回滚→冒烟全通过、回滚 < 15min | 修脚本/配置后重跑 |
| 11 | `docker compose config -q` | 退出码 0 | 修 compose |
| 12 | 汇总 §1–§5 与证据路径 → 本报告 §7 签字 | QA + R0 + 人类 PO 签字 | — |

## 10. G3 运行时证据（2026-11-10 首次真实执行）

> 执行方式：本机 `scripts\verify_all.cmd`（Windows PowerShell 5.1；Python 自动创建 `.venv`；pip 走清华镜像、npm 走 npmmirror）。
> 产物目录：`reports/verify-<timestamp>/`；本会话最初 `pwsh` 不可用（RISK-08），由用户在 2026-11-09 23:32 – 2026-11-10 00:40 完成环境搭建与多轮修复，最终运行 **RESULT: PASS (all steps green)**。

| 步骤 | 结果 | 关键证据 |
|---|---|---|
| preflight-tools | ✅ | python / node / pnpm 版本正常打印 |
| openapi-validate | ✅ | `contracts/openapi.yaml: OK` |
| contract-tests | ✅ | **55 passed**（0 failed / 0 xfail） |
| backend-ruff | ✅ | `All checks passed!` |
| backend-mypy | ✅ | `Success: no issues found in 61 source files` |
| backend-pytest | ✅ | 退出码 0（pytest 失败必然非 0；该步日志编码混杂，具体条数建议用 `--junitxml` 复跑确认） |
| frontend-lint | ✅ | 无错误 |
| frontend-tsc | ✅ | 无错误（`tsc --noEmit`） |
| frontend-test | ✅ | **91 passed (8 files)** |
| frontend-build | ✅ | `built in 3.22s` |

**期间的人工修复（非 Worker 产出，以当前工作区为准）**：mypy（`sqlalchemy.TypeEngine` 导入、`cleanup.rowcount` 取法）、ruff 6 处、前端 eslint `prefer-const`、`web/tests/setup.ts` 的 antd/jsdom 桩、`exam.test.tsx` mock 类型与若干测试断言。
这些修改**未经 G3 复核**，但已在同一环境跑绿；后续任何改动仍需重跑 `verify_all`。

### 10.1 G4 实测证据（2026-11-10）

| 项 | 结果 | 证据 |
|---|---|---|
| 后端单测 | ✅ **164 passed / 0 failed** | `reports/backend-junit.xml` |
| 安全负例 | ✅ **134 tests / 0 failed / 1 skipped / 1 xfailed**（跳过项为 `EXAM_HEALTHZ_503=1` 条件用例） | `reports/security-junit.xml`、`reports/g4-20261110/security3.log` |
| E2E | ✅ **5 passed / 5 skipped / 1 xfailed**：5 条 UI 主链路全绿；5 条为旧 API 骨架（`EXAM_E2E_API` 未开启，设计内跳过）；04c 真实到期为条件 xfail（2026-11-10 复跑 24.03s，证据 `reports/e2e/20261110-091432/`） | `reports/e2e-junit.xml`、`reports/e2e/20261110-091432/` |
| PostgreSQL 迁移 | ✅ 0001→0006 全部成功（PG 16.4，含 GIN 索引） | 本次容器执行日志 |
| 备份 | ✅ dump 33900 bytes、TOC 90、sha256 校验通过 | `reports/drill-backups/exam_20261109T175350Z.dump` |
| 恢复演练 | ✅ **PASS**：restore 0s（<1800s）；行数 users 4/4、questions 3/3、papers 1/1、exams 0/0、exam_sessions 0/0；临时库已删 | `reports/drill-backups/drill-logs/backup-20261109.log` |
| compose config | ✅ exit 0（`--env-file deploy/.env.example`） | 本次执行 |
| 性能冒烟（k6 100 写 + 100 读 VU / 3m） | ⚠️ **两轮代码优化后**：CI 4 vCPU 上 read p95 **19.96–37.5ms ✅**、p50 start 310→**166ms**、p50 save 99→**32ms**、失败率 0；但开场 burst/保存 p95 仍 **500–1176ms ❌**（4 vCPU CPU 争用；同配置 run 间波动 711–1176ms）。单请求服务端 CPU 已压到 2.7–5.2ms（§10.2.10）→ 结论：**需 ≥8 vCPU 或评审拆分 burst 与稳态口径** | `reports/perf/**`、`reports/ci/run-*/`、CI run `42859094367` artifact |
| staging 部署→回滚演练 | ✅ **PASS**（2026-11-10 13:28 本机 Docker）：deploy **17s** → smoke **1s** → rollback **11s**（≤900s）→ smoke **1s**，总 **31s**，`steps` 四步全 PASS | `reports/release-drill-20261110T052821Z.json`、`deploy/evidence/release-drill-staging-20261110T052821Z.log` |

**期间修复（测试缺陷，非实现缺陷）**：`test_audit_system_record_has_null_actor_and_dash_ip` 曾把 `auth.login_failed`（`actor_id=None` 但 ip 为真实地址）误当系统记录，导致偶发失败；已改为只筛 `action=user.create` 且 `actor_id=None` 的记录。

**G4 仍缺（性能容量，需产品或选型决策）**：
1. ~~staging 部署→回滚演练~~ ✅ 已完成（四步 PASS、rollback 11s）；
2. ~~CI 三 job 首跑~~ ✅ 已完成（run `42848561341`：backend 76s / frontend 78s / contract 16s / ci-gate 绿；期间修复 DEF-005/006/007）；
3. **性能门槛仍未通过**：已完成两轮代码优化（§10.2.9/§10.2.10，含逐请求剖析），CI 4 vCPU 上 read p95 **19.96–37.5ms ✅**、p50 start **310→166ms**、p50 save **99→32ms**、失败率 0；但开场 burst 与保存 p95 仍 **500–1176ms ❌**。单请求服务端 CPU 已到 2.7–5.2ms 低位，4 vCPU（且与 k6/PG 共享）上 p95 结构性达不到 300ms，需要：
   - **选型**：生产按 ≥8 vCPU、`WEB_CONCURRENCY≈vCPU` 部署，在达标 runner 上重跑 `perf` job 销项；或
   - **门槛口径评审**：把「同秒开考 burst（建会话）」与「稳态保存/读取」分开度量（`answer_save` 目前把 100 个 start 混入 p95）。**需 R0/QA 批准，不得私自改阈值。**
4. 完成 3 之后，在满足选型的 runner 上重跑 `perf` job 销项。

**结论**：功能、契约、安全、E2E、备份恢复、compose、**staging 演练、CI 三 job 首跑**均有真实运行证据；**性能已完成两轮真实优化并把单请求 CPU 压到 2.7–5.2ms（读 301→20ms、start p50 310→166ms、save p50 99→32ms、失败率 0），但 4 vCPU 容量下开场 burst 的 p95 未达门槛**。G4 = **CONDITIONAL（性能容量/口径待决策）**。

### 10.2 PERF-001 性能缺陷记录（G4 阻断项）— 2026-11-10 原生 PG 复测与修复

**结论先行**：PERF-001 是**真实的应用容量问题（单 worker 部署 + 每个请求的 CPU 成本）叠加两处环境放大因素**，不是纯粹的 Docker 端口代理问题。修复项（多 worker 启动、池按 worker 摊分）已合入部署配置；在参考机上 **12 worker + 快提交（`synchronous_commit=off`）时全部门槛 PASS**；`fsync=on` 时写/开场尾延迟 300–520ms，尚未在 Linux/SSD 实测。

#### 10.2.1 定位过程（三轮，逐轮排除一个变量）

| 轮次 | 环境 | 观测 | 结论 |
|---|---|---|---|
| 1 | API 日志被 PowerShell / `docker compose logs` 管道捕获 | `POST /exams/1/sessions` 中位 10.6s、P95 40s；大量 `QueuePool limit ... timeout 10.00` 500 | `AccessLogMiddleware` 的模块级 `_WRITE_LOCK` + **每行 `flush()`** 把请求串行化在慢速 stdout 上 → **测量假象**，但同时是真实运维风险（RISK-15） |
| 2 | 原生 PG 16.4 + 单 uvicorn worker（= 镜像默认），API stdout 落文件 | session_read p95 **596ms**、answer_save p95 **20.9s**、失败率 0.36%；PG 进程 CPU 仅 2.5s | **应用单进程单核 CPU 饱和**：200 VU 下吞吐 92 req/s。读路径随 worker 数线性改善 → 部署容量问题 |
| 3 | 同一原生栈，改 worker 数 / 提交模式 | save 尾延迟对 worker 数不敏感（4→12 worker：589→523ms），`synchronous_commit=off` 后骤降到 217ms | **开场 100 并发建会话 + 交卷的 WAL fsync 串行**是本机（NTFS）写尾延迟主因，属存储/OS 属性 |

> 更正（两次）：首测曾把 10–40s 延迟**全部**归因于 Docker Desktop 端口代理；原生复测（§10.2.2）证明归因不完整，Docker 修复后的复测（§10.2.6）进一步证明**端口代理在健康环境下不是瓶颈**。30s 级延迟由「日志锁 + 单 worker + 提交落盘」共同造成。

#### 10.2.2 实测矩阵（原生 PG 16.4；Windows 11 / i5-13600KF 20 逻辑核；k6 同机；100 写 + 100 读 VU、3min）

| # | 配置 | answer_save p95 | save_p95（仅保存请求） | session_read p95 | 失败率 | 判定（门槛 300ms / 0.1%） |
|---|---|---|---|---|---|---|
| 1 | 1 worker，fsync=on | 20935ms | 20984ms | 596ms | 0.36% | FAIL |
| 2 | 4 workers，fsync=on | 589ms | 450ms | 82ms | 0 | FAIL（save） |
| 3 | 8 workers，fsync=on | 571ms | 330ms | 62ms | 0 | FAIL（save） |
| 4 | 12 workers，fsync=on | 523ms | 301ms | 38ms | 0 | FAIL（save，差 1ms） |
| 5 | 4 workers，sync=off | 420ms | 228ms | 38ms | 0 | FAIL（开场 burst 排队） |
| 6 | **12 workers，sync=off** | **217ms ✅** | **67ms ✅** | **34ms ✅** | **0 ✅** | **PASS（全门槛）** |

证据目录：`reports/perf/native-20261110-0925/`（#1）、`native-w4-20261110-0935/`（#2）、`native-w8-20261110-0945/`（#3）、`native-w12-20261110-0950/`（#4）、`native-w4-syncoff-20261110-1000/`（#5）、`native-w12-syncoff-20261110-1000/`（#6，各含 `summary.json` / `k6.log`）。复现手册见 `docs/75-perf-baseline.md`。

#### 10.2.3 修复（已合入，部署配置层）

| 文件 | 变更 | 目的 |
|---|---|---|
| `deploy/docker-entrypoint.sh` | 无参数启动时加 `--workers "${WEB_CONCURRENCY:-1}"` | 让镜像能吃满多核；默认仍为 1（向后兼容），由 compose 注入 |
| `deploy/docker-compose.yml` | api 增加 `WEB_CONCURRENCY=${WEB_CONCURRENCY:-4}`、`DB_POOL_SIZE=${DB_POOL_SIZE:-10}`、`DB_MAX_OVERFLOW=${DB_MAX_OVERFLOW:-10}` | 4 worker × 20 连接 = 80 < postgres 默认 `max_connections=100`；容量与连接预算显式化 |
| `deploy/.env.example` | 新增三变量与「workers × (size+overflow) < 100」约束说明 | 避免 staging/prod 调参踩连接上限 |
| `server/app/db.py`、`server/app/settings.py` | （前一轮）池参数可配置，默认 20/30/10/1800 | 可按 worker 摊分连接预算 |

校验：`deploy/docker-compose.yml` 用 PyYAML 解析通过（四服务齐全、api env 含新键）；**Docker 已损坏，`docker compose config` 未能复跑**，该步在 Linux/CI 首跑时补。

#### 10.2.4 剩余项与建议

1. **Linux/CI 复核（必须）**：用 `docs/75-perf-baseline.md` 在 fsync=on 的 Linux + SSD 上跑同一 k6，确认 save p95 < 300ms（本机 fsync 偏慢是已知环境差异，不可用 `synchronous_commit=off` 冒充通过）。
2. worker 数按核数定：参考机（20 逻辑核）取 12 才全绿；4 核机器建议 4，并预期读路径 ~80ms、写尾仍受存储 fsync 影响。
3. 开场 burst（100 学生同一秒开考）是写尾的主要来源：若要进一步压低，可评估「建会话响应裁剪/预热」等产品侧优化（需契约评审，当前不做）。
4. RISK-15：`access_log` 全局锁 + 逐行 flush 在慢 stdout 下会串行化全部请求；生产需保证日志消费者不阻塞（detached + json-file 限额），长期可改为队列异步写。

#### 10.2.5 复测门槛

`http_req_failed < 0.1%`、两个场景 P95 < 300ms（`tests/perf/k6/exam_flow.js` 阈值）。判定只看 k6 `summary.json`，不接受口头「差不多」。

#### 10.2.6 追加证据（2026-11-10 12:30 起，Docker/WSL 修复后）

Docker Desktop 恢复可用（engine 29.9.3、`docker compose config` exit 0）后，对**首测的环境归因**做了直接复测，结论再次修正：

| 复测项 | 方法 | 结果 |
|---|---|---|
| host→container 端口代理并发连接 | `postgres:16.4-alpine` 发布 `56432`，宿主 psycopg 并发建 50 条连接 | **50/50 成功，总耗时 0.10s，最慢 71.7ms** |
| compose 配置（含 §10.2.3 容量改动） | `docker compose --env-file deploy/.env.staging config -q` | **exit 0**，`WEB_CONCURRENCY/DB_POOL_SIZE/DB_MAX_OVERFLOW` 正确渲染 |

**结论修正**：首测「50 个并发连接全部 ConnectionTimeout」不是 Docker 的固有上限，而是当时 **WSL/Docker 已进入损坏状态**的表现。因此：
- PERF-001 的真正原因 = **单 worker CPU 容量 + `access_log` 全局锁遇慢 stdout + 本机 NTFS 提交落盘**；
- 端口代理在健康环境下不是瓶颈 → Linux/CI 上「宿主 k6 → 发布端口 → 容器 api」的压测路径可行（CI `perf` job 按此设计，见 `.github/workflows/ci.yml`）。

#### 10.2.7 容器内实测（2026-11-10 12:50，fsync=on，8 worker，Docker Desktop on Windows）

拓扑：`postgres:16.4-alpine` + `exam-api`（本次 Dockerfile 构建，`WEB_CONCURRENCY=8 / DB_POOL_SIZE=5 / DB_MAX_OVERFLOW=5`），k6 v0.49.0 直接跑在 **api 容器内**（`127.0.0.1:8000` loopback），100 写 + 100 读 VU、3min。**这是第一份「Linux 容器 + fsync=on」的真实数据。**

| 指标 | 结果 | 门槛 | 判定 |
|---|---|---|---|
| `session_read` p95 | **14.2ms**（p90 9.9ms / p99 38.9ms） | <300ms | ✅ |
| `save_p95`（仅保存请求） | **199.7ms**（p90 148.5ms / p99 359ms） | <300ms | ✅ |
| `answer_save` p95（含开场 100 个会话创建） | **428.4ms**（p90 319ms / p99 593ms / max 749ms） | <300ms | ❌ |
| `http_req_failed` | 0.09% | <0.1% | ✅（贴线） |

**解读**：
- 读路径与保存路径在容器内均达标，且比原生 Windows 好一个量级（read 14.2ms vs 38ms）→ **多 worker + 容器网络内的应用容量没有问题**；
- 唯一超标的是**开场 burst**（100 名学生同一秒建会话）：每条 = INSERT session + audit 行 + COMMIT，p99 593ms；
- 该 tail 在**本机原生（NTFS）与 Docker Desktop 容器（VM 磁盘仍落在 Windows NTFS 的 VHDX 上）**都出现，说明瓶颈是**宿主存储的 flush 延迟**，不是应用逻辑，也不是容器本身；
- 因此**本机无法代表真实 Linux SSD**：要把 `answer_save` p95 压到 300ms 以下，必须在一台真实 Linux/SSD runner（GitHub Actions ubuntu 或云主机）上跑 CI `perf` job 或 `docs/75 §6` 命令。

证据：`reports/perf/docker-container-20261110-1250/`（`summary.json`、`k6-container.log`）。

> 诊断补充（同一容器，仅把 PG 改为 `synchronous_commit=off`，`reports/perf/docker-container-syncoff-20261110-1255/`）：
> `answer_save` p95 **252ms ✅**、`session_read` p95 **21.9ms ✅**、保存 p95 **229ms ✅**、失败率 **0 ✅**。
> 同一拓扑、同一数据量，仅去掉提交落盘 → 开场 burst 从 428ms 掉到 252ms，**确认写尾就是 WAL fsync 串行**（该配置只用于诊断，不得用于生产判定）。

#### 10.2.8 GitHub Actions 上的真实 Linux runner 实测（2026-11-10/11，perf job）

`runs-on: ubuntu-latest`（GitHub 托管，4 vCPU）；拓扑 = compose 起 `postgres` + 构建的 `exam-api`（发布 18000），k6 在 runner 宿主上压测；100 写 + 100 读 VU / 3min。

| run | commit / 配置 | session_read p95 | 保存 p95 | answer_save p95（含开场 burst） | 失败率 | 备注 |
|---|---|---|---|---|---|---|
| `42847410502` | `22cd4c6`，`WEB_CONCURRENCY=8` | **90.4ms ✅** | 510ms ❌ | **1601ms ❌**（`session_start` p95 1791ms） | 0.13% ❌（21 次 EOF + 21 次读场景建会话失败） | 8 worker 在 4 vCPU 上过订阅；k6 无法写 summary（DEF-008） |
| `42849133088` | `75d9112`，`WEB_CONCURRENCY=4` | **301.6ms ❌**（p90 250ms） | 860ms ❌ | **会话建连 p95 1173ms** | **0 ✅** | 无 EOF，但 4 worker 处理 200 VU 仍不足；artifact 完整（summary + 容器日志） |
| `42851587298` | `4af7b38`（会话序列化优化后），4 worker | **19.96ms ✅** | **349.3ms ❌** | **503.9ms ❌**（`session_start` p95 710.9ms） | **0 ✅** | 优化见效：读 15×、开场 1.65× |
| `42853200977` | `e2deea7`，8 worker | **43.8ms ✅** | 664.8ms ❌ | 1021.5ms ❌（`session_start` p95 1409ms） | 0 ✅ | 8 worker 在 4 vCPU 上再次过订阅，确认 **4 worker 更优** |
| `42857195723` | `90bb69d`（第二轮：合并查询/跳过空 answers），池 5+5 | 24.2ms ✅ | **p50 32.4ms / p95 10.2s ❌** | p50 166ms / p95 10.2s ❌ | 0 ✅（k6 侧） | **80 次 `QueuePool(5+5) timeout`** 把 p95 拖到 10–20s：优化后并发压力更大，池成了新瓶颈 |
| `42859094367` | `e2487a6`，池 12+12 | **37.5ms ✅** | 1006ms ❌ | 1115ms ❌（start p95 1176ms） | **0 ✅** | 无 pool timeout；4 vCPU 上 run 间方差大（同配置 p95 711ms vs 1176ms） |

#### 10.2.9 会话响应路径优化（2026-11-11）与容量结论

**优化内容**（已合入 `server/app/sessions/`）：
1. `POST /exams/{id}/sessions`、`GET /sessions/{id}` 直接返回 `Response(state.model_dump_json())`，
   跳过 FastAPI 对 100 题大对象再做一次 `response_model` 校验 + 编码（实测输出与默认路径**逐字节一致**）；
2. `service._questions_for()`：未启用乱序时按 `(paper_id, exam_id)` 缓存已构建的 100 个
   `SessionQuestionOut`（LRU 32），不再每请求重建。

**效果（同为 GitHub 4 vCPU runner，4 worker）**：`session_read` p95 **301.6 → 19.96ms**（15×），
`save_p95` **860 → 349ms**，`session_start` p95 **1173 → 711ms**，失败率保持 0。

**第二轮优化（2026-11-11 01:00，`90bb69d`）与剖析结论**：
1. 开考建会话：`exam` + 本人会话合并为一条 outer join（少 1 次 DB 往返）；新建会话跳过 answers 查询；
2. 读会话：会话 + 考试合并为一条 join（读路径每请求少 1 次往返）；
3. `db.reset_engine()` 清空题面缓存，避免测试跨库 paper_id 撞缓存。

本机 in-process 剖析（无网络、20 核空载）确认**单请求服务端 CPU 已到低位**：
`GET /sessions/{id}` **2.7–3.2ms**、`POST /exams/{id}/sessions`（新建）**3.1–3.4ms**、`PUT answers`（10 题）**≈5.2ms**
（cProfile：GET handler 1.7ms、每次 SQLAlchemy execute ≈0.44ms、commit ≈2.1ms）。

CI 侧的 p50 变化证明优化有效：**start 310→166ms、save 99→32ms、read 6.7→5.1ms**（run `42857195723`）；
但该轮**连接池 5+5 触发 80 次 timeout**（优化后并发压力更大），已把 CI 池提到 12+12（run `42859094367`，0 timeout）。

**最终判断**：单请求 CPU 已约 3–5ms，开场 100 并发在 4 vCPU（还要与 k6、PG 共享）上理论下界即 ≈100×4ms/2.5核 ≈ 160ms，
叠加 commit fsync 与方差，**p95 在 4 vCPU 上无法稳定 <300ms**；不同 run 同配置 p95 在 711–1176ms 间波动也印证是 CPU 争用而非代码热点。
要到门槛需 **≥8 vCPU 选型**，或评审把「同秒开考 burst」与「稳态保存/读取」拆分度量。

**容量结论**：优化后剩余瓶颈是**开场 100 并发建会话的 CPU 排队**。按 4 worker / 100 并发推算
单次 start ≈ 12ms CPU（占满 4 核 ≈ 300ms），因此：
- 4 vCPU runner 上 `session_start` p95 理论下限已接近 300ms，**无法稳定压到 300ms 以下**；
- 8 worker 反而更差（1409ms）→ 不是 worker 数问题，是核数问题；
- 若生产按 **≥8 vCPU** 部署（`WEB_CONCURRENCY≈vCPU`），有望进入门槛；这需要在达标的 runner 上验证。
- 另一条路径是**评审门槛口径**：把「同秒开考 burst」与「稳态保存/读取」分开度量（`answer_save` 目前把 100 个 start 混进 p95，而 start 是幂等建会话、与“作答保存”是两个语义）。**该改动必须 R0/QA 评审，不得私自调阈值。**

**解读**：
- 读路径在 8 worker 时 90ms 达标，4 worker 时 302ms 贴线失败 → **4 vCPU 是当前瓶颈**，而 8 worker 虽压低延迟却在 4 核上过订阅导致连接被重置（EOF，0.13% 失败率）。
- 开场 100 并发建会话在 4 vCPU 上稳定 1.1–1.8s，即使最优本机 20 核也只能到 428ms（§10.2.7）→ **`p95<300ms` 的开场 burst 在当前架构 + 4 vCPU 上不可达**。
- 结论：这不是"配置一调就绿"的问题，需要**两条同时做**：① 生产按 ≥8 vCPU 选型（`WEB_CONCURRENCY≈vCPU`）；② 优化开场建会话的每请求 CPU（缓存冻结题面、认证上下文复用、响应序列化）。在完成前，G4 性能项保持 **CONDITIONAL（容量）**，不得写成通过。

### 10.3 CI 首跑与流水线缺陷（2026-11-10/11）

| run | 事件 | 结论 | 说明 |
|---|---|---|---|
| `42841073643` | push（首推） | success（**但假绿**） | backend/frontend/contract 全 skipped，ci-gate 仍 success → DEF-005 |
| `42841208142` | push | failure | 真实暴露 DEF-006（mypy 找不到 `app.reports`）与 DEF-007（前端 1s 超时） |
| `42846874520` | push | **success** | backend/frontend 绿；contract skipped（本次未改契约路径）；DEF-005/006/007 已修 |
| `42848561341` | push | **success（三 job 全跑）** | backend 76s / frontend 78s / contract 16s / ci-gate 4s → **CI 三 job 首跑全绿** |
| `42847410502` | workflow_dispatch | failure | perf job 首跑失败（容量，见 §10.2.8） |
| `42849133088` | workflow_dispatch | failure | perf job 第二跑（4 worker）：失败率 0，但延迟未达标 |
| `42851333266` / `42852784487` | push | success | 会话优化 / perf 环境调参两次 push run（backend 绿） |
| `42851587298` | workflow_dispatch | failure | perf 三跑（优化后 4 worker）：read 19.96ms ✅、save 349ms、burst 711ms、失败率 0 |
| `42853200977` | workflow_dispatch | failure | perf 四跑（优化后 8 worker）：证明 8 worker 更差，已回退 4 worker |
| `42857195723` | workflow_dispatch | failure | 第二轮优化（合并查询/跳过空 answers）：p50 start 166ms / save 32ms / read 5.1ms；80 次 pool timeout 拖垮 p95 |
| `42858654549` | push | success | 池 12+12 提交的 push run |
| `42859094367` | workflow_dispatch | failure | 池 12+12：无 timeout，read 37.5ms ✅、save p95 1006ms、start p95 1176ms（4 vCPU 方差） |
| `42863847703` | workflow_dispatch | failure | **启动预热 + 冻结快照缓存**（`11a1886`）：backend/frontend/contract 全绿；read p95 **19.8ms ✅**、save p95 538ms（−46%）、start p95 796ms（−32%）、失败率 0；跳过 warm-up 窗口后服务端 save p95 **264.6ms ✅** → 仅剩开场/首波 burst（见 §10.4.5） |

> 全部 run 均在 `https://github.com/fengshouleng/fengshouleng/actions`；artifacts：perf job 上传 `reports/perf/**`（k6 日志 + summary.json + 失败时容器日志）。

### 10.4 服务端侧延迟拆解与冷启动修复（2026-11-11）

#### 10.4.1 为什么还要拆一次：k6 数字可能含压测机排队

`perf` job 里 k6、postgres、api、docker 共享同一台 **4 vCPU** runner。k6 的
`http_req_duration` 从「VU 发请求」开始计时，因此**压测机自身被 CPU 饿住时的调度延迟也被算进 P95**，
这类失败无法区分「应用慢」与「测量方式不对」。取巧的做法是改阈值，正确做法是拿到**服务端视角**的分布。

`app/middleware/access_log.py` 本来就对每个请求在响应结束时用 `time.perf_counter()` 记了
`latency_ms`（不含网络与客户端排队），perf job 失败时会把容器日志归档为 artifact。于是新增
`tests/perf/analyze_server_latency.py`，直接从归档日志重建服务端侧 P50/P95/P99，并按 5s 时间桶拆开。

```bash
python tests/perf/analyze_server_latency.py \
  reports/ci/run-42859094367/perf-42859094367-1/ci/compose-logs.txt \
  --json reports/perf/server-side-latency-42859094367.json
```

#### 10.4.2 结论一：失败不是客户端假象（服务端真的慢）

来源 run `42859094367`（`e2487a6`，4 worker、池 12+12），35 840 行 access 日志：

| 路由族 | 样本 | p50 | p90 | p95 | p99 | max |
|---|---|---|---|---|---|---|
| `PUT /sessions/{id}/answers`（门槛项） | 580 | 110.3ms | 951.0ms | **1026.5ms** | 1152.3ms | 1199.8ms |
| `GET /sessions/{id}`（读门槛项） | 34 705 | 10.5ms | 23.6ms | **38.5ms** | 149.0ms | 1131.9ms |
| `POST /exams/{id}/sessions`（开场/恢复） | 200 | 754.6ms | 1008.7ms | **1044.3ms** | 1079.5ms | 1452.6ms |
| `POST /auth/login` | 105 | 321.7ms | 360.1ms | 371.1ms | 679.0ms | 686.2ms |
| 其他（健康检查等） | 250 | 35.2ms | 66.5ms | 128.2ms | 252.9ms | 319.5ms |

- **读路径服务端 p95 = 38.5ms，已达标**；写路径与开场是真慢，所以 k6 判红是对的；
- 顺带确认：本机空载时这些接口只有 3–5ms CPU（§10.2.9），**同一个请求在 4 vCPU 争用下变成 10–100×**。

#### 10.4.3 结论二：尾部一半来自「冷启动」，不是稳态容量

按 5s 桶看 save（每 30s 一波 100 并发保存）：

| 时间桶 | count | p50 | p95 | max |
|---|---|---|---|---|
| t≈20s（第一波） | 100 | **766.2ms** | 1060.4ms | 1105.6ms |
| t≈50/55s | 37 / 63 | 147.8 / 975.2ms | 702.4 / 1169.4ms | — |
| t≈80/85s | 39 / 61 | 54.2 / 195.7ms | 110.6 / 430.1ms | — |
| t≈110/115s | 36 / 64 | 31.4 / 67.8ms | 65.5 / 184.6ms | — |
| t≈140/145s | 32 / 68 | 34.7 / 62.3ms | 149.1 / 129.5ms | — |
| t≈170/175s | 24 / 56 | 40.4 / 99.6ms | 112.1 / 237.4ms | — |

**同一规模、同一负载，第一波与最后一波差 7.7×**（p50 766ms → 99ms）。同时读侧在保存突发所在桶被拖慢
（t≈55s 桶 p95 799ms，其余桶 13–48ms）。这说明：**冷启动（空连接池 + 空缓存）+ 100 并发突发
把整机占满**，稳态其实接近门槛（末段 save p95 已到 112–237ms）。

#### 10.4.4 修复：把冷启动成本挪到接流量之前（三处改动）

| 改动 | 位置 | 作用 |
|---|---|---|
| **启动预热** | `server/app/warmup.py`、`server/app/main.py`（lifespan） | 进程接流量前把 QueuePool 按 `DB_POOL_SIZE+DB_MAX_OVERFLOW` 一次填满（含 overflow），并装载已冻结试卷快照 / 已发布考试的题面视图。启动日志输出一行 `{"event":"startup.warmup","pool_connections":..,"papers":..,"questions":..}` |
| **冻结快照进程内缓存** | `server/app/scoring/service.py` | `papers.frozen_snapshot` 冻结后不可变，按 `(数据库URL, paper_id)` LRU 缓存，自动保存每请求省掉「查 papers 大 JSON 列 + 解析 100 题」；`db.reset_engine()` 与 `clear_snapshot_cache()` 负责失效 |
| **压测环境放宽连接预算** | `deploy/docker-compose.perf.yml`、`.github/workflows/ci.yml` | perf 覆盖文件把 postgres `max_connections` 放宽到 200、`shared_buffers=256MB`；CI 池改 15+15（4×30=120），配合预热让 100 并发保存不再有「冷建连接」阶段 |

设计约束（写进代码注释与 `deploy/.env.example`）：
- 预热**只读**、**失败只记日志**，任何异常都不得让服务起不来；
- 连接预算仍是 `WEB_CONCURRENCY × (POOL_SIZE + MAX_OVERFLOW) < postgres max_connections`，预热只是**提前**占用，不是额外占用；
- 预热结果可核对：`docker compose logs api | grep startup.warmup`；perf job 新增「确认启动预热已生效」步骤，一条都没有就立即失败（避免又变成「改了但没生效」的假绿）；
- `WARMUP_ENABLED=false` 可关闭（排障用）。

本机原生 PG 验证（2 worker、池 4+4、真实 9 份冻结试卷）：

```text
{"level":"info","logger":"app.warmup","event":"startup.warmup","enabled":true,
 "pool_connections":8,"papers":9,"questions":900,"exams":9}
pg_stat_activity: datname=exam 连接数 10（= 8 预热 + 2 常驻）
probe: GET /sessions/{id} p50 5.3ms、PUT /sessions/{id}/answers p50 5.1ms（无回归）
```

回归验证：`ruff` 全绿、`mypy app` 62 文件通过、`pytest` **172 passed**（新增
`server/tests/sessions/test_snapshot_cache.py` 8 条，覆盖「缓存命中 / 失效 / 跨库隔离 /
reset_engine 清理 / 预热三种降级路径」）。

#### 10.4.5 复跑结果（run `42863847703`，commit `11a1886`，同为 4 vCPU / 4 worker）

| 指标（k6 口径） | 修复前 `42859094367` | 修复后 `42863847703` | 变化 |
|---|---|---|---|
| `save_p95` p50 | —（未单记） | **66.2ms** | — |
| `save_p95` p95 | 1006ms | **538.4ms** | **−46%** |
| `read_p95` p95 | 37.5ms | **19.8ms** | **−47%** |
| `session_start_duration` p50 / p95 | —/ 1176ms | **470.5 / 796.5ms** | **−32%** |
| `submit_duration` p95 | — | 263.6ms | — |
| `http_req_failed` | 0 | **0** | — |
| `answer_save` 场景 p95 | 1115ms | 613.2ms | −45% |

服务端侧（同一份日志重建，`tests/perf/analyze_server_latency.py`）：

| 族 | 修复前 p50 / p95 | 修复后 p50 / p95 |
|---|---|---|
| save | 110.3 / 1026.5ms | **70.8 / 529.6ms** |
| read | 10.5 / 38.5ms | **7.3 / 21.1ms** |
| start | 754.6 / 1044.3ms | **506.6 / 755.3ms** |

各波保存（每 30s 一波 100 并发，修复后）：

| 时间桶 | count | p50 | p95 |
|---|---|---|---|
| t≈20s（**第一波**） | 100 | **470.4ms** | 595.9ms |
| t≈50s | 100 | 104.2ms | **282.7ms** |
| t≈80s | 100 | 89.7ms | **293.1ms** |
| t≈110s | 100 | 41.4ms | 95.4ms |
| t≈140s | 100 | 34.0ms | 83.6ms |
| t≈170s | 80 | 79.3ms | 283.4ms |

**关键结论（决定下一步该做什么）**：跳过开头 40s（warm-up 窗口）后，服务端侧
**save p95 = 264.6ms ✅、read p95 = 18.8ms ✅**——**稳态已经达标**。仍然判红的唯一原因是
**第一波保存（470ms p50）+ 同秒 100 个开考建会话（p95 755–796ms）**被混进同一个
`save_p95` / `answer_save` 数字里；第一波又与「开考瞬间 100 个会话创建 + 读 VU 起量 +
prepare_data 之后 PG 的首次检查点」叠在同一秒。

> 注：预热在 CI 上 `papers=0/questions=0` 属预期——perf job 先起 api（空库）再造数；
> 但**连接池 30/worker（4 worker 共 120）已按预期填满**，冷建连接成本已从压测窗口移除。

因此 G4 性能项还剩两种收敛方式，都需要决策而不能靠改代码绕过（详见 `docs/76` 草案）：
1. **≥8 vCPU runner** 重跑 `perf` job（第一波与开场 burst 的 CPU 争用按核数近似线性改善）；
2. **R0/QA 批准把「开场/首波 burst」与「稳态读写」分开度量**（草案 `docs/76-perf-gate-scope-proposal.md`，
   未改任何阈值与任务卡）。


