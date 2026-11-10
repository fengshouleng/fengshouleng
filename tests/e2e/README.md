# tests/e2e/ — 关键链路 E2E（Playwright Python / pytest）

- **负责人**：R5 QA（preset `qa-verify`），**只报不修**
- **任务卡**：`contracts/tasks/TASK-QA-001.yaml`（E2E 关键链路）
- **被测契约**：`contracts/openapi.yaml` **v1.0.3**（V1 冻结版）；权限基线 `docs/12-permission-matrix.md` **v1.3**
- **v1.0.3 同步说明**（CHG-033..CHG-036，见 `docs/13-contract-changes.md` §8.1）：
  E2E 只用到契约的**请求/页面**面，v1.0.3 未改请求模型与页面选择器，故本目录**无需改断言**；
  v1.0.3 的三处增量（`healthz.db`、`AuditLog.actor_id=null`、教师同角色 draft → 403）都不在
  E2E 的页面链路上，分别由 `tests/contract` 与 `tests/security` 断言（E2E 不重复）；
  E2E 依赖的 `replayed`/`applied`/`server_time` 口径自 v1.0.2 起未变。本目录不新增 xfail。
- **技术选型**：Python Playwright + pytest（复用 `tests/requirements.txt` 的 `playwright>=1.45`；
  **不引入** `@playwright/test`，不改 `web/package.json`）

## 1. 用例与链路

| 用例 | 链路 | 覆盖 AC | 默认 |
|---|---|---|---|
| `T-E2E-01` | 教师登录 → 建 3 题（单选/多选/判断）→ 组卷 40+30+30=100 → 发布考试（时间窗覆盖当前） | AC-01/05/09/10 | 执行 |
| `T-E2E-02` | 学生登录 → 我的考试 → 开始 → 作答 → **刷新回填** → 交卷 → 成绩 100 | AC-01/12/13/16/17 | 执行 |
| `T-E2E-03` | 重复开始会话（同一 `session_id`）、重复交卷（同分、`replayed=true`、不新增会话） | AC-13/15 | 执行 |
| `T-E2E-04a` | 倒计时归零自动交卷（Playwright Clock 快进，不真实等待） | AC-13/14 | 执行 |
| `T-E2E-04b` | 服务端结算后保存 409 `EXAM_EXPIRED` → 前端跳成绩页（保留已保存得分） | AC-13/14 | 执行 |
| `T-E2E-04c` | 真实等待 deadline 到期（约 10 分钟） | AC-14 | **xfail**（`EXAM_E2E_WAIT_EXPIRY=1` 才执行） |

> 用例 ID 与 `docs/60-test-plan.md` §5 的 `TC-E2E-01..05` 对齐关系：
> `T-E2E-01 → TC-E2E-01/02`、`T-E2E-02 → TC-E2E-03/05`、`T-E2E-03 → TC-E2E-05`、
> `T-E2E-04 → TC-E2E-04/05`。计划文档仍按旧 ID 维护，改计划需另开 QA 文档任务。

默认整模块 **skip**；仅 `EXAM_E2E=1` 时执行。旧版 API 级骨架
`tests/e2e/test_exam_flow.py` 仅在 `EXAM_E2E_API=1` 时执行（保留 `TC-E2E-*` 用例实体）。

> **2026-11-11 修复（DEF-009）**：API 骨架原先用 `build_exam_scenario()` 生成的**随机账号**登录，
> 而那些账号从未被创建过 → 永远 401，5 条用例只能长期 skip。现改为使用 seed 里**真实存在**的账号
> （`EXAM_TEACHER_USERNAME` / `EXAM_STUDENT_USERNAME` / 对应 `*_PASSWORD`），本机实测 **5 passed**。
> 证据：`reports/e2e/20261111-015008/`。

## 2. 前置依赖

```bash
pip install -r tests/requirements.txt
python -m playwright install chromium          # Linux CI: --with-deps
```

## 3. 起栈与 seed

### 3.1 本地（uvicorn + vite）

```bash
# 终端 1：后端（默认 SQLite: server/app.db）
cd server
pip install -e ".[dev]"
alembic upgrade head
APP_ENV=local python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 终端 2：前端（5173，/api 反代到 localhost:8000）
cd web
pnpm install
pnpm dev

# 终端 3：生成演示账号与演示题库/试卷（仅空库、幂等）
cd server
APP_ENV=local EXAM_SEED_PASSWORD='<本地强口令>' \
  python -m app.cli seed-demo --password-env EXAM_SEED_PASSWORD
```

PowerShell（Windows）：

