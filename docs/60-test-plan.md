# 60 · 测试计划与 AC 映射（TASK-QA-000）

- 版本：v1.1　日期：2026-11-09　作者：R5 QA（preset `qa-verify`；独立验证，**只报不修**）
- 依据：`docs/00-prd.md`（AC-01..AC-22）、`docs/12-permission-matrix.md` v1.1、`docs/11-data-model.md`、`contracts/openapi.yaml` **v1.0.1**（2026-11-09）、`contracts/tasks/TASK-QA-000.yaml`
- 写作用域：`tests/**`、`docs/60-test-plan.md`、`docs/61-gate-report.md`；**禁止**修改 `contracts/**`、`server/**`、`web/**`、`docs/00-prd.md`
- 环境事实：本机 `pwsh` 不可用（exit `0xC0000142`，DLL 初始化失败）→ 本文件所列命令**均未在本地执行**，`tests.verified=false`；证据由 CI 回填到 `reports/`

---

## 1. 目标、范围与证据链

| 项 | 内容 |
|---|---|
| 目标 | 让 AC-01..AC-22 每条都有唯一可追的用例 ID；让“契约漂移/缺失 AC/错误码不一致”在 CI 中必然变红 |
| 分层 | 契约（`tests/contract`）→ 接口（`server/tests`，BE 编写）→ 组件（`web/tests`，FE 编写）→ E2E（`tests/e2e`）→ 安全（`tests/security`）→ 性能（`tests/perf`） |
| 不变式 | 用例 ID 全局唯一；`AC → 用例` 单向可查；`implemented` 的用例必须在 `tests/` 存在实体（由 `tests/contract/test_ac_matrix.py` 强制） |
| 证据 | 每次门控产出 `docs/61-gate-report.md` 一节 + `reports/<gate>-<run>/` 下的 junit xml / 日志 / trace |

## 2. 测试环境与版本锁定

| 环境 | 用途 | 说明 |
|---|---|---|
| CI（GitHub Actions） | 契约/接口/安全/E2E/性能 | **唯一可信执行环境**（RISK-08）；Python 3.12、Node 20 |
| 本地 | 只做文档与只读核对 | shell 不可用，禁止尝试 `pytest` |
| staging（Compose） | G4 发布候选、备份恢复演练 | 与生产同构（ADR-008），独立项目名与库 |

依赖（`tests/requirements.txt`）：`pytest>=8.0`、`PyYAML>=6.0.1`、`openapi-spec-validator>=0.7.1`、`httpx>=0.27`、`playwright>=1.45`。

环境变量：

| 变量 | 默认 | 用途 |
|---|---|---|
| `EXAM_BASE_URL` | `http://127.0.0.1:8000/api` | 被测 API 根（含 `/api` 前缀，对应 `servers[0].url`） |
| `EXAM_E2E` | 未设置 | 置 `1` 才真正执行 E2E（未设置时整模块 skip，避免 CI 早红） |
| `EXAM_QA_PASSWORD` | 工厂默认值 | 测试账号统一密码，禁止硬编码生产口令 |
| `DATABASE_URL` | `sqlite:///./qa.db` | 接口层测试库；CI 用独立 schema/临时文件 |

## 3. 测试数据策略

### 3.1 账号矩阵（每个 run 重新生成，前缀 `qa-<run_id>-`）

| 账号 | 角色 | 用途 |
|---|---|---|
| `qa-stu-01` | student | 主链路作答、成绩、错题本（正例） |
| `qa-stu-02` | student | IDOR 负例：用 A 的 `session_id` 请求 result / session（AC-18、matrix §2.1） |
| `qa-stu-03` | student | 错题本 `wrong_count=2`：两场考试错同一题（AC-20） |
| `qa-stu-disabled` | student | 管理员停用后 8h 内再请求 → 401（AC-04） |
| `qa-tea-01` | teacher | 建题、组卷、发考试、看汇总（AC-05/09/10/21） |
| `qa-tea-02` | teacher | 越权写负例：改 tea-01 的题/卷 → 403（matrix §2.2） |
| `qa-admin-01` | admin | 建号/停用、审计查询（AC-21/22） |

### 3.2 题库 / 试卷 / 考试

- **题库**：10 题 = 4 单选 + 3 多选（2 题 `all_or_nothing`、1 题 `partial`）+ 3 判断；难度 1..5 覆盖；标签 `qa`、`l1`；全部带 `explanation`（AC-05）；判断题选项固定 `A=对/B=错`（docs/11 §2）。
- **试卷**：P100 = 10 题 × 10 分（合计 100，AC-09）；P3 = 3 题小卷，供快速链路与判分用例。
- **考试**（全部经 API 由 `qa-tea-01` 构造，不直写库）：

| ID | 时间窗（UTC） | 状态 | 用途 |
|---|---|---|---|
| E-LIVE | `start_at=now-5m`、`end_at=now+2h`、`duration=30` | published | 主链路、断网恢复、幂等交卷 |
| E-FUTURE | `start_at=now+1h` | published | AC-11 `EXAM_NOT_STARTED` |
| E-EXPIRED | `start_at=now-3h`、`duration=30`、`end_at=now-1h` | published | AC-14 `EXAM_EXPIRED` + 自动判分 |
| E-FROZEN | 引用 P100，`publish=true` | published | AC-10 改卷 → 409，旧考试不受影响 |
| E-DRAFT | `publish=false` | draft | matrix §2.4 未发布考试负例 |

- **时间构造**：`now = datetime.now(timezone.utc).replace(microsecond=0)`；断言允许 ±5s 漂移；**禁止**依赖本机时区（容器统一 `TZ=UTC`）。
- **隔离**：`run_id = f"{int(time.time())}-{uuid4().hex[:6]}"`；账号名、题干、试卷标题全部带前缀；CI 每 job 一个独立 DB schema，E2E 每 job 独立 Compose project。
- **清理**：接口层用事务回滚或重建 schema；E2E 保留数据（证据），由 job 结束统一 drop。
- **可重复**：工厂 `seed=42` 固定 → 同 seed 同数据；跨 run 由前缀区分，不靠删除保证唯一。

