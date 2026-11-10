# 10 · 架构决策记录（ADR）与模块边界

- 版本：v1.0（草案）　作者：R2 Architect（bootstrap 阶段由 Lead 代行）　日期：2026-11-08
- 约束：2 周 / 1–2 人 / 单机构 / 同时在线 ≤100 / 生产级最小集
- 契约：`contracts/openapi.yaml` v1.0 为本文件的可执行部分

## ADR-001 技术栈与仓库形态

**决策**：单仓库（monorepo），后端 `server/`（FastAPI + SQLAlchemy 2 + Alembic + Pydantic v2 + pytest），前端 `web/`（React 18 + TS + Vite + Ant Design + TanStack Query），部署 `deploy/`（Docker Compose + Nginx）。
**理由**：前后端同一 PR 可改契约与实现；避免多仓库同步成本；2 周内不可引入额外基础设施。
**后果**：CI 用路径过滤（`server/**` 与 `web/**` 分开跑）；契约测试作为跨目录统一门。

## ADR-002 模块化单体，不拆微服务

**决策**：单进程 FastAPI 应用，内部按领域包分层：`auth / users / questions / papers / exams / sessions / scoring / reports / audit`。
**理由**：100 并发与 2 周约束下，微服务的部署、可观测性与一致性成本远大于收益；单体 + 清晰模块边界后续可拆。
**后果**：禁止跨模块直接访问他人 ORM 模型（走 service 层）；跨模块事务仅限同一用例内。

## ADR-003 契约优先（OpenAPI 为唯一真相）

**决策**：R2 冻结 `contracts/openapi.yaml`；前端类型由 `openapi-typescript` 生成；后端用 Pydantic 模型映射契约，并由 `pytest` 契约测试比对响应 schema 与错误码。
**理由**：3 个 Agent 角色（FE/BE/QA）并行时，唯一稳定共享物是契约文件；避免“以口头描述为准”的漂移。
**后果**：改接口 = 改契约 + 重过 G2；不允许 Worker 先写代码再补契约。

## ADR-004 认证：服务端 Session + HttpOnly Cookie + CSRF 双提交

**决策**：登录成功后服务端存 `auth_sessions` 记录，下发 `session_id`（HttpOnly, Secure, SameSite=Lax, 8h）；非 GET 请求必须带 `X-CSRF-Token`（与会话绑定的随机值，前端从 `GET /auth/me` 获取）。
**备选**：JWT（免存储、易横向扩展，但吊销与刷新复杂）。
**理由**：管理员停用账号需即时生效（AC-04）；单机部署无扩展诉求；CSRF 双提交实现成本低。
**后果**：会话表需清理任务；审计中间件记录 `actor_id`；前端请求层统一注入 CSRF 头。

## ADR-005 交卷幂等

**决策**：`exam_sessions.status ∈ {in_progress, submitted, expired}`；交卷在单事务内 `SELECT ... FOR UPDATE` 校验状态，`submitted` 再提交直接返回既有成绩（200，非 409）。`(exam_id, student_id)` 唯一约束兜底。
**理由**：AC-13 要求重复提交不重复计分；网络重试是常态。
**后果**：交卷接口必须带 `Idempotency` 语义说明；成绩计算与状态变更在同一事务。

## ADR-006 自动保存防乱序

**决策**：答案保存请求带 `client_seq`（单调递增整数）；服务端只接受 `client_seq > last_client_seq` 的请求，否则返回 200 但不覆盖（返回当前 `saved_seq`）。
**理由**：弱网下请求乱序到达，后发先至的旧数据会覆盖新答案（AC-15）。
**后果**：前端维护 seq；服务端 `last_client_seq` 落库；并发写按会话行锁。

## ADR-007 判分规则

