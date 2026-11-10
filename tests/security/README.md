# tests/security/ — 权限负例与安全基线（TASK-QA-002）

- **负责人**：R5 QA（preset `qa-verify`），**只报不修**
- **任务卡**：`contracts/tasks/TASK-QA-002.yaml`（T3.3 安全负例）
- **被测契约**：`contracts/openapi.yaml` **v1.0.3**（V1 冻结版）
- **权限基线**：`docs/12-permission-matrix.md` **v1.3**（§1 权限矩阵、§2 判定链与 §2.0 可见域、§3 CSRF/会话）
- **同步依据**：`docs/13-contract-changes.md` **§8.1.2**（v1.0.3 必须同步的 3 处断言）+ CHG-033/CHG-036
- **技术选型**：pytest + httpx（同步），**黑盒**打运行中的 API；不 import `server/` 代码
- **当前状态**：本机 shell 不可用（`0xC0000142`），**本目录用例未在本地执行**，`tests.verified=false`；
  预期结果为“全部 pass，仅 1 条已知缺口 xfail”（见 §7）；另有 2 条**环境前置**导致的 skip
  （healthz 503 分支需 `EXAM_HEALTHZ_503=1`、系统审计记录需先 `create-admin`），见 §6.12/§6.13

## 1. 文件

| 文件 | 内容 |
|---|---|
| `conftest.py` | API 客户端、账号/场景 fixture、skip 逻辑、断言工具、脱敏 |
| `test_permission_matrix.py` | docs/12 §1 的未登录/学生/教师负例（参数化） |
| `test_csrf_session.py` | CSRF 缺失/错误、认证先于 CSRF、登出/旧 Cookie/停用/伪造 Cookie |
| `test_idor.py` | IDOR（学生 B 读学生 A、教师 B 读/改教师 A 的题目/试卷/考试、**教师 B 对教师 A 的 draft 资源 → 403**）、403/404 防枚举、`/me/exams` 不含 draft |
| `test_contract_v103.py` | **v1.0.3 新增**：`GET /healthz` 的 `db=="ok"`（503 分支可选）、`AuditLog.actor_id` 系统记录为 null / 用户记录为整数（CHG-034/035） |
| `test_injection_xss.py` | SQL 元字符、XSS、路径/分页注入、导入通道注入 |
| `test_secrets.py` | 登录失败不可区分、password/password_hash 不泄漏、弱密码 422、审计脱敏 |

证据目录：`reports/security/`（占位 README，运行产物由 CI 写入，**不提交大文件**）。

## 2. 起栈与 seed（本地开发）

> 默认账号/口令**仅限本地开发**；staging/prod 必须用环境变量显式覆盖。

### 2.1 后端

```bash
cd server
pip install -e ".[dev]"
alembic upgrade head
APP_ENV=local python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

PowerShell：

```powershell
cd server
alembic upgrade head
$env:APP_ENV="local"; python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

API 根为 `http://127.0.0.1:8000/api`（与契约 `servers[0].url` 一致）。

### 2.2 演示账号（seed-demo）

```bash
cd server
APP_ENV=local EXAM_SEED_PASSWORD='<本地强口令>' \
  python -m app.cli seed-demo --password-env EXAM_SEED_PASSWORD
```

`seed-demo` 只在**空库**执行，重复执行幂等跳过；生成：

| 用户名 | 角色 | 用途 |
|---|---|---|
| `demo-teacher` | teacher | 教师 A；建题/组卷/发考试 |
| `demo-student-1` | student | 学生 A；IDOR 受害者 |
| `demo-student-2` | student | 学生 B；IDOR 攻击者 |

**管理员不在 `seed-demo` 的生成范围内**，需单独创建（安全用例里 admin 正例、
停用账号、弱密码、审计查询都依赖它）：

```bash
cd server
APP_ENV=local EXAM_QA_PASSWORD='<本地强口令>' \
  python -m app.cli create-admin \
    --username qa-admin --display-name QA管理员 \
    --password-env EXAM_QA_PASSWORD
```

- 未传 `--password-env` 且 `APP_ENV` 非 staging/prod 时，`seed-demo` 使用开发默认密码
  `dev-demo-password-123`（**仅本地开发**）。
- 若 seed/create-admin 用了自定义口令，运行测试时必须显式导出同名
  `EXAM_QA_PASSWORD`（或按角色的 `EXAM_TEACHER_PASSWORD` 等）。