### 3.3 工厂设计（`tests/factories.py`，不导入任何生产代码）

| 工厂 | 产出 | 覆盖 |
|---|---|---|
| `AccountFactory` | `Account(username, display_name, role, password)`；`teacher()/student()/admin()/disabled_student()/idor_pair()` | AC-01/03/04/18 |
| `QuestionFactory` | 契约 `QuestionCreate` payload（`single/multiple/judge` 三种，合法选项与答案） | AC-05/16/17 |
| `PaperFactory` | `PaperCreate`（`points` 整数分，合计严格 100） | AC-09 |
| `ExamFactory` | `ExamCreate`（`live()/future()/expired()/draft()` 四种时间窗预设） | AC-10/11/14 |
| `ImportFileFactory` | CSV bytes：`bad_row=True` 制造 1 行缺答案；`duplicate=True` 制造重复行 | AC-06/08 |
| `AnswerScript` | 单调递增 `client_seq` 的保存请求序列 + 乱序（`stale`）序列 | AC-12/15 |
| `build_exam_scenario()` | 上述组合的完整场景（账号+题库+卷+考试+作答脚本） | E2E 全链路 |

工厂只产出**契约 JSON**，落库方式由调用方决定（走 API / 直连 DB / fixtures），因此 BE/FE 代码重构不影响测试数据。

## 4. AC-01..AC-22 全量映射

### 4.1 映射表（机器可读版本见 §4.2，`test_ac_matrix.py` 同时校验两者一致）

| AC 编号 | 用例 ID | 层级 | 依赖任务卡 | 是否可自动化 | 证据路径 |
|---|---|---|---|---|---|
| AC-01 | TC-CT-01、TC-API-01、TC-E2E-01 | 接口 | TASK-BE-001 | 是 | `reports/gate-g4/<run>/AC-01.log` |
| AC-02 | TC-CT-02、TC-API-02、TC-SEC-02 | 接口 | TASK-BE-001 | 是 | `reports/gate-g4/<run>/AC-02.log` |
| AC-03 | TC-CT-03、TC-API-03、TC-SEC-03 | 安全 | TASK-BE-001 | 是 | `reports/gate-g4/<run>/AC-03.log` |
| AC-04 | TC-CT-04、TC-API-04 | 接口 | TASK-BE-001 | 是 | `reports/gate-g4/<run>/AC-04.log` |
| AC-05 | TC-CT-05、TC-API-05、TC-E2E-01 | E2E | TASK-BE-002 | 是 | `reports/gate-g4/<run>/AC-05.log` |
| AC-06 | TC-CT-06、TC-API-06 | 接口 | TASK-BE-002 | 是 | `reports/gate-g4/<run>/AC-06.log` |
| AC-07 | TC-CT-07、TC-API-07 | 接口 | TASK-BE-002 | 是 | `reports/gate-g4/<run>/AC-07.log` |
| AC-08 | TC-CT-08、TC-API-08 | 接口 | TASK-BE-002 | 是 | `reports/gate-g4/<run>/AC-08.log` |
| AC-09 | TC-CT-09、TC-API-09、TC-E2E-02 | E2E | TASK-BE-003 | 是 | `reports/gate-g4/<run>/AC-09.log` |
| AC-10 | TC-CT-10、TC-API-10 | 接口 | TASK-BE-003 | 是 | `reports/gate-g4/<run>/AC-10.log` |
| AC-11 | TC-CT-11、TC-API-11 | 接口 | TASK-BE-004 | 是 | `reports/gate-g4/<run>/AC-11.log` |
| AC-12 | TC-CT-12、TC-E2E-04 | E2E | TASK-BE-004 | 是（Playwright `context.set_offline(true)`） | `reports/gate-g4/<run>/AC-12/` |
| AC-13 | TC-CT-13、TC-API-13、TC-E2E-05 | 接口 | TASK-BE-004 | 是 | `reports/gate-g4/<run>/AC-13.log` |
| AC-14 | TC-CT-14、TC-API-14 | 接口 | TASK-BE-004 | 是（时间窗注入，非 `sleep` 30min） | `reports/gate-g4/<run>/AC-14.log` |
| AC-15 | TC-CT-15、TC-API-15 | 接口 | TASK-BE-004 | 是 | `reports/gate-g4/<run>/AC-15.log` |
| AC-16 | TC-CT-16、TC-API-16 | 接口 | TASK-BE-005 | 是 | `reports/gate-g4/<run>/AC-16.log` |
| AC-17 | TC-CT-17、TC-API-17 | 接口 | TASK-BE-005 | 是 | `reports/gate-g4/<run>/AC-17.log` |
| AC-18 | TC-CT-18、TC-SEC-18 | 安全 | TASK-BE-004 | 是 | `reports/gate-g4/<run>/AC-18.log` |
| AC-19 | TC-CT-19、TC-API-19 | 接口 | TASK-BE-005 | 是 | `reports/gate-g4/<run>/AC-19.log` |
| AC-20 | TC-CT-20、TC-API-20 | 接口 | TASK-BE-005 | 是 | `reports/gate-g4/<run>/AC-20.log` |
| AC-21 | TC-CT-21、TC-SEC-21 | 安全 | TASK-BE-001 | 是 | `reports/gate-g4/<run>/AC-21.log` |
| AC-22 | TC-CT-22、TC-API-22 | 接口 | TASK-BE-001 | 是 | `reports/gate-g4/<run>/AC-22.log` |