**决策**：提交时在事务内逐题计算并落 `answers.is_correct / earned_points`，成绩汇总到 `exam_sessions.score`；判分规则随试卷快照冻结（`paper_snapshot` JSON），不随题库后续编辑变化。
**多选规则**：`all_or_nothing`：全对得满分，其余 0；`partial`：无错选时 `选中正确数 / 正确总数 × 分值`，四舍五入 2 位小数；有错选 0 分。
**理由**：AC-10/AC-17；成绩必须可重算且与当时试卷一致。
**后果**：`papers.frozen_snapshot` 在考试发布时写入；禁止运行时改判分公式。

## ADR-008 部署拓扑

**决策**：Docker Compose 四服务：`nginx`（TLS 终止 + 静态资源 + 反代 `/api`）、`api`（uvicorn/gunicorn）、`postgres`、`backup`（每日 pg_dump 到挂载卷，保留 7 天）。staging 与 prod 同构，仅域名/数据不同。
**理由**：R6 一人可运维；备份演练可重复；无 K8s 需求。
**后果**：`deploy/docker-compose.yml` 与 `deploy/nginx.conf`；`docs/70-runbook.md` 提供上线/回滚步骤；健康检查 `/healthz`。

## ADR-009 v1.0.1 契约修订决策（承接 C-01..C-22）

- 状态：**Accepted**　日期：2026-11-09　作者：R2 Architect
- 输入：`docs/60-test-plan.md` §8 契约缺陷清单（TASK-QA-000，只报不修）、`contracts/openapi.yaml` v1.0.0
- 输出：`contracts/openapi.yaml` **v1.0.1**；逐条 CHG 见 `docs/13-contract-changes.md`；权限矩阵同步至 `docs/12` v1.1

**决策 1 —— 恢复会话必须回读服务端答案（C-01 / CHG-001）**
`SessionState` 增 `answers: [{question_id, selected}]`（required）与 `submitted_at`（可空）。理由：AC-12 要求断网/刷新后“已答内容不丢”，而 v1.0.0 只有 `saved_seq`，前端只能靠本地缓存自证，无法由接口证明；不新增 `GET /sessions/{id}/answers` 以免多一个权限面。
后果：`GET /sessions/{id}` 与 `POST /exams/{id}/sessions`(200) 必须回填答案；E2E 断言恢复后 `answers` 与断网前一致。

**决策 2 —— 学生需要只读发现入口 `GET /me/exams`（C-02 / CHG-003）**
学生无权 `GET /exams`，而建会话需要 `exam_id`，主链路在契约层断裂。新增 `GET /me/exams`（仅 student，只读）：返回 `published` 全部考试 + 该生已有会话的 `closed` 考试，带 `session_status ∈ {not_started,in_progress,submitted,expired}` 与 `score`/`session_id`；不含 `draft`。
后果：前端“可考/已考”列表只看一个接口；教师仍走 `GET /exams`。

**决策 3 —— 到期自动交卷必须可观测，但不扩错误体（C-13 / CHG-004）**
到期后服务端在任何会话读写路径上同步触发自动交卷与判分（幂等）；`GET /sessions/{id}` 返回 **200** 且 `status=expired`、`score` 非空、`submitted_at` 非空。**拒绝**“409 专用错误体”方案：`tests/contract/test_error_codes.py` R1 要求所有 4xx 共用同一个 `Error`（code/message/request_id），扩字段会破坏全局一致性与防泄露约束。
后果：客户端不再解析 409 判断“是否已交卷”；`PUT /answers` 的 409 `EXAM_EXPIRED` 仅表示“不能再写”，引导客户端转读 `GET /sessions/{id}`。

**决策 4 —— 交卷幂等口径统一为「只有 200」（C-15 / CHG-005、CHG-002）**
与本文件 ADR-005 对齐：重复提交返回 200 + 既有成绩，**删除 submit 的 409**；`SubmitResult.replayed` 标识重放（optional，`required` 被 QA 断言锁定为 4 字段）。`ALREADY_SUBMITTED` 保留在 `Error.code` 枚举中但不再返回（deprecated）。
后果：BE 不再需要 `ALREADY_SUBMITTED` 分支；FE 用 `replayed` 提示“已交卷，显示原成绩”；任何依赖 409 的前端逻辑必须删除。

