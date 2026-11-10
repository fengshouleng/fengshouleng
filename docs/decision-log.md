# Decision Log（R0 Lead 维护）

> 规则：任何门控结论、契约变更、范围调整、失败升级都必须在此留痕。
> 每条决策格式：ID / 日期 / 决策 / 依据 / 影响面 / 复核人。

| ID | 日期 | 决策 | 依据 | 影响面 |
|---|---|---|---|---|
| D-001 | 2026-11-08 | 交付目标定为「生产级最小集」，并显式写入非目标清单 | 用户 Q1 选择；2 周 + 1–2 人无法承载完整 SaaS | PRD §3、G4 判据 |
| D-002 | 2026-11-08 | 技术栈：React18+TS+Vite / FastAPI+SQLAlchemy+Alembic / SQLite→PostgreSQL16 / Docker Compose+Nginx+GitHub Actions | 用户 Q2 授权默认；团队熟悉度高、同构部署成本低 | ADR-001、ADR-008、TASK-BE-000、TASK-FE-000 |
| D-003 | 2026-11-08 | 2 周内范围收窄：错题本、管理后台、统计图表列为 P1/P2，可按预案砍单 | 用户 Q3（1–2 人/1–2 周）；关键路径为考试+判分+成绩 | PRD §3.3、风险 RISK-04 |
| D-004 | 2026-11-08 | 采用「契约优先 + 模块化单体」，不拆微服务 | 单体运维成本最低；OpenAPI 作为前后端唯一共享真相 | ADR-002、ADR-003 |
| D-005 | 2026-11-08 | 认证用「服务端 Session + HttpOnly Cookie + CSRF 双提交」，不用 JWT | 可即时吊销、无刷新令牌复杂度，适合小团队与审计要求 | ADR-004、OpenAPI securityScheme |
| D-006 | 2026-11-08 | Agent 落地形态 A：Lead 主会话 + subagent/workflow，不启用持久队友 | 1–2 周项目，持久队友的编排/等待成本高于收益 | README、presets/agent-presets.yaml |
| D-007 | 2026-11-08 | 并发上限 3（G3 后短暂到 4），不是技术限制而是人类验收带宽限制 | 1–2 名人类无法审 6 路并行产出 | 流程 §4.2、§7.1 |
| D-008 | 2026-11-08 | 假设 A1–A4 采用默认值：客观题（单选/多选/判断）、Excel/CSV 导入、单机构 500 人/并发 100、本地账号 | 用户 Q5 无特殊约束；G1 一次性确认，非阻塞 | PRD §6、TASK-BE-002、TASK-BE-001 |
| D-009 | 2026-11-08 | 环境受限：本机 pwsh 不可用（0xC0000142），自动化验证推迟到 CI/QA | 实测 `pwsh -Command` 连续失败，退出码 3221225794 | 风险 RISK-08、G3/G4 执行方式 |
| D-010 | 2026-11-08 | 防枚举口径：角色不符或「可见但非本人」→ 403；不可见或不存在 → 404；AC-18 的 IDOR 结果读取固定 403 | AC-18 与 QA TC-SEC-18 均硬断言 403；R2 的 C-17 口径需与 PRD 一致 | docs/12 v1.1、TASK-QA-002、docs/13 C-17 |
| D-011 | 2026-11-09 | 契约 v1.0.2 口径：`MyExamPage` 增 `server_time`；`points ≤ 100` 且 `total ≤ 10000`；logout 有效 204 / 无效 401（认证先于 CSRF）；教师单会话 result 固定 403（走考试汇总）；`average_score` 无数据返回 0；`replayed`/`applied` 必需化；`ALREADY_SUBMITTED` 从枚举移除 | QA 对 v1.0.1 的同步审查问题（CHG-024..032 共 9 条，logout 401 冲突并入 CHG-029）；准则：客户端可断言、无歧义 | 契约 v1.0.2、docs/12 v1.2、docs/10 ADR-010、tests/contract 断言 |
| D-012 | 2026-11-09 | V1 的「越权尝试（403）写审计」不强制（security 套件保留 xfail）；密码策略仅 `minLength=8`，复杂度规则不作为 V1 要求 | QA-002 结论 + docs/12 §4 标为可选；避免为次要能力扩大范围 | tests/security、PRD NFR 备注、G4 报告残留风险 |

