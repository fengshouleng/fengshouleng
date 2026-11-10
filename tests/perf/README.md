# tests/perf/ — 性能冒烟（TASK-QA-003）

- **负责人**：R5 QA（preset `qa-verify`，**只报不修**）
- **任务卡**：`contracts/tasks/TASK-QA-003.yaml`（WBS T3.4）
- **被测契约**：`contracts/openapi.yaml` **v1.0.2**（`/exams/{exam_id}/sessions`、
  `/sessions/{session_id}`、`/sessions/{session_id}/answers`、`/sessions/{session_id}/submit`、
  `/auth/login`、`/me/exams`）
- **门线（NFR）**：100 并发下保存 / 读取接口 **P95 < 300ms**，错误率 **< 0.1%**（PRD §5、`docs/60-test-plan.md` TC-PERF-01）
- **不在本卡范围**：TC-PERF-02「内网首屏 < 2s」（人工 + Lighthouse，见测试计划 §3.3）；
  判分正确性（由 `server/tests` 与 E2E 覆盖）

---

## 1. 技术选型与文件结构

| 选择 | 理由 |
|---|---|
| **k6**（JS 场景脚本） | 单二进制、原生 scenario/threshold/summary；阈值不达标时进程退出码 **99**，CI 直接判红；**不需要改 `server/`、`web/` 或前端依赖** |
| **Python + httpx**（仅造数） | `httpx` 已在 `tests/requirements.txt`；通过公开 API 造数，不导入生产代码、不直连库，SQLite / PostgreSQL 同一套逻辑 |
| 登录放在 `exam_flow.js` 的 `setup()` | k6 的 setup 本身只执行一次，登录集中在这里即可；**setup 的 Cookie jar 不会传给各 VU**，所以必须把 `session_id` Cookie 值随 setup 返回值下发、VU 用 `Cookie` 头回放——拆成 `login_setup.js` 也要做同样的事，反而多一层模块与路径耦合。实现见脚本 `authHeaders()` |

```
tests/perf/
  README.md                 # 本文件
  prepare_data.py           # 幂等造数：1 教师 + 100 学生 + 100 题/100 分卷 + 1 场进行中考试
  k6/exam_flow.js           # k6 主脚本：setup 登录 + answer_save / session_read 场景 + 阈值 + summary
  data/students.json        # 运行期产物（prepare_data.py 生成；只含用户名，不含口令）
```

`reports/perf/README.md` 规定证据目录与报告模板；本卡产物写到 `reports/perf/<run_id>/`。

---

## 2. 独立环境要求（**先读，硬性**）

> 任务卡 forbidden：**禁止在共享 staging 上直接压满**（需独立环境/时段）。

1. **必须使用独立性能库 / 独立 Compose project**，不要复用 `exam-staging`、更不要对 production 压测。
   推荐 `COMPOSE_PROJECT_NAME=exam-perf` + 独立 `POSTGRES_DB`，压测后整套 `down -v` 回收。
2. 本脚本默认 **100 名考生 + 100 个刷新 VU**（总 200 VU、稳态约 200 req/s 读取 + 约 3.3 req/s 保存），
   会明显占用 CPU/IO；不要把压测机和被压 API 放在共享办公机上取正式证据。
3. 任务卡要求 **SQLite 与 PostgreSQL 两种环境分别跑，至少 PostgreSQL**。正式 G4 证据以
   PostgreSQL（staging 同构栈）为准；SQLite 结果只作本地预检。
4. 压测窗口内该环境只服务本次压测；不要与 E2E/功能测试并发跑同一套栈。
5. 口令只从环境变量读取；不要把 `deploy/.env.*`、`EXAM_QA_PASSWORD`、admin 口令写进命令历史/仓库。

---

## 3. 环境准备

### 3.1 工具

```bash
# k6（外部二进制，不在 tests/requirements.txt；版本需支持 scenarios / k6/execution / constant-vus）
k6 version        # 建议 v0.49+；CI 固定版本并记录到报告

# Python 依赖（httpx）
python --version  # 3.12（与 CI 一致）
pip install -r tests/requirements.txt
```

### 3.2 启动被测栈

**A. 本地 SQLite（只做预检）**