- 本套件会通过 admin 的 `POST /users` 创建**唯一前缀** `qa-sec-*` 的教师 B/停用学生；
  没有删除用户接口，测试数据保留是预期行为（见 §6）。

### 2.3 Compose（staging 同构）

```bash
cp deploy/.env.example deploy/.env.e2e        # 填写 POSTGRES_*、NGINX_HTTP_PORT
# 纯 HTTP 测试栈需关闭 Secure Cookie：在 deploy/.env.e2e 加 SESSION_COOKIE_SECURE=false
docker compose -f deploy/docker-compose.yml --env-file deploy/.env.e2e \
  -p exam-sec up -d --build

# 容器内 APP_ENV 默认 staging → 必须显式提供密码
docker compose ... exec -e EXAM_SEED_PASSWORD='<强口令>' api \
  python -m app.cli seed-demo --password-env EXAM_SEED_PASSWORD
docker compose ... exec -e EXAM_QA_PASSWORD='<强口令>' api \
  python -m app.cli create-admin --username qa-admin --display-name QA管理员 \
    --password-env EXAM_QA_PASSWORD
```

## 3. 执行

```bash
pip install -r tests/requirements.txt

EXAM_API_URL=http://127.0.0.1:8000/api \
EXAM_QA_PASSWORD='<seed/create-admin 的口令>' \
python -m pytest tests/security -q

# 门控证据（CI 回填报告）
EXAM_API_URL=http://127.0.0.1:8000/api \
python -m pytest tests/security -q \
  --junitxml=reports/security/security.xml
```

单文件 / 单用例（每个用例可独立运行，前置数据由 fixture 按需构造）：

```bash
EXAM_API_URL=http://127.0.0.1:8000/api python -m pytest tests/security/test_permission_matrix.py -q
EXAM_API_URL=http://127.0.0.1:8000/api python -m pytest tests/security/test_idor.py -q -k "draft"
```

### 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `EXAM_API_URL` | 无（未设置 → 整目录 skip） | API 根，含 `/api`；显式设置后才执行 |
| `EXAM_QA_PASSWORD` | `dev-demo-password-123`（仅开发） | 测试账号统一口令 |
| `EXAM_TEACHER_USERNAME` | `demo-teacher` | 教师 A |
| `EXAM_STUDENT_A_USERNAME` | `demo-student-1` | 学生 A（IDOR 受害者） |
| `EXAM_STUDENT_B_USERNAME` | `demo-student-2` | 学生 B（IDOR 攻击者） |
| `EXAM_ADMIN_USERNAME` | `qa-admin` | 管理员（需 `create-admin`） |
| `EXAM_TEACHER_PASSWORD` | 回退 `EXAM_QA_PASSWORD` | 教师 A 口令覆盖 |
| `EXAM_STUDENT_A_PASSWORD` | 回退 `EXAM_QA_PASSWORD` | 学生 A 口令覆盖 |
| `EXAM_STUDENT_B_PASSWORD` | 回退 `EXAM_QA_PASSWORD` | 学生 B 口令覆盖 |
| `EXAM_ADMIN_PASSWORD` | 回退 `EXAM_QA_PASSWORD` | 管理员口令覆盖 |
| `EXAM_HEALTHZ_503` | 未设置（该用例 skip） | 置 `1` 时执行 `GET /healthz` 的 503 分支断言（需先制造 DB 故障，见 §6.12） |

> `EXAM_API_URL` 未设置时所有测试模块 `pytest.mark.skipif` 整模块 skip；
> 账号登录 401（未 seed / 口令不符）时，依赖该账号的用例 skip，并提示 seed 命令。
> 运行完整覆盖（含 admin/教师 B）需要 §2.2 的 seed-demo **和** create-admin。

## 4. 覆盖矩阵（与 docs/12 v1.3 逐行对应）

### 4.1 docs/12 §1 接口权限矩阵