**无法自动化的项（仅 3 条，均已给人工判据）**：TC-PERF-02 首屏 <2s（人工 + Lighthouse，PRD §5）、备份恢复演练 <30min（T4.3 手工 runbook + 计时）、审计 180 天保留的“不可被教师删除”需人工抽查（AC-21 的正例由 TC-SEC-21 覆盖）。

> 任务卡依赖说明：`TASK-BE-003/004/005`、`TASK-FE-002..006` 尚未在 `contracts/tasks/` 建卡（当前仅 BE-000/001/002、FE-000/001），映射表中的 BE-003/004/005 为**计划依赖**，派发前需 R0 补齐；编号沿用 `server/README.md` 与 `TASK-BE-001.next` 的约定。

### 4.2 机器可读 AC 矩阵（`test_ac_matrix.py` 解析此块；改映射必须同步改表）

<!-- AC-MATRIX:BEGIN -->
```yaml
version: 1
updated: "2026-11-09"
ac_total: 22
ac_matrix:
  - ac: AC-01
    summary: 教师正确密码登录 → 200 + user/csrf_token + HttpOnly session_id Cookie
    primary: TC-API-01
    cases:
      - {id: TC-CT-01, layer: 契约, depends_on: [TASK-QA-000], automated: true, status: implemented, evidence: "reports/gate-g4/<run>/TC-CT-01.log"}
      - {id: TC-API-01, layer: 接口, depends_on: [TASK-BE-001], automated: true, status: planned, evidence: "reports/gate-g4/<run>/AC-01.log"}
      - {id: TC-E2E-01, layer: E2E, depends_on: [TASK-BE-001, TASK-FE-001], automated: true, status: skeleton, evidence: "reports/gate-g4/<run>/e2e-01/"}
  - ac: AC-02
    summary: 密码错误 → 401，响应体不区分“账号不存在/密码错误”
    primary: TC-API-02
    cases:
      - {id: TC-CT-02, layer: 契约, depends_on: [TASK-QA-000], automated: true, status: implemented, evidence: "reports/gate-g4/<run>/TC-CT-02.log"}
      - {id: TC-API-02, layer: 接口, depends_on: [TASK-BE-001], automated: true, status: planned, evidence: "reports/gate-g4/<run>/AC-02.log"}
      - {id: TC-SEC-02, layer: 安全, depends_on: [TASK-BE-001], automated: true, status: planned, evidence: "reports/gate-g4/<run>/SEC-02.log"}
  - ac: AC-03
    summary: 学生 GET /api/users → 403 且不泄露用户数据
    primary: TC-SEC-03
    cases:
      - {id: TC-CT-03, layer: 契约, depends_on: [TASK-QA-000], automated: true, status: implemented, evidence: "reports/gate-g4/<run>/TC-CT-03.log"}
      - {id: TC-API-03, layer: 接口, depends_on: [TASK-BE-001], automated: true, status: planned, evidence: "reports/gate-g4/<run>/AC-03.log"}
      - {id: TC-SEC-03, layer: 安全, depends_on: [TASK-BE-001], automated: true, status: planned, evidence: "reports/gate-g4/<run>/SEC-03.log"}
  - ac: AC-04
    summary: 管理员停用账号后，该账号 8h 内再请求 → 401（会话即时失效）
    primary: TC-API-04
    cases:
      - {id: TC-CT-04, layer: 契约, depends_on: [TASK-QA-000], automated: true, status: implemented, evidence: "reports/gate-g4/<run>/TC-CT-04.log"}
      - {id: TC-API-04, layer: 接口, depends_on: [TASK-BE-001], automated: true, status: planned, evidence: "reports/gate-g4/<run>/AC-04.log"}
  - ac: AC-05
    summary: 教师新建含题干/选项/答案/解析的单选题 → 列表可见且字段完整
    primary: TC-E2E-01
    cases:
      - {id: TC-CT-05, layer: 契约, depends_on: [TASK-QA-000], automated: true, status: implemented, evidence: "reports/gate-g4/<run>/TC-CT-05.log", note: "C-10 已修复；断言 explanation 必填"}
      - {id: TC-API-05, layer: 接口, depends_on: [TASK-BE-002], automated: true, status: planned, evidence: "reports/gate-g4/<run>/AC-05.log"}
      - {id: TC-E2E-01, layer: E2E, depends_on: [TASK-BE-002, TASK-FE-001], automated: true, status: skeleton, evidence: "reports/gate-g4/<run>/e2e-01/"}
  - ac: AC-06
    summary: 导入含 1 行错误 → 200 部分成功 + 成功条数 + 失败行号与原因
    primary: TC-API-06
    cases:
      - {id: TC-CT-06, layer: 契约, depends_on: [TASK-QA-000], automated: true, status: implemented, evidence: "reports/gate-g4/<run>/TC-CT-06.log"}
      - {id: TC-API-06, layer: 接口, depends_on: [TASK-BE-002], automated: true, status: planned, evidence: "reports/gate-g4/<run>/AC-06.log"}
  - ac: AC-07
    summary: 题目被试卷引用时删除 → 409 QUESTION_IN_USE，不执行删除
    primary: TC-API-07
    cases:
      - {id: TC-CT-07, layer: 契约, depends_on: [TASK-QA-000], automated: true, status: implemented, evidence: "reports/gate-g4/<run>/TC-CT-07.log"}
      - {id: TC-API-07, layer: 接口, depends_on: [TASK-BE-002], automated: true, status: planned, evidence: "reports/gate-g4/<run>/AC-07.log"}
  - ac: AC-08
    summary: 同一导入文件重复提交 → 不产生重复题目（题干+类型幂等去重）
    primary: TC-API-08
    cases:
      - {id: TC-CT-08, layer: 契约, depends_on: [TASK-QA-000], automated: true, status: implemented, evidence: "reports/gate-g4/<run>/TC-CT-08.log", note: "C-11 已修复；判定键 skipped_duplicates"}
      - {id: TC-API-08, layer: 接口, depends_on: [TASK-BE-002], automated: true, status: planned, evidence: "reports/gate-g4/<run>/AC-08.log"}
  - ac: AC-09
    summary: 勾选 10 题设分值合计 100 → 保存成功且 total_points=100
    primary: TC-E2E-02
    cases:
      - {id: TC-CT-09, layer: 契约, depends_on: [TASK-QA-000], automated: true, status: implemented, evidence: "reports/gate-g4/<run>/TC-CT-09.log", note: "C-08 已修复；分值/合计约束已断言"}
      - {id: TC-API-09, layer: 接口, depends_on: [TASK-BE-003], automated: true, status: planned, evidence: "reports/gate-g4/<run>/AC-09.log"}
      - {id: TC-E2E-02, layer: E2E, depends_on: [TASK-BE-003, TASK-FE-001], automated: true, status: skeleton, evidence: "reports/gate-g4/<run>/e2e-02/"}
  - ac: AC-10
    summary: 已用于已发布考试的试卷再改 → 409，旧考试数据不受影响（快照）
    primary: TC-API-10
    cases:
      - {id: TC-CT-10, layer: 契约, depends_on: [TASK-QA-000], automated: true, status: implemented, evidence: "reports/gate-g4/<run>/TC-CT-10.log"}
      - {id: TC-API-10, layer: 接口, depends_on: [TASK-BE-003], automated: true, status: planned, evidence: "reports/gate-g4/<run>/AC-10.log"}
  - ac: AC-11
    summary: 考试未到开始时间创建会话 → 409 EXAM_NOT_STARTED
    primary: TC-API-11
    cases:
      - {id: TC-CT-11, layer: 契约, depends_on: [TASK-QA-000], automated: true, status: implemented, evidence: "reports/gate-g4/<run>/TC-CT-11.log", note: "C-22 码表 201/200/409/404 已断言"}
      - {id: TC-API-11, layer: 接口, depends_on: [TASK-BE-004], automated: true, status: planned, evidence: "reports/gate-g4/<run>/AC-11.log"}
  - ac: AC-12
    summary: 作答中断网 30s 后恢复保存 → 已答内容不丢且可继续交卷
    primary: TC-E2E-04
    cases:
      - {id: TC-CT-12, layer: 契约, depends_on: [TASK-QA-000], automated: true, status: implemented, evidence: "reports/gate-g4/<run>/TC-CT-12.log", note: "C-01 已修复；answers 必填"}
      - {id: TC-E2E-04, layer: E2E, depends_on: [TASK-BE-004, TASK-FE-001], automated: true, status: skeleton, evidence: "reports/gate-g4/<run>/e2e-04/"}
  - ac: AC-13
    summary: 已提交会话再次提交 → 幂等成功（同一成绩），不重复计分、不产生第二条成绩
    primary: TC-API-13
    cases:
      - {id: TC-CT-13, layer: 契约, depends_on: [TASK-QA-000], automated: true, status: implemented, evidence: "reports/gate-g4/<run>/TC-CT-13.log", note: "C-15 已修复；submit 无 409、replayed 必填（v1.0.2）"}
      - {id: TC-API-13, layer: 接口, depends_on: [TASK-BE-004], automated: true, status: planned, evidence: "reports/gate-g4/<run>/AC-13.log"}
      - {id: TC-E2E-05, layer: E2E, depends_on: [TASK-BE-005], automated: true, status: skeleton, evidence: "reports/gate-g4/<run>/e2e-05/"}
  - ac: AC-14
    summary: 时长到达后继续保存 → 409 EXAM_EXPIRED，服务端自动提交并判分
    primary: TC-API-14
    cases:
      - {id: TC-CT-14, layer: 契约, depends_on: [TASK-QA-000], automated: true, status: implemented, evidence: "reports/gate-g4/<run>/TC-CT-14.log", note: "C-13 已修复；GET /sessions 200+expired+score"}
      - {id: TC-API-14, layer: 接口, depends_on: [TASK-BE-004], automated: true, status: planned, evidence: "reports/gate-g4/<run>/AC-14.log"}
  - ac: AC-15
    summary: 较小 client_seq 覆盖较新答案 → 拒绝覆盖，仅接受更大 seq
    primary: TC-API-15
    cases:
      - {id: TC-CT-15, layer: 契约, depends_on: [TASK-QA-000], automated: true, status: implemented, evidence: "reports/gate-g4/<run>/TC-CT-15.log", note: "C-16 已修复；applied 必填（v1.0.2）"}
      - {id: TC-API-15, layer: 接口, depends_on: [TASK-BE-004], automated: true, status: planned, evidence: "reports/gate-g4/<run>/AC-15.log"}
  - ac: AC-16
    summary: 单选/判断：正确满分、错误 0 分
    primary: TC-API-16
    cases:
      - {id: TC-CT-16, layer: 契约, depends_on: [TASK-QA-000], automated: true, status: implemented, evidence: "reports/gate-g4/<run>/TC-CT-16.log"}
      - {id: TC-API-16, layer: 接口, depends_on: [TASK-BE-005], automated: true, status: planned, evidence: "reports/gate-g4/<run>/AC-16.log"}
  - ac: AC-17
    summary: 多选全对给分/部分给分两档；部分给分 = 选中正确数/正确总数×分值，2 位小数
    primary: TC-API-17
    cases:
      - {id: TC-CT-17, layer: 契约, depends_on: [TASK-QA-000], automated: true, status: implemented, evidence: "reports/gate-g4/<run>/TC-CT-17.log"}
      - {id: TC-API-17, layer: 接口, depends_on: [TASK-BE-005], automated: true, status: planned, evidence: "reports/gate-g4/<run>/AC-17.log"}
  - ac: AC-18
    summary: 学生 B 请求学生 A 的 result → 403（IDOR）
    primary: TC-SEC-18
    cases:
      - {id: TC-CT-18, layer: 契约, depends_on: [TASK-QA-000], automated: true, status: implemented, evidence: "reports/gate-g4/<run>/TC-CT-18.log", note: "C-17 已修复；IDOR=403/不存在=404"}
      - {id: TC-SEC-18, layer: 安全, depends_on: [TASK-BE-004], automated: true, status: planned, evidence: "reports/gate-g4/<run>/SEC-18.log"}
  - ac: AC-19
    summary: 未交卷请求 result → 409 NOT_SUBMITTED
    primary: TC-API-19
    cases:
      - {id: TC-CT-19, layer: 契约, depends_on: [TASK-QA-000], automated: true, status: implemented, evidence: "reports/gate-g4/<run>/TC-CT-19.log"}
      - {id: TC-API-19, layer: 接口, depends_on: [TASK-BE-005], automated: true, status: planned, evidence: "reports/gate-g4/<run>/AC-19.log"}
  - ac: AC-20
    summary: 同题错两次 → 错题本仅一条且 wrong_count=2
    primary: TC-API-20
    cases:
      - {id: TC-CT-20, layer: 契约, depends_on: [TASK-QA-000], automated: true, status: implemented, evidence: "reports/gate-g4/<run>/TC-CT-20.log"}
      - {id: TC-API-20, layer: 接口, depends_on: [TASK-BE-005], automated: true, status: planned, evidence: "reports/gate-g4/<run>/AC-20.log"}
  - ac: AC-21
    summary: 教师查审计 → 403；管理员查 → 200（近 180 天）
    primary: TC-SEC-21
    cases:
      - {id: TC-CT-21, layer: 契约, depends_on: [TASK-QA-000], automated: true, status: implemented, evidence: "reports/gate-g4/<run>/TC-CT-21.log"}
      - {id: TC-SEC-21, layer: 安全, depends_on: [TASK-BE-001], automated: true, status: planned, evidence: "reports/gate-g4/<run>/SEC-21.log"}
  - ac: AC-22
    summary: 任意写操作后审计存在 actor/action/target/ip/时间
    primary: TC-API-22
    cases:
      - {id: TC-CT-22, layer: 契约, depends_on: [TASK-QA-000], automated: true, status: implemented, evidence: "reports/gate-g4/<run>/TC-CT-22.log", note: "C-05 已修复；actor_id/ip 必填"}
      - {id: TC-API-22, layer: 接口, depends_on: [TASK-BE-001], automated: true, status: planned, evidence: "reports/gate-g4/<run>/AC-22.log"}
```
<!-- AC-MATRIX:END -->