## 待决事项（G1/G2 前必须闭环）

| 编号 | 事项 | 需谁决策 | 截止 |
|---|---|---|---|
| P-001 | 确认假设 A1–A4 | 人类 PO | G1 前 |
| P-002 | 多选「部分给分」是否在 V1 开启（默认开启，按题配置） | 人类 PO + R2 | G1 前 |
| P-003 | OpenAPI v1.0 抽查（30 分钟） | 人类 PO + R0 | G2 前 |
| P-004 | 补提交 `web/pnpm-lock.yaml`（当前 CI 退化为 `--no-frozen-lockfile`） | R4 + 人类（需可执行环境） | G3 前 |
| P-005 | GitHub 上把 `ci-gate` 设为 `main` 分支保护必需检查 | 人类运维 | 首次推送前 |
| P-006 | `tests/requirements.txt` 去掉契约 job 不用的 `playwright` | R5 | TASK-QA-001 前 |
| P-007 | 首管理员种子与运维 CLI（`create-admin`/`seed-demo`/`check`）及 `/healthz` DB 探针 | R3（TASK-BE-007） | TASK-QA-001 前 |
| P-008 | 把 `docker compose config -q` 纳入 CI（REL-001 唯一可执行门） | R6 | REL-002 前 |
| P-009 | 契约小澄清：无有效会话时 401 优先于 CSRF/参数错误，写入 security 说明（需下一次 CHG 批次，不单独升版） | R2 | 下一 CHG 批次 |
| P-010 | `/healthz` 200 schema 未声明 `db` 字段（实现已返回 `db:"ok"`）；下一次 CHG 批次补 schema | R2 | 下一 CHG 批次 |
| P-011 | `AuditLog.actor_id/ip` 契约必填，但系统操作（CLI create-admin）写 null；下一次 CHG 批次把 `actor_id` 改 `[integer,"null"]` 并约定系统操作 ip 写 `"-"` | R2 | 下一 CHG 批次 |

## 门控记录

| 门 | 结论 | 证据/说明 |
|---|---|---|
| G0 启动 | PASS | 用户 5 问已答（Q1–Q5），W0 基线建立 |
| G1 范围冻结 | PASS（默认假设放行） | 用户以「继续」指令授权按 A1–A4 默认值推进；PRD v0.1 + 范围冻结记录为基线；A1–A4 若变更走 ADR 重审 |
| G2 契约冻结 | ✅ PASS（v1.0.3，机器门通过） | openapi-spec-validator → OK；contract 55 passed / 0 xfail（2026-11-10 本机真实执行） |
| G3 模块合并 | ✅ PASS（运行时，2026-11-10） | 本机 `verify_all` **14 步全绿**：contract 55 passed、backend ruff/mypy/pytest 通过、frontend lint/tsc/**91 tests**/build 通过；证据 `reports/verify-20261110-004039/`。G4 剩余：security/E2E/perf/演练 |
| G4 发布候选 | 🟡 CONDITIONAL（2026-11-10） | 已证：security 134（0 failed）、E2E 5 UI 链路、PG 迁移、备份+恢复演练 PASS、compose config。**性能：原生 PG 复测完成，根因=单 worker 容量 + 日志锁放大 + 本机 NTFS 提交 fsync；12 worker + 快提交时全门槛 PASS，fsync=on 的 Linux/SSD 待复核**（docs/61 §10.2、docs/75）；缺：staging 部署回滚演练、CI 首跑、性能 Linux 销项 |
| G5 上线验收 | PENDING | — |

## 首批派发（B3）

| Worker | 任务卡 | 上下文策略 | 并发 | 状态 |
|---|---|---|---|---|
| R3 Backend | TASK-BE-000（脚手架） | Spawn（全新会话，隔离） | 1/3 | ✅ 已合并 |
| R4 Frontend | TASK-FE-000（脚手架） | Spawn | 2/3 | ✅ 已合并 |
| R5 QA | TASK-QA-000（测试计划+契约测试） | Spawn（对抗性独立） | 3/3 | ✅ 已合并（contract_issue：22 条） |

