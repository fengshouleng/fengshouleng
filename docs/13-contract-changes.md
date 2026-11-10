# 13 · 契约变更登记（Contract Change Register）

> 规则：契约冻结（G2）后任何变更必须登记 CHG 编号，由 R2 出 ADR 补丁，R0 广播，受影响任务重读契约。
> 本文件只记录**提案与状态**；实际修改以 `contracts/openapi.yaml` 版本号为准。

## 0. 上一轮修订概览（v1.0.0 → v1.0.1，历史）

| 项 | 内容 |
|---|---|
| 版本 | `contracts/openapi.yaml` **v1.0.0 → v1.0.1** |
| 日期 | 2026-11-09 |
| 处置人 | R2 Architect（B3c，输入 = TASK-QA-000 契约审查 `docs/60-test-plan.md` §8 C-01..C-22） |
| 决策记录 | [docs/10-arch.md](10-arch.md) **ADR-009**（本轮五条主决策） |
| 处置结果 | 22 条缺陷全部处置：**21 条修复（v1.0.1 落地）**，1 条（C-22 的 `EXAM_NOT_PUBLISHED`）以「404 防枚举」替代；**0 条延后** |
| 断言影响 | `tests/contract` 现有 7 个 xfail 中 **6 个应转为 XPASS→删除标记**（C-01/03/05/08/09/10），C-11 需 QA 改判定键（见 §7） |
| 门控 | 契约变更**必须重过 G2**（`docs/60-test-plan.md` §6.1 第 1 步），再继续 G4 |

**兼容性总原则（v1.0.1 时）**：新增响应字段尽量 optional（`applied`/`replayed`/`session_id`(ExamItem)/`frozen_at`/`snapshot_version`/`updated_at`/`user_agent`/`detail`）；只有 QA 明确要求「可强制断言」或 AC 硬性要求的字段才进 `required`。C-15/C-05/C-10/C-21 属于**语义修正**，见 §4。

> **v1.0.2 修订该原则的适用边界**（见 §0.1 / ADR-010）：凡是服务端**总能**给出确定值的可观测字段（`server_time`/`replayed`/`applied`）一律进 `required`；
> 只有在某些状态下客观无值的字段（`frozen_at`/`snapshot_version`/`score`/`submitted_at`）才继续 optional，并改用「条件不变量 + description」表达。

---

## 0.1 本轮（v1.0.1 → v1.0.2）修订概览

| 项 | 内容 |
|---|---|
| 版本 | `contracts/openapi.yaml` **v1.0.1 → v1.0.2** |
| 日期 | 2026-11-09 |
| 处置人 | R2 Architect（输入 = QA 对 v1.0.1 的同步审查：8 条 v1.0.1 自身引入/遗留的一致性问题 + 1 条废弃码清理） |
| 决策记录 | [docs/10-arch.md](10-arch.md) **ADR-010**（本轮六条主决策） |
| 处置结果 | **9 条全部修复（CHG-024..CHG-032），0 条延后**；无新增/删除接口，只有必填收紧、上限自洽、权限收紧与措辞统一 |
| 断言影响 | **4 处 QA 断言需同步**（§7 第 7–10 条）：`TC-CT-09`（points 上限 100）、`TC-CT-13`（`replayed` 进 required）、`TC-CT-15`（`applied` 进 required）、`TC-CT-25`（枚举移除 `ALREADY_SUBMITTED`） |
| 门控 | 契约变更**必须重过 G2**（`docs/60-test-plan.md` §6.1 第 1 步），再继续 G4 |

**本轮性质**：修的是 v1.0.1 自身引入/遗留的一致性问题（不是新需求）。取舍原则是「契约内部自洽 + 可硬断言 + 与中间件实现一致」：
可观测字段提为 `required`、数值上限互相自洽、logout 与教师结果只留一种口径、废弃码不留在枚举里。

> **历史 ADR 保留原文**：`docs/10-arch.md` ADR-009 决策 4/6 中「`replayed` 保持 optional」「`ALREADY_SUBMITTED` 保留但不再返回」
> 是 v1.0.1 的当时决策，作为历史保留（docs/10 只追加 ADR-010，不改写旧 ADR）。**该轮口径以 ADR-010 为准；v1.0.3 起的新增/变更口径以 ADR-011 为准（见 §8.1）。**

---

## 0.2 v1.0.2 逐条登记（CHG-024..CHG-032）