### 4.3 跨 AC 的非功能用例

| 用例 ID | 内容 | 层级 | 可自动化 |
|---|---|---|---|
| TC-CT-00 | `openapi-spec-validator` 结构校验 + `$ref` 全解析 + operationId 唯一 + 全局 security/CSRF/分页一致性 | 契约 | 是 |
| TC-CT-23 | 所有 4xx 响应引用统一 `Error` schema | 契约 | 是 |
| TC-CT-24 | 所有声明 `X-CSRF-Token` 的写接口同时声明 CSRF 失败 403（C-09 已修复） | 契约 | 是 |
| TC-CT-25 | `Error.code` 可枚举且覆盖 16 个稳定错误码（C-03/v1.0.2 移除 ALREADY_SUBMITTED） | 契约 | 是 |
| `test_c02_me_exams_contract` | `/me/exams` 存在、`session_status` 枚举、仅 student / 教师 403（C-02） | 契约 | 是 |
| `test_c21_response_objects_minimized` | 5 个关键响应对象 `additionalProperties: false`，无敏感字段（C-21） | 契约 | 是 |
| TC-SEC-24 | 题干/解析 XSS 输出转义 | 安全 | 是 |
| TC-SEC-25 | 搜索/分页参数 SQL 注入负例 | 安全 | 是 |
| TC-SEC-26 | argon2id 哈希、弱密码拒绝、会话 8h 过期 | 安全 | 是 |
| TC-PERF-01 | 100 并发保存/读取 P95 < 300ms | 性能 | 是（locust/k6） |
| TC-PERF-02 | 内网首屏 < 2s | 性能 | 否（人工 + Lighthouse） |

