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
| `DEF-001` | AC-__ | `P0/P1/P2` |  | `open/fixed/wontfix` |  |  |

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
| 回滚 commit | `__________`（本次发布前的最后一个 G4 通过版本） |
| 回滚方式 | `docs/70-runbook.md` §回滚（镜像 tag 回退 + Alembic `downgrade` + 健康检查） |
| 数据兼容 | 本次是否含破坏性迁移：`是/否`；若是，回滚是否需要恢复备份：`是/否` |
| 回滚实测耗时 | `____ min`（判据 < 15min） |
| 回滚后冒烟 | `GET /healthz` → 200；教师登录 → 200；学生答题 → 200 |

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
| 性能冒烟（k6 100 写 + 100 读 VU / 3m） | ⚠️ **原生 PG 复测：应用侧容量问题已定位并修复配置**；12 worker + 快提交（`synchronous_commit=off`）下**全门槛 PASS**（save p95 217ms / save-only 67ms / read p95 34ms / 失败率 0）；fsync=on 时 save p95 300–520ms 仍未过，见 §10.2 | `reports/perf/native-*/summary.json`、`docs/75-perf-baseline.md` |

**期间修复（测试缺陷，非实现缺陷）**：`test_audit_system_record_has_null_actor_and_dash_ip` 曾把 `auth.login_failed`（`actor_id=None` 但 ip 为真实地址）误当系统记录，导致偶发失败；已改为只筛 `action=user.create` 且 `actor_id=None` 的记录。

**G4 仍缺（环境型，非代码问题）**：
1. 性能最终确认（原生 PG 复测已完成并定位到「单 worker 容量 + 本机 NTFS 提交落盘」两因素；修复项已合入部署配置。**fsync=on 的 Linux/SSD 环境尚未实测**，需在 Linux 或 CI runner 用 `docs/75-perf-baseline.md` 复现门槛）；
2. staging 部署→回滚演练（脚本为 bash + docker compose；本机 Windows 缺 git/bash，且 **Docker Desktop/WSL 在压测后已损坏**，建议在 CI/Linux runner 执行）；
3. CI 三 job 首跑（本机未安装 git，需推送到 GitHub 后观察）。

**结论**：功能、契约、安全、E2E、备份恢复、compose 配置均有真实运行证据；**性能问题已完成分层定位并修复部署配置（多 worker + 池摊分），参考机 12 worker + 快提交时全门槛 PASS**；fsync=on 时的写尾延迟需 Linux/SSD 复核；staging 部署回滚演练未执行。G4 = **CONDITIONAL（性能 Linux 复核 + staging 演练）**。

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

