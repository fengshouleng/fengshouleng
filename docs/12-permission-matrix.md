# 12 · RBAC 权限矩阵 v1.3

- 版本：v1.3　日期：2026-11-09　作者：R2 Architect（随契约 **v1.0.3** 同步：CHG-033 认证优先于 CSRF/参数校验、CHG-035 审计系统记录 `actor_id=null`/`ip="-"`、CHG-036 可见性判定定稿；上一版 v1.2 随 v1.0.2 同步 CHG-029/CHG-030，v1.1 随 v1.0.1 同步 CHG-003/CHG-012/CHG-013）
- 角色：`student` / `teacher` / `admin`
- 资源归属：题目、试卷带 `owner_id`，考试带 `created_by`；教师只能操作自己的资源，管理员可见全部
- **可见域（v1.0.3 / CHG-036 定稿）**：`student` 可见域 = 已发布考试 + 自己的会话/成绩/解析；`teacher` 可见域 = 自己的题目/试卷/考试 + **同为教师角色的他人资源（管理视图，含 `draft`）**；`admin` 全可见。可见与否只决定 **403 还是 404**，不改变“只能改自己的”这一归属约束
- 实现要求：**服务端强制校验**（前端隐藏按钮不算），所有越权一律走 §2 的 403/404 判定，响应体只能是统一 `Error`

## 1. 接口权限矩阵

| 接口 | student | teacher（自己的） | admin |
|---|---|---|---|
| `POST /auth/login` | ✅ | ✅ | ✅ |
| `POST /auth/logout` | ✅（有效会话 204；无有效会话 401） | ✅（同左） | ✅（同左） |
| `GET /auth/me` | ✅ | ✅ | ✅ |
| `GET/POST /users`、`PATCH /users/{id}` | ❌ | ❌ | ✅ |
| `GET /questions`（列表/详情） | ❌ | ✅ | ✅ |
| `POST/PUT/DELETE /questions` | ❌ | ✅（仅自己创建） | ✅ |
| `POST /questions/import` | ❌ | ✅ | ✅ |
| `GET/POST /papers`、`PUT/DELETE` | ❌ | ✅（仅自己的） | ✅ |
| `GET/POST /exams`、`PATCH /exams/{id}` | ❌ | ✅（仅自己的） | ✅ |
| `GET /exams/{id}/results` | ❌ | ✅（仅自己的考试） | ✅ |
| `POST /exams/{id}/sessions` | ✅ | ❌ | ❌ |
| `GET /sessions/{id}`、`PUT .../answers`、`POST .../submit` | ✅（仅自己的会话） | ❌ | ❌ |
| `GET /sessions/{id}/result`（v1.0.2 收紧，CHG-030） | ✅（仅自己的） | ❌ **教师走考试汇总**（见备注） | ✅ |
| **`GET /me/exams`**（v1.0.1 新增，C-02） | ✅ | ❌ | ❌ |
| `GET /me/results`、`GET /me/wrong-questions` | ✅ | ❌ | ❌ |
| `GET /audit-logs` | ❌ | ❌ | ✅ |
| `GET /healthz` | 公开 | 公开 | 公开 |

> **`GET /sessions/{id}/result` 的教师口径（v1.0.2 变更，CHG-030）**：单会话逐题结果只对学生本人开放；
> 教师即使该会话属于**自己创建**的考试，调用也一律 **403 `FORBIDDEN`**（第 3 步角色判定）。
> 教师读数据的唯一入口是 **考试汇总** `GET /exams/{id}/results`（名单 + 状态 + 分数 + 平均分）。
> 该变更同时纠正了 v1.1 矩阵中 teacher 一格写「✅（自己考试的汇总）」的歧义：汇总不是逐题结果。

> `GET /me/exams` 与 `GET /exams` 的分工：学生用 `/me/exams`（只读、只见 `published` + 自己已考的 `closed`）；
> 教师/admin 用 `/exams`（管理视角）。学生调用 `/exams` → 403；教师调用 `/me/exams` → 403。

## 2. 403 / 404 判定（防枚举，C-17；v1.0.3 / CHG-036 定稿）