| docs/12 行 | 负例/正例 | 用例 |
|---|---|---|
| `POST /auth/login` | 公开；登录失败不区分账号/密码 | `test_permission_matrix.py::test_login_is_public_and_csrf_exempt`；`test_secrets.py::test_wrong_password_and_unknown_user_are_indistinguishable` |
| `POST /auth/logout` | 有效 204；旧 Cookie 第二次 401；缺/错 CSRF 403 | `test_csrf_session.py::test_valid_logout_204_then_old_cookie_is_401` 等 |
| `GET /auth/me` | 未登录 401；响应无 password | `test_permission_matrix.py::test_unauthenticated_returns_401_auth_required`；`test_secrets.py::test_me_response_does_not_expose_password_fields` |
| `GET/POST /users`、`PATCH /users/{id}` | student/teacher 403；admin 200；弱密码 422 | `test_permission_matrix.py::test_student_forbidden_on_admin_and_teacher_endpoints` / `test_teacher_forbidden_on_admin_endpoints`；`test_secrets.py::test_weak_password_is_rejected_by_user_create` |
| `GET /questions`、`GET /questions/{id}` | student 403；teacher B 读 A → 403 | `test_permission_matrix.py` + `test_idor.py::test_teacher_b_cannot_read_teacher_a_resources` |
| `POST/PUT/DELETE /questions` | student 403；teacher B 改 A → 403 | `test_permission_matrix.py` + `test_idor.py::test_teacher_b_cannot_update_teacher_a_question` / `...delete...` |
| `POST /questions/import` | student 403；注入 payload 不 500 | `test_permission_matrix.py` + `test_injection_xss.py::test_import_with_injection_payload_returns_200_not_500` |
| `GET/POST /papers`、`PUT/DELETE` | student 403；teacher B 改/删 A → 403；**教师 B 对 A 的 draft 试卷 GET/PUT/DELETE → 403（不得 404，CHG-036）** | `test_permission_matrix.py` + `test_idor.py::test_teacher_b_cannot_update_teacher_a_paper` / `...delete...`；`test_teacher_b_cannot_read_teacher_a_draft_paper` / `...update...draft_paper` / `...delete...draft_paper` |
| `GET/POST /exams`、`PATCH /exams/{id}` | student 403；teacher B 改 A → 403；**教师 B 对 A 的 draft 考试 GET/PATCH → 403（不得 404，CHG-036）** | `test_permission_matrix.py` + `test_idor.py::test_teacher_b_cannot_patch_teacher_a_exam`；`test_teacher_b_cannot_read_teacher_a_draft_exam` / `...patch...draft_exam` |
| `GET /exams/{id}/results` | student 403；teacher B 403；创建者/admin 200 | `test_permission_matrix.py`、`test_idor.py::test_teacher_owner_and_admin_can_read_exam_results` |
| `POST /exams/{id}/sessions` | teacher/admin 403；draft 考试学生 404 | `test_permission_matrix.py::test_teacher_forbidden_on_student_endpoints` / `test_admin_forbidden_on_student_only_endpoints`；`test_idor.py::test_draft_exam_session_returns_404_for_student` |
| `GET /sessions/{id}`、`PUT .../answers`、`POST .../submit` | teacher/admin 403；学生 B 用 A 的 session → 403 | `test_permission_matrix.py`；`test_idor.py::test_student_b_cannot_access_student_a_session` / `...save_answers...` |
| `GET /sessions/{id}/result` | teacher 一律 403（CHG-030）；学生 B → 403；admin/本人 200 | `test_idor.py::test_teacher_cannot_read_single_session_result_even_own_exam`；`test_student_b_cannot_read_student_a_result_without_business_data`；`test_owner_can_read_own_session_and_result` |
| `GET /me/exams` | teacher/admin 403 | `test_permission_matrix.py::test_teacher_forbidden_on_student_endpoints` / `test_admin_forbidden_on_student_only_endpoints` |
| `GET /me/results`、`GET /me/wrong-questions` | teacher/admin 403 | 同上 |
| `GET /audit-logs` | student/teacher 403；admin 200 | `test_permission_matrix.py`；`test_admin_can_list_users_and_audit_logs` |
| `GET /healthz` | 公开；200 必带 `status`/`db`（`db=="ok"`，CHG-034）；503 分支见 §6.12 | `test_permission_matrix.py::test_healthz_is_public`；`test_contract_v103.py::test_healthz_returns_db_ok` / `test_healthz_503_is_unified_error_without_db`（默认 skip） |

> §1 中所有「❌」组合均已映射到至少一个用例；不存在只测正例不测负例的行。

### 4.2 docs/12 §2.1「QA 必覆盖负例」