```bash
cd server
pip install -e ".[dev]"
alembic upgrade head
APP_ENV=local python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**B. Compose + PostgreSQL（正式证据，独立 project）**

```bash
cp deploy/.env.example deploy/.env.perf
# 至少改：COMPOSE_PROJECT_NAME=exam-perf、NGINX_HTTP_PORT=18080、
#        POSTGRES_PASSWORD=<随机强口令>、SESSION_COOKIE_SECURE=false（纯 HTTP 压测）
docker compose -f deploy/docker-compose.yml --env-file deploy/.env.perf -p exam-perf up -d --build
docker compose -f deploy/docker-compose.yml --env-file deploy/.env.perf -p exam-perf exec -T api \
  python -m app.cli check
```

### 3.3 首个 admin（空库必需）

见 `docs/70-runbook.md` §3.5；密码只走 stdin / 环境变量：

```bash
# Compose（-T 必须）
docker compose -f deploy/docker-compose.yml --env-file deploy/.env.perf -p exam-perf \
  exec -T -e BOOTSTRAP_PASSWORD="$EXAM_ADMIN_PASSWORD" api \
  python -m app.cli create-admin --username admin --display-name 性能管理员 \
  --password-env BOOTSTRAP_PASSWORD

# 本地（工作目录 server/）
python -m app.cli create-admin --username admin --display-name 管理员
```

> `prepare_data.py` 用 admin 账号通过 `POST /users` 建 100 个学生；**不会**调用 `seed-demo`
> （`seed-demo` 只有 2 个学生、且仅空库，无法满足 100 并发）。

---

## 4. 数据准备（幂等）

### 4.1 环境变量

| 变量 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `EXAM_API_URL` | 否 | `http://127.0.0.1:8000/api` | API 根（含 `/api`）；Compose 默认 `http://127.0.0.1:18080/api` |
| `EXAM_RUN_ID` | 否 | `perf-local` | 数据命名空间，1–40 位 `[A-Za-z0-9._-]`；**每次正式压测建议用新值**（如 `perf-20261109-012000`） |
| `EXAM_ADMIN_USERNAME` | 否 | `admin` | admin 用户名 |
| `EXAM_ADMIN_PASSWORD` | **是** | — | admin 口令（仅环境变量） |
| `EXAM_QA_PASSWORD` | **是** | — | 100 个测试账号统一口令，8–128 位（仅环境变量） |

### 4.2 执行

```bash
export EXAM_API_URL=http://127.0.0.1:18080/api
export EXAM_RUN_ID=perf-$(date -u +%Y%m%d-%H%M%S)
export EXAM_QA_PASSWORD='<独立性能库测试口令，>=8 位>'
export EXAM_ADMIN_PASSWORD='<admin 口令>'

python tests/perf/prepare_data.py
```

PowerShell：

```powershell
$env:EXAM_API_URL="http://127.0.0.1:18080/api"
$env:EXAM_RUN_ID="perf-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
$env:EXAM_QA_PASSWORD="<...>"
$env:EXAM_ADMIN_PASSWORD="<...>"
python tests/perf/prepare_data.py
```

产物：

- `tests/perf/data/students.json`：k6 init 读取的学生清单 + `exam.exam_id` + `api_url` + `run_id`；
  **不含口令**（k6 从 `EXAM_QA_PASSWORD` 读取）；
- `reports/perf/<run_id>/prepare.json`：同一份数据的证据副本（同时创建 summary 输出目录）；
- stdout 打印上述 JSON（进度日志走 stderr），便于人工核对。

该文件关键结构：

```json
{
  "schema_version": 1,
  "task": "TASK-QA-003",
  "run_id": "perf-20261109-012000",
  "api_url": "http://127.0.0.1:18080/api",
  "exam": {"exam_id": 42, "paper_id": 17, "start_at": "...Z", "end_at": "...Z", "duration_min": 120, "status": "published"},
  "teacher": {"user_id": 3, "username": "perf-perf-20261109-012000-teacher"},
  "students": [{"user_id": 4, "username": "perf-...-stu-001", "display_name": "性能学生 001"}, ...]
}
```

### 4.3 幂等与“考试轮换”