```powershell
cd server
alembic upgrade head
$env:APP_ENV="local"; python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

cd ..\web
pnpm install; pnpm dev

cd ..\server
$env:APP_ENV="local"; $env:EXAM_SEED_PASSWORD="<本地强口令>"
python -m app.cli seed-demo --password-env EXAM_SEED_PASSWORD
```

**演示账号（seed-demo）**：`demo-teacher`、`demo-student-1`、`demo-student-2`。
未传 `--password-env` 且 `APP_ENV` 非 staging/prod 时，CLI 使用开发默认密码
`dev-demo-password-123`（**仅本地开发**，staging/prod 会拒绝该默认值）。
E2E 默认也读取该开发密码；**禁止**在 staging/prod 使用。

> 若 seed 时用了自定义 `EXAM_SEED_PASSWORD`，运行测试前必须显式导出同名口令：
> `EXAM_TEACHER_PASSWORD='<seed 口令>' EXAM_STUDENT_PASSWORD='<seed 口令>'`，
> 否则登录会在用例开始阶段失败（报错会提示先 seed）。

### 3.2 Compose（staging 同构）

```bash
cp deploy/.env.example deploy/.env.e2e        # 填写 POSTGRES_*、NGINX_HTTP_PORT 等
# 纯 HTTP 的 E2E 栈必须关闭 Secure Cookie（staging 默认 true，http 下浏览器不会回传）：
#   在 deploy/.env.e2e 中加 SESSION_COOKIE_SECURE=false
docker compose -f deploy/docker-compose.yml --env-file deploy/.env.e2e \
  -p exam-e2e up -d --build

# 容器内 APP_ENV 默认 staging → 必须显式提供密码（默认开发密码会被拒绝）
docker compose -f deploy/docker-compose.yml --env-file deploy/.env.e2e -p exam-e2e \
  exec -e EXAM_SEED_PASSWORD='<强口令>' api \
  python -m app.cli seed-demo --password-env EXAM_SEED_PASSWORD

# 前端由 nginx 托管（默认 http://127.0.0.1:8080，见 NGINX_HTTP_PORT）
EXAM_E2E=1 \
EXAM_BASE_URL=http://127.0.0.1:8080 \
EXAM_API_URL=http://127.0.0.1:8080/api \
python -m pytest tests/e2e -q
```

服务端日志（失败时用例会打印位置）：

```bash
# 本地：启动 uvicorn 时重定向
python -m uvicorn app.main:app --port 8000 > reports/e2e/server.log 2>&1
# Compose
docker compose ... logs -f api > reports/e2e/server.log 2>&1
export EXAM_SERVER_LOG=reports/e2e/server.log
```

## 4. 运行

```bash
# 全部（默认 EXAM_BASE_URL=http://127.0.0.1:5173，EXAM_API_URL=http://127.0.0.1:8000/api）
EXAM_E2E=1 python -m pytest tests/e2e -q

# 只跑某条链路
EXAM_E2E=1 python -m pytest tests/e2e -q -k t_e2e_02
EXAM_E2E=1 python -m pytest tests/e2e/test_exam_ui_flow.py -q -k "t_e2e_04"

# 可视化调试
EXAM_E2E=1 EXAM_E2E_HEADED=1 EXAM_E2E_SLOWMO_MS=200 python -m pytest tests/e2e -q -k t_e2e_01

# 真实等待到期（约 10 分钟，默认 xfail）
EXAM_E2E=1 EXAM_E2E_WAIT_EXPIRY=1 python -m pytest tests/e2e -q -k t_e2e_04c

# 旧版 API 级骨架（非 Playwright）—— 用 seed 里真实存在的账号
EXAM_E2E=1 EXAM_E2E_API=1 \
EXAM_BASE_URL=http://127.0.0.1:8000/api \
EXAM_TEACHER_USERNAME=demo-teacher EXAM_TEACHER_PASSWORD=dev-口令 \
EXAM_STUDENT_USERNAME=demo-student-1 EXAM_STUDENT_PASSWORD=dev-口令 \
  python -m pytest tests/e2e/test_exam_flow.py -q
# 期望：5 passed（2026-11-11 本机实测 5 passed in 0.45s）
```