| # | 场景 | 用例 |
|---|---|---|
| 1 | 学生 B 用学生 A 的 `session_id` 读/写会话与成绩 | `test_idor.py::test_student_b_cannot_access_student_a_session`、`...save_answers...` |
| 2 | 教师 A 改教师 B 的题目/试卷/考试 | `test_idor.py::test_teacher_b_cannot_update_teacher_a_question`、`...paper...`、`...patch...exam`、`...delete...` |
| 3 | 学生 `PATCH /users/{id}` 改自己 role | `test_permission_matrix.py::test_student_cannot_change_own_role`（另参数化覆盖任意 id） |
| 4 | 学生创建 draft 考试的会话 → 404 | `test_idor.py::test_draft_exam_session_returns_404_for_student` |
| 5 | 学生调 `/audit-logs`、`/users` → 403 | `test_permission_matrix.py` |
| 6 | 教师调 `/me/exams`、`POST /exams/{id}/sessions` → 403 | `test_permission_matrix.py::test_teacher_forbidden_on_student_endpoints` |
| 7 | 不存在 id 的 `GET /sessions/999999999/result` → 404 | `test_idor.py::test_nonexistent_session_returns_404` |
| 8 | 任意 403/404 只含 `code/message/request_id` | `test_idor.py::test_403_and_404_use_same_error_shape`；`assert_error` 全局断言 |
| 9 | 教师读自己考试的学生会话结果 → 403（CHG-030） | `test_idor.py::test_teacher_cannot_read_single_session_result_even_own_exam` |
| 10 | 同一 Cookie 第二次登出 → 401（CHG-029） | `test_csrf_session.py::test_valid_logout_204_then_old_cookie_is_401` |
| 11 | **教师 B 对教师 A 的 draft 试卷/考试 `GET/PUT/DELETE /papers`、`GET/PATCH /exams` → 403（不得 404，v1.0.3 / CHG-036）** | `test_idor.py::test_teacher_b_cannot_read_teacher_a_draft_paper` / `...update...draft_paper` / `...delete...draft_paper` / `...read...draft_exam` / `...patch...draft_exam` |
| 12 | **无有效会话 + 缺/错 CSRF（或参数/请求体非法）→ 401（v1.0.3 / CHG-033）** | `test_csrf_session.py::test_unauthenticated_write_returns_401_before_csrf`（含非法 body / `client_seq=0` / 非整数路径参数） |
| 13 | 学生 `POST /exams/{draft}/sessions` → 404，且该考试不出现在 `GET /me/exams`（v1.0.3 / CHG-036） | `test_idor.py::test_draft_exam_session_returns_404_for_student`、`test_my_exams_excludes_draft_exam` |

### 4.3 其他安全基线

| 主题 | 用例 |
|---|---|
| 未登录访问全部受保护接口 → 401（排除 health/login） | `test_permission_matrix.py::test_unauthenticated_returns_401_auth_required`（参数化 30 个接口） |
| 有效会话 + 缺/错 CSRF → 403 `CSRF_INVALID`；登录豁免 | `test_csrf_session.py` 全文件 |
| 认证先于 CSRF：无会话写请求 → 401（CHG-029） | `test_csrf_session.py::test_unauthenticated_write_returns_401_before_csrf` |
| **认证优先于参数校验：无会话 + 非法 body/参数 → 401（非 422，CHG-033）** | `test_csrf_session.py::test_unauthenticated_write_returns_401_before_csrf` |
| 过期/吊销/伪造/停用账号 → 401 | `test_csrf_session.py::test_tampered_or_expired_session_cookie_is_401`、`test_disabled_account_existing_session_is_revoked` |
| 登录失败 code/message 完全一致（AC-02） | `test_secrets.py::test_wrong_password_and_unknown_user_are_indistinguishable` |
| 响应/审计不含 password（AC-22/C-21） | `test_secrets.py::test_*_does_not_expose_password_fields`、`test_failed_login_password_is_not_written_to_audit` |
| 弱密码创建 → 422 | `test_secrets.py::test_weak_password_is_rejected_by_user_create` |
| SQL 注入 / XSS 不 500、不执行 | `test_injection_xss.py` 全文件 |
| 审计含 actor/action/target/ip/时间（AC-22） | `test_permission_matrix.py::test_audit_records_write_operations`；`test_contract_v103.py::test_audit_user_record_has_integer_actor_and_real_ip` |
| **系统记录 `actor_id=null`、`ip=="-"`；用户记录 `actor_id` 整数、`ip` 非空（CHG-035）** | `test_contract_v103.py::test_audit_system_record_has_null_actor_and_dash_ip`（无系统记录时 skip）、`test_audit_user_record_has_integer_actor_and_real_ip` |
| **`GET /healthz` 200 且 `db=="ok"`（CHG-034）** | `test_permission_matrix.py::test_healthz_is_public`；`test_contract_v103.py::test_healthz_returns_db_ok` |
| 越权尝试是否落审计 | `test_permission_matrix.py::test_denied_attempt_is_audited_known_gap`（**xfail**，见 §6/§7） |