**决策 5 —— 403/404 防枚举判定顺序固化（C-17 / CHG-012）**
顺序：401（未认证）→ 403 `CSRF_INVALID`（写接口）→ 403 `FORBIDDEN`（角色无权）→ 403 `FORBIDDEN`（角色允许、资源可见但归属不符，**IDOR 归此类**，因 AC-18 硬性要求 403）→ 404 `NOT_FOUND`（资源不可见或确实不存在，两者不区分）。
后果：不定义 `EXAM_NOT_PUBLISHED`（draft 考试统一 404），避免用 403/404 差异枚举资源；`docs/12` §2 给出可断言的判定表。

**决策 6 —— 可断言的收紧（附带）**
`Error.code` 枚举化（C-03）；`Exam.created_by`（C-04）；`AuditLog.actor_id/ip` 提为必填 + 默认 180 天（C-05）；分值 `>0` 与 `multipleOf 0.01`、`total_points = Σpoints`（C-08/C-14）；logout 与全部 CSRF 写接口补 403（C-09）；`ImportResult.skipped_duplicates` + `total` 口径（C-11）；`explanation` 必填（C-10，**口径**：DB 层 `questions.explanation` 仍允许 null 以兼容历史行，但 API 的 `QuestionCreate`/`Question` 均必填，`docs/11` 需补该脚注）；`MyResultItem.session_id`（C-06）；`Paper.frozen_at`/`Exam.snapshot_version`（C-12）；`AnswerSaveResponse.applied`（C-16）；考试状态机与 422/503（C-18/C-19）；会话状态枚举统一（C-20）；5 个响应对象 `additionalProperties: false`（C-21）；会话创建 201/200/409/404（C-22）。

**总后果**：契约版本升为 v1.0.1，**必须重过 G2** 才能继续 G4；`TASK-BE-004`/`TASK-FE-004` 契约依据同步；语义修正项（C-15/C-05/C-10/C-21）需 R0 广播并要求 BE/FE 重读契约。

## ADR-010 v1.0.2 契约一致性修订（可观测字段必需化、上限一致、logout/教师结果口径、废弃码清理）

- 状态：**Accepted**　日期：2026-11-09　作者：R2 Architect
- 输入：QA 对 v1.0.1 的同步审查（8 条由 v1.0.1 自身引入或遗留的一致性问题）+ 废弃码清理 1 条
- 输出：`contracts/openapi.yaml` **v1.0.2**；逐条 CHG-024..CHG-032 见 `docs/13-contract-changes.md`；权限矩阵同步至 `docs/12` v1.2

**决策 1 —— 可观测字段「能返回就必需」（CHG-024、CHG-028）**
`MyExamPage.server_time`、`SubmitResult.replayed`、`AnswerSaveResponse.applied` 全部进 `required`；`SessionState.score`/`submitted_at` 保持 optional（`in_progress` 时确实无值），但 description 写死「`status ∈ {submitted, expired}` 时必有值」。
理由：v1.0.1 为“兼容性”把 `replayed`/`applied` 留在 optional，代价是 QA 只能断言“字段存在”，无法断言“服务端真的告诉客户端这是重放/是否落库”——而这两个字段恰恰是幂等语义的唯一可观测证据（AC-13/AC-15）。服务端本来就**总是**知道 true/false，没有理由让它缺省。
后果：响应序列化必须显式赋值；前端可无条件依赖；`SessionState` 的两个条件字段不引入 `if/then`（避免 OpenAPI 3.1 条件 schema 在代码生成里失真），改由 description + status 条件断言表达。