| 对象 | 行为 |
|---|---|
| 教师 / 100 学生 | 用户名 `perf-<run_id>-teacher` / `perf-<run_id>-stu-001..100`，存在即复用 |
| 100 道题 | 题干 `[<run_id>] 性能题 001：...`，同 run_id 复用，缺哪道补哪道 |
| 试卷 | 复用“草稿 + 100 题号一致 + 总分 100”的同名卷；否则新建（`... v2` 起） |
| 考试 | 复用条件：`published` + 时间窗有效 + **0 个会话**；否则自动新建“新试卷 + 新考试” |

**为什么第二轮会新建考试**：考试一旦发布，试卷即冻结（`paper.frozen_at`），且服务端没有
“重置考试/删除会话”接口；上一轮 20% 的学生已交卷后，再用同一考试压测会得到 200 + 终态
（脚本会 `exec.test.abort` 并提示）。因此脚本自动轮换 **试卷 + 考试**，账号与题目继续复用。
同一 `EXAM_RUN_ID` 重跑不会重复建账号/题目，也不会污染上一轮成绩。

其他说明：

- 换了 `EXAM_QA_PASSWORD` 但沿用旧 `run_id` 时，旧账号口令不会自动改；加 `--reset-passwords`
  重跑（会对已存在账号 PATCH 重置口令并吊销其旧会话）。
- 准备完成后脚本会登录首/末两名学生核对 `GET /me/exams` 能看到该考试且
  `session_status=not_started`，再登录教师核对考试详情；失败即退出码 1。
- 无删除考试 API：专用性能库会在多轮后积累历史考试/冻结试卷；用新 `run_id` 或重建库清理。

---

## 5. 执行压测

### 5.1 命令

```bash
# 仓库根目录执行（handleSummary 以 cwd 解析 reports/perf/<run_id>/summary.json）
set -o pipefail
mkdir -p "reports/perf/${EXAM_RUN_ID}"
k6 run tests/perf/k6/exam_flow.js 2>&1 | tee "reports/perf/${EXAM_RUN_ID}/k6.log"
rc=${PIPESTATUS[0]}; echo "k6 exit=$rc"
```

PowerShell：

```powershell
New-Item -ItemType Directory -Force "reports/perf/$env:EXAM_RUN_ID" | Out-Null
k6 run tests/perf/k6/exam_flow.js 2>&1 | Tee-Object -FilePath "reports/perf/$env:EXAM_RUN_ID/k6.log"
"k6 exit=$LASTEXITCODE"
```

> `prepare_data.py` 已创建 `reports/perf/<run_id>/`；若换了 `EXAM_RUN_ID` 或 `PERF_REPORT_DIR`
> 而没重新造数，先手动建目录（k6 `handleSummary` 不会创建目录）。

### 5.2 k6 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `EXAM_API_URL` | 数据文件里的 `api_url` | 优先级高于数据文件；必须指向同一环境（否则 exam_id 无效） |
| `EXAM_RUN_ID` | 数据文件里的 `run_id` | 若显式设置必须与数据文件一致（防串环境） |
| `EXAM_QA_PASSWORD` | **必填** | 与数据准备时一致 |
| `EXAM_STUDENT_FILE` | `tests/perf/data/students.json` | 学生清单路径；也支持 `EXAM_STUDENT_JSON`（内联 JSON） |
| `PERF_VUS` | `100` | `answer_save` 场景 VU 数 |
| `PERF_DURATION` | `3m` | 两个场景时长（k6 duration 格式；≥10s） |
| `PERF_READ_VUS` | `100` | `session_read` 场景 VU 数 |
| `PERF_READ_INTERVAL` | `0.5s` | 每个读 VU 的 think time；设 `0s` 为闭环最大吞吐 |
| `PERF_SAVE_INTERVAL` | `30s` | 保存间隔（任务要求每 30s 真实 `sleep`） |
| `PERF_SAVE_ANSWERS` | `10` | 每次保存携带的随机题数（从 100 题中抽） |
| `PERF_SUBMIT_RATIO` | `0.2` | 末段按比例交卷；`1` = 全部交卷（评分事务会计入 answer_save P95） |
| `PERF_REPORT_DIR` | `reports/perf/<run_id>` | summary.json 输出目录 |

### 5.3 场景模型