| CHG | 缺陷 | 变更内容 | 兼容性 | 影响文件 / 实施方 | QA 动作 |
|---|---|---|---|---|---|
| CHG-024 | `MyExamPage` 缺 `server_time`：前端无法校时；`in_progress` 但已过 `deadline_at` 的条目也没有结算口径 | `MyExamPage.server_time: date-time` 进 `required`；`GET /me/exams` description 写死：前端以 `server_time` 校时；`session_status=in_progress` 且已过 `deadline_at` 时，服务端在**读取时**按到期规则结算，返回 `expired` 并带 `score`（与 `GET /sessions/{id}` 的 200+expired 语义一致） | 响应新增必填字段 | BE-004（`/me/exams`）；FE-004 校时 | 新增「根级有 server_time」「过期条目返回 expired+score」用例 |
| CHG-025 | `PaperQuestion.points.maximum: 1000` 与 `Paper.total_points.maximum: 10000` 不自洽（100 题 × 1000 分可越界） | 单题上限改 **100**；`total_points.maximum` 保持 **10000**；description 写明 100 题 × 100 分 = 10000 恰不越界 | 收紧校验（单题 > 100 → 422） | BE-003 组卷校验；FE-003 表单上限 | **改断言**：`TC-CT-09` 的 `points.maximum` 由 1000 改为 100 |
| CHG-026 | `PaperCreate` 同时写「客户端不提交 total / 提交了也忽略」与「合计必须等于其声明总分」，自相矛盾 | 统一为：请求**只含** `title` + `questions`（每题 `points`）；`total_points` 由服务端计算并返回；服务端校验 `1 ≤ Σpoints ≤ 10000`，越界/非法 → 422。删除“声明总分”表述 | 语义澄清（请求模型未变） | BE-003；FE-003 | 增加「Σpoints 越界 → 422」「响应 total_points = Σ」断言 |
| CHG-027 | `frozen_at`/`snapshot_version` 为 optional，QA 无法断言“冻结/发布后必有值” | 字段**保持 optional**（draft 可为空/null），description 写死**条件不变量**：`Paper.status=frozen` → `frozen_at` 必返回；`Exam.status ∈ {published, closed}` → `snapshot_version` 必返回；**不引入 `if/then`**，QA 按 `status` 条件断言 | 无 schema 破坏 | BE-003/004 | 按条件断言，不要写死“永远 required” |
| CHG-028 | `replayed`/`applied` optional，QA 无法保证服务端总返回；`score`/`submitted_at` 的条件必填只写在别处 | `SubmitResult.replayed`、`AnswerSaveResponse.applied` 进 `required`；`SessionState.score/submitted_at` **保持 optional**，description 写死「`status ∈ {submitted, expired}` 时必有值」 | **语义修正**：两个字段必须显式返回 | BE-004 响应序列化 | **改断言**：`TC-CT-13` required 集合加 `replayed`（见 §7 第 7 条）；`TC-CT-15` 改断言 `applied ∈ required` |
| CHG-029 | logout 同时声明「已失效会话返回 204」与 401 响应，语义冲突 | 统一：有效会话 → **204**；无有效会话（未登录/已吊销/过期/停用）→ **401** `AUTH_REQUIRED`；删除“已失效会话返回 204”；写明**认证先于 CSRF**（未认证即使缺 CSRF 头也 401 而非 403）。**401 响应保留**：所有受保护接口都必须有 401 出口（`TC-CT-04`，logout 不在公开集合内） | 语义修正：登出不再“恒 204” | BE-001 中间件；FE-001 登出处理 | 新增「二次登出 → 401」；`TC-CT-04` 保持通过 |
| CHG-030 | `GET /sessions/{id}/result` 声明教师可读（“自己考试的汇总”），与实现、AC、docs/12 口径冲突 | 收紧为**仅学生本人可读**（admin 可读全部；**teacher 一律 403**，教师走 `GET /exams/{id}/results`）；契约 description 写死，docs/12 矩阵 teacher 行 ✅ → ❌ + 备注，并加负例 9 | **权限收紧**：依赖教师读单会话结果的代码/前端必须删 | BE-005、FE-005；docs/12 | 新增「教师读单会话结果 → 403」用例 |
| CHG-031 | `ExamResultSummary.average_score` 无除零口径（0 份已判分时可能 null/500） | description 写死：无已判分会话时返回 **0**；字段保持 `required` + `number` | 语义澄清 | BE-005 | 断言「0 份已判分 → average_score == 0」 |
| CHG-032 | `ALREADY_SUBMITTED` 自 v1.0.1 起已不可能返回，却仍在枚举中（且 docs/10 表与实现常量可能残留） | 从 `Error.code` 枚举**移除**；`Error.description` 说明“实现若残留常量，删除或用 `CONFLICT` 替代”；docs/10 错误码表删除该行 | **枚举收紧**：返回该码即违契 | BE-004；docs/10 | **改断言**：`TC-CT-25` expected 集合删 `ALREADY_SUBMITTED`（见 §7 第 10 条） |

---

## 1. 阻断 G4 的四条（优先修复，已全部落地）

### CHG-001（**Applied**，v1.0.1）— C-01 恢复会话回读

| 项 | 内容 |
|---|---|
| 提出人 | R0（Lead），2026-11-08（原 CHG-001 提案）；R2 落地 2026-11-09 |
| 来源 | AC-12（断网/刷新恢复）+ QA C-01：`SessionState` 无 `answers`，`GET /sessions/{id}/answers` 不存在 → 服务端已保存答案不可回读 |
| 修改 | `components.schemas.SessionState` 新增 `answers: AnswerItem[]`（进 `required`）与 `submitted_at: string\|null`（可空）；`last_client_seq` 一并进 `required`。`AnswerItem` = `{question_id, selected[]}`，未作答的题不出现在数组中 |
| 落点 | `GET /sessions/{session_id}`、`POST /exams/{exam_id}/sessions`（200 恢复分支）均返回该字段 |
| 兼容性 | 向后兼容（新增字段）；旧客户端忽略即可 |
| 影响面 | TASK-BE-004 必须回读 `answers`（解除 TASK-FE-004 的 contract_issue 阻断）；TASK-FE-004 刷新恢复不再依赖本地缓存 |
| 验证 | QA `TC-CT-12` 去掉 xfail；E2E `TC-E2E-04` 断言「恢复后 `answers` 与断网前一致」 |

### CHG-003（**Applied**，v1.0.1）— C-02 学生发现考试 `GET /me/exams`

| 项 | 内容 |
|---|---|
| 来源 | QA C-02：学生无权 `GET /exams`，而 `POST /exams/{id}/sessions` 需要 `exam_id` → 学生无法得知可参加哪些考试 |
| 修改 | 新增 `GET /me/exams`（tag `me`，`operationId=listMyExams`），新增 `MyExamItem`/`MyExamPage`。字段：`exam_id/exam_title/start_at/end_at/duration_min`（required）+ `session_status`（required，枚举 `not_started|in_progress|submitted|expired`）+ `session_id`/`score`/`total_points`/`submitted_at`（可空） |
| 范围 | 返回该生可见的 `published` 考试 + 该生已有会话的 `closed` 考试；**不含 `draft`**（不可见 → 不出现） |
| 权限 | 仅 `student`；教师/admin → 403（其入口是 `GET /exams`）。权限矩阵已同步（docs/12 §1） |
| 兼容性 | 纯新增接口，无破坏 |
| 验证 | QA 新增契约用例（`/me/exams` 有 `session_status` 与成绩字段、学生可读、教师 403）；FE 新增「可考/已考列表」页 |

### CHG-004（**Applied**，v1.0.1）— C-13 到期自动交卷可观测