**唯一判定顺序**（实现与测试都按这个顺序断言，短路返回，前面的步骤一旦命中不再继续）：

| 步 | 条件 | 返回 | code |
|---|---|---|---|
| 1 | 未登录 / 会话失效 / 账号停用（**无有效会话**） | **401** | `AUTH_REQUIRED` |
| 2 | 已认证，写接口缺 / 错 `X-CSRF-Token` | **403** | `CSRF_INVALID` |
| 3 | **角色**整体无权调用该接口（如 student 调 `/users`、teacher 调 `/me/exams`） | **403** | `FORBIDDEN` |
| 4 | 角色允许、资源对当前主体**可见**但**归属不符**（他人会话/成绩/题目/试卷/考试） | **403** | `FORBIDDEN` |
| 5 | 资源对当前主体**不可见**（**学生视角**的 draft/未发布考试、软删除题目、学生视角的他人私有资源）或**确实不存在** | **404** | `NOT_FOUND` |

**第 1 步优先于一切**（v1.0.3 / CHG-033）：无有效会话时返回 **401**，**优先于第 2 步的 CSRF 校验，也优先于参数/请求体校验（422）**。未认证请求即使缺少 `X-CSRF-Token`、或 `client_seq`/`answers`/`exam_id` 等参数非法，也是 401，不是 403/422。

### 2.0 可见域按角色定义（v1.0.3 / CHG-036，消除「他人 draft 资源 403/404」张力）

| 资源族 | student | teacher（同角色） | admin |
|---|---|---|---|
| 会话 / 成绩（`/sessions/*`、`/me/*`） | 仅自己的；他人 → **403**（AC-18，IDOR） | 第 3 步 **403**（无该接口权限） | 全部 |
| 题目 / 试卷（`/questions/*`、`/papers/*`） | 第 3 步 **403** | 自己的 ✅；他人 → **403（含 `draft`）** | 全部 |
| 考试（`/exams/*`） | 第 3 步 **403**；`POST /exams/{id}/sessions` 对 `draft`/未发布 → **404** | 自己的 ✅；他人 → **403（含 `draft`）**；不存在 → **404** | 全部 |
| 审计（`/audit-logs`） | 第 3 步 **403** | 第 3 步 **403** | 全部 |

要点：

1. **IDOR 返回 403**（AC-18 硬性要求，`TC-SEC-18` 按 403 断言）：会话/成绩对同角色是“可见但非本人”的资源，属第 4 步。
2. **“不可见即不存在”只按角色生效**（第 5 步）：对**学生**，draft/未发布考试与不存在的考试一律 404（不区分“未发布”与“不存在”，因此不定义 `EXAM_NOT_PUBLISHED`）；对**教师**，同角色的他人题目/试卷/考试（**含 draft**）在管理视图内**可见**，故走第 4 步 **403**，**不得返回 404**。这正是 v1.0.3（CHG-036）对 QA-002 所报张力的处置：旧表述把“他人 draft”一律归入 404，与 §2.1 #2「教师 A 改教师 B 的资源 → 403」及实现冲突。
3. **403 与 404 的响应体完全相同结构**：统一 `Error{code,message,request_id}`，**禁止**夹带 `items/score/selected/correct_answer` 等业务字段（契约 `Forbidden`/`NotFound` description 已写死，C-21 另对 5 个响应对象设 `additionalProperties: false`）。
4. 判定顺序**先角色后归属**：学生访问 `/audit-logs` 是第 3 步 403，不能因为“审计记录存不存在”而返回 404。
5. **教师读单会话结果是第 3 步（角色判定）**：`GET /sessions/{id}/result` 的角色允许集合只有 `student` 与 `admin`（v1.0.2 / CHG-030）；教师即使是考试创建者也被拒，且必须是 403 而不是 404——避免“教师能否看到这条会话”成为存在性侧信道。
6. **参数校验（422）永远在判定链末端**：401（会话）→ 403（CSRF/角色/归属）→ 404（不可见）→ 422（字段校验）。因此“未登录 + 参数非法”是 401，“角色不符 + 参数非法”是 403，不会先报 422（v1.0.3 / CHG-033）。