> 派发方式：`subagent`（form A）。任务卡已写入 `contracts/tasks/`，Worker 只读任务卡与契约，不继承主会话上下文；返回强制 JSON schema。
> 环境：`pwsh` 仍不可用（0xC0000142），Worker 被明确禁止执行 shell 命令，验证证据由 CI 补跑（RISK-08）。

## 变更记录

- 2026-11-08：W0 基线建立（PRD v0.1 / ADR / 数据模型 / 契约 v1.0 / 首批任务卡 / preset）。
- 2026-11-08：G1 以默认假设放行（A1–A4）；G2 契约 v1.0 条件冻结。
- 2026-11-08：B3 首批派发 R3/R4/R5 三个 Worker（并发 3，符合流程 §7.1 上限）。
- 2026-11-08：提出 **CHG-001**（`SessionState` 缺已保存答案，阻断 TASK-FE-004），登记 `docs/13-contract-changes.md`，待 QA 契约审查后合并为 v1.0.1。
- 2026-11-08：全链路任务卡备齐（22 张，覆盖 BE/FE/QA/REL），索引见 `contracts/tasks/README.md`；B4 起按索引派发。
- 2026-11-08：确认 **CI 流水线（TASK-REL-000）为验证通道前置项**，建议优先于 B4 业务卡派发（RISK-08 缓解）。
- 2026-11-08：R3 返回 **TASK-BE-000 done**（17 文件），G3 静态初审通过；发现 docs/10 错误码表与契约不一致（400/422 均映射 VALIDATION_ERROR、导入部分成功走 200），已修正文档。派发 **TASK-BE-001**（B3b，R3 复用/新会话）。
- 2026-11-08：R4 返回 **TASK-FE-000 done**（23 文件），G3 静态复审通过；派发 **TASK-REL-000（CI）**（RISK-08 缓解前置项）。
- 2026-11-08：R6 返回 **TASK-REL-000 done**：CI 三 job（backend/frontend/contract）+ `ci-gate` 汇总；遗留 web/pnpm-lock.yaml 缺失、ci-gate 需人工设为分支保护必需检查。
- 2026-11-08：R3 返回 **TASK-BE-001 done**（21 文件，35 用例静态自审）：argon2id、会话 Cookie、CSRF 全局依赖、RBAC、审计脱敏；迁移链起点 `0001_auth`（无 baseline，已确认）。派发 **TASK-BE-002**（题库，迁移接 `0002_questions`）。
- 2026-11-08：**G2 契约 v1.0.1 通过（静态复核）**：R2 修订 22 条缺陷（1156→1518 行），新增 `/me/exams`、`SessionState.answers/submitted_at`、`SubmitResult.replayed`、`Error.code` 枚举、审计必填、分值与防枚举口径；ADR-009 记录；TASK-BE-004/FE-004 同步。派发 **R5 同步 tests/contract**（6 个 xfail 清理 + C-11 判定键 + 新增 C-02/13/15/16/17/21/22 用例）。
- 2026-11-09：R4 返回 **TASK-FE-001 done**（6 文件）：AuthGate 启动引导（放在 routes.tsx 根路由，不改 App.tsx）、RequireRole、登录/returnTo 防开放重定向、登出；遗留小项（AppLayout 菜单入口转 FE-002、已登录访问 /login 行为暂不处理）。派发 **TASK-REL-001**（部署骨架）。
- 2026-11-08：R5 返回 **TASK-QA-000 contract_issue**：22 条契约缺陷（C-01 恢复会话缺答案回读、C-02 学生无法发现考试、C-13 到期自动交卷不可观测、C-15 交卷幂等与 409 矛盾 为 4 条 G4 阻断项）；已派 **R2 修订契约至 v1.0.1**。
- 2026-11-09：R6 返回 **TASK-REL-001 done**（14 文件）：compose 四服务/双网络/健康检查、非 root 镜像、备份脚本、staging 冒烟脚本、runbook 与备份文档。GAP 处理：`psycopg` 回填 pyproject、根 `.dockerignore` 已补、`/healthz` DB 探针与种子 CLI 立 **TASK-BE-007**、compose config 纳入 CI 记 P-008。
- 2026-11-09：R5 返回测试同步完成（6 个 xfail 清理、C-11 判定键、新增 C-02/13/15/16/17/21/22 用例），并发现 v1.0.1 遗留 10 条一致性问题；派 **R2 修订 v1.0.2**（9 项修复）。
- 2026-11-09：R3 返回 **TASK-BE-002 done**（约 25 用例）：题库 CRUD/导入（CSV+XLSX）、软删、引用 409、幂等去重；迁移 `0002_questions` 接 `0001_auth`；`env.py` 补模型 import；Lead 代改 `server/pyproject.toml` 增 `openpyxl`。派发 **TASK-BE-003**（组卷+快照，迁移接 `0003_papers`）。
- 2026-11-09：R2 返回 **契约 v1.0.2 done**（1518→1594 行，CHG-024..032）：server_time、points/total 自洽、PaperCreate 重写、replayed/applied 必需化、logout 204/401、教师单 result 403、average_score=0、ALREADY_SUBMITTED 移除；ADR-010。派发 **R5 同步 4 处断言**。
- 2026-11-09：遗留小项：`server/app/errors.py` 的 `ALREADY_SUBMITTED` 常量待 BE-004 清理；`web/src/api/types.ts` 为 v1.0.1 手写镜像，待后续 FE 卡或 `pnpm gen:types` 更新。
- 2026-11-09：R5 返回 v1.0.2 断言同步完成（4 处目标 + 额外修复 2 处既有断言 bug：TC-CT-06 ImportFailure、TC-CT-20 WrongQuestion 的 `$ref` 判据）。docs/60 三处 v1.0.1 口径已由 Lead 修正；契约「401 优先于 CSRF」澄清记 P-009。派发 **TASK-BE-007**（种子/CLI/health DB 探针）。
- 2026-11-09：R4 返回 **TASK-FE-002 done**（8 文件）：题库页列表/筛选/表单联动/导入结果/删除 409 提示、AppLayout 菜单与路由挂载、兼容既有 testid。派发 **TASK-FE-003**（组卷页）。
- 2026-11-09：R3 返回 **TASK-BE-003 done**（11 文件，10 用例）：`papers/paper_questions` + 迁移 `0003_papers`、`freeze_paper` 快照（不 commit，由 BE-004 在发布事务内提交）、归属/409/幂等；follow-up 修复：`numeric(6,2)`→`numeric(7,2)`（模型 + 0003 + docs/11）、questions 测试改 `CREATE TABLE IF NOT EXISTS`。派发 **TASK-BE-004**（考试会话/判分，迁移接 `0004_exams`）。
- 2026-11-09：R3 返回 **TASK-BE-007 done**（7 文件）：`app.cli`（create-admin/seed-demo/check，密码仅 stdin/env）、`/healthz` DB 探针（503 + 统一错误体）+ 测试。runbook GAP-01/02/07 同步并入 REL-002 处理；`/healthz` schema 补 `db` 记 P-010。派发 **TASK-REL-002**（备份恢复演练）。
- 2026-11-09：R3 返回 **TASK-BE-004 done**（24 文件）：exam/exam_session/answer 三表 + `0004_exams`、`/me/exams`、会话开始/恢复、client_seq 自动保存（applied=false）、幂等交卷（replayed）、到期自动结算、三档判分（`scoring/score_question` 表驱动）。G3 严格复核通过：client_seq 单调、单事务状态迁移、判分仅依赖快照、未作答按 0 分。env.py 模型导入由 Lead 补齐；`types.ts` 残留 ALREADY_SUBMITTED 记入 FE 清理项。派发 **TASK-BE-005**（成绩/错题本，`0005_wrong_book`）。
- 2026-11-09：R6 返回 **TASK-REL-002 done**（10 文件）：备份原子写 + 校验 + 7 日/4 周保留、异地副本开关、新鲜度告警、恢复演练脚本、cron 入口、docs/71 重写、runbook GAP-01/02/07 关闭。**演练未实跑（G4 证据缺失，RISK-08）**；compose 定点备份/异地副本待 REL-001 后续镜像变更（docs/71 §7.2 有片段）。派发 **TASK-FE-004**（考试答题页）。
- 2026-11-09：R4 返回 **TASK-FE-003 done**（9 文件 + 9 用例）：组卷列表/编辑器/只读详情、选题/设分/排序/partial、总分与字段 422 回显、frozen 只读、409 文案；AppLayout 与路由挂载。已知回归点：antd InputNumber 的 `data-*` 透传需 CI 验证。派发 **TASK-FE-005**（成绩/错题本页）。
- 2026-11-09：R3 返回 **TASK-BE-005 done**（14 文件）：`wrong_book` + `0005` 迁移、交卷事务内错题 upsert（replayed 不计数）、逐题结果/我的成绩/错题本/教师汇总（平均分无数据=0）。已知取舍：逐题解析按 question_id 回读题库（不在冻结快照内），题库编辑会使历史解析变化——记入已知限制。Lead 补 `errors.NOT_SUBMITTED`、`env.py` 导入 wrong_book。派发 **TASK-BE-006**（审计查询/保留，迁移接 `0005`）。
- 2026-11-09：R4 返回 **TASK-FE-005 done**（16 文件）：成绩页（409/403/expired 处理）、我的成绩、错题本、教师汇总与索引页、路由替换 FE-004 占位、菜单与 8 个用例。派发 **TASK-REL-003**（监控告警）。
- 2026-11-09：R4 返回 **TASK-FE-004 done**（8 文件）：`useExamSession`（恢复/回填/30s debounce/client_seq/离线重试/倒计时/409 刷新）、ExamPage、MyExamsPage、路由与菜单；与 FE-005 对 routes.tsx/AppLayout 的并行写入已合并（Lead 已核对 routes 全量存在）。**缺口**：教师侧「考试管理页」没有对应卡，E2E-01 的发布考试无法走 UI → 补立 **TASK-FE-007**（🟡 待派发）。派发 **TASK-QA-001**（E2E，Python Playwright）。
- 2026-11-09：R3 返回 **TASK-BE-006 done**（10 文件）：`0006_audit` 幂等补索引、GET /audit-logs（admin、180 天默认窗、过滤分页）、`python -m app.audit.cleanup`、detail 脱敏含 answer 键、12 用例。**决策**：审计与业务同事务提交（失败整体回滚），保留可追溯性优先；系统操作 null actor 记 P-011（v1.0.3 契约放宽）。派发 **TASK-FE-007**（考试管理页）。
- 2026-11-09：R6 返回 **TASK-REL-003 done**（8 文件）：健康探测/资源告警/慢请求日报/告警钩子/监控文档。发现 api JSON 访问日志需后端改造（已立 **TASK-BE-008** 🟡）；compose max-file 与 cron 安装记 docs/72 待应用片段。派发 **TASK-REL-004**（staging 上线回滚演练，生产待 G4）。
- 2026-11-09：R4 返回 **TASK-FE-007 done**（6 文件）：教师考试管理页（列表/创建/发布/关闭、状态流转与错误回显）、菜单与路由替换占位、测试。前端功能面补齐。派发 **TASK-QA-002**（权限负例/安全基线，黑盒 httpx）。
- 2026-11-09：R5 返回 **TASK-QA-001 done**（6 文件）：Playwright Python 四条主链路 + 支撑库 + 证据目录约定；T-E2E-01 发布步骤因当时 FE-007 未落地改走 API（FE-007 已落地，可在 CI 后升级为 UI 发布）；到期场景用 Clock 快进 + closed 结算两种替代（真实等待 xfail）。派发 **TASK-BE-008**（结构化访问日志，补 REL-003 前置）。
- 2026-11-09：R3 返回 **TASK-BE-008 done**（5 文件）：纯 ASGI `AccessLogMiddleware`（不缓冲响应体、异常也记行）、JSON 行字段与脱敏、13 个用例；docs/72 的 level 枚举与小数位由 Lead 对齐（warning/2 位）。派发 **TASK-QA-003**（性能冒烟，k6 100 VU）。
- 2026-11-09：R5 返回 **TASK-QA-002 done**（8 文件）：权限矩阵逐行负例、CSRF/会话/IDOR/注入/XSS/密码与枚举、`xfail` 标记「越权尝试未写入审计（V1 不强制）」。指出 draft 资源 403/404 口径张力 → 派 **R2 v1.0.3** 收口（P-009/010/011 + draft 404 + healthz db + AuditLog.actor_id 可空），作为 V1 最后一次契约修订。
- 2026-11-09：R6 返回 **TASK-REL-004 done**（8 文件）：`deploy.sh`（G4 门禁 + 备份门禁 + 迁移 dry-run + 健康检查 + 冒烟 + 失败自动回滚）、`rollback.sh`（镜像回切与迁移降级策略、15 分钟判据）、`staging_drill.sh`（部署→冒烟→回滚→冒烟，输出 JSON）、发布检查单与事故模板、runbook §14。staging 演练待 docker 环境。派发 **TASK-FE-006**（管理后台，P2 收尾）。
- 2026-11-09：R2 返回 **契约 v1.0.3 done**（4 文件）：认证优先（15 个写接口）、healthz `db`、`AuditLog.actor_id` 可空 + 系统 ip `"-"`、draft 404/可见 403 口径（ADR-011）、V1 冻结声明。派发 **R5 同步 v1.0.3 断言**；BE-006 的 `ip="-"` 实现由 Lead 直接补（1 行）。
- 2026-11-09：R5 返回 **v1.0.3 断言同步 done**：tests/contract 55 例（0 xfail）、security 新增 10 例（draft 403 硬断言）、healthz `db`/`AuditLog` 可空/判定链文案锁；实现侧静态核对一致。Lead 更新 docs/61 gate report 到 v1.0.3 口径。
- 2026-11-09：R4 返回 **TASK-FE-006 done**（8 文件）：管理后台（用户管理 + 审计日志只读），P2 功能面全齐。Lead 完成 **G4 报告组装**（docs/61 §9 预检清单 12 步 + v1.0.3 口径自检表）。仅剩 QA-003 性能冒烟在飞。
- 2026-11-09：R5 返回 **TASK-QA-003 done**（4 文件）：k6 场景（100 VU 作答/读取、P95<300ms、错误率<0.001、退出码 99）、`prepare_data.py`（幂等造数、试卷轮换避免复用终态会话）、性能 README。**全部 26 张卡 + 6 轮契约/测试同步收官**。唯一未完成：所有测试的真实执行（需 CI 或可执行环境）。
- 2026-11-10：**首次真实执行（G2/G3 转 PASS）**。本机 `scripts\verify_all.cmd` 在 2026-11-09 23:32 – 2026-11-10 00:40 间多轮运行，最终 **RESULT: PASS (all steps green)**：`openapi-spec-validator` OK、contract **55 passed / 0 xfail**、backend ruff + mypy(61 files) + pytest 通过、frontend lint + tsc + **91 tests** + build 通过；证据目录 `reports/verify-20261110-004039/`。
  期间在人工循环中修复：mypy（`TypeEngine` 导入、`cleanup.rowcount` 取法）、ruff 6 处、前端 eslint `prefer-const`、`web/tests/setup.ts` 的 antd/jsdom 桩、`exam.test.tsx` mock 类型等（以当前工作区为准，未经 G3 复核但已跑绿）。
  **下一步**：起 API + seed + 前端 dev server → `-WithSecurity -WithE2E`；k6 与备份/发布演练需独立环境；CI 首跑仍待 `git push`。