### 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `EXAM_E2E` | 未设置 | 置 `1` 才执行；否则整模块 skip |
| `EXAM_BASE_URL` | `http://127.0.0.1:5173` | 前端入口（dev 5173 / nginx 8080） |
| `EXAM_API_URL` | `http://127.0.0.1:8000/api` | 直连 API（构造数据、旁证断言） |
| `EXAM_TEACHER_USERNAME` | `demo-teacher` | 教师账号（seed-demo） |
| `EXAM_TEACHER_PASSWORD` | 开发默认密码 | 教师口令；staging/prod 必须显式设置 |
| `EXAM_STUDENT_USERNAME` | `demo-student-1` | 学生账号（seed-demo） |
| `EXAM_STUDENT_PASSWORD` | 开发默认密码 | 学生口令；staging/prod 必须显式设置 |
| `EXAM_E2E_RUN_ID` | `%Y%m%d-%H%M%S` | 运行 ID（数据前缀 + 证据目录） |
| `EXAM_E2E_HEADED` | 未设置 | `1` = 有头浏览器 |
| `EXAM_E2E_SLOWMO_MS` | `0` | 每步慢放毫秒 |
| `EXAM_E2E_TIMEOUT_MS` | `20000` | 页面操作默认超时 |
| `EXAM_E2E_BROWSER` | `chromium` | `chromium` / `firefox` / `webkit` |
| `EXAM_E2E_BROWSER_ARGS` | 空 | 逗号分隔的浏览器启动参数（CI 容器可加 `--no-sandbox`） |
| `EXAM_SERVER_LOG` | `reports/e2e/{run_id}/server.log` | 失败时打印的服务端日志位置 |
| `EXAM_E2E_WAIT_EXPIRY` | 未设置 | `1` 时执行 `T-E2E-04c` 真实等待到期 |
| `EXAM_E2E_API` | 未设置 | `1` 时执行旧版 API 骨架 |

## 5. 证据

```
reports/e2e/{run_id}/
  {test_name}/
    trace.zip          # playwright show-trace <path>
    video/*.webm       # context 关闭时落盘
    01-*.png ...       # 关键步骤截图
    failure.png        # 仅失败
    failure.html       # 仅失败：DOM 快照
    failure.txt        # 仅失败：url / server_log 位置
```

- 失败时终端额外打印证据目录、服务端日志位置与 `playwright show-trace` 命令；
- 用例本身 **不清理** 数据（保留证据由 CI job 结束统一 drop/重建）；
- 所有数据带 `[e2e-<run_id>-<case>]` 前缀，可跨 run 保留而不互相污染。

## 6. 定位约定（只依赖稳定标识）

- 页面定位全部使用 `data-testid`：`login-*`、`questions-create`、`questions-submit`、
  `questions-create-answer-text`、`papers-save`、`papers-total-points`、`exam-start`、
  `answer-{question_id}-{key}`、`exam-save-status`、`exam-submit`、`exam-confirm-submit`、
  `result-score`、`result-item-{question_id}` 等；
- antd 内部仅两处使用稳定 wrapper class：`label.ant-radio-button-wrapper`（题型切换）与
  `label.ant-radio-wrapper` / `label.ant-checkbox-wrapper`（答案勾选），其余不依赖 class；
- 自动保存不等待 30s debounce：用“切题触发 `forceSave`”后断言 `exam-save-status` 出现
  “答案已保存”。

## 7. 已知缺口 / open questions（QA 静态核对）

1. **教师端无“发布考试”UI**：`web/src/routes.tsx` 的 `/exams` 仍是占位页（`page-exams`，
   注释「待 TASK-FE-003 接入」），不存在 `exams-publish` 等 testid。`T-E2E-01` 的发布一步
   通过契约 `POST /exams` 完成；建题、组卷仍走真实 UI。FE 接入后应改为 UI 发布并补 testid。
2. **无法经公开 API 注入短 deadline**：契约 `duration_min ≥ 10` 且 `duration_min ≤ 时间窗`，
   最短 deadline ≈ 10 分钟。`T-E2E-04a` 用 Playwright Clock 快进触发前端自动交卷；
   `T-E2E-04b` 用 `PATCH /exams/{id}` `closed` 触发服务端同一 `EXAM_EXPIRED` 结算路径；
   真实到期由 `T-E2E-04c` 在 `EXAM_E2E_WAIT_EXPIRY=1` 时验证，默认 xfail。
3. **`replayed` 的 UI toast 不可稳定触发**：`ExamPage` 的 `message.info('已交卷，显示原成绩')`
   依赖“交卷接口返回 `replayed=true`”，但会话已 `submitted` 后交卷按钮被禁用，正常 UI 无法
   二次点击。`T-E2E-03` 用同源 `fetch`（复用 Cookie + CSRF）断言契约 `replayed=true` 与同分，
   并断言“再次进入考试 → 跳同一成绩页”以证明会话复用。
4. `page_api()` 假定前端与 API 同源且 API 前缀为 `/api`（Vite dev proxy / nginx 默认）；
   使用自定义 `VITE_API_BASE` 或独立 API 域名时需同步调整。
5. 题库/试卷的删除、断网模拟（`context.set_offline`）不在本卡范围，见
   `docs/60-test-plan.md` 的 `TC-E2E-04`（旧骨架）与 TASK-QA-002/003。
