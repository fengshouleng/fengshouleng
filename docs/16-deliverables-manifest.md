# 16 · 交付物清单与验证状态（Handoff Manifest）

> 用途：交接时一眼看清「有什么、在哪、验证到哪一步」。
> 核心口径：**「静态审查通过」≠「已验证」**。本清单把两者严格分开，不做美化。

## 1. 契约与设计文档

| 文件 | 内容 | 状态 |
|---|---|---|
| [contracts/openapi.yaml](contracts/openapi.yaml) | API 契约 v1.0.3（冻结终态） | ✅ `openapi-spec-validator` OK（2026-11-10） |
| [docs/00-prd.md](00-prd.md) | PRD v0.1：F1–F7、AC-01..22、非目标、假设 | ✅ 静态 |
| [docs/01-scope-freeze.md](01-scope-freeze.md) | 范围冻结与砍单预案 | ✅ 静态 |
| [docs/10-arch.md](10-arch.md) | ADR-001..011、模块边界、错误码 | ✅ 静态 |
| [docs/11-data-model.md](11-data-model.md) | 9 张表、约束、状态机 | ✅ 静态 |
| [docs/12-permission-matrix.md](12-permission-matrix.md) | RBAC 矩阵 v1.2/v1.3、403/404 顺序 | ✅ 静态 |
| [docs/13-contract-changes.md](13-contract-changes.md) | CHG 登记（v1.0.1→v1.0.3 全部收口） | ✅ 静态 |
| [docs/02-risk.md](02-risk.md) | RISK-01..12 | ✅ 静态 |
| [docs/60-test-plan.md](60-test-plan.md) / [61-gate-report.md](61-gate-report.md) | AC 映射与门报告（§10 已有 G3 运行时证据） | 🔄 部分回填 |
| [docs/70](70-runbook.md)/[71](71-backup-restore.md)/[72](72-monitoring.md)/[73](73-release-checklist.md)/[74](74-incident-template.md)/[75](75-perf-baseline.md) | 运行手册、备份、监控、发布、事故、性能基线 | ✅ 静态（75 含实测矩阵与复现命令） |
| [docs/15-first-ci-run.md](15-first-ci-run.md) | 首次推送与首跑清单 | ✅ 静态 |
| [docs/17-first-run-predictions.md](17-first-run-predictions.md) | 首跑红项预测（按概率排序 + 修复方向） | ✅ 静态 |
| [scripts/verify_all.ps1](scripts/verify_all.ps1) / [.sh](scripts/verify_all.sh) | 一键验收预检（契约/后端/前端/可选安全/E2E/性能） | ⚠️ 未执行 |

## 2. 后端（`server/`，19 张卡）

| 模块 | 对应功能 | 测试 |
|---|---|---|
| `app/main.py` / `errors.py` / `db.py` / `settings.py` | 应用骨架、统一错误体、request_id | `tests/test_health.py` |
| `app/auth/` `app/users/` | 登录/登出/me、会话、CSRF、RBAC、用户管理 | `tests/auth/`、`tests/users/`（35 例） |
| `app/questions/` | 题库 CRUD、导入（CSV/XLSX）、软删、幂等去重 | `tests/questions/`（~25 例） |
| `app/papers/` | 组卷、分值、快照冻结 | `tests/papers/`（10 例） |
| `app/exams/` `app/sessions/` `app/scoring/` | 考试/会话/自动保存/幂等交卷/三档判分 | `tests/exams/`、`tests/sessions/` |
| `app/reports/` | 逐题结果、我的成绩、错题本、教师汇总 | `tests/reports/` |
| `app/audit/` | 审计查询、清理、脱敏 | `tests/audit/`（12 例） |
| `app/cli.py` | `create-admin` / `seed-demo` / `check` | `tests/cli/` |
| `app/middleware/access_log.py` | 结构化访问日志 | `tests/middleware/`（13 例） |
| `migrations/versions/` | `0001_auth` → `0006_audit` | ⚠️ 未执行 `alembic upgrade head` |

## 3. 前端（`web/`，9 张卡）