- 2026-11-10（G4 证据批次，Lead 直接执行）：
  - **security**：`134 tests / 0 failed / 1 skipped / 1 xfailed`，证据 `reports/security-junit.xml`；期间修复测试缺陷 `test_audit_system_record_has_null_actor_and_dash_ip`（原先把 `auth.login_failed` 的 `actor_id=None` 误当系统记录，导致偶发失败；改为只筛 `action=user.create`）。
  - **E2E（Playwright）**：`5 passed / 5 skipped / 1 xfailed`（5 条 UI 主链路全绿；5 条为旧 API 骨架设计内跳过；04c 真实到期条件 xfail），证据 `reports/e2e-junit.xml`、`reports/e2e/20261110-014943/`。
  - **后端**：`164 passed / 0 failed`（`reports/backend-junit.xml`）。
  - **PostgreSQL**：迁移 0001→0006 成功（PG 16.4 临时容器，含 GIN）。
  - **备份 + 恢复演练**：dump 33900B / TOC 90 / sha256 校验通过；restore 0s、行数全一致、临时库已删 → **PASS**，台账 DR-20261110-01。
  - **compose config**：exit 0。
  - **G4 判 CONDITIONAL PASS**：缺 perf(k6)、staging 部署回滚演练、CI 首跑（均环境型）。证据汇总见 `docs/61-gate-report.md §10.1`。