## 5. E2E 关键链路（G4 必过，`tests/e2e/test_exam_flow.py`）

```
TC-E2E-01 教师登录(qa-tea-01) → 新建 3 题（单选/多选/判断，含解析）→ 列表校验字段完整      [AC-01/05]
TC-E2E-02 组卷（快链 3 题 / 全量 10 题，分值合计 100）→ total_points=100 → 发布考试 → 试卷冻结  [AC-09/10]
TC-E2E-03 学生登录(qa-stu-01) → POST /exams/{id}/sessions 新建会话 → 取题 → 保存答案 seq=1  [AC-01]
TC-E2E-04 断网模拟：拦截/丢弃保存请求 30s → 恢复 → 重放 seq=2 → 服务端 saved_seq=2           [AC-12]
TC-E2E-05 交卷 → 判分 → 逐题 result → 我的成绩 → 错题本 → 教师汇总 → 重复交卷幂等             [AC-13/16/17/19/20]
```

操作细节：

1. **断网模拟**：Playwright 用 `context.set_offline(True)`（或 `page.route` 丢弃 `/answers` 请求）30s 后恢复；httpx 版用“本地队列 + 不发送”模拟，重连后重放同一 `client_seq` 序列。断言：恢复后 `GET /sessions/{id}` 返回的已存答案与断网前一致（**C-01 已修复**：`SessionState.answers` 必填，可直接断言）。
2. **重复交卷**：同一 `session_id` 连续 `POST /submit` 两次，断言两次 200、`score` 相同、`exam_sessions` 仍只有 1 条（教师汇总 `total_sessions` 不变）。
3. **到时自动交卷**：用 E-EXPIRED（`start_at=now-3h`）省去等待；断言保存返回 409 且 `code=EXAM_EXPIRED`，随后 `GET /result` 返回 200 且已判分。
4. **幂等与并发**：AC-13 另加 2 线程同时 submit（`tests/security` 或接口层），断言仅一条成绩、无 5xx。