| 项 | 内容 |
|---|---|
| 来源 | QA C-13：`EXAM_EXPIRED` 只有 `Error` 体，客户端收到 409 不知已自动交卷、无法跳成绩页 |
| 决策 | **不改错误体**（保持 `Error` 三字段，`tests/contract/test_error_codes.py` R1 要求统一）。改为在 `GET /sessions/{id}` 上可观测：到期时服务端同步触发自动交卷判分，返回 `status=expired`、`score` 非空、`submitted_at` 非空，HTTP **200**（不是 409） |
| 修改 | `GET /sessions/{id}` description 写死该语义；`SessionState.status/score/submitted_at` 加 description；`PUT .../answers` 的 409 说明改为「已到期 → 客户端转读 `GET /sessions/{id}`」；`GET .../result` 明确 `submitted|expired` 均可读 |
| 兼容性 | 语义澄清，无 schema 破坏（`score` 由可选 number 变为 `number\|null`，字段仍在） |
| 验证 | `TC-CT-14` 保持硬断言（保存 409 `EXAM_EXPIRED`）；新增「`GET /sessions/{id}` 对过期会话返回 200 + expired + score」用例；E2E `TC-E2E-05` 第 3 步改为读 `GET /sessions/{id}` 而非解析错误体 |

### CHG-005（**Applied**，v1.0.1）— C-15 交卷幂等语义统一（移除 submit 409）

| 项 | 内容 |
|---|---|
| 来源 | QA C-15：`POST /sessions/{id}/submit` 同时声明 200 与 409，与 ADR-005「重复提交返回 200 既有成绩」自相矛盾 |
| 修改 | **删除 submit 的 `409` 响应**（唯一出口：`200` / `401` / `403` / `404`）；description 写死「只有 200」：首次 `replayed=false`，重复提交或已自动交卷 `replayed=true` 且成绩与首次一致，不重复计分、不新增成绩行；会话不存在→404、非本人→403 |
| 兼容性 | **语义修正**：任何依赖「重复提交 409 ALREADY_SUBMITTED」的实现/前端分支必须删除。`Error.code.ALREADY_SUBMITTED` 保留在枚举中但 v1.0.1 起不再返回（标注 deprecated） |
| 影响面 | TASK-BE-004 must 第 39 行原本已写「200，非 409」，实现无需改；**前端**（TASK-FE-004）需按 `replayed` 提示“已交卷，显示原成绩” |
| 验证 | `TC-CT-13` 保持；新增「submit 响应集合不含 409」结构断言；E2E `TC-E2E-05` 重复交卷断言两次 200 + score 相同 |

---

## 2. 其余修复（逐条登记）