**决策 2 —— 数值上限必须互相自洽（CHG-025、CHG-026）**
`PaperQuestion.points.maximum` 1000 → **100**，`Paper.total_points.maximum` 保持 **10000**（100 题 × 100 分 = 10000）；`PaperCreate` 删除“客户端不提交 total / 提交了也忽略”与“合计必须等于其声明总分”这对矛盾表述，统一为：请求只含 `title` + `questions`，`total_points` 由服务端计算并返回，服务端校验 `1 ≤ Σpoints ≤ 10000`。
理由：v1.0.1 的 1000 上限使“100 题 × 1000 分 = 100000”成为合法请求，却必然撞上 `total_points.maximum: 10000` → 契约内部自相矛盾（同样的请求既合法又非法）。而“声明总分”这个概念在请求模型里根本不存在（`PaperCreate` 只有 title/questions），继续写成校验对象会让 BE 实现去校验一个不存在的字段。
后果：组卷校验变成单一事实（服务端算），单题 > 100 → 422；`TC-CT-09` 的 `maximum == 1000` 断言必须由 QA 改为 100。

**决策 3 —— 快照字段用「条件不变量」而非条件 schema（CHG-027）**
`Paper.frozen_at` 与 `Exam.snapshot_version` 保持 optional，但 description 写死：`Paper.status=frozen` 时 `frozen_at` 必返回；`Exam.status ∈ {published, closed}` 时 `snapshot_version` 必返回；draft 阶段可为空。
理由：两者在 draft 阶段客观不存在，做成无条件 required 会逼实现返回假值（`null` 或 `0`）；而用 `if/then` 会让 `openapi-spec-validator`、`openapi-typescript` 与 Pydantic 映射三处都复杂化，收益仅是“自动化可断言”，代价是四处工具链风险。
后果：QA 按 `status` 分支断言；契约里不出现 `if/then`；冻结/发布路径的测试必须显式检查这两个字段非空。

**决策 4 —— logout 只留一种口径，并写明认证先于 CSRF（CHG-029）**
有效会话 → **204**；无有效会话（未登录/已吊销/过期/停用）→ **401** `AUTH_REQUIRED`；删除「已失效会话返回 204」；保留 401 响应出口，并在 description 写死「先认证、后 CSRF」，即未认证请求即使不带 `X-CSRF-Token` 也返回 401 而不是 403。
理由：v1.0.1 同时声明“已失效会话返回 204（幂等）”与 401，两者对同一输入给出不同答案；实现里中间件的真实顺序是会话校验在前，契约必须与之一致。保留 401 还因为 `TC-CT-04` 要求所有受保护接口都有 401 出口，且 logout 不在公开接口集合内。
后果：登出**不是**恒 204 的幂等接口，前端第二次调用会拿到 401，应把 401 当“已登出”处理；`docs/12` §3 同步。

**决策 5 —— 教师不读单会话逐题结果，只读考试汇总（CHG-030）**
`GET /sessions/{id}/result` 收敛为**仅学生本人**（admin 可读全部）；教师即使是自己创建的考试也一律 **403** `FORBIDDEN`，其入口是 `GET /exams/{id}/results`。`docs/12` 矩阵 teacher 行由 ✅ 改 ❌ 并加备注。
理由：v1.0.1 写“教师只能走 `GET /exams/{id}/results`”，但权限矩阵仍给 teacher ✅，且实现按矩阵做了“教师可读自己考试的会话结果” → 三处不一致。单会话逐题结果会暴露学生答卷细节，教师业务上只需要汇总（名单/状态/分数/平均分）；以角色维度拒绝（第 3 步）比按归属拒绝更简单，也避免“教师能否看到这条会话”成为存在性侧信道。
后果：`TC-SEC-18` 的 403 语义不变（学生 IDOR 仍是 403）；新增教师 403 用例；前端教师端不得再请求该接口。