## 6. 门控执行顺序与 G4 放行判据

### 6.1 执行顺序（严格串行，前者红则后者不跑）

| 序 | 门 | 命令（CI，bash） | 失败即停 |
|---|---|---|---|
| 1 | G2 契约 | `python -m pytest tests/contract -q --junitxml=reports/gate-g2/contract.xml` | 是 |
| 2 | G3 模块 | `cd server && python -m pytest -q --junitxml=../reports/gate-g3/server.xml`；`cd web && pnpm test -- --run` | 是 |
| 3 | G4-契约 | `python -m pytest tests/contract -q --junitxml=reports/gate-g4/contract.xml` | 是 |
| 4 | G4-接口 | `python -m pytest server/tests -q --junitxml=reports/gate-g4/server.xml` | 是 |
| 5 | G4-安全 | `python -m pytest tests/security -q --junitxml=reports/gate-g4/security.xml` | 是 |
| 6 | G4-E2E | `EXAM_E2E=1 EXAM_BASE_URL=http://127.0.0.1:8080/api python -m pytest tests/e2e -q --junitxml=reports/gate-g4/e2e.xml` | 是 |
| 7 | G4-性能 | `python -m pytest tests/perf -q`（或 `k6 run tests/perf/save_read_100.js`） | 是 |
| 8 | G4-运维 | `docker compose -f deploy/docker-compose.yml config`；备份恢复演练（T4.3）；回滚演练 | 是 |
| 9 | G4-人工 | 首屏 Lighthouse、审计抽查（180 天保留 / 教师不可删） | 否（记 TODO） |

> PowerShell 等价：`$env:EXAM_E2E="1"; $env:EXAM_BASE_URL="http://127.0.0.1:8080/api"; python -m pytest tests/e2e -q`。本机 shell 不可用，上述命令**须在 CI 执行**。

### 6.2 G4 放行判据（全部满足才可发布候选）

- [ ] `tests/contract` **0 failed**；xfail 仅限本文件 §8 已登记的契约缺陷（当前 **0 个**：C-01..C-22 已按 v1.0.1 处置），且缺陷单状态为 `accepted-by-R2` 或已修复
- [ ] AC 矩阵 22/22 覆盖，`implemented` 用例在 `tests/` 全部存在（`test_ac_matrix.py` 绿）
- [ ] `server/tests` 0 failed；P0 主链路接口用例全绿
- [ ] `tests/security` 0 failed：AC-03/18/21 + IDOR + CSRF + 注入 + XSS + 弱密码
- [ ] E2E 主链路连续 2 次通过；含断网恢复、重复交卷、到时自动交卷
- [ ] 性能：100 并发下保存/读取 P95 < 300ms（PRD §5）
- [ ] 备份恢复演练 < 30min，回滚演练 < 15min（runbook 已写、已演）
- [ ] 无 P0/P1 缺陷；P2 缺陷有登记与规避方案
- [ ] 证据齐全：命令 + 退出码 + 通过/失败数 + 覆盖率 + 缺陷单 + 回滚点 commit（`docs/61-gate-report.md` 全部回填）
- [ ] 契约缺陷 C-01..C-22 已由 R2 给出处置（修复 / ADR 接受 / 明确延后），未处置项不得新增

## 7. 证据存放约定

```
reports/
  gate-g2/<date>/        contract.xml + contract.log
  gate-g3/<date>/        server.xml + web.log
  gate-g4/<date>/        contract.xml server.xml security.xml e2e.xml perf.log
    e2e-01..05/          截图、playwright trace.zip、失败重跑日志
  gate-g5/<date>/        staging 冒烟 + 24h 监控截图
```

命名：`<gate>-<yyyymmdd-HHMM>-<git-sha7>/`；junit xml 必须保留（用于统计 passed/failed/skipped/xfail）。

## 8. 契约缺陷清单（QA 只报不修，逐行审查 `contracts/openapi.yaml`）

> v1.0.1（2026-11-09）处置后状态见 §8.1；R2 的逐条变更见 docs/13-contract-changes.md 与 ADR-009。
> 本轮 22 条均为 `已修复（待 CI 验证）`，无 `仍存在` 项；C-22 的 `EXAM_NOT_PUBLISHED` 以 404 防枚举替代。
> 问题列中的 `Lxxx` 为 v1.0.0 审查时的行号，v1.0.1 已重排，定位以字段/接口名为准。