| CHG | QA | 变更内容 | 兼容性 | 影响文件 / 实施方 | QA 动作 |
|---|---|---|---|---|---|
| CHG-002 | C-15 | `SubmitResult` 新增 `replayed: boolean`（重复提交为 true）。原候选名 `idempotent`，最终定名 `replayed`；**必须 optional**（`required` 由 `test_tc_ct_13` 锁定为 4 字段） | 纯新增，可选 | BE-004 返回；FE-004 提示 | 新增 `replayed` 存在性用例（不断言 required） |
| CHG-006 | C-03 | `Error.code` 改 `enum`（17 值）：`VALIDATION_ERROR/AUTH_REQUIRED/AUTH_INVALID_CREDENTIALS/CSRF_INVALID/FORBIDDEN/NOT_FOUND/CONFLICT/METHOD_NOT_ALLOWED/INTERNAL_ERROR/HTTP_ERROR/EXAM_NOT_STARTED/EXAM_EXPIRED/QUESTION_IN_USE/PAPER_FROZEN/NOT_SUBMITTED/IMPORT_PARTIAL/ALREADY_SUBMITTED`（末项 deprecated），与 docs/10 错误码表 + `server/app/errors.py` 对齐。**v1.0.2 起 `ALREADY_SUBMITTED` 已从枚举移除（16 值），见 CHG-032** | 枚举收紧：实现若返回表外 code 会违契 | BE-001..005 统一走 `AppError` | `TC-CT-25` 去 xfail |
| CHG-007 | C-04 | `Exam` 新增 `created_by` 并进 `required`；同步 `updated_at`（C-18）与 `snapshot_version`（C-12，optional） | 响应新增必填字段 | BE-003 返回；RBAC 依赖 | 新增「教师改他人考试 → 403/404」用例 |
| CHG-008 | C-05 | `AuditLog.actor_id`/`ip` 提为 `required`；新增 `user_agent`/`detail`（optional）；`GET /audit-logs` description 写死默认近 180 天、倒序、只增不改 | **语义修正**：审计写入必须落 actor 与 IP | BE-001 审计中间件 | `TC-CT-22` 去 xfail |
| CHG-009 | C-08 | `PaperQuestion.points`：`exclusiveMinimum: 0`、`maximum: 1000`、`multipleOf: 0.01`；`Paper.total_points`：`minimum: 0`、`multipleOf: 0.01`、只读（= Σpoints）；`PaperCreate.description` 写明「服务端强制 total = Σpoints，10×10=100 口径，不符 → 422」。**v1.0.2 调整**：`points.maximum` 由 1000 降为 **100**（CHG-025），`PaperCreate` 的 total 口径由 CHG-026 重写 | 收紧校验；`total` 不接受客户端写入 | BE-003 组卷校验 | `TC-CT-09` 去 xfail |
| CHG-010 | C-09 | `/auth/logout` 补 `403`（`CSRF_INVALID`）；新增 `components.responses.CsrfForbidden`；`Forbidden`/`Conflict` description 增补 code 口径。全部声明 `X-CSRF-Token` 的写接口现有 403 出口 | 纯新增 403 出口 | BE-001 CSRF 中间件 | `TC-CT-24` 去 xfail |
| CHG-011 | C-11 | `ImportResult` 新增 `skipped_duplicates: integer` 并进 `required`；`total` 口径写死 = 文件数据行数 = `imported + skipped_duplicates + len(failed)` | 响应新增必填字段 | BE-002 导入 | **QA 需改判定键**（见 §7） |
| CHG-012 | C-17 | 403/404 防枚举策略写死（`Forbidden`/`NotFound` description + docs/12 §2）：① 角色不符 → 403；② 资源可见但归属不符（IDOR）→ **403**（AC-18 硬性）；③ 资源不可见（未发布/软删/他人私有）→ **404** | 语义澄清 | BE 全部归属校验 | 新增 403/404 判定矩阵用例 |
| CHG-013 | C-22 | `POST /exams/{id}/sessions` description 写死返回码表：201 新建 / 200 恢复（含已交卷、已到期）/ 409 `EXAM_NOT_STARTED` / 409 `EXAM_EXPIRED` / 404（不存在或 draft 或不可见）；幂等并发只有一个 201；`deadline_at` 仅新建时计算 | 语义澄清，无 schema 破坏 | BE-004 | 新增逐码用例（AC-11/13/14） |
| CHG-014 | C-06 | `MyResultItem` 新增 `session_id` 并进 `required`（跳转 `/sessions/{id}/result`） | 响应新增必填字段 | BE-005 | 新增「成绩列表可跳逐题结果」用例 |
| CHG-015 | C-07 | 新增 `components.responses.InvalidCredentials`：固定 `code=AUTH_INVALID_CREDENTIALS` + 固定 message「用户名或密码错误」，不区分账号不存在/密码错误/停用，耗时恒定；`/auth/login` 401 改引用它 | 语义澄清 | BE-001 | 强化 `TC-CT-02`（断言固定 code） |
| CHG-016 | C-10 | `QuestionCreate` 与 `Question` 的 `required` 均加入 `explanation`（`QuestionCreate` 加 `minLength: 1`） | **语义修正**：响应必带解析 | BE-002；docs/11 `explanation 可空`仅指 DB 层 | `TC-CT-05` 去 xfail |
| CHG-017 | C-12 | `Paper` 新增 `frozen_at: string\|null`（optional）；`Exam` 新增 `snapshot_version: integer`（optional） | 纯新增，可选 | BE-003 | 新增「冻结后可读 frozen_at」用例 |
| CHG-018 | C-14 | `PaperQuestion.partial_rule` 加 `default: all_or_nothing` + 「仅 multiple 生效」；`points`/`earned_points`/`score`/`total_points` 统一 `multipleOf: 0.01`；`SessionQuestion.points`/`ResultItem.*_points` 同步 | 纯约束增强 | BE-003/005 | 新增「2 位小数」用例 |
| CHG-019 | C-16 | `AnswerSaveResponse` 新增 `applied: boolean`（optional）：true=已落库，false=旧 seq 被忽略（HTTP 仍 200，`saved_seq` 为当前值）。**不采用**「旧 seq 409 STALE_SEQ」方案（避免把正常重试打成错误） | 纯新增，可选 | BE-004；FE-004 可据此静默 | 新增 `applied=false` 用例（AC-15） |
| CHG-020 | C-18 | `ExamPatch`/`Exam.status` 写死状态机：只允许 `draft→published`、`published→closed`，其余 → 409 `CONFLICT`；已发布考试改时间窗仅 `now < start_at`；`PATCH /exams/{id}` 补 `422` | 语义收紧 | BE-003 | 新增非法迁移 409 用例 |
| CHG-021 | C-19 | `PATCH /users/{id}`、`PATCH /exams/{id}` 补 `422`；`GET /healthz` 补 `503`（统一 `Error`，code=INTERNAL_ERROR）；`BadRequest`/`ValidationError` description 明确 code | 纯新增出口 | BE-001/003、R6 探针 | 新增 healthz 503 用例 |
| CHG-022 | C-20 | 统一会话状态枚举：`SessionState.status`、`ExamResultSummary.items[].status` = `[in_progress, submitted, expired]`；`SubmitResult.status` = `[submitted, expired]`；三处 description 一致声明「expired = 到期自动交卷且已判分，语义等价 submitted 且结果可读」。**不把 SubmitResult 改成 `$ref`**（否则 `TC-CT-14` 的 enum 断言失效） | 语义澄清 | BE-004/005 | 保持 `TC-CT-14` |
| CHG-023 | C-21 | `User`/`LoginResponse`/`MeResponse`/`SessionState`/`ResultItem` 设 `additionalProperties: false`（输出最小化，防止误带 `password_hash`/他人数据） | **语义修正**：多返回字段即违契 | BE-001/004/005 响应序列化白名单 | 新增「响应无额外字段」用例 |

---

## 3. 未采纳 / 替代方案

| QA | 建议 | R2 处置 |
|---|---|---|
| C-17 | 「资源存在但无权访问 → 404」 | **部分调整**：`AC-18`（PRD）明确“学生 B 请求 A 的 result → 403”，且 `tests/contract/test_error_codes.py::test_tc_ct_18_result_forbidden` 与 `TC-SEC-18` 都对 403 有硬断言。故最终口径为：**角色不符 → 403**；**资源可见但非本人（IDOR）→ 403**；**资源对当前主体不可见 → 404**。若要求“存在但无权也 404”，必须同时修订 AC-18 与 TC-SEC-18，属 PRD 变更，不在本轮 R2 权限范围（已列入待确认项）。 |
| C-22 | 为 draft/未发布考试定义 `EXAM_NOT_PUBLISHED`（409/403） | **未采纳**：改为统一 **404 `NOT_FOUND`**。理由：区分“未发布”与“不存在”本身就是资源存在性泄露（C-17 防枚举）；`EXAM_NOT_PUBLISHED` 不进 `Error.code` 枚举 |
| C-13 | 409 改专用 schema（含 `code/session_status/score`） | **未采纳**：会破坏 `test_error_codes.py` R1（所有 4xx 必须是统一 `Error`），改为 `GET /sessions/{id}` 可观测（CHG-004） |
| C-16 | 旧 seq 返回 `409 STALE_SEQ` | **未采纳**：弱网重试是常态，把正常幂等忽略打成错误会让前端重试风暴；改用 `applied=false`（CHG-019） |
| C-01 | 新增 `GET /sessions/{id}/answers` 独立接口 | **未采纳**：`SessionState.answers` 已覆盖刷新/恢复场景，少一个接口少一处权限面 |

