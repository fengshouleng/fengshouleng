# 选择题答题系统（Online MCQ Exam System）

教师建题库 → 组卷发考试 → 学生在线作答 → 自动判分 → 成绩与错题本；管理员管账号与审计。

- **交付目标**：2 周内可上线的生产级最小集（单机构、约 500 学生、同时在线 ≤ 100）
- **技术栈**：React 18 + TypeScript + Vite / FastAPI + SQLAlchemy 2 + Alembic / SQLite（本地）→ PostgreSQL 16（生产）/ Docker Compose + Nginx + GitHub Actions
- **交付方式**：DSH 多 Agent 流水线 —— R0 Lead（编排/验收）+ R1 PM-Spec + R2 Architect + R3 Backend + R4 Frontend + R5 QA + R6 Release
- **方案基线**：`docs/` 下的 PRD、架构 ADR、数据模型、权限矩阵、门控记录；契约唯一真相是 `contracts/openapi.yaml`

## 当前状态

| 阶段 | 状态 | 说明 |
|---|---|---|
| W0 需求与契约基线 | ✅ 完成 | PRD v0.1 / ADR-001..011 / 数据模型 / 契约 v1.0.3 / 任务卡 / preset |
| G1 范围冻结 | ✅ 放行（采用默认假设 A1–A4） | 见 `docs/01-scope-freeze.md`；变更走 ADR |
| G2 契约冻结 | ✅ v1.0.3（机器门通过） | 22+9+4 条缺陷/一致性问题全处置；`openapi-spec-validator` → OK；contract 55 passed / 0 xfail |
| 卡进度 | ✅ 完成 | 26 张任务卡 + 6 轮契约/测试同步全部完成 |
| 自动化验证 | ✅ G3 通过；🟡 G4 条件通过 | contract 55 / backend **172** / frontend **91** 全绿；security **134** / E2E **5 UI 链路** / 备份恢复 **PASS** / **监控告警链路自测 27/27** / compose config OK / **staging 部署→回滚 PASS（17s/11s）** / **CI 三 job 首跑全绿**（run `42848561341`）。**性能（四轮优化）：4 vCPU 上 read p95 301→**19.8ms** ✅、save p95 10.2s→**538ms**、start p95 1409→**796ms**、失败率 0；**启动预热 + 冻结快照缓存后，跳过 warm-up 窗口的服务端 save p95 = 264.6ms ✅（稳态已达标）**，仍红的只是「开场/首波 100 并发 burst」（第一波 p50 470ms）→ 需 ≥8 vCPU，或 R0/QA 按 `docs/76` 草案拆分「burst 与稳态」口径（未改阈值）** |
| 代码入库 | ✅ 已初始化 | Git 2.56；`.gitattributes` 统一换行（sh/py/yml=LF，ps1/cmd=CRLF）；初始提交 `d28d652`，工作区干净 |

## 目录结构

```
docs/                 需求、架构、数据模型、权限矩阵、决策日志、风险
  decision-log.md     R0 决策留痕（所有门控与变更）
  00-prd.md           PRD v0.1（含验收标准）
  01-scope-freeze.md  G1 范围冻结记录与变更流程
  02-risk.md          风险登记册
  10-arch.md          架构决策记录（ADR-001..008）+ 模块边界
  11-data-model.md    数据模型、约束、索引、状态机
  12-permission-matrix.md  RBAC 权限矩阵
contracts/
  openapi.yaml        API 契约 v1.0（唯一共享真相）
  tasks/              任务卡（TASK-TEMPLATE + 全链路 22 张，覆盖 BE/FE/QA/REL）
presets/
  agent-presets.yaml  R0–R6 的 DSH 角色预设与 toolFilter
server/               后端代码（由 TASK-BE-000 生成）
web/                  前端代码（由 TASK-FE-000 生成）
tests/                E2E/契约/安全测试（由 QA 角色维护）
deploy/               部署与运维脚本（由 R6 维护）
scripts/              验收入口（verify_all.ps1 / verify_all.sh，见 scripts/README.md）
```

## Agent 流水线怎么跑

1. R0 读 `docs/decision-log.md`、`docs/00-prd.md`、`contracts/openapi.yaml`，确认当前门控状态；
2. R0 从 `contracts/tasks/` 取一张任务卡，补全 `write_scope` 与验收命令后派发：
   - 需要继承用户对话 → `subagent_fork`（PM / Architect）
   - 需要干净隔离或并行 → `subagent`（Backend / Frontend / QA / Release）
   - 批次内多任务并行 → `workflow` 脚本，按 `return_schema` 校验返回；
3. Worker 只返回结构化摘要（status / files / tests / contract_issues），不回传推理过程；
4. R0 做合并前检查（diff 越界 + 测试 + 契约兼容）→ QA 独立验证 → 人类门签字；
5. 失败处理：理解偏差复用原会话 → 卡死换新 Spawn → 契约冲突回 R2 → 三次失败升级人工。

## 门控

| 门 | 判据 |
|---|---|
| G1 范围冻结 | PO 签字 `prd-v1.0` |
| G2 契约冻结 | OpenAPI lint 通过、AC→接口 100% 覆盖、权限矩阵完整 |
| G3 模块合并 | 任务卡 DoD、无越界写、契约测试绿 |
| G4 发布候选 | E2E/权限负例全绿、无 P0/P1、备份恢复演练通过 |
| G5 上线验收 | 生产冒烟 + 24h 无 P0 |

## CI

- 工作流：`.github/workflows/ci.yml`（`backend` / `frontend` / `contract` 三个 job + `ci-gate` 汇总检查，任一失败整体失败）
- 判据与本地复现：见 [`.github/workflows/README.md`](.github/workflows/README.md)
- `web/pnpm-lock.yaml` 已由本机 `pnpm install` 生成（待提交）；提交后 CI 自动从 `--no-frozen-lockfile` 切换为 frozen
- 需人类在 GitHub 把 `ci-gate` 设为 `main` 分支保护的必需检查（Worker 无权限）
- 首次推送步骤与首跑失败处置：见 [docs/15-first-ci-run.md](docs/15-first-ci-run.md)
- 交付物清单与验证状态（含「静态 vs 已验证」口径）：见 [docs/16-deliverables-manifest.md](docs/16-deliverables-manifest.md)
- 一键验收预检：`./scripts/verify_all.ps1`（Windows）或 `./scripts/verify_all.sh`（Linux/macOS），日志写入 `reports/verify-<ts>/`
- 首跑红项预测与处置：见 [docs/17-first-run-predictions.md](docs/17-first-run-predictions.md)

## 环境说明

- 本机未安装 pwsh 7：`scripts\verify_all.cmd` 自动回退 Windows PowerShell 5.1；2026-11-10 已在本机完成首次真实验收预检（14 步全绿）。pip 走清华 PyPI 镜像、pnpm/npm 走 npmmirror，均可用 `verify_all.ps1` 参数覆盖。
- 文件沙箱策略：workspace-write；生产变更走 DSH 审批（ask），不做自动化自批。