| 页面 | 路由 | 关键交互 |
|---|---|---|
| 登录/角色守卫 | `/login` | AuthGate、RequireRole、returnTo 防开放重定向 |
| 题库管理 | `/questions` | 筛选、表单联动、导入结果、409 提示 |
| 组卷 | `/papers`、`/papers/new`、`/papers/:id` | 选题/设分/排序/partial、frozen 只读 |
| 考试管理（教师） | `/exams` | 创建/发布/关闭、状态流转 |
| 考试作答（学生） | `/me/exams`、`/exams/:examId` | 倒计时、30s 自动保存、断网重试、刷新回填、幂等交卷 |
| 成绩/错题本 | `/results/:sessionId`、`/my-results`、`/wrong-questions` | 逐题结果、403/409、分页 |
| 教师汇总 | `/exam-results` | 提交数/平均分/明细 |
| 管理后台 | `/users`、`/audit-logs` | 用户管理、审计日志（✅ 已完成） |

## 4. 测试与质量资产

| 资产 | 路径 | 状态 |
|---|---|---|
| 契约测试 | `tests/contract/`（含 AC 矩阵、错误码、OpenAPI 结构） | ✅ 55 passed / 0 xfail |
| 安全负例 | `tests/security/`（矩阵逐行、CSRF、IDOR、注入/XSS） | ✅ 134 tests / 0 failed（1 skip + 1 xfail） |
| E2E | `tests/e2e/`（Playwright Python，4 条主链路） | ✅ 5 UI passed（5 skip=旧 API 骨架，1 xfail） |
| 性能 | `tests/perf/`（k6 100 写 + 100 读 VU，P95<300ms / 失败率<0.1% 阈值）、[docs/75](75-perf-baseline.md) 复现手册、CI 手动 `perf` job | 🟡 **两轮真实优化 + 逐请求剖析**：4 vCPU 上 read p95 **19.96–37.5ms ✅**、start p50 **310→166ms**、save p50 **99→32ms**、失败率 0；单请求服务端 CPU 2.7–5.2ms。开场 burst p95 仍 500–1176ms（4 vCPU 共享争用）→ **需 ≥8 vCPU 或 R0/QA 评审拆分阈值**（`docs/61 §10.2.10`） |
| 前端单测 | `web/tests/`（client/login/questions/papers/exam/result/exams/admin） | ✅ 91 passed (8 files) |
| 后端单测 | `server/tests/`（164 例） | ✅ 164 passed / 0 failed |

## 5. 部署与运维（`deploy/`，5 张卡）

| 能力 | 文件 | 状态 |
|---|---|---|
| 容器编排 | `docker-compose.yml`、`Dockerfile.api/web`、`nginx.conf` | ✅ `compose config` 通过（exit 0） |
| 备份/恢复 | `backup/*.sh`、`restore_drill.sh` | ✅ 备份 + 恢复演练 **PASS**（DR-20261110-01） |
| 监控告警 | `monitoring/*.sh`、`docs/72` | ⚠️ dry-run 未执行（验证优先级低） |
| 发布/回滚 | `release/deploy.sh`、`rollback.sh`、`staging_drill.sh` | ✅ **staging 部署→回滚演练 PASS**（部署 17s、回滚 11s、两次冒烟全过，`reports/release-drill-20261110T052821Z.json`）；演练暴露的 2 个脚本缺陷已修（DEF-002/003） |
| CI | `.github/workflows/ci.yml`（含手动 `perf` job） | ✅ **CI 三 job 首跑全绿**（run `42848561341`：backend 76s / frontend 78s / contract 16s / ci-gate 4s）；⚠️ `perf` job 在 4 vCPU runner 未达门槛（容量问题，见 `docs/61 §10.2.8`） |

## 6. 诚实结论

- ✅ **设计与实现**：V1 功能面完整（F1–F7），契约经 3 轮修订收敛到 v1.0.3。
- ✅ **已验证（2026-11-10 本机真实执行）**：contract 55、backend 164、frontend 91、security 134（0 failed）、E2E 5 条 UI 链路、PG 迁移 0001→0006、备份 33900B/TOC 90、恢复演练 PASS、compose config OK。
- ⚠️ **未验证（环境型）**：**性能门槛的最终销项**（原生 PG 已实测 6 组、参考机 12 worker + 快提交全绿；但 fsync=on 的 Linux/SSD 未测，见 `docs/61 §10.2`、`docs/75`）、staging 部署回滚演练（本机缺 bash/git 与镜像构建条件）、CI 三 job 首跑（未推送）。
- 🟡 **放行口径**：G4 判 **CONDITIONAL PASS**——代码、安全、E2E 证据充分；剩余为环境型（性能 Linux 销项、staging 演练、CI 首跑），不改变代码结论但阻塞正式签字；详见 [docs/61-gate-report.md §10.1](61-gate-report.md)。
- 🟢 **P2 已清**：FE-006 管理后台已完成；v1.0.3 断言已同步（contract 0 xfail）。