---

## 4. 语义修正与影响面（必须广播）

| CHG | 影响 | 谁必须重读 | 回归点 |
|---|---|---|---|
| C-15 / CHG-005 | submit 不再有 409；重复提交=200+既有成绩 | BE-004、FE-004、QA E2E | 幂等用例、并发双提交 |
| C-05 / CHG-008 | 审计 `actor_id`/`ip` 必填 | BE-001 审计中间件 | AC-22 用例 |
| C-10 / CHG-016 | `explanation` 必填（请求与响应） | BE-002、FE-002、QA 导入用例 | AC-05、导入失败行 |
| C-21 / CHG-023 | 5 个响应对象 `additionalProperties: false` | BE-001/004/005 | 响应结构比对用例 |
| C-13 / CHG-004 | 到期后 `GET /sessions/{id}` 返回 200 + expired + score | BE-004、FE-004 | E2E 到时自动交卷 |
| C-17 / CHG-012 | IDOR=403；不可见=404（不再二选一） | BE 全部、QA 安全用例 | AC-03/18、matrix §2 |
| CHG-024 | `/me/exams` 必带 `server_time`，过期条目读取即结算为 `expired`+`score` | BE-004、FE-004 | 「我的考试」列表、断网恢复 |
| CHG-028 | `replayed`/`applied` 必返回；`score`/`submitted_at` 条件必填 | BE-004、FE-004 | 交卷/保存响应字段断言 |
| CHG-030 | 教师不可读单会话结果（一律 403，走考试汇总） | BE-005、FE-005、QA 安全用例 | AC-18、matrix §1/§2 负例 9 |
| CHG-032 | `ALREADY_SUBMITTED` 从枚举移除 | BE-004（删常量/映射 CONFLICT）、FE-004（删分支） | `TC-CT-25` |

---

## 5. 版本与门控

- `contracts/openapi.yaml` 的 `info.version` = **1.0.3**（v1.0.2 为上一轮），`info.description` 已写入本轮 v1.0.3 变更摘要（CHG-033..CHG-036）与冻结声明。
- **v1.0.3 是 V1 冻结前的最后一次契约修订**：自本版起契约进入**冻结终态（V1）**，只允许 **CHG 级变更**（先登记 CHG + 出 ADR 补丁，再改契约与版本号），不再接受“顺手修一下”的直接编辑。
- 依 ADR-003：契约即真相。**G2 必须按 v1.0.3 重跑**（`python -m pytest tests/contract -q`），G4 的契约门同样以 v1.0.3 为准。
- **v1.0.3 与现有契约测试的已知断言缺口（3 处，QA 需同步）**：见 §8.1.2。在更新前 G2 因“断言未覆盖新必填项/新口径”而偏弱（healthz 少了 `db` 断言、`actor_id` 缺可空断言、缺「教师侧 draft 资源 → 403」用例），**不要用回退契约来“修绿”**。
- 历史：v1.0.2 与现有契约测试的 4 处断言冲突（`TC-CT-09`/`TC-CT-13`/`TC-CT-15`/`TC-CT-25`，见 §7 第 7–10 条）保持原记录；v1.0.3 未推翻这些口径。
- 前端类型需重新生成（`openapi-typescript`），TASK-FE-004 的入参/出参类型以 v1.0.3 为准（`replayed`/`applied`/`server_time` 必填；`AuditLog.actor_id` 可空；healthz 多 `db`）。

---

## 6. 变更历史