**决策 6 —— 废弃码不留在枚举里（CHG-032）与除零口径（CHG-031）**
`ALREADY_SUBMITTED` 从 `Error.code` 枚举移除（16 值），docs/10 错误码表同步删行；代码残留常量由 BE-004 删除或映射为 `CONFLICT`。`ExamResultSummary.average_score` 写死：无已判分会话时返回 **0**（仍 required number）。
理由：一个永不可能返回的枚举值会诱导前端写死分支、也让“枚举 = 可能取值集合”这一断言失真（`TC-CT-25` 用集合相等）；平均分在没有已判分会话时若返回 null，会把 `required number` 变成谎言，若抛 5xx 则是把空数据当故障。
后果：错误码表与契约逐值一致；教师端空考试显示 0 分平均；QA 需同步删除 `TC-CT-25` expected 集合里的该项。

**总后果**：契约升为 **v1.0.2**，必须重过 G2 才能继续 G4；4 处 QA 断言需同步（docs/13 §7.1），在同步前 G2 的红是“断言未跟上契约”，不得用回退契约修绿。语义修正项（CHG-028/029/030/032）需 R0 广播，BE-004/BE-005/FE-004/FE-005 重读契约。

## ADR-011 v1.0.3 契约收口（认证优先、healthz `db`、审计 null actor、可见性判定定稿）

- 状态：**Accepted**　日期：2026-11-09　作者：R2 Architect
- 输入：`docs/13-contract-changes.md` §8 的三条挂起待办（P-009 / P-010 / P-011）+ QA-002 在 `tests/security/README.md` §6.11 记录的「教师读他人 draft 资源 403/404」口径张力
- 输出：`contracts/openapi.yaml` **v1.0.3**；逐条 CHG-033..CHG-036 见 `docs/13-contract-changes.md` §8.1；权限矩阵同步至 `docs/12` v1.3
- 性质：**V1 冻结前的最后一次契约修订**。自 v1.0.3 起契约进入冻结终态，只允许 CHG 级变更（先登记 CHG + 追加 ADR，再改契约与版本号）

**决策 1 —— 认证优先于 CSRF，也优先于参数校验（CHG-033 / P-009）**
无有效会话（未登录 / 已吊销 / 8h 过期 / 账号停用 / 伪造 Cookie）时，受保护接口**一律返回 401 `AUTH_REQUIRED`**，且该 401 **优先于** `X-CSRF-Token` 校验（403 `CSRF_INVALID`）与参数/请求体校验（422 `VALIDATION_ERROR`）。判定链固定为：401 → 403(CSRF) → 403(角色) → 403(归属) → 404(不可见) → 422(字段)。写入 `components.securitySchemes.sessionCookie.description` 与全部写接口 description（含 `/auth/logout`）。
理由：中间件真实顺序就是「先会话、后 CSRF、后业务校验」；契约此前只在 logout 一处写明，其余写接口的 401/403/422 优先级只能靠实现猜。且未认证请求不应进入授权/参数分支——否则可借 403/422 的差异探测 CSRF 机制是否启用、参数规则是什么。
后果：QA 的「无会话 + 缺 CSRF 头 → 401」从单接口扩展为全部写接口；「无会话 + 参数非法」同样断言 401，不得断言 422。

**决策 2 —— `/healthz` 的 200 响应显式声明 `db` 并必填（CHG-034 / P-010）**
200 schema 增加 `db: {type: string}` 并进 `required`（`required: [status, db]`；`version` 实现在返回但保持 optional）。实现语义：每次执行 `SELECT 1`，通过 → `{status:"ok", version, db:"ok"}`；失败 → 整体 503 统一 `Error`（`code=INTERNAL_ERROR`），不返回该对象。
理由：BE-007 已返回 `db`，契约却不声明，QA 无法断言「探针真的探了库」，只能断言“服务活着”——这正是 GAP-02 要修的。选 `type: string` 而非 `enum: [ok]`，是为将来多状态（如 `degraded`）留扩展位，且 `status` 已承担整体可用性判断。
后果：健康检查断言从 `required=[status]` 改为 `{status, db}`；503 分支保持统一错误体，不夹带 `db`。