- 2026-11-10（性能验证，PERF-001）：下载 k6 v0.49.0，在临时 PG16.4 上实测 100 写 + 100 读 VU / 1m。
  - 首轮（默认连接池）：`http_req_failed=67%`、session start 通过 15/129、读场景 0/100，日志大量 `QueuePool limit ... timeout 30s`。
  - 修复 1：`server/app/db.py` 非 SQLite 池改为 `pool_size=20/max_overflow=30/pool_timeout=10/pool_recycle=1800`（SQLite 不受影响，后端 164 用例重跑仍绿）。
  - 次轮：失败率降至 51%、session start 100/108，但 P95 仍 ≈40s。
  - **环境归因（决定性证据）**：用 psycopg 直连同一映射端口做基准，**50 个并发连接全部 ConnectionTimeout**；单请求登录 121ms、健康检查正常、PG 容器 CPU 0% → 瓶颈是 **Docker Desktop 的 host↔container 端口代理**，不是 SQLAlchemy/应用逻辑。
  - 修复 2（保留的产品改进）：池参数可配置（`DB_POOL_SIZE/DB_MAX_OVERFLOW/DB_POOL_TIMEOUT/DB_POOL_RECYCLE`，默认 20/30/10/1800）；改后 backend 164、contract 55 复跑仍绿。
  - **结论修正**：PERF-001 由「应用性能缺陷」改判为「环境归因」；需在 Linux/CI 或「API 与 PG 同 Docker 网络」环境复测，门槛仍是 P95<300ms、错误率<0.1%。
  - 环境状态：压测后本机 **Docker Desktop / WSL 损坏**（`WSL REGDB_E_CLASSNOTREG`、`Docker Desktop terminated unexpectedly`），本机无法继续容器内复测与 staging 演练。