## 5. 断言口径

1. **统一错误体**：`assert_error()` 硬断言 `set(body) == {"code","message","request_id"}`，
   `request_id` 必须是 UUID；403/404 不得出现 `items/score/selected/correct_answer` 等业务字段。
2. **403/404 判定顺序**：严格按 docs/12 §2：
   未登录 401 → CSRF 403 → 角色 403 → 归属 403 → 不可见/不存在 404。
   因此“学生调 `/users`”即使资源不存在也断言 403，而不是 404。
3. **IDOR = 403**：学生 B 读学生 A 的 session/result 断言 403（AC-18）；
   教师在 `GET /sessions/{id}/result` 上**一律 403**（CHG-030），不得改成 404。
4. **认证先于 CSRF、且先于参数校验**（v1.0.3 / CHG-033）：无有效会话的写请求一律断言 401
   `AUTH_REQUIRED`——即使缺 `X-CSRF-Token`，或 body/查询/路径参数非法（也**不得**先报 422）。
5. **XSS**：接口必须是 `application/json`，非 `text/html`；JSON 解码值等于原文或 HTML 转义形式；
   不尝试在浏览器里执行（浏览器级 XSS 属 E2E 范围）。
6. **脱敏**：失败信息只包含 method/url/status + 截断 body；密码、CSRF token、
   `session_id` 值在 `redact()` 中替换为 `***`；不打印 Cookie 请求头。
7. **审计可空口径**（v1.0.3 / CHG-035）：系统记录断言 `actor_id is None`（**键必须存在**）且
   `ip == "-"`；用户记录断言 `actor_id` 为整数（排除 `bool`）、`ip` 为非空字符串且不是 `"-"`。
   断言“键存在”与“值可为 null”是两件事，不可只测其一。

## 6. 已知限制 / open questions

1. **8h TTL 真实到期无法黑盒等待**：没有 API 能伪造 `expires_at`，本套件用
   “伪造/失效 Cookie → 401”覆盖同一客户端口径；真实 TTL 需要 DB/时钟注入或 CI fake time，
   当前未做。`test_tampered_or_expired_session_cookie_is_401` 的 docstring 已注明。
2. **越权尝试未落审计**：`docs/12 §4` 把“被拒绝的越权尝试”标为可选；当前实现只在正常写操作
   写审计，未记录 403 尝试。对应用例 `test_denied_attempt_is_audited_known_gap` 标 **xfail**，
   并列入 `open_questions`。若后续实现记录，该用例会 XPASS（不阻断）。
3. **argon2id 哈希不可从 API 观察**：黑盒只能断言“响应/审计无 password/password_hash”与
   “弱密码 422”；数据库内哈希算法由 `server/tests` 和代码评审保证（AC-02/NFR 安全）。
4. **弱密码只覆盖契约的 `minLength=8`**：契约 `UserCreate.password` 只有长度约束，没有复杂度
   （大小写/符号）要求；因此“弱密码”取 `<8` 字符（含空串）断言 422。若产品要求复杂度，
   需先改契约（CHG）。
5. **服务端日志文件不可读**：套件只能断言 HTTP 响应与 `GET /audit-logs` 的内容；
   “日志不含密码”还需 CI 在服务端日志落盘后做 grep（见 `reports/security/README.md`）。
6. **浏览器 DOM XSS / CSP**：本套件只验证 API 输出是 JSON 数据；浏览器是否执行属 E2E/前端安全测试。
7. **无速率限制/锁定测试**：契约未定义 429/lockout，AC-02 只要求失败不区分账号；本套件不测。
8. **本地 HTTP 不断言 `Secure`**：`session_id` 在 local/test 下 `Secure=false`；套件断言
   `HttpOnly`/`SameSite=Lax`/`Path=/`/`Max-Age=28800`，`Secure` 由 staging/prod 配置保证。