### 2.1 QA 必覆盖负例

| # | 场景 | 期望 |
|---|---|---|
| 1 | 学生 B 用学生 A 的 `session_id` 请求 `GET /sessions/{id}` / `/result` / `PUT /answers` / `POST /submit` | **403** `FORBIDDEN`（不得返回答案、成绩、正确选项） |
| 2 | 教师 A 修改（或读）教师 B 的题目/试卷/考试——**无论 `draft` / `frozen` / `published`**（v1.0.3 / CHG-036） | **403** `FORBIDDEN`（**不得 404**：教师同角色可见域含 draft） |
| 3 | 学生调用 `PATCH /users/{id}` 改自己 role | **403** `FORBIDDEN` |
| 4 | 学生创建 **draft** 考试的会话（`POST /exams/{id}/sessions`） | **404** `NOT_FOUND`（不是 409/403） |
| 5 | 学生调用 `GET /audit-logs` / `GET /users` | **403** `FORBIDDEN` |
| 6 | 教师调用 `GET /me/exams` / `POST /exams/{id}/sessions` | **403** `FORBIDDEN` |
| 7 | 不存在 id 的 `GET /sessions/999999/result` | **404** `NOT_FOUND` |
| 8 | 任意 403/404 响应 | 只含 `code/message/request_id`，无业务数据 |
| 9 | 教师请求 `GET /sessions/{id}/result`（**含自己考试的学生会话**，v1.0.2 / CHG-030） | **403** `FORBIDDEN`（第 3 步角色判定；不得改成 404，也不得返回逐题明细） |
| 10 | 同一 Cookie 第二次 `POST /auth/logout`（会话已吊销，v1.0.2 / CHG-029） | **401** `AUTH_REQUIRED`（不是 204；客户端视为“已登出”） |
| 11 | 教师 B `GET/PUT/DELETE /papers/{id}`、`GET/PATCH /exams/{id}` 指向教师 A 的 **draft** 试卷/考试（v1.0.3 / CHG-036） | **403** `FORBIDDEN`（教师可见域含 draft，**不得因“未发布”返回 404**） |
| 12 | 无有效会话 + 缺/错 `X-CSRF-Token`（或参数非法）的写请求，如 `POST /questions`、`PUT /sessions/{id}/answers`（v1.0.3 / CHG-033） | **401** `AUTH_REQUIRED`（优先于 403 `CSRF_INVALID` 与 422） |
| 13 | 学生 `POST /exams/{draft}/sessions`（与 #4 同一口径；另：该考试不出现在 `GET /me/exams`）（v1.0.3 / CHG-036） | **404** `NOT_FOUND`（学生视角不可见，不是 403） |

## 3. 会话、时间窗与 CSRF

- 非 GET 请求必须带 `X-CSRF-Token`，与服务端会话绑定；缺失/不匹配 → **403** `CSRF_INVALID`（`/auth/logout` 也适用，C-09）。
- **认证先于 CSRF，且 401 优先于参数校验**（v1.0.2 / CHG-029；v1.0.3 / CHG-033 扩展）：无有效会话的写请求返回 **401** `AUTH_REQUIRED`，不会返回 403——即使它同时缺少 `X-CSRF-Token` 或请求体/参数非法（也不会先报 422）。故 `/auth/logout` 的有效会话返回 **204**，同一 Cookie 第二次调用（会话已吊销）返回 **401**，**不再有“已失效会话返回 204”的口径**。
- 会话过期（8h）→ **401** `AUTH_REQUIRED`，前端清空状态并跳登录。
- 账号停用（`is_active=false`）→ 该账号现有会话全部失效（AC-04）→ 401。
- 时间窗（契约 `POST /exams/{id}/sessions` description 已写死，C-22）：