| CHG | 版本 | 日期 | 状态 | 备注 |
|---|---|---|---|---|
| CHG-001 | v1.0.1 | 2026-11-08 → 2026-11-09 | **Applied** | C-01 恢复会话回读（`SessionState.answers`/`submitted_at`） |
| CHG-002 | v1.0.1 | 2026-11-08 → 2026-11-09 | **Applied** | C-15 交卷重放标志，字段定名 `replayed`（原候选 `idempotent`） |
| CHG-003 | v1.0.1 | 2026-11-09 | **Applied** | C-02 新增 `GET /me/exams` |
| CHG-004 | v1.0.1 | 2026-11-09 | **Applied** | C-13 到期自动交卷可观测 |
| CHG-005 | v1.0.1 | 2026-11-09 | **Applied** | C-15 submit 移除 409 |
| CHG-006 | v1.0.1 | 2026-11-09 | **Applied** | C-03 `Error.code` 枚举 |
| CHG-007 | v1.0.1 | 2026-11-09 | **Applied** | C-04 `Exam.created_by` |
| CHG-008 | v1.0.1 | 2026-11-09 | **Applied** | C-05 审计 actor_id/ip 必填 + 180 天口径 |
| CHG-009 | v1.0.1 | 2026-11-09 | **Applied** | C-08 分值约束与 total 口径 |
| CHG-010 | v1.0.1 | 2026-11-09 | **Applied** | C-09 logout 403 / CSRF 失败 403 |
| CHG-011 | v1.0.1 | 2026-11-09 | **Applied** | C-11 `ImportResult.skipped_duplicates` |
| CHG-012 | v1.0.1 | 2026-11-09 | **Applied** | C-17 403/404 防枚举策略 |
| CHG-013 | v1.0.1 | 2026-11-09 | **Applied** | C-22 会话创建 201/200/409/404 |
| CHG-014 | v1.0.1 | 2026-11-09 | **Applied** | C-06 `MyResultItem.session_id` |
| CHG-015 | v1.0.1 | 2026-11-09 | **Applied** | C-07 登录失败固定 code/message |
| CHG-016 | v1.0.1 | 2026-11-09 | **Applied** | C-10 `explanation` 必填 |
| CHG-017 | v1.0.1 | 2026-11-09 | **Applied** | C-12 `Paper.frozen_at` / `Exam.snapshot_version` |
| CHG-018 | v1.0.1 | 2026-11-09 | **Applied** | C-14 `partial_rule` 默认值 + 2 位小数 |
| CHG-019 | v1.0.1 | 2026-11-09 | **Applied** | C-16 `AnswerSaveResponse.applied` |
| CHG-020 | v1.0.1 | 2026-11-09 | **Applied** | C-18 考试状态机 + 422 |
| CHG-021 | v1.0.1 | 2026-11-09 | **Applied** | C-19 422 统一 + `/healthz` 503 |
| CHG-022 | v1.0.1 | 2026-11-09 | **Applied** | C-20 会话状态枚举统一 |
| CHG-023 | v1.0.1 | 2026-11-09 | **Applied** | C-21 输出最小化（additionalProperties: false） |
| CHG-024 | **v1.0.2** | 2026-11-09 | **Applied** | `MyExamPage.server_time` 必填 + `/me/exams` 到期结算口径 |
| CHG-025 | **v1.0.2** | 2026-11-09 | **Applied** | 分值上限自洽：单题 1000 → 100（总分上限 10000 不变） |
| CHG-026 | **v1.0.2** | 2026-11-09 | **Applied** | `PaperCreate` 去矛盾：只收 title+questions，total 由服务端算 |
| CHG-027 | **v1.0.2** | 2026-11-09 | **Applied** | 快照条件不变量（frozen_at / snapshot_version），不引入 if/then |
| CHG-028 | **v1.0.2** | 2026-11-09 | **Applied** | `replayed`/`applied` 进 required；`score`/`submitted_at` 条件必填 |
| CHG-029 | **v1.0.2** | 2026-11-09 | **Applied** | logout 语义统一（204 / 401），认证先于 CSRF |
| CHG-030 | **v1.0.2** | 2026-11-09 | **Applied** | 教师不可读单会话结果（403），教师走考试汇总 |
| CHG-031 | **v1.0.2** | 2026-11-09 | **Applied** | `average_score` 无已判分时返回 0 |
| CHG-032 | **v1.0.2** | 2026-11-09 | **Applied** | `Error.code` 移除废弃码 `ALREADY_SUBMITTED`（16 值） |
| CHG-033 | **v1.0.3** | 2026-11-09 | **Applied** | P-009 认证优先：无有效会话 → 401 优先于 CSRF 校验与参数校验（`sessionCookie.description` + 全部写接口） |
| CHG-034 | **v1.0.3** | 2026-11-09 | **Applied** | P-010 `/healthz` 200 schema 显式 `db: string` 并进 `required`（实现 `db:"ok"`） |
| CHG-035 | **v1.0.3** | 2026-11-09 | **Applied** | P-011 `AuditLog.actor_id` → `[integer, "null"]`（系统操作 null）+ 系统操作 `ip="-"`；`AuditLogPage` 同步 |
| CHG-036 | **v1.0.3** | 2026-11-09 | **Applied** | QA-002 可见性口径定稿：学生视角 draft/未发布 → 404；可见但非本人 → 403；角色不符 → 403（教师同角色他人资源含 draft 一律 403） |

---

## 7. 待 QA / 实施同步事项

1. **`test_tc_ct_08` 的判定键需更新**：本契约按 QA 建议命名为 `skipped_duplicates`，而用例当前接受集合是 `{"skipped","duplicates","duplicated","deduplicated"}` → 该 xfail 仍会「失败即 xfail」，无法证明缺陷已修。请把 `skipped_duplicates` 加入接受集合（或改为断言 `skipped_duplicates`），随后删除 xfail 标记。
2. **`test_tc_ct_13` 用 `==` 锁定了 `SubmitResult.required` 为 4 字段**（v1.0.1 时的口径）：因此 `replayed` 当时只能 optional。
   **v1.0.2 已推翻该口径**——`replayed` 已提为 required，QA 必须同步断言，见下方第 7 条。
3. **`test_tc_ct_14` 依赖 `SubmitResult.status` 是内联 enum**：故未把该字段改成 `$ref`（CHG-022 已说明）。QA 若重构为 `$ref`，需同步支持 `$ref` 解析。
4. 需新增（当前 tests/contract 无覆盖）：`/me/exams` 契约用例、submit 无 409 结构断言、`GET /sessions/{id}` 过期返回 200+expired+score、`applied=false`、`replayed=true`、`additionalProperties` 最小化。
5. **任务卡同步**：`TASK-BE-004`、`TASK-FE-004` 的 `contract_refs` 与验收依据需按 **v1.0.2** 更新（v1.0.1 已更新过一版；本轮新增 `replayed`/`applied`/`server_time` 必填与教师结果 403 口径）；`TASK-BE-002`（导入 `skipped_duplicates`、`explanation` 必填）、`TASK-BE-003`（`created_by`/`frozen_at`/状态机/单题 ≤100）、`TASK-BE-005`（`MyResultItem.session_id`、`average_score` 除零、教师不含单会话结果）需 R0 在派发前补一句「按 v1.0.2 契约」。
6. **`docs/11-data-model.md` 的 `explanation 可空`** 与 CHG-016 表述不一致（DB 允许 null、API 必填）。docs/11 不在 R2 本轮写作用域，已在 ADR-009 记录该口径，请文档 owner 补脚注。

### 7.1 v1.0.2 必须同步的 QA 断言（4 处，均为本轮有意收紧）

> 契约 v1.0.2 与现有 `tests/contract` 存在**已知断言冲突**：这些用例断言的是 v1.0.1 的宽松口径，必须由 QA 更新后 G2 才能转绿。
> 在更新前 G2 的红色是“断言未同步”，不是契约缺陷——**不要靠回退契约来修绿**。