- 2026-11-10（性能二次定位，原生 PG 复测；Docker 不可用后的替代路径）：
  - **方案**：D:\pgsql 原生 PG 16.4（ASCII 路径，中文路径会让 initdb 报 `invalid byte sequence`）+ 原生 uvicorn（与镜像同参数）+ 本机 k6，100% 绕开 Docker 端口代理。手册 `docs/75-perf-baseline.md`。
  - **假象排除**：API stdout 被 PowerShell/`docker compose logs` 管道捕获时，`access_log` 的「全局锁 + 每行 flush」把请求串行化，p95 20–31s 并产生 `QueuePool` 500；改 stdout 落文件后同类负载 p95 ≈700ms。→ 新增 RISK-15（真实运维风险）。
  - **容量结论**：单 worker（镜像默认）读路径 p95 596ms、写 20.9s（单核 CPU 饱和，PG CPU 仅 2.5s）→ 应用侧确为容量问题，**不是纯环境问题**（修正首测的单一 Docker 代理归因）。
  - **写尾归因**：100 并发开考 burst 的 p95 对 worker 数不敏感（4/8/12 worker：589/571/523ms），`synchronous_commit=off` 后降到 217ms → 本机 NTFS 的 WAL fsync 串行是写尾主因（存储/OS 属性，需 Linux/SSD 复核）。
  - **实测矩阵（6 组）与门槛判定**：见 `docs/61-gate-report.md §10.2.2`；通过配置为 **12 worker + `synchronous_commit=off`**：save p95 217ms、read p95 34ms、失败率 0（全门槛 PASS）。
  - **产品修复（部署配置）**：`deploy/docker-entrypoint.sh` 支持 `WEB_CONCURRENCY`（`--workers`）；compose api 增加 `WEB_CONCURRENCY=4 / DB_POOL_SIZE=10 / DB_MAX_OVERFLOW=10`（4×20=80 < postgres max_connections=100）；`.env.example` 记录连接预算公式。Docker 不可用，`docker compose config` 未复跑，仅做 YAML 解析校验。
  - **剩余**：在 fsync=on 的 Linux/SSD 或 CI runner 复跑 `docs/75`，save p95 <300ms 才为 G4 性能销项；`synchronous_commit=off` 仅用于定位，禁止用于生产判定。