| ID | 关联 AC/文档 | 问题 | 建议 |
|---|---|---|---|
| C-01 | AC-12（PRD L75） | `SessionState`（L991-1005）无 `answers`/`selected`，`AnswerSaveResponse`（L1026-1031）只回 `saved_seq/saved_at`，`GET /sessions/{id}/answers` 不存在 → **服务端已保存答案无法回读**，“断线恢复不丢答案”无法用接口证明 | `SessionState` 增 `answers: AnswerItem[]`（或 `SessionQuestion.selected`），或新增 `GET /sessions/{id}/answers` |
| C-02 | 主链路 / matrix L19 | 学生无权 `GET /exams`，且无 `/me/exams` 一类只读发现接口；`POST /exams/{id}/sessions` 又需要 `exam_id` → **学生无法得知可参加哪些考试** | 增 `GET /me/exams`（只读、仅 published 且时间窗内） |
| C-03 | AC-02/07/11/13/14/15/19/21 | `Error.code`（L739-741）仅有 `type: string` + `examples`，**未枚举**；`QUESTION_IN_USE`、`EXAM_NOT_STARTED`、`EXAM_EXPIRED`、`NOT_SUBMITTED`、`CSRF_INVALID`、`AUTH_INVALID_CREDENTIALS` 等均不可被契约约束 | `code` 改 `enum`（或每响应用 `oneOf` 固定 code 集合），并补 `AUTH_INVALID_CREDENTIALS`/`CSRF_INVALID`/`NOT_SUBMITTED`/`PAPER_FROZEN` |
| C-04 | AC-10 / RBAC | `Exam`（L930-943）required 无 `created_by`/`owner_id`，而 `Paper` 有 `owner_id`（L897）→ 教师“仅自己的考试”在契约层不可见、响应也无法审计归属 | `Exam` 增 `created_by: integer` 并加 required |
| C-05 | AC-21/22、docs/11 L137-149 | `AuditLog`（L1135-1145）required 仅 `[id, action, target_type, target_id, created_at]`，**`actor_id`/`ip` 可选**；且缺 `user_agent`/`detail`；`/audit-logs` 未定义“默认近 180 天” | `actor_id`/`ip` 提为 required；补 `user_agent`/`detail`；description 写死 180 天默认范围 |
| C-06 | AC-20 邻接 / 成绩导航 | `MyResultItem`（L1073-1081）无 `session_id` → 成绩列表无法跳到逐题结果（需 `/sessions/{id}/result`） | 增 `session_id` |
| C-07 | AC-02 | 登录失败复用通用 `Error`，未规定统一 `AUTH_INVALID_CREDENTIALS` 且 `message` 不得区分账号存在性 → “不区分账号不存在/密码错误”只靠实现约定 | 在 `401` 响应 description 固化单一 code 与统一 message |
| C-08 | AC-09 | `PaperQuestion.points`（L892）无 `exclusiveMinimum: 0`；`PaperCreate` 无“合计=100”约束说明；`Paper.total_points` 无计算口径 → 组卷校验不可测 | `points` 加 `exclusiveMinimum: 0`；description 写明 `total_points=Σpoints` 与 100 分校验 |
| C-09 | AC-03/22、docs/12 L40 | `X-CSRF-Token` 为 required header（L675-680），但 `/auth/logout`（L70-79）未声明 403；其他写接口的 403 与角色越权共用，未区分 `CSRF_INVALID` | 所有声明 CSRF 的写接口补 `403`，并在 description 标明 `CSRF_INVALID` |
| C-10 | AC-05 | `QuestionCreate`（L838-856）required 不含 `explanation`，响应 `Question` 也非必填 → 契约允许“无解析”的题 | `explanation` 提为 required（或明确 AC-05 允许空解析并由 PRD 修订） |
| C-11 | AC-08 | `ImportResult`（L876-884）仅 `imported/failed/total`，无“去重跳过数”；`total` 口径未定义（文件行数 vs 有效行数）→ 第二次导入的“0 新增”与“全失败”不可区分 | 增 `skipped`（或 `duplicates`）并定义 `total` |
| C-12 | AC-10 | `Paper.status` 有 `frozen`（L903）但无 `frozen_at`/`snapshot_version`，也无快照字段 → “考试持有快照、旧数据不受影响”无法由响应证明 | `Paper` 增 `frozen_at`；`Exam` 增 `snapshot_version`（或 `paper_snapshot_id`） |
| C-13 | AC-14 | `EXAM_EXPIRED` 返回 409 仅有 `Error`（code/message/request_id），无 `score`/`session_status`/`submitted_at`；`AnswerSaveResponse` 无过期标志 → 客户端收到 409 不知道已自动交卷、无法跳成绩页 | 409 响应改专用 schema（含 `code=EXAM_EXPIRED`、`session_status=expired`、`score`），或允许保存返回 200 + `expired: true` |
| C-14 | AC-17 | `PaperQuestion.partial_rule`（L893）非 required、无 `default`；`points`/`earned_points` 无 `multipleOf: 0.01` → 两档规则默认值与“2 位小数”不可断言 | `partial_rule` 加 `default: all_or_nothing` 并写明仅多选生效；分数加 `multipleOf: 0.01` |
| C-15 | AC-13 / ADR-005 L34 | `POST /sessions/{id}/submit`（L555-572）同时声明 `200` 与 `409`，summary 称“幂等”；ADR-005 规定重复提交返回 200 既有成绩 → **契约自相矛盾**，未说明 409 的触发条件；`SubmitResult` 无 `replayed/idempotent` 标志 | 明确“重复提交=200 同一成绩”，删 409 或限定为“会话不存在/非本人”；`SubmitResult` 增 `already_submitted: boolean` |
| C-16 | AC-15 | `AnswerSaveResponse`（L1026-1031）无 `applied` 标志，客户端只能比较 `saved_seq != client_seq` 才能判断旧 seq 被忽略 | 增 `applied: boolean`，或旧 seq 返回 `409 STALE_SEQ`（二选一并写死） |
| C-17 | AC-03/18、docs/12 L5 | 权限矩阵要求“越权不泄露资源是否存在”，AC 要求 403；`/sessions/{id}/result` 同时声明 403（L588）与 404（L589），未规定 IDOR 场景返回哪个 → 安全用例无确定期望值 | 明确：资源存在但非本人 → 403；不存在 → 404；并在 description 固化 |
| C-18 | docs/11 L154 状态机 | `ExamPatch.status`（L965）允许任意 `draft/published/closed` 跳转，无非法迁移的 409；`Exam` 无 `updated_at` | 补状态迁移规则与 409；`Exam` 增 `updated_at` |
| C-19 | 全链路错误处理 | 错误响应不一致：`PATCH /users/{id}`、`PATCH /exams/{id}` 无 422（而 `POST /users`、`PUT /papers` 有）；`GET /healthz` 无 503（DB 不可用时健康检查如何失败未定义，deploy 依赖它） | 统一参数校验 422；`/healthz` 补 `503` |
| C-20 | AC-14 | 状态枚举不一致：`SessionState.status`=[in_progress,submitted,expired]（L997），`SubmitResult.status`=[submitted,expired]（L1038），`ExamResultSummary.items[].status` 复用三值（L1131）→ 自动交卷后会话状态未定义 | 统一枚举，并说明 `expired` 是否等价 `submitted` 可读 |
| C-21 | NFR 输出最小化 | 所有对象默认 `additionalProperties: true`，登录/`me`/会话响应即使误带 `password_hash` 或其他学生数据，契约测试也发现不了 | 对 `User`/`LoginResponse`/`MeResponse`/`SessionState`/`ResultItem` 设 `additionalProperties: false` |
| C-22 | AC-11/13/14 | `POST /exams/{id}/sessions` 同时声明 200/201/409，但未区分“幂等恢复=200”“新建=201”“未开始=409”“已截止=409”“草稿考试=404/403（matrix §2.4）” → 客户端与测试无法确定期望码 | 逐状态写死返回码与 code（`EXAM_NOT_STARTED`/`EXAM_EXPIRED`/`EXAM_NOT_PUBLISHED`） |