7. **`test_tc_ct_13` 的 required 集合与相关断言（CHG-028，最关键）**：QA 需同步 `test_tc_ct_13` 的 required 集合与相关断言——
   该用例当前用 `==` 锁定 `SubmitResult.required == {session_id, status, score, submitted_at}`，并显式断言 `"replayed" not in required`
   （理由写作“CHG-002 要求 optional”）。v1.0.2 已把 `replayed` 提为 **required**（CHG-028，服务端总是返回 true/false）。
   请改为：`required == {session_id, status, score, submitted_at, replayed}`（或改用 `<=` 超集断言），并**删除**「replayed 应保持 optional」那条断言。
8. **`test_tc_ct_15`（CHG-028）**：`applied` 同理由 optional 变 required。请把 `{"saved_seq", "saved_at"} <= required` 改为
   `{"saved_seq", "saved_at", "applied"} <= required`，并删除 `assert "applied" not in required` 与「applied 应保持 optional」的说明。
9. **`test_tc_ct_09`（CHG-025/026）**：`PaperQuestion.points.maximum` 断言由 `1000` 改为 `100`；建议补边界用例
   「100 题 × 100 分 = 10000 合法」「单题 100.01 → 422」「Σpoints = 0 → 422」「Σpoints = 10000.01 → 422」。
   同时确认 `PaperCreate.description` 仍含 `total_points`/`Σ`/`100`/`422` 四个判据（v1.0.2 已保留）。
10. **`test_tc_ct_25`（CHG-032）**：`expected` 集合删除 `ALREADY_SUBMITTED`（枚举现为 16 值）；建议加一条反向断言
    `assert "ALREADY_SUBMITTED" not in code["enum"]`，防止实现/文档回潮。
    **代码侧残留（不在 R2 写作用域，需 owner 处理）**：`server/app/errors.py` 仍有 `ALREADY_SUBMITTED` 常量（BE-004 删除或映射 `CONFLICT`）；
    `web/src/api/types.ts` 是由 v1.0.1 生成的类型，仍含该枚举值——必须用 `openapi-typescript` 按 v1.0.2 重新生成，不要手改。
11. **条件不变量用例（CHG-027）**：`Paper.frozen_at`、`Exam.snapshot_version` 仍为 optional。新增用例必须**按 `status` 条件断言**
    （`Paper.status=frozen` 才要求 `frozen_at` 非空；`Exam.status ∈ {published, closed}` 才要求 `snapshot_version` 非空），
    不得改成无条件 required —— 契约已明确**不引入 `if/then`**，否则 `openapi-spec-validator` 与前端代码生成都会变复杂。
12. **logout 用例（CHG-029）**：`TC-CT-04`（所有受保护接口必须有 401）**保持通过**——v1.0.2 保留了 logout 的 401；
    新增「无 Cookie/已吊销 Cookie 调 logout → 401 `AUTH_REQUIRED`」与「有效会话 → 204」两条断言。

---

## 8. v1.0.3 待办批次（P-009/010/011）—— ✅ **已全部关闭**（v1.0.3 落地，见 §8.1）

> **状态（2026-11-09，v1.0.3）：本节无任何挂起项。** 三条待办已分别由 **CHG-033 / CHG-034 / CHG-035** 落地；
> 本节保留为历史记录（提案原文 + 落地映射），供追溯“为什么改”。后续若再出现挂起项，另开 §9，不得复用本节。

| 编号 | 事项 | 来源 | 契约改动 | 落地 |
|---|---|---|---|---|
| 待办-1（P-009） | 无有效会话时 **401 优先于 CSRF/参数错误**，写入 security 说明 | QA v1.0.1 同步审查 | `components.securitySchemes.sessionCookie.description` + 受影响写接口 description | ✅ **CHG-033**（v1.0.3） |
| 待办-2（P-010） | `/healthz` 200 schema 补 `db: string` 字段 | BE-007 实现（`db:"ok"`） | `paths./healthz.get.responses.200.schema` | ✅ **CHG-034**（v1.0.3，`db` 同时进 `required`） |
| 待办-3（P-011） | `AuditLog.actor_id` 改 `type: [integer, "null"]`；系统操作 `ip` 约定写 `"-"` | BE-006 系统记录（`actor_id=None`） | `components.schemas.AuditLog` + `AuditLogPage` | ✅ **CHG-035**（v1.0.3） |

**QA 同步点（已并入 §8.1.2 执行）**：待办-1 需补「缺 CSRF 头 + 无会话 → 401」用例；待办-2 需把 health schema 断言从 `required=[status,version]` 改为含 `db`；待办-3 需补「系统记录 actor_id=null 可反序列化」用例。

---

## 8.1 本轮（v1.0.2 → v1.0.3）修订概览

| 项 | 内容 |
|---|---|
| 版本 | `contracts/openapi.yaml` **v1.0.2 → v1.0.3** |
| 日期 | 2026-11-09 |
| 处置人 | R2 Architect（输入 = 本节 §8 三条挂起待办 + QA-002 报的「他人 draft 资源 403/404」口径张力 + 枚举复核） |
| 决策记录 | [docs/10-arch.md](10-arch.md) **ADR-011**（本轮四条主决策 + AC-18 边界） |
| 处置结果 | **4 条全部落地（CHG-033..CHG-036），0 条延后，0 条挂起**；§8 待办批次**全部关闭** |
| 性质 | **V1 冻结前的最后一次契约修订**；自本版起只允许 **CHG 级变更**（先 CHG + ADR，再改契约） |
| 断言影响 | **3 处 QA 断言需同步**（§8.1.2）：healthz `required` 含 `db`、`actor_id` 可空断言、draft 资源 404/403 用例 |
| 门控 | 契约变更**必须重过 G2**（按 v1.0.3），再继续 G4 |

**本轮取舍原则**：只关闭挂起项与消除口径冲突，**不新增接口、不删接口、不改错误码枚举**；凡是与实现一致且可被黑盒断言的口径，写进 schema/description 并标明 CHG 号。

### 8.1.1 逐条登记（CHG-033..CHG-036）