| 场景 | VU | 流程 | 说明 |
|---|---|---|---|
| `answer_save` | `PERF_VUS`（100） | 首次 `POST /exams/{id}/sessions`（201/200）→ 循环：随机作答并 `PUT /sessions/{id}/answers`（`client_seq` 单调）→ `sleep(30s)`；到 80% 时长后按 `PERF_SUBMIT_RATIO` 交卷 | 100 名真实考生；`gracefulStop=45s` |
| `session_read` | `PERF_READ_VUS`（100） | 首次 `POST /exams/{id}/sessions` 复用会话 → 循环 `GET /sessions/{id}` → `sleep(0.5s)` | **同一批 100 名学生**的刷新/重连流量，只读不写；`startTime=5s` 错开建会话高峰 |

- 两个场景合计 200 个 k6 VU，但**只有 100 名考生 / 100 个会话**；读取场景是同一批考生的
  刷新流量，不是新增考生。若压测机资源有限，可 `PERF_READ_VUS=20` 降载（此时读取 P95
  只在 20 并发下成立，报告必须标注）。
- 保存频率来自业务行为（每 30s 自动保存），不是把写入打满；读取场景才是持续压力来源。
  需要极限写入时调大 `PERF_VUS` 或单独写脚本（不在本卡范围）。

### 5.4 阈值（不达标 → k6 退出码 **99**）

| 阈值表达式 | 含义 |
|---|---|
| `http_req_duration{scenario:answer_save}: p(95)<300` | 保存场景（会话创建 + 保存 + 少量交卷）P95 < 300ms |
| `http_req_duration{scenario:session_read}: p(95)<300` | 读取场景（会话创建 + 100 并发 GET）P95 < 300ms |
| `http_req_failed: rate<0.001` | 全局错误率 < 0.1%（4xx/5xx 与网络错误都计入） |
| `save_p95: p(95)<300` | 自定义 Trend，与保存场景同口径（报告直接引用） |
| `read_p95: p(95)<300` | 自定义 Trend，与读取场景同口径（报告直接引用） |

自定义指标：`save_p95`、`read_p95`、`submit_duration`（交卷评分耗时）、
`session_start_duration`、`login_duration`（setup 登录耗时，辅助定位）。

> 说明：`save_p95` / `read_p95` 是任务卡指定的 **Trend 名称**，每个样本是单次请求耗时，
> 报告取该 Trend 的 `p(95)`；它们不是预先算好的分位值。`summary.json` 里同时保留
> `http_req_duration{scenario:...}` 与这两个自定义 Trend，便于交叉核对。

> 交卷默认只覆盖 20%：交卷是“判分 + 错题本 + 审计”的写事务，100 VU 同时交卷会形成评分
> 尖峰并把评分耗时混进保存 P95。20 个交卷样本约占总样本 3%–4%（< 5%），不改变 P95 门线；
> 要压测交卷链路请显式 `PERF_SUBMIT_RATIO=1`，并在报告注明此时 P95 含评分事务。

---

## 6. 结果解读与证据

### 6.1 输出

- **终端**：`handleSummary` 打印每个阈值的 PASS/FAIL，以及
  `http_req_duration{scenario:answer_save|session_read}`、`save_p95`、`read_p95`、
  `submit_duration`、`http_req_failed`、`http_reqs` 的 avg/min/med/p(90)/p(95)/p(99)/max；
- **文件**：`reports/perf/<run_id>/summary.json`（k6 原始 summary，含全部 metrics/thresholds，
  可直接入 CI artifact）；`k6.log` 为人工 tee 的完整日志；
- **数据准备证据**：`reports/perf/<run_id>/prepare.json`。

### 6.2 判定口径

| 判据 | 读法 |
|---|---|
| 保存 P95 | `summary.json` → `metrics["http_req_duration{scenario:answer_save}"].values["p(95)"] < 300` |
| 读取 P95 | 同上，`{scenario:session_read}` |
| 错误率 | `metrics.http_req_failed.values.rate < 0.001` |
| 吞吐 | `metrics.http_reqs.values.rate`（req/s）；保存吞吐也可看 `save_p95.values.count / 时长` |
| 分位 | 同一 `values` 里的 `med`(P50)、`p(95)`、`p(99)`；**报告必须写全 P50/P95/P99** |
| 退出码 | `0` = 全部阈值通过；`99` = 至少一个阈值未过（仍会写 summary）；其他非 0 = 脚本/环境错误 |

