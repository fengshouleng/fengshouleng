# 75 · 性能基线复测手册（PERF-001）

> 目的：在没有可用 Docker 的环境（本机 Windows）复现「100 写 + 100 读 VU，P95 < 300ms、失败率 < 0.1%」这条 G4 门槛，
> 并把「应用容量」与「环境/存储」两个变量分开测量。结论与实测矩阵见 `docs/61-gate-report.md §10.2`。

## 0. 结论速览（参考机：Windows 11 / i5-13600KF 20 逻辑核）

| 部署 | 读 P95 | 写/开场 P95 | 是否达标 |
|---|---|---|---|
| 原生 PG，1 uvicorn worker（镜像默认） | 596ms | 20.9s | ❌ |
| 原生 PG，4 worker，fsync=on | 82ms | 589ms | ❌（写尾） |
| 原生 PG，12 worker，fsync=on | 38ms | 523ms | ❌（写尾，差 1ms） |
| 原生 PG，12 worker，`synchronous_commit=off` | 34ms | 217ms | ✅（仅诊断用） |
| **容器（Docker Desktop）+ 8 worker，fsync=on，k6 在容器内** | **14.2ms** | **428ms**（save-only 200ms） | ❌（仅开场 burst 超标） |
| GitHub runner（4 vCPU，优化前，4 worker） | 301.6ms | 1173ms | ❌ |
| **GitHub runner（4 vCPU，会话序列化优化后，4 worker）** | **19.96ms** | **711ms**（save 349ms） | ❌（仅开场 burst 超标） |
| GitHub runner（4 vCPU，优化后，8 worker） | 43.8ms | 1409ms | ❌（过订阅，明确更差） |
| GitHub runner（4 vCPU，第二轮优化，池 5+5） | 24.2ms | p50 166ms / p95 10.2s（80 次 pool timeout） | ❌（池太小） |
| GitHub runner（4 vCPU，第二轮优化，池 12+12） | 37.5ms | p95 1176ms（p50 未单独记） | ❌（4 vCPU CPU 争用，run 间方差大） |

- **读路径**是应用容量问题：worker 数决定（本机 4 worker 即达标；容器内 8 worker 读 P95 14ms）。
- **写/开场尾延迟**由 PostgreSQL 提交落盘（WAL fsync）主导：本机原生（NTFS）与 Docker Desktop 容器（VM 磁盘仍是 Windows NTFS 上的 VHDX）**都**出现 300–900ms 的开场 COMMIT 串行；
  把提交改成异步后同类负载 p95 降到 217ms（诊断）。**生产必须保持 fsync=on，因此必须在真实 Linux/SSD 上复核**（CI `perf` job 或 §6 命令）。
- 端口代理不是瓶颈（Docker 修复后实测宿主 50 并发连接 50/50 成功）；容器内压测建议把 k6 放进容器网络（见 §6）。

## 1. 前置条件

- Python 3.12 venv（仓库根 `.venv`）已安装 `psycopg[binary]` 与 `alembic`（`server/` 依赖）；
- k6 v0.49.0：`reports/tools/k6-v0.49.0-windows-amd64/k6.exe`；
- PostgreSQL 16 官方 Windows binaries zip（EnterpriseDB `postgresql-16.4-1-windows-x64-binaries.zip`）。

> **硬约束：PostgreSQL 的二进制与数据目录必须放在纯 ASCII 路径。**
> 放在含中文的仓库路径下会在 `initdb` 的 post-bootstrap 阶段报
> `FATAL: invalid byte sequence for encoding "UTF8": 0xd4 0xf1`（实测把整份 binaries 复制到 `D:\pgsql` 后通过）。

## 2. 起原生 PostgreSQL（一次性）

```powershell
# 解压后把 pgsql 目录放到 ASCII 路径
Copy-Item <解压目录>\pgsql D:\pgsql -Recurse

$env:LC_ALL='C'; $env:LANG='C'
& D:\pgsql\bin\initdb.exe -D D:\pgsql\data -U exam --auth=trust --encoding=UTF8 --no-locale
& D:\pgsql\bin\pg_ctl.exe -D D:\pgsql\data -l D:\pgsql\server.log `
    -o "-p 55432 -c listen_addresses=127.0.0.1 -c max_connections=300" start

& D:\pgsql\bin\psql.exe -h 127.0.0.1 -p 55432 -U exam -d postgres -c "CREATE DATABASE exam;"
& D:\pgsql\bin\psql.exe -h 127.0.0.1 -p 55432 -U exam -d postgres -c "ALTER USER exam PASSWORD 'exam_pw_2026';"
```

迁移 + 首个管理员（口令只走环境变量，勿写进命令历史/仓库）：

```powershell
Set-Location server
$env:DATABASE_URL='postgresql+psycopg://exam:exam_pw_2026@127.0.0.1:55432/exam'
$env:PYTHONUTF8='1'
..\.venv\Scripts\python.exe -m alembic upgrade head
$env:PERF_ADMIN_PW='<admin 口令>'
..\.venv\Scripts\python.exe -m app.cli create-admin --username perf-admin --display-name "性能管理员" --password-env PERF_ADMIN_PW
```

## 3. 起 API（worker 数 = 容量变量）

**API stdout 必须重定向到文件，禁止用 PowerShell 管道 / 前台 `docker compose up` 捕获。**
原因：`app/middleware/access_log.py` 用模块级锁 + 每行 `flush()` 写 stdout，慢速消费者会把所有请求串行化
（实测同一负载 p95 从 ~700ms 放大到 20–31s，并诱发 `QueuePool` 超时 500）。

```powershell
$dir = 'reports\perf\<run 名>'; New-Item -ItemType Directory -Force $dir | Out-Null
$env:DATABASE_URL='postgresql+psycopg://exam:exam_pw_2026@127.0.0.1:55432/exam'
$env:APP_ENV='production'
$env:SESSION_COOKIE_SECURE='false'    # 纯 HTTP 压测：否则 cookie 不回传，表现为“登录 200 随后 401”
$env:PYTHONUTF8='1'
$env:DB_POOL_SIZE='4'; $env:DB_MAX_OVERFLOW='4'   # 12 worker × 8 = 96 < max_connections
Start-Process -FilePath '.\.venv\Scripts\python.exe' `
  -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8004','--workers','12' `
  -WorkingDirectory (Resolve-Path server).Path `
  -RedirectStandardOutput "$dir\api.out.log" -RedirectStandardError "$dir\api.err.log" -WindowStyle Hidden
```