| CHG | 事项 | 变更内容 | 兼容性 | 影响文件 / 实施方 | QA 动作 |
|---|---|---|---|---|---|
| CHG-033 | P-009 认证优先于 CSRF 与参数校验 | `sessionCookie.description` 与**全部写接口** description 写明：无有效会话 → **401 `AUTH_REQUIRED`**，**优先于** CSRF 校验（403 `CSRF_INVALID`）与参数/请求体校验（422）。判定链固定为 401 → 403(CSRF) → 403(角色) → 403(归属) → 404(不可见) → 422 | 语义澄清（与中间件实现一致，无响应结构变化） | BE-001 中间件（顺序已如此）；FE-001 仅需把 401 当“未登录”处理 | 新增「无会话 + 缺 CSRF 头（或参数非法）→ 401」；扩展现有 `test_unauthenticated_write_returns_401_before_csrf` |
| CHG-034 | P-010 `/healthz` 200 缺 `db` | 200 schema 显式声明 `db: {type: string}` 并加入 `required`（`required: [status, db]`）；`version` 保持 optional（实现在返回，为兼容不强制）。实现每次 `SELECT 1`，通过返回 `db:"ok"`，失败整体 503 | **响应必填新增**（QA 断言从 `[status]` 扩到 `{status, db}`） | BE-007 已实现（`server/app/api/health.py`）；R6 探针无改动 | 契约断言：`required` 含 `db`；运行时断言 `body["db"] == "ok"` |
| CHG-035 | P-011 `AuditLog.actor_id` 不可空、系统记录难表达 | `actor_id` 改 `type: [integer, "null"]`（字段仍必填、总是出现；系统操作/CLI 记录为 null）；`AuditLog.description` 删除旧文「不允许 null」；`ip` 约定**系统操作记 `"-"`**；`AuditLogPage` 补同口径说明；`GET /audit-logs` description 补系统记录与 `actor_id` 过滤语义 | 放宽（`integer|null`）；读取方需容忍 null | BE-006 已按 `actor_id=None` 落库；`ip` 的 `"-"` 约定需 BE-006 在系统记录路径显式落 `"-"` 而非 null | 契约断言 `type` 含 `"null"`；新增「系统记录 `actor_id=null` 可反序列化、`ip == "-"`」 |
| CHG-036 | QA-002 他人 draft 资源 403/404 张力 | 统一口径并写进 `Forbidden`/`NotFound` description、`GET/PUT/DELETE /questions`、`GET/PUT/DELETE /papers`、`GET /exams`、`GET /exams/{id}`、`PATCH /exams/{id}`、`GET /exams/{id}/results`、`POST /exams/{id}/sessions`：**学生视角**的 draft/未发布资源 → **404**；**可见但非本人** → **403**；**角色不符** → **403**；教师（同角色）他人的题目/试卷/考试**含 draft** 一律 **403**（管理视图可见，不得 404） | 语义澄清（与学生侧 404、教师侧 403 的实现一致；无 schema 变化） | BE-002/003/005 归属校验；QA 安全用例；docs/12 §2/§2.0/§2.1 | 新增「教师 B 对教师 A 的 draft 试卷/考试 → 403」；保留并正式化「学生 draft 考试建会话 → 404」 |
| 复核 | `Error.code` 枚举 | 确认含 `NOT_SUBMITTED`（`server/app/errors.py` 在用，未交卷读逐题结果 409）与 `IMPORT_PARTIAL`（导入部分失败，HTTP 200）；**本轮不改枚举**（仍 16 值），仅补 `Error.description` 说明 | 无变化 | — | 无需改断言（`TC-CT-25` 仍 16 值） |

### 8.1.2 QA 必须同步的断言（3 处）

1. **healthz 的 `required` 必须含 `db`（CHG-034）**：契约 `paths./healthz.get.responses.200.content.application/json.schema.required` 现为 `[status, db]`。
   - 契约断言：`{"status","db"} <= set(required)`，且 `properties["db"]["type"] == "string"`；不得再断言 `required == ["status"]`。
   - 运行时断言：`GET /api/healthz` → 200 且 `body["db"] == "ok"`（`status == "ok"` 保持）；503 分支仍是统一 `Error{code,message,request_id}`，**不含** `db`。
   - 现有 `tests/security/test_permission_matrix.py::test_healthz_is_public` 只断言 `status == "ok"`，建议补 `db == "ok"`（不阻断，属补强）。
2. **`actor_id` 允许 null 的断言（CHG-035）**：
   - 契约断言：`AuditLog.properties.actor_id.type` 为 `["integer","null"]`（或 `"null" in type`）；`AuditLog.required` **仍含** `actor_id`（字段必填 ≠ 值不可空，二者不可混为一谈）。
   - 运行时断言：`GET /audit-logs` 返回的 `items[]` 中，系统记录（若有）`actor_id is None` 且 `ip == "-"`；用户记录 `actor_id` 为整数、`ip` 为非空字符串。
   - **注意**：现有 `tests/contract/test_openapi_valid.py` 只检查 `AuditLog.required` 是否含 `actor_id`（仍通过），**没有**类型断言，故必须新增。
3. **draft 资源 404 / 403 用例（CHG-036）**：
   - **学生视角 → 404**：`POST /exams/{draft_exam}/sessions` → **404 `NOT_FOUND`**（已有 `test_idor.py::test_draft_exam_session_returns_404_for_student`，保留；可补 `GET /me/exams` 不含该考试）。
   - **教师同角色 → 403**：教师 B 对教师 A 的 **draft** 试卷/考试执行 `GET/PUT/DELETE /papers/{id}`、`GET/PATCH /exams/{id}` → **403 `FORBIDDEN`**（**不得 404**）。这是 `tests/security/README.md` §6.11 记录的口径张力，v1.0.3 已定稿，QA 应把该 open question 关闭并补用例。
   - 断言仍走统一 `Error` 三字段；403/404 不得携带业务数据。

> **冻结声明（v1.0.3）**：`contracts/openapi.yaml` v1.0.3 是 **V1 的冻结版本**。此后任何修改（含措辞级）
> 都必须先在本文件登记 **CHG-037+** 并在 `docs/10-arch.md` 追加 ADR，再动契约与 `info.version`；
> 不得直接编辑契约“顺手修”。