### 8.1 v1.0.1 处置状态（2026-11-09，待 CI 验证）

> 口径：`已修复（待 CI 验证）` = 契约已落地且 `tests/contract` 已有硬断言或静态核对通过；`仍存在（原因）` = 未处置。  
> **本轮 22/22 均为前者**；CI 重跑 `python -m pytest tests/contract -q` 并通过后，才可把“待 CI 验证”改为“已验证”。

| ID | 状态 | 契约落点 / QA 验证 |
|---|---|---|
| C-01 | 已修复（待 CI 验证） | `SessionState.answers` 必填 + `submitted_at`；TC-CT-12 已去 xfail |
| C-02 | 已修复（待 CI 验证） | `GET /me/exams` + `MyExamItem.session_status` 四值枚举；`test_c02_me_exams_contract`、docs/12 对齐用例 |
| C-03 | 已修复（待 CI 验证） | `Error.code` 17 值 enum；TC-CT-25 已去 xfail 并锁定枚举 |
| C-04 | 已修复（待 CI 验证） | `Exam.created_by` 进 required；契约静态核对 |
| C-05 | 已修复（待 CI 验证） | `AuditLog.actor_id/ip` required + `user_agent/detail` + 180 天口径；TC-CT-22 已去 xfail |
| C-06 | 已修复（待 CI 验证） | `MyResultItem.session_id` 进 required；契约静态核对 |
| C-07 | 已修复（待 CI 验证） | `InvalidCredentials` 固定 `AUTH_INVALID_CREDENTIALS` + 固定 message；TC-CT-02 |
| C-08 | 已修复（待 CI 验证） | `points > 0`/上限/`multipleOf`、`total_points = Σpoints`、422 口径；TC-CT-09 已去 xfail |
| C-09 | 已修复（待 CI 验证） | `/auth/logout` 及所有写接口 403 `CSRF_INVALID`；TC-CT-24 已去 xfail |
| C-10 | 已修复（待 CI 验证） | `QuestionCreate/Question.explanation` required；TC-CT-05 已去 xfail |
| C-11 | 已修复（待 CI 验证） | `ImportResult.skipped_duplicates` required + `total` 公式；TC-CT-08 判定键已改并去 xfail |
| C-12 | 已修复（待 CI 验证） | `Paper.frozen_at` / `Exam.snapshot_version` 已增（optional）；契约静态核对 |
| C-13 | 已修复（待 CI 验证） | `GET /sessions/{id}` 到期返回 200 + `expired` + `score`，错误体不扩字段；TC-CT-14 已扩展 |
| C-14 | 已修复（待 CI 验证） | `partial_rule.default` + 分值 `multipleOf: 0.01`；TC-CT-09/17 |
| C-15 | 已修复（待 CI 验证） | submit 删除 409；`SubmitResult.replayed` optional；TC-CT-13 已扩展 |
| C-16 | 已修复（待 CI 验证） | `AnswerSaveResponse.applied`（optional）；TC-CT-15 已扩展 |
| C-17 | 已修复（待 CI 验证） | `Forbidden`/`NotFound` 判定顺序：IDOR=403、不可见=404；TC-CT-18 已扩展 |
| C-18 | 已修复（待 CI 验证） | `ExamPatch` 状态机 + 422、`Exam.updated_at`；契约静态核对 |
| C-19 | 已修复（待 CI 验证） | `PATCH /users/{id}`、`PATCH /exams/{id}` 补 422，`/healthz` 补 503；契约静态核对 |
| C-20 | 已修复（待 CI 验证） | `SessionState`/`SubmitResult`/`ExamResultSummary` 三处状态枚举统一；TC-CT-14 + 静态核对 |
| C-21 | 已修复（待 CI 验证） | `User/LoginResponse/MeResponse/SessionState/ResultItem` 设 `additionalProperties: false`；`test_c21_response_objects_minimized` |
| C-22 | 已修复（待 CI 验证） | 会话创建 201/200/409/404 码表 + 幂等；`EXAM_NOT_PUBLISHED` 以 404 替代；TC-CT-11 已扩展 |

## 9. 风险与假设

- RISK-08（本机 shell 不可用）已发生：本文件所有“通过”结论均需 CI 证据，**不得**以本地推断替代。
- 依赖任务卡缺口：`TASK-BE-003/004/005`、`TASK-FE-002..006` 未建卡；AC-09..AC-20 的接口/E2E 用例在卡补齐前只能停留在 `planned`。
- 时间敏感用例（AC-11/14）统一用“构造过去/未来时间窗”方式，不用 `sleep`，避免 CI 抖动。
- 并发与幂等（AC-13/15）在 SQLite 上语义弱于 PostgreSQL（docs/11 L3）→ G4 前必须在 PostgreSQL 上复跑一次。