`check` 失败（例如保存返回非 200）不一定让 `http_req_failed` 变红——报告要同时看
`summary.json` 的 `root_group.checks` 与 `k6.log` 中的 `[perf]` 日志。

> 口径提醒：`answer_save` 场景每个 VU 只做 **一次** `POST /exams/{id}/sessions`，该响应包含
> 100 题快照，是场景里最重的请求之一。若 `answer_save` 的 P95 与 `session_start_duration`
> 的 P95 接近，说明门线主要反映“进入考试”的读取耗时；报告应同时引用 `save_p95`（纯保存）
> 说明保存路径，并明确写出这一口径，避免把进入考试的成本误读为保存变慢。

### 6.3 不达标时的定位建议（QA 只报不修）

1. **先排除环境**：压测机与 API 是否同机抢 CPU、Compose 资源限制、是否误连了共享库；
2. **慢查询/锁等待**：PostgreSQL 用 `pg_stat_statements` / `pg_stat_activity`（`wait_event_type='Lock'`）；
   SQLite 看 `database is locked` 与写事务排队；
3. **会话读取成本**：`GET /sessions/{id}` 每次组装 100 题快照；看是否 N+1、是否重复查询试卷/题目；
4. **写路径**：保存事务里的审计写入、`client_seq` 行锁、SQLite 单写者；交卷事务的评分/错题本；
5. **服务端并发模型**：uvicorn worker 数、FastAPI 同步路由的线程池大小、DB 连接池；
6. **网络/代理**：nginx `proxy_read_timeout`、keep-alive、压测机连接数限制。

建议写入报告与后续 BE/FE 任务卡，**不在本卡改代码**。

---

## 7. 清理

- 专用 Compose 栈：`docker compose ... -p exam-perf down -v`（**仅** `exam-perf`；
  确认 `COMPOSE_PROJECT_NAME`，切勿对共享/生产项目执行）；
- 本地 SQLite：删除 `server/app.db`（若为本次压测专用的库文件）；
- 账号/题目/历史考试会保留（无删除考试 API）；用新 `EXAM_RUN_ID` 隔离，或直接重建性能库；
- 证据保留：`summary.json` + `k6.log` + `prepare.json`，按 `reports/perf/README.md` 归档。

---

## 8. 已知限制

1. **本仓库执行环境 shell 不可用**（`pwsh` / 进程创建失败，exit `0xC0000142`）：本目录脚本
   **未在本地执行**，`tests.verified=false`；首次运行以独立环境/CI 的真实输出为准。
2. k6 是系统二进制，不在 `tests/requirements.txt`；CI 需固定版本（建议 v0.49+）并记录到报告。
3. 单机同时跑 k6 与 API 时，压测机 CPU 会成为瓶颈；正式证据建议 k6 与被压 API 分机。
4. 本脚本只压“开始会话 / 读取会话 / 保存 / 少量交卷”路径，不覆盖登录风暴、导入、报表导出；
   登录只在 setup 发生 100 次。
5. 不验证判分正确性、重复交卷幂等（分别由 API/E2E 用例覆盖）；`PERF_SUBMIT_RATIO` 只控制比例。
6. 前端首屏（TC-PERF-02）不在本卡：人工 + Lighthouse，记录在 G4 人工项。
7. 考试轮换会累积历史考试/试卷；这是服务端“冻结 + 无删除”语义的结果，不是脚本 bug。

---

## 9. 追溯

| 文档 | 关系 |
|---|---|
| `contracts/tasks/TASK-QA-003.yaml` | 本卡 must/acceptance/forbidden 的唯一来源 |
| `docs/00-prd.md` §5 | NFR：100 并发保存/读取 P95 < 300ms |
| `docs/60-test-plan.md` §4.3、§6.1 | TC-PERF-01/02；G4 第 7 步命令 |
| `docs/70-runbook.md` §1–§5 | 环境变量、Compose 起栈、admin 创建、冒烟 |
| `reports/perf/README.md` | 证据目录与报告模板 |