- 2026-11-10（12:30，环境恢复 + 入库 + Docker 复测）：
  - **Git 入库**：Git 2.56 已安装（`C:\Program Files\Git`，附 bash 5.3）；仓库 `git init -b main`，新增 `.gitattributes`（`*.sh/*.py/*.yml`=LF、`*.ps1/*.cmd`=CRLF，避免 Linux/容器脚本被 CRLF 破坏），`.gitignore` 补 `*.egg-info/`、`pytest-of-*/`、`tests/perf/data/`；**287 文件、初始提交 `d28d652`**，工作区干净。
  - **Docker/WSL 恢复**：`wsl -l -v` 可见 `docker-desktop`（Stopped 可启动），Docker engine **29.9.3** 可用；`docker compose config`（.env.example 与 .env.staging）**exit 0**，§10.2.3 的容量变量正确渲染 → RISK-13 关闭。
  - **首测环境归因被推翻**：健康 Docker 下宿主 psycopg 对发布端口**并发 50 条连接 50/50 成功（0.10s，最慢 71.7ms）** → 旧结论「端口代理无法承载并发」是 WSL/Docker 损坏期表现，不是 Docker 固有上限；PERF-001 真因确认为「单 worker + 日志锁 + NTFS fsync」。
  - **CI 增加手动 `perf` job**：`workflow_dispatch` 触发，compose 起 postgres+api（`deploy/docker-compose.perf.yml` 临时发布 18000），造数 + `grafana/setup-k6-action@v1` + k6 阈值判定，summary 归档 artifact；`ci-gate` 已纳入 `perf`（PR 中 skipped 属预期）。
  - **下一步**：容器内（Linux 文件系统、fsync=on）复跑 100+100 VU，验证 save p95 <300ms；随后 staging 部署→回滚演练。