| 场景 | 状态码 | code |
|---|---|---|
| 首次进入、`published` 且窗内 | **201**（新建会话） | — |
| 已有会话（作答中/已交卷/已到期） | **200**（恢复，含 `answers`/`score`） | — |
| `now < start_at` | **409** | `EXAM_NOT_STARTED` |
| `now > end_at` 或考试 `closed`（无既有会话） | **409** | `EXAM_EXPIRED` |
| 考试不存在 / `draft` / 不可见 | **404** | `NOT_FOUND` |
| 写会话时已到期或已交卷（`PUT /answers`） | **409** | `EXAM_EXPIRED`（随后读 `GET /sessions/{id}` 得 `status=expired` + `score`） |

- 交卷 **只有 200**：重复提交返回既有成绩（`replayed=true`），不存在 409（C-15，见 ADR-005/ADR-009）。

## 4. 审计要求

- 全部写操作（含被拒绝的越权尝试，可选）写 `audit_logs`：`action`、`target_type`、`target_id`、`ip` 为**必填**（AC-22，C-05），`actor_id` 字段也必填（**总是出现**）但**值可为 `null`**：用户触发的写操作 → 真实 `users.id`；**系统操作 / CLI 记录**（备份清理、初始化、定时任务、无请求上下文的动作）→ `actor_id=null`（v1.0.3 / CHG-035，BE-006 传 `actor_id=None`），**不得伪造成某个用户 id**。
- **系统操作的 `ip` 固定记 `"-"`**（不是 null、不是空串；v1.0.3 / CHG-035）；用户操作的 `ip` 必须是真实来源地址字符串。`user_agent`/`detail` 可选，`detail` 必须脱敏（禁止明文密码/答案）。
- 仅 admin 可查询；默认返回最近 180 天（`from`/`to` 闭区间）；记录只增不改不删。`GET /audit-logs` 的 `actor_id` 查询参数只按用户过滤，系统记录（null）不匹配任何整数过滤值。

## 5. OpenAPI 映射（v1.0.3）

- `components.securitySchemes.sessionCookie`（apiKey in cookie）应用于除 `login`、`healthz` 外的全部接口；公开接口集合严格等于 `{GET /healthz, POST /auth/login}`（QA `TC-CT-00` 硬断言）。其 `description` 固化**认证优先**（v1.0.3 / CHG-033）：无有效会话 → 401 `AUTH_REQUIRED`，优先于 CSRF 与参数校验。
- `X-CSRF-Token` 通过 `components.parameters.CsrfToken` 在**每个写接口**引用，且每个引用它的接口都声明 `403`（C-09）。
- 401 统一引用 `components.responses.Unauthorized`；CSRF 失败引用 `CsrfForbidden`；登录失败引用 `InvalidCredentials`；角色/归属越权引用 `Forbidden`；不可见/不存在引用 `NotFound`；状态冲突引用 `Conflict`（含 `EXAM_NOT_STARTED`/`EXAM_EXPIRED`/`QUESTION_IN_USE`/`PAPER_FROZEN`/`NOT_SUBMITTED`/`CONFLICT`）。
- `GET /healthz` 的 200 响应 schema 必填 `{status, db}`（`db: {type: string}`，实现执行 `SELECT 1` 后返回 `"ok"`；探针失败整体 503）（v1.0.3 / CHG-034）。
- `components.schemas.AuditLog.actor_id` 为 `type: [integer, "null"]`（系统操作为 null），`ip` 系统操作记 `"-"`；`AuditLogPage.items[]` 同步（v1.0.3 / CHG-035）。
- 所有 4xx/5xx 响应体必须是同一个 `Error` schema；`Error.code` 为枚举（C-03，见契约）。v1.0.2 起枚举中**不再有** `ALREADY_SUBMITTED`（CHG-032）；枚举共 16 值，`NOT_SUBMITTED` 与 `IMPORT_PARTIAL` 保留在用（v1.0.3 复核）。新码不得擅自添加，须先走 CHG + ADR。
- `GET /sessions/{id}/result` 的 403 同时覆盖两种情形：① 角色不允许（teacher，第 3 步）；② 学生非本人（IDOR，第 4 步）。契约 description 已写死，二者都是统一 `Error`，学生侧不得因“他人会话”改成 404（AC-18）。
- **v1.0.3 之后契约冻结（V1）**：任何后续调整只能走 CHG + ADR（见 docs/13 §8.1 与 ADR-011）。