**决策 3 —— 审计 `actor_id` 必填但可空；系统操作 `ip` 记 `"-"`（CHG-035 / P-011）**
`AuditLog.actor_id` 改为 `type: [integer, "null"]`，**字段仍必填**（总是出现）；系统操作/CLI 记录（备份清理、初始化、定时任务等无登录主体的动作）为 `null`，用户触发的写操作为真实 `users.id`。同时把**系统操作的 `ip` 约定为字符串 `"-"`**（不是 null、不是空串）；`AuditLogPage` 补同口径说明，`GET /audit-logs` 说明 `actor_id` 查询参数只按用户过滤（系统记录不匹配整数过滤值）。
理由：BE-006 的 `record(actor_id=None)` 是真实代码路径；旧文案「系统任务不得写入本表，若必须写入则以系统账号 id 记录，不允许 null」既与实现相反，又诱导实现伪造一个不存在的“系统账号”。`ip` 之所以不用 null：字段类型保持 `string` 不变（避免同时放宽两个字段），用哨兵值 `"-"` 表达“无来源”，且不影响 `required`。
后果：读取方（前端/QA）必须容忍 `actor_id=null`；BE-006 在系统记录路径需显式落 `ip="-"`（当前 `record()` 在 `ip=None` 时落 null，属实现待补齐项，不在 R2 写作用域）。

**决策 4 —— 可见性按角色定义，draft 资源的 403/404 定稿（CHG-036 / QA-002）**
统一三条：**他人 draft/未发布资源（学生视角不可见）→ 404**；**可见但非本人 → 403**；**角色不符 → 403**。具体：
- **学生视角**：`draft`/未发布考试不可见 → `POST /exams/{id}/sessions` 返回 **404**（不区分“不存在”与“未发布”，延续 C-17/C-22 的防枚举口径）；
- **教师（同角色）视角**：他人的题目/试卷/考试（**含 draft / frozen / published**）在管理视图内**可见** → 一律 **403 `FORBIDDEN`**，**不得返回 404**；
- **学生会话/成绩**：他人会话/成绩属“可见但非本人” → **403**（AC-18 硬性）。
理由：旧表述把“他人 draft”一律归入第 5 步 404，却同时在 §2.1 #2 要求“教师 A 改教师 B 的资源 → 403”，而实现统一 403 → 文档自相矛盾、QA 只能绕开（只用 published 资源测 403，draft 组合不敢断言）。把“可见域”显式按角色定义后，两条规则不再冲突：**同一资源对不同角色的可见性可以不同**，而 403/404 只取决于“对**当前主体**是否可见”。
**与 AC-18 的边界（重要）**：AC-18 约束的是**会话/成绩**资源族的 IDOR（学生 B 读学生 A 的 `result` → 403），CHG-036 **不修改** AC-18，也不改变 `GET /sessions/{id}/result` 的教师 403（CHG-030 的角色判定）。两者结论都是 403，但**理由不同、不可互换**：
  - 学生 IDOR = 第 4 步“**归属**不符”（角色允许、资源可见）；
  - 教师读单会话结果 = 第 3 步“**角色**不允许”（与资源归属无关，教师即便是考试创建者也被拒）。
  因此不能以“AC-18 要求 403”为由把教师结果接口改成按归属放行；也不能以“教师走考试汇总”为由把学生 IDOR 改成 404。`GET /exams/{id}/results`（教师汇总：名单/状态/分数/平均分）与 `GET /sessions/{id}/result`（学生逐题）不互为替代出口。
后果：教师侧 403 用例可覆盖 draft（QA 关闭 `tests/security/README.md` §6.11 的 open question）；学生 draft 考试 404 用例保留；`Forbidden`/`NotFound` 两个 response description 与 docs/12 §2、§2.0、§2.1 同步。

**总后果**：契约升为 **v1.0.3** 并**冻结（V1）**，必须重过 G2 才能继续 G4；QA 需同步 3 处断言（docs/13 §8.1.2）；语义澄清项（CHG-033/CHG-036）需 R0 广播，BE-001/002/003/005/006/007 与 FE-001/004/005 按 v1.0.3 重读契约；`server/app/api/health.py` 已满足 CHG-034，`server/app/audit/service.py` 的 `ip="-"` 约定由 BE-006 补齐。