## 4. 造数 + 压测

```powershell
$env:EXAM_API_URL='http://127.0.0.1:8004/api'
$env:EXAM_RUN_ID='perf-<时间戳>'          # 每次正式压测换新值
$env:EXAM_ADMIN_USERNAME='perf-admin'
$env:EXAM_ADMIN_PASSWORD='<admin 口令>'
$env:EXAM_QA_PASSWORD='<学生统一口令>'
.\.venv\Scripts\python.exe tests\perf\prepare_data.py     # 100 学生 + 100 题 + 100 分卷 + 已发布考试

$env:EXAM_STUDENT_FILE='tests\perf\data\students.json'
$env:PERF_REPORT_DIR=(Join-Path (Get-Location) $dir)
Start-Process -FilePath 'reports\tools\k6-v0.49.0-windows-amd64\k6.exe' `
  -ArgumentList 'run','tests\perf\k6\exam_flow.js' -WorkingDirectory (Get-Location).Path `
  -RedirectStandardOutput "$dir\k6.log" -RedirectStandardError "$dir\k6.err.log" -WindowStyle Hidden -Wait
```

- 每个 `exam` 只适合一轮压测；重跑 `prepare_data.py` 会在上一轮产生会话后自动轮换试卷 + 考试。
- 阈值：`http_req_duration{scenario:answer_save|session_read} p(95)<300`、`http_req_failed rate<0.001`。
- 读 `summary.json` 判门槛；`answer_save` 场景 p95 含开场 100 个 `POST /exams/{id}/sessions`（真实同秒开考场景），不要只看 save 请求。

## 5. 变量对照实验（定位时必做）

| 想验证 | 做法 |
|---|---|
| 是否 CPU 容量问题 | 只改 `--workers`，看 `session_read` p95 是否随 worker 线性下降 |
| 是否是提交落盘 | `ALTER SYSTEM SET synchronous_commit=off; SELECT pg_reload_conf();` 后重跑（**只用于定位，生产必须 on**） |
| 是否是日志管道 | 把 stdout 从管道改到文件；若 p95 从 20s 级掉到亚秒级，即命中 RISK-15 |
| 单请求基线 | `reports\perf\probe_latency.py <api base>`：空载时 GET 会话 ~7ms、GET p95 ~20ms 属正常 |

## 6. Linux / CI 上的等价做法（最终销项用）

```bash
# 1) compose 起 api + postgres（同 network，不经宿主端口代理）
docker compose -f deploy/docker-compose.yml --env-file deploy/.env.staging -p exam-perf up -d postgres api
# 2) 造数 + 压测（在 compose 内或同网络机器上指向 api:8000）
EXAM_API_URL=http://127.0.0.1:8080/api EXAM_RUN_ID=perf-$(date -u +%Y%m%d-%H%M%S) \
EXAM_ADMIN_PASSWORD=... EXAM_QA_PASSWORD=... python tests/perf/prepare_data.py
k6 run tests/perf/k6/exam_flow.js
```

- 连接预算：`WEB_CONCURRENCY × (DB_POOL_SIZE + DB_MAX_OVERFLOW) < postgres max_connections`（默认 100）。
  例：`4 × (10+10) = 80`；`12 × (4+4) = 96`。
- **必须保持 `synchronous_commit=on`（默认）**；若 Linux 上写尾仍超阈值，先看磁盘 fsync 能力（云盘 IOPS/突发额度），再考虑产品侧压缩开场写路径。

## 7. 已知坑（按踩坑顺序）

1. **中文路径** → `initdb` 在 post-bootstrap 报 `invalid byte sequence for encoding "UTF8"`；把 binaries + data 都放 ASCII 路径。
2. **沙箱/权限**：数据目录放在工作区外可能报 `could not change permissions of directory ... Permission denied`；放 ASCII 路径且确保当前用户可写。
3. **`SESSION_COOKIE_SECURE`**：`APP_ENV=production` 默认 Secure；纯 HTTP 压测会假 401，显式设 `false`。
4. **stdout 捕获**：见 §3；不要把 API/容器日志接到慢消费者上做压测。
5. **`--workers` 在 Windows 下用 spawn**：子进程日志可能不落重定向文件（本次实测 access 日志为空，不影响压测结果）；Linux 无此现象。
6. **每轮重造数据**：旧考试的会话/交卷状态会让第二轮结果失真。