9. **测试数据保留**：无删除用户 API；教师 B/停用学生用 `qa-sec-*` 唯一前缀，跨 run 不冲突，
   但长期共用 DB 会累积账号，建议 CI 每次重建库。
10. **admin / 教师 B 依赖**：未执行 `create-admin` 时，涉及 admin/教师 B 的用例 skip；
    这不影响纯学生/教师负例，但会降低覆盖率。
11. **（已关闭 · v1.0.3 / CHG-036）教师读他人 draft 资源的 403/404 口径**：
    **原张力**：docs/12 v1.2 §2 第 5 步把“他人 draft 资源”归为 404，但 §2.1 #2 又明确
    “教师 A 修改教师 B 的题目/试卷/考试 → 403”；实现统一 403，测试为免误报只对已发布/可见资源断言。
    **定稿（v1.0.3 / CHG-036）**：契约把可见域写进 `Forbidden`/`NotFound` description 与
    docs/12 v1.3 §2/§2.0——**教师（同角色）他人的题目/试卷/考试无论 draft/frozen/published
    一律 403**（教师管理视图可见，**不得 404**）；**学生视角**的 draft/未发布 → 404。
    本套件据此把“教师 B 对教师 A 的 draft 资源 → 403”改为**硬断言**（不再回避）：
    `test_idor.py::test_teacher_b_cannot_read_teacher_a_draft_paper` / `...update...draft_paper` /
    `...delete...draft_paper` / `...read...draft_exam` / `...patch...draft_exam`；
    学生侧 draft 仍断言 404（`test_draft_exam_session_returns_404_for_student`），
    并补 `GET /me/exams` 不含 draft（`test_my_exams_excludes_draft_exam`）。
    契约侧文案由 `tests/contract/test_error_codes.py::test_chg_036_visibility_documented` 锁定。
12. **`/healthz` 的 503 分支无法在正常栈上黑盒触发**（v1.0.3 / CHG-034）：需要真实依赖故障
    （DB 停服/迁移未就绪）。用例 `test_contract_v103.py::test_healthz_503_is_unified_error_without_db`
    默认 **skip**，设 `EXAM_HEALTHZ_503=1` 后执行；契约侧静态断言（503 是统一 `Error`、无 `db`）
    在 `tests/contract/test_openapi_valid.py::test_chg_034_healthz_db_required`。
13. **系统审计记录依赖环境产生**：`actor_id=null`/`ip="-"` 的系统记录来自 CLI `create-admin`、
    备份清理等无请求上下文的动作，**无法经 API 构造**。`test_audit_system_record_has_null_actor_and_dash_ip`
    在近 180 天记录（最多翻 5 页）中找不到系统记录时 **skip** 并提示先执行 `create-admin`；
    用户记录断言（`test_audit_user_record_has_integer_actor_and_real_ip`）不受影响。

## 7. 预期结果与证据

- 在 §2 完整 seed（seed-demo + create-admin）且 API 健康时，预期：
  **全部用例 pass，除 `test_denied_attempt_is_audited_known_gap` 为 xfail（预期缺口，见 §6.2）**；
  另有两处**条件 skip**：`test_healthz_503_is_unified_error_without_db`（需 `EXAM_HEALTHZ_503=1`，
  见 §6.12）与 `test_audit_system_record_has_null_actor_and_dash_ip`（库里无系统记录时，见 §6.13）。
- **v1.0.3 同步后本目录不再有针对 healthz / `actor_id` / draft 口径的 xfail**：这 3 处已由
  CHG-034/035/036 落地并转为硬断言（`docs/13` §8.1.2）；剩余 xfail 仍只有 §6.2 的越权审计缺口
  （与 v1.0.3 无关，属 docs/12 §4 的“可选”项）。
- 本机未执行（shell 不可用）；`tests.verified=false`，CI 执行后把
  `reports/security/security.xml` 与失败摘要回填 `docs/61-gate-report.md` 的 G4 安全一节。
  > 本次任务写作用域为 `tests/security/**`、`reports/security/**`，`docs/61-gate-report.md`
  > 由 QA Lead/门控任务更新（本任务不改 `docs/`）。
- 失败输出约定：`case_id + 期望状态/code + 实际状态 + 脱敏 body`；不输出密码/Cookie。