## 模块边界（后端包）

| 模块 | 职责 | 允许依赖 | 禁止 |
|---|---|---|---|
| `auth` | 登录/登出/会话校验/CSRF | users | 业务领域 |
| `users` | 账号 CRUD、角色、停用 | auth（仅读会话主体） | 考试/题库业务 |
| `questions` | 题库 CRUD、导入、解析校验 | audit | papers/exams 模型 |
| `papers` | 组卷、分值、快照冻结 | questions（只读） | 考试运行时 |
| `exams` | 考试发布/时间窗/状态 | papers（只读快照） | 作答数据 |
| `sessions` | 作答会话、自动保存、交卷 | exams、scoring | 用户管理 |
| `scoring` | 判分、成绩汇总、错题本 | 只读快照 | 修改题库/试卷 |
| `reports` | 教师汇总、导出 | scoring、sessions（只读） | 写业务数据 |
| `audit` | 审计中间件、查询 | 无 | 业务逻辑 |

## 错误码约定

统一响应：`{ "code": "STRING_CODE", "message": "人类可读", "request_id": "uuid" }`

| HTTP | code 示例 | 场景 |
|---|---|---|
| 400 | `VALIDATION_ERROR` | 请求体/格式错误（JSON 解析失败等） |
| 401 | `AUTH_REQUIRED` / `AUTH_INVALID_CREDENTIALS` | 未登录 / 密码错 |
| 403 | `FORBIDDEN` | 角色或资源归属不符 |
| 404 | `NOT_FOUND` | 资源不存在 |
| 409 | `EXAM_NOT_STARTED` / `EXAM_EXPIRED` / `QUESTION_IN_USE` / `PAPER_FROZEN` / `NOT_SUBMITTED` / `CONFLICT` | 状态冲突（`NOT_SUBMITTED` 用于未交卷请求逐题结果） |
| 422 | `VALIDATION_ERROR` | 字段校验失败（Pydantic 422） |
| 200 | `IMPORT_PARTIAL`（明细随响应返回） | 批量导入部分失败（HTTP 仍 200） |

> **v1.0.2 清理（CHG-032）**：`ALREADY_SUBMITTED` 已从本表与契约枚举中**移除**——交卷自 v1.0.1 起就是幂等 200
> （C-15/ADR-005），该码没有任何合法出口。代码中如残留该常量，由 BE-004 实现时**删除**或用 `CONFLICT` 替代；
> 任何接口返回它都算违契（`TC-CT-25` 会红）。
>
> 错误码与契约 `Error.code` 枚举必须**逐值一致**（16 值）：`VALIDATION_ERROR / AUTH_REQUIRED / AUTH_INVALID_CREDENTIALS /
> CSRF_INVALID / FORBIDDEN / NOT_FOUND / CONFLICT / METHOD_NOT_ALLOWED / INTERNAL_ERROR / HTTP_ERROR /
> EXAM_NOT_STARTED / EXAM_EXPIRED / QUESTION_IN_USE / PAPER_FROZEN / NOT_SUBMITTED / IMPORT_PARTIAL`。

## 目录结构

```
server/
  app/
    main.py            # 应用工厂、中间件（审计/request_id/异常处理）
    settings.py        # 环境变量（pydantic-settings）
    db.py              # engine/session
    auth/ users/ questions/ papers/ exams/ sessions/ scoring/ reports/ audit/
  migrations/          # Alembic
  tests/               # pytest（unit + contract）
web/
  src/
    api/               # 生成类型 + 请求层（CSRF/401 处理）
    pages/ components/ hooks/ stores/
    routes.tsx
tests/                 # E2E（Playwright）与契约测试
deploy/                # docker-compose、nginx、backup、runbook
```