- 2026-11-10（13:00，容器内性能实测完成）：
  - **拓扑**：`postgres:16.4-alpine` + 本次构建的 `exam-api`（`WEB_CONCURRENCY=8 / DB_POOL_SIZE=5 / DB_MAX_OVERFLOW=5`），k6 v0.49.0 拷入 api 容器、以 loopback 打 `127.0.0.1:8000`（不经宿主端口代理），100 写 + 100 读 VU / 3min。
  - **结果（与 native-20261110-0925 同负载对比）**：`session_read` p95 **14.2ms**、保存 p95 **199.7ms**、失败率 0.09%，均达标；`answer_save` p95 **428ms 超标**，超标部分集中在开场 100 并发建会话（p99 593ms）。读路径比原生 Windows 好一个量级。
  - **归因确认（诊断）**：同容器仅 `synchronous_commit=off` → `answer_save` p95 **252ms ✅**、read 21.9ms ✅、失败率 0 ✅。→ 唯一超标项就是 **WAL fsync 串行**。
  - **边界**：Docker Desktop 的 VM 磁盘仍是 Windows NTFS 上的 VHDX，因此容器也不代表真实 Linux SSD；本机（原生 + 容器）都复现同一写尾，**不能在本机销项**。
  - **销项路径（已具备）**：CI 手动 `perf` job（compose 起 postgres+api、造数、k6 阈值判定、artifact）→ 推送到 GitHub 后 Actions 手动 Run 一次，真实 ubuntu/SSD 上应通过。
  - **G4 状态不变**（CONDITIONAL）：剩余 = Linux/SSD 的 perf 销项 + staging 部署回滚演练 + CI 三 job 首跑。
