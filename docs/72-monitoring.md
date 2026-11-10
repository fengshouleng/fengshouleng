# 72 · 监控、日志与告警（轻量方案）

- 版本：v1.0　作者：R6 Release（TASK-REL-003）　日期：2026-11-09
- 状态：**脚本与文档已交付；执行环境 shell 不可用（0xC0000142），脚本尚未实跑**。需在可执行环境按 §10 跑一遍 `smoke_alert.sh` 并把证据回填任务卡（`tests.verified=false`）。
- 适用：`deploy/docker-compose.yml` 四服务（nginx / api / postgres / backup），staging 与 prod 同构（ADR-008）。
- 相关：`deploy/monitoring/`、`deploy/backup/freshness_check.sh`、`docs/70-runbook.md`、`docs/71-backup-restore.md`。
- 读者假设：有 Docker 基础、能读中文、**未参与本项目**。按 §3 安装，按 §8 排障即可。
- 明确约束：**不引入 Prometheus / Grafana / ELK / 任何常驻 agent**；告警 = 本地告警文件 + `ALERT_CMD` 命令钩子（可接 webhook/邮件）。

> 本文所有命令都在**仓库根目录**执行。`$COMPOSE` 一律指
> `docker compose -f deploy/docker-compose.yml --env-file deploy/.env.staging -p exam-staging`，
> 生产改为 `.env.prod` + `-p exam-prod`。本文不出现任何真实口令/令牌。

---

## 1. 方案总览（为什么是“文件 + 钩子”）

| 层 | 机制 | 负责发现什么 | 谁执行 |
|---|---|---|---|
| 容器内 | compose `healthcheck`（已有） | 进程是否 ready、是否被重建 | Docker 自身 |
| 宿主 | `deploy/monitoring/health_probe.sh`（本卡） | 经 nginx 的整条链路（nginx + api + DB）是否可用 | cron，每分钟 |
| 宿主 | `deploy/monitoring/resource_alerts.sh`（本卡） | 磁盘、备份新鲜度、容器退出/不健康 | cron，每 5 分钟 |
| 宿主 | `deploy/monitoring/slow_requests.sh`（本卡） | 慢请求与 5xx 趋势（日报） | cron，每日 |
| 人 | `docs/70-runbook.md` §4/§9、本文 §8 | 按症状定位到具体服务/日志 | 值班人 |

设计取舍：

- 单机、≤100 并发、1 人运维 → 不引入时序数据库与 dashboard；指标值就是“退出码 + 告警文件 + 日报”。
- 告警通道不写死在代码里：所有脚本调用同一个 `ALERT_CMD` 接口，换 webhook/邮件/企业微信只改配置。
- **本卡写作用域只有 `deploy/monitoring/**` 和本文件**。以下内容以“待应用片段”给出，不在本卡落地：
  - api 的 JSON 行访问日志（`server/**` 被禁止修改）；
  - nginx 的 JSON/带耗时 `log_format`（`deploy/nginx.conf` 不在写作用域）；
  - compose 的 `logging` 轮转（`deploy/docker-compose.yml` 不在写作用域）；
  - 宿主机 crontab（`deploy/cron/**` 不在写作用域）。

---

## 2. 交付物与职责

| 文件 | 作用 | 运行位置 | 有副作用吗 |
|---|---|---|---|
| `deploy/monitoring/lib.sh` | 共用：日志、`ALERT_CMD`、告警文件、冷却、配置加载 | 被 source | 否（仅被调用时写文件） |
| `deploy/monitoring/health_probe.sh` | `/api/healthz` + `/nginx-health` 外部探测，连续失败告警 | 宿主 | 写状态/告警文件，调钩子 |
| `deploy/monitoring/resource_alerts.sh` | 磁盘 >85%、备份新鲜度、容器状态 | 宿主 | 写告警文件，调钩子 |
| `deploy/monitoring/slow_requests.sh` | 慢请求/5xx 日报，Top 路径 | 宿主 | 写日报到证据目录 |
| `deploy/monitoring/slow_requests.awk` | 解析 api JSON / nginx JSON / nginx 文本 | 被 awk 调用 | 否 |
| `deploy/monitoring/smoke_alert.sh` | 真实触发三类告警的自测（验收） | 宿主 | 只写临时目录 + 证据文件 |
| `deploy/monitoring/monitoring.env.example` | 配置模板（阈值、告警命令） | 复制为 `monitoring.env` | 否 |
| `docs/72-monitoring.md` | 本文：指标、阈值、排障、升级 | — | — |

运行期文件（全部在 `STATE_DIR`，默认 `deploy/evidence/monitoring/`）：

| 文件 | 含义 |
|---|---|
| `alert_health_probe.log` | 健康探测连续失败（存在即异常，恢复后自动删除） |
| `alert_disk_high.log` | 磁盘使用率超阈值 |
| `alert_backup_stale.log` | 备份新鲜度检查失败（容器/本地两种模式） |
| `alert_container_unhealthy.log` | 容器不在 running / unhealthy / 退出码非 0 |
| `health_probe.state` | 连续失败计数（`fail_count=`），人工 `--reset` 可清 |
| `.cooldown_alert_*.log` | 告警钩子冷却标记（防止每分钟重复通知） |
| `slow-requests-YYYYMMDD.log` | 按 UTC 日期追加的慢请求日报 |
| `smoke-alert-<UTC>.log` | `smoke_alert.sh` 自测证据 |

> 告警文件是 `key=value` 覆盖写（不是历史堆积），内容含 `reason/time/host/source/message`；
> 需要历史请随发布证据归档，或让 `ALERT_CMD` 自己写审计日志。

---

## 3. 安装与首次验证（非作者照此执行）

### 3.1 配置

```bash
cd /opt/exam                                  # 换成真实仓库绝对路径
cp deploy/monitoring/monitoring.env.example deploy/monitoring/monitoring.env
chmod 600 deploy/monitoring/monitoring.env
vi deploy/monitoring/monitoring.env           # 至少改 BASE_URL、STATE_DIR、ALERT_CMD
```

要点：

- `BASE_URL` 是**从宿主经 nginx** 的入口（staging 默认 `http://127.0.0.1:8080`；prod TLS 后填 `https://域名`）。
  **不要**在 URL 里放用户名/密码/token：探测日志与告警会打印 `base=`/URL，凭据会进入告警文件。
- `STATE_DIR` 用绝对路径（cron 的 cwd 不可靠）；cron 用户必须可写。
  同一台机器跑 staging 与 prod 时，**两套 `STATE_DIR` 必须分开**（例如 `.../monitoring-staging` / `.../monitoring-prod`），
  否则连续失败计数与告警文件会互相覆盖。
- `ALERT_CMD` 留空时只落告警文件，不会对外发送；配置示例见 `monitoring.env.example`。
- 配置优先级：`monitoring.env` > 进程环境变量 > 脚本默认值（原因是脚本先 source 文件再取默认值）。
  临时覆盖文件里的同名变量时，用 `MONITORING_ENV_FILE= BASE_URL=... bash ...` 禁用配置文件。
- 若仓库是从 Windows 检出，确认脚本是 LF 行尾（`file deploy/monitoring/*.sh` 不应出现 CRLF），否则 bash 会报 `$'\r': command not found`。

### 3.2 dry-run（不联网、不写文件、不调钩子）

```bash
bash deploy/monitoring/health_probe.sh --dry-run
bash deploy/monitoring/resource_alerts.sh --dry-run
bash deploy/monitoring/slow_requests.sh --dry-run
```

三条命令都应打印计划并以 **0** 退出。`health_probe --dry-run` 会打印端点、阈值、状态目录，并把
`ALERT_CMD` 显示为“已配置/未配置”（**不打印命令内容**，避免泄露 webhook 令牌）。

### 3.3 告警链路自测（验收必跑）

```bash
bash deploy/monitoring/smoke_alert.sh
echo "exit=$?"
```

它会：用 `127.0.0.1:1` 制造真实健康探测失败；把磁盘阈值设为 0% 触发磁盘告警；伪造 1970 年的备份触发陈旧告警；
用混合格式日志验证慢请求统计；验证 `--reset`/`--dry-run` 无副作用。预期 `passed` 全过、`failed=0`，
证据写入 `deploy/evidence/monitoring/smoke-alert-<UTC>.log`。失败时加 `--keep` 保留临时目录：
`bash deploy/monitoring/smoke_alert.sh --keep`。

### 3.4 手工触发一次真实告警（staging 验收动作）

```bash
# 用一个必然连不上的端口，避免影响正在运行的 staging
MONITORING_ENV_FILE= \
STATE_DIR=deploy/evidence/monitoring \
BASE_URL=http://127.0.0.1:9 \
HEALTH_FAIL_THRESHOLD=1 \
bash deploy/monitoring/health_probe.sh; echo "exit=$?"

cat deploy/evidence/monitoring/alert_health_probe.log   # 应含 reason=health_probe_failed
STATE_DIR=deploy/evidence/monitoring \
  bash deploy/monitoring/health_probe.sh --reset        # 确认后清理
```

预期：第一次 `exit=3`（达到阈值并告警）；`--reset` 后 `exit=0` 且告警文件消失。
这一步就是验收项“告警脚本在 staging 可手动触发并留下记录”。

### 3.5 安装定时任务

见 §7 的 crontab **待应用片段**（本卡不改 `deploy/cron/**`）。安装后确认：

```bash
crontab -l | grep deploy/monitoring
```

---

## 4. 指标清单、阈值与告警

阈值是**起步值**，按真实流量在 `monitoring.env` 调整；所有阈值都可在文件中覆盖。

| # | 指标 | 数据来源 | 采集 | 阈值 / 判定 | 告警 reason | 处置 |
|---|---|---|---|---|---|---|
| M1 | 站点健康（含 DB） | `GET /api/healthz` | `health_probe.sh` 每分钟 | 连续 3 次非 200、或 `status!=ok`、或 `db!=ok` | `health_probe_failed` | §8.2 |
| M2 | nginx 存活 | `GET /nginx-health` | 同上 | 连续 3 次非 200 或响应体非 `ok` | `health_probe_failed` | §8.3 |
| M3 | 5xx 错误率 | api/nginx 日志 | 人工查询（§8.4），日报附 5xx 计数 | 15 分钟内 5xx 占比 >5% 或 5xx >10 条/分：关注；>20%：严重 | 不自动告警（V1） | §8.4 |
| M4 | 慢请求 | api/nginx 日志 | `slow_requests.sh` 每日 | `latency_ms>1000`；>20 条/日或单路径 >10 条/日：立项优化；单请求 >10s：立即查 | 日报（exit 0，不打扰） | §8.5 |
| M5 | 磁盘使用率 | `df -P` | `resource_alerts.sh` 每 5 分钟 | `>85%` 告警；`>95%` 按 P1 处理 | `disk_high` | §8.6 |
| M6 | 备份新鲜度 | `/backups/.last_backup_ok(.epoch)` | 复用 `deploy/backup/freshness_check.sh` | 距最近成功备份 `>26h`，或从未成功，或时间戳在未来 | `backup_stale` | §8.7、`docs/71` §2.3 |
| M7 | 容器状态 | `docker inspect` / `compose ps` | `resource_alerts.sh` 每 5 分钟 | 不在 `running`、`health=unhealthy`、退出码非 0、服务缺失 | `container_unhealthy` | §8.8 |
| M8 | 备份失败告警文件 | `/backups/.alert_*` | 人工查询 / 备份日志 | 文件存在即说明有未处理失败 | `backup_failed` 等（由 backup 脚本写） | §8.7 |
| M9 | 证书到期 | 宿主 `openssl x509 -enddate` | 人工（V1 未自动化） | 剩余 <30 天必须处理 | 无（日历项） | `docs/70` §8 |

补充判定：

- **M1/M2 的“连续 3 次”是跨多次运行的计数**（state 文件），不是一次脚本里重试 3 次；cron 每分钟一次 ≈ 3 分钟确认，能过滤单次网络抖动。
- M1 的 `db:"ok"` 是 TASK-BE-007 的硬语义：DB 不可用时 `/healthz` 返回 503，因此本探针同时覆盖“api 活着但库不可用”。
- M3/M4 依赖日志格式。默认 nginx combined 没有耗时、api 目前也没有 JSON 访问日志（见 §5），所以上线初期这两项只能看 `unknown_latency` 与 5xx 文本查询；格式片段应用后才是完整口径。
- 阈值不是 SLA：本方案只负责“发现并通知”，不承诺自动恢复。

---

## 5. 日志规范与轮转

### 5.1 目标：api 输出 JSON 行访问日志

**契约（目标格式，一行一个 JSON 对象，写 stdout）：**

| 字段 | 类型 | 必填 | 语义 |
|---|---|---|---|
| `ts` | string | ✅ | RFC3339 UTC，毫秒精度，如 `2026-11-09T00:00:00.123Z` |
| `level` | string | ✅ | `info` / `warning` / `error`（4xx 用 `warning`，5xx 用 `error`，其余 `info`） |
| `logger` | string | ✅ | 固定 `app.access`（便于过滤） |
| `request_id` | string | ✅ | 来自 `X-Request-ID` 中间件；沿用入站值或服务端生成 |
| `method` | string | ✅ | HTTP 方法 |
| `path` | string | ✅ | **只记路径，不含 query**（避免把令牌写进日志） |
| `status` | number | ✅ | HTTP 状态码 |
| `latency_ms` | number | ✅ | 服务端测得耗时（毫秒，保留 2 位小数） |
| `actor_id` | string \| null | ✅ | 登录用户 id；匿名/系统请求用 `null`（**不要省略字段**） |
| `error_code` | string | ❌ | 仅 5xx/业务错误时出现（与契约 `Error.code` 对齐） |

示例（单行）：

```json
{"ts":"2026-11-09T00:00:00.123Z","level":"info","logger":"app.access","request_id":"6f1c0f3e-3d5a-4d8a-9d2b-2b0f0f9a1c11","method":"GET","path":"/api/questions","status":200,"latency_ms":12.4,"actor_id":"42"}
```

**禁止**写入日志（违反即按事故处理）：

- 密码、`POSTGRES_PASSWORD`、`DATABASE_URL`、Cookie、`session_id`、CSRF token、`Authorization` 头；
- 用户答案/选项内容、题干/解析正文、试卷快照；
- 完整请求/响应体、完整 URL query（可能带 token）；
- 多行 JSON（堆栈必须转义成单行 `stack` 字段，或另起一条 `level=error` 的 JSON 行）。

**现状**：TASK-BE-008 已落地 `server/app/middleware/access_log.py`（纯 ASGI 中间件，不缓冲响应体、5xx 也记行、写入 stdout JSON 行），并在 `server/app/main.py` 注册（request_id 之后）。
字段与上表一致（`logger=app.access`、`level` 4xx=warning/5xx=error、`latency_ms` 2 位小数、`path` 不含 query、`actor_id` 未登录为 `null`）。
`tests/middleware/test_access_log.py` 覆盖 13 个场景（含 awk 可解析性与敏感值不出现）。`slow_requests.sh` 可直接解析该格式；`unknown_latency` 应归零。

### 5.2 待应用片段 A：api JSON 行日志（`server/**`，不在本卡）

> 由后端卡按此语义实现；本卡不改 `server/**`。实现后必须补测试：匿名/登录/5xx 各一条，
> 断言**单行可 `json.loads`**、字段齐全、不含请求体。

```python
# 参考实现（不是本卡已落地的代码）
import json
import logging
import sys
import time
from datetime import datetime, timezone

ACCESS = logging.getLogger("app.access")
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")

@app.middleware("http")
async def access_log_middleware(request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    latency_ms = round((time.perf_counter() - started) * 1000, 1)
    ACCESS.info(json.dumps({
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "level": "info",
        "logger": "app.access",
        "request_id": getattr(request.state, "request_id", None),
        "method": request.method,
        "path": request.url.path,          # 不含 query
        "status": response.status_code,
        "latency_ms": latency_ms,
        "actor_id": getattr(request.state, "actor_id", None),  # 未认证为 None
    }, ensure_ascii=False, separators=(",", ":")), extra={"json": True})
    return response
```

落地时注意：

1. 中间件顺序：确保记录日志时 `request.state.request_id` 已经存在；最稳妥是在同一个中间件里生成/沿用 id，
   不要依赖“另一个中间件的注册顺序”。
2. 未捕获异常也要有访问日志：在 `try/except` 里记录 500 后重新抛出，避免“有错误无访问行”。
3. uvicorn 自带 access log 要关掉（entrypoint 加 `--no-access-log` 或 `access_log=False`），否则同一请求两行、口径混乱。
4. 日志量大时按 §5.4 轮转；`json.dumps` 不要 `indent`。

### 5.3 待应用片段 B：nginx 访问日志带耗时（`deploy/nginx.conf`，不在本卡）

当前 `deploy/nginx.conf` 没有自定义 `log_format`，nginx 默认 combined 格式**没有 `$request_time`**，
因此慢请求只能靠 api 的 JSON。要让 nginx 侧也可统计，在 `deploy/nginx.conf` 的 http 上下文（本文件被 include
进 `http{}`，文件顶部写即可）加：

```nginx
# 待应用（本卡不改 deploy/nginx.conf）：JSON 行、含 request_time（秒）与 request_id
log_format json_access escape=json
    '{"ts":"$time_iso8601","remote_addr":"$remote_addr",'
    '"method":"$request_method","path":"$uri","status":$status,'
    '"request_time":$request_time,"upstream_response_time":"$upstream_response_time",'
    '"request_id":"$http_x_request_id"}';

access_log /dev/stdout json_access;
```

要点：

- `$uri` 是规范化后的路径，不含 query；`$request_time` 单位是**秒**（`slow_requests.awk` 自动 ×1000）。
- API 的 `request_id` 只在客户端传了 `X-Request-ID`（或 nginx 用 `$request_id` 覆盖）时才有值；
  若需要跨层追踪，可加 `proxy_set_header X-Request-ID $request_id;`（同样属 nginx.conf 改动）。
- 应用后必须 `nginx -t` 再重载；`deploy/nginx.conf` 变更与回滚按 `docs/70-runbook.md` §8/§7 执行。

### 5.4 待应用片段 C：compose 的 json-file 轮转（`deploy/docker-compose.yml`，不在本卡）

目标：每个服务日志最多 `10m × 3`（≈30 MB），防止磁盘被日志打满。当前文件顶部 anchor 是
`max-size: "10m"` + **`max-file: "5"`**；把 anchor 改为：

```yaml
# 待应用（本卡不改 deploy/docker-compose.yml）
x-logging: &default-logging
  driver: json-file
  options:
    max-size: "10m"
    max-file: "3"
```

应用步骤（会重建容器，选低峰）：

```bash
$COMPOSE config -q                                   # 先做结构自检
$COMPOSE up -d --force-recreate                      # 或按发布流程 down/up
docker inspect --format '{{json .HostConfig.LogConfig}}' "$($COMPOSE ps -q api)"
# 期望 {"Type":"json-file","Config":{"max-file":"3","max-size":"10m"}}
```

> Docker 的 logging 选项**只在容器创建时生效**，改完必须重建容器，`restart` 不够。
> 日志被轮转后旧的 `docker logs` 仍可查当前文件；需要历史时在重建前 `docker compose logs > 证据文件`。

### 5.5 日志位置

| 位置 | 内容 | 查看 |
|---|---|---|
| 容器 stdout | api（uvicorn + 迁移）、nginx（access/error）、postgres、backup | `$COMPOSE logs --since 15m <service>` |
| 容器 `/backups/logs/backup-YYYYMMDD.log` | 备份流水（按 UTC 日期） | `$COMPOSE exec -T backup cat /backups/logs/backup-$(date -u +%Y%m%d).log` |
| 容器 `/backups/drill-logs/` | 恢复演练结论 | `$COMPOSE exec -T backup ls -lt /backups/drill-logs` |
| 宿主 `deploy/evidence/monitoring/` | 告警文件、状态、慢请求日报、自测证据 | `ls -lt deploy/evidence/monitoring` |
| 宿主 crontab 重定向（若按 §7） | cron 每次运行的 stdout | `tail -n 100 deploy/evidence/monitoring/cron-health.log` |
| Docker json-file 本体 | 容器日志文件 | `docker inspect --format '{{.LogPath}}' <cid>`（只读排查） |

### 5.6 常用查询

```bash
COMPOSE="docker compose -f deploy/docker-compose.yml --env-file deploy/.env.staging -p exam-staging"

# 最近 15 分钟 5xx 条数（api JSON 落地后；当前可先用 nginx 文本）
$COMPOSE logs --no-color --no-log-prefix --since 15m api | grep -c '"status":5' || true

# 最近 15 分钟 nginx 5xx（combined 文本）
$COMPOSE logs --no-color --no-log-prefix --since 15m nginx | grep -cE '" 5[0-9][0-9] ' || true

# 按 request_id 串起一次请求（api JSON 落地后；id 从响应头 X-Request-ID 取）
$COMPOSE logs --no-color --no-log-prefix --since 1h api | grep '<request_id>' || true

# 慢请求 Top 10（自动处理 JSON/nginx 两种格式）
bash deploy/monitoring/slow_requests.sh --since 2h

# 备份最近成功时间
$COMPOSE exec -T backup cat /backups/.last_backup_ok
```

---

## 6. 告警通道配置（`ALERT_CMD`）

### 6.1 接口

脚本只做一件事：`sh -c "$ALERT_CMD"`，并通过环境变量传上下文：

| 变量 | 取值 |
|---|---|
| `ALERT_REASON` | `health_probe_failed` / `disk_high` / `backup_stale` / `container_unhealthy` |
| `ALERT_MESSAGE` | 人类可读摘要（含端点/百分比/备份年龄；**不含密码/令牌/答案**） |
| `ALERT_FILE` | 告警落盘文件绝对路径 |
| `ALERT_SOURCE` | `health_probe` / `resource_alerts` / `slow_requests` |

行为约定（与 `deploy/backup/lib.sh` 的 `BACKUP_ALERT_CMD` 一致）：

- 告警命令失败只在 stderr 打 `WARN`，**不改变**本次判定/退出码（否则会掩盖真实原因）；
- 告警文件总是先写（即使没配通道）；
- 同一告警有冷却：健康 3600s、资源 1800s（可配），冷却期内只刷新告警文件、不重复调钩子；
- 恢复后脚本自动删除告警文件与冷却标记（不需要人工“消警”）。

### 6.2 配置示例（不要写密钥进仓库）

`deploy/monitoring/monitoring.env` 是唯一配置入口（权限 600、禁止提交）。完整示例见
`deploy/monitoring/monitoring.env.example`，最小可用配置：

```bash
BASE_URL=https://exam.example.com
STATE_DIR=/opt/exam/deploy/evidence/monitoring

# 通用 webhook；URL 从环境变量读，避免把令牌写进 ALERT_CMD/仓库
export ALERT_WEBHOOK_URL=https://alert.example.invalid/hook/CHANGE_ME
ALERT_CMD='curl -fsS -m 5 -H "Content-Type: application/json" -d "{\"text\":\"[$ALERT_SOURCE] $ALERT_REASON: $ALERT_MESSAGE\"}" "$ALERT_WEBHOOK_URL"'
```

注意：

- `monitoring.env` 被 `source`，所以 `ALERT_WEBHOOK_URL` 必须 `export`，钩子子进程才读得到；
  `ALERT_CMD` 本身不必 export（同一个 shell 使用）。
- 换通道只改 `ALERT_CMD`；**不要**在脚本里加 `curl` 或邮箱。
- 容器内备份脚本仍用 `BACKUP_ALERT_CMD`（另一个接口）；本卡的 `resource_alerts.sh` 已经在宿主侧
  调用 `freshness_check.sh` 并在失败时走 `ALERT_CMD`，因此**不需要**再给容器配一遍，否则同一次陈旧会收到两条。
- 若同时使用 `deploy/cron/exam-backup-cron.sh` 的 `HOST_ALERT_CMD`，同样注意避免与 `resource_alerts.sh` 重复告警（见 §7.3）。

### 6.3 验证钩子（不依赖真实故障）

`smoke_alert.sh` 用临时记录器当 `ALERT_CMD`，验证“写文件 + 调钩子”闭环，不会碰真实通道。
若要在 staging 验证真实 webhook，用 §3.4 的手工失败命令（`HEALTH_FAIL_THRESHOLD=1`）即可。

---

## 7. 定时任务（待应用片段 D，不在本卡写作用域）

### 7.1 crontab

追加到现有 crontab（**不要**执行 `crontab deploy/cron/exam-backup.cron` 覆盖已有任务）：

```cron
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
MAILTO=""
MONITORING_ENV_FILE=/opt/exam/deploy/monitoring/monitoring.env

# 每分钟：外部健康探测（连续 3 次失败告警；exit 0/1/3 见脚本头）
* * * * * cd /opt/exam && bash deploy/monitoring/health_probe.sh >> deploy/evidence/monitoring/cron-health.log 2>&1

# 每 5 分钟：磁盘 >85% / 备份新鲜度 >26h / 容器状态
*/5 * * * * cd /opt/exam && bash deploy/monitoring/resource_alerts.sh >> deploy/evidence/monitoring/cron-resource.log 2>&1

# 每日 07:30（宿主本地时间）：慢请求日报；容器时间戳为 UTC
30 7 * * * cd /opt/exam && bash deploy/monitoring/slow_requests.sh >> deploy/evidence/monitoring/cron-slow.log 2>&1
```

要点：

- `cd /opt/exam` 必须真实存在，且 `/opt/exam` 换成仓库绝对路径；脚本内部会据此算 `REPO_ROOT`。
- `MONITORING_ENV_FILE` 是 crontab 环境变量（Vixie cron 支持）；若你的 cron 不支持，改成
  `MONITORING_ENV_FILE=/opt/exam/deploy/monitoring/monitoring.env bash deploy/monitoring/health_probe.sh` 内联写法。
- `MAILTO=""` 与现有备份模板一致：不把日志当邮件；告警走 `ALERT_CMD`。
- cron 的 PATH 很短，脚本全部用 `bash` 显式调用；`curl`/`docker` 必须在 `PATH` 中（模板已含 `/usr/local/bin`、`/usr/bin`）。
- 验证 cron 环境（不等待）：
  ```bash
  env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    MONITORING_ENV_FILE=/opt/exam/deploy/monitoring/monitoring.env \
    bash -c 'cd /opt/exam && bash deploy/monitoring/health_probe.sh --dry-run'
  ```

### 7.2 cron 日志轮转

`cron-*.log` 是追加写，建议加宿主 logrotate（路径按实际改）：

```
/opt/exam/deploy/evidence/monitoring/cron-*.log {
    weekly
    rotate 8
    compress
    missingok
    notifempty
    copytruncate
}
```

### 7.3 与现有备份 cron 的分工（避免重复告警）

`deploy/cron/exam-backup.cron` 已有“每小时 freshness”和“每月 drill”。两种做法**二选一**，避免同一次
`stale_backup` 两个入口各发一条：

- **推荐**：备份 cron 保留 drill/export，注释掉它的每小时 `freshness` 行；由本卡
  `resource_alerts.sh`（每 5 分钟）统一做新鲜度检查与 `ALERT_CMD` 通知；
- 或者：保留备份 cron 的 freshness（`HOST_ALERT_CMD` 已配置时），本卡 `resource_alerts.sh`
  加 `--skip-freshness`（代价是备份新鲜度只有每小时粒度）。

不要同时启用“容器 loop 日备”和 cron 的 02:00 定点备份（`docs/71` §7.3 已说明）。

---

## 8. 排障入口（按症状定位一次故障）

### 8.1 60 秒定位顺序

```bash
COMPOSE="docker compose -f deploy/docker-compose.yml --env-file deploy/.env.staging -p exam-staging"

# 1) 三个探针（从外到内）
curl -fsS -m 3 http://127.0.0.1:8080/nginx-health; echo
curl -fsS -m 5 http://127.0.0.1:8080/api/healthz; echo

# 2) 容器状态
$COMPOSE ps

# 3) 当前告警（文件存在即未恢复）
ls -lt deploy/evidence/monitoring/alert_*.log 2>/dev/null || echo "无告警文件"
cat deploy/evidence/monitoring/alert_*.log 2>/dev/null || true

# 4) 最近日志
$COMPOSE logs --no-color --since 15m api nginx | tail -n 100

# 5) 健康探测计数（判断是抖动还是持续故障）
cat deploy/evidence/monitoring/health_probe.state 2>/dev/null || true
```

判定顺序：**nginx 自身 → api 进程 → DB → 备份/磁盘**。不要一上来就 `up -d`，会覆盖现场。

### 8.2 `/api/healthz` 异常

| 现象 | 最可能原因 | 检查 | 处置 |
|---|---|---|---|
| 连接拒绝 / 超时 | nginx 未起或端口错 | `$COMPOSE ps nginx`、`ss -ltnp \| grep 8080` | 起 nginx；核对 `NGINX_HTTP_PORT` |
| 502 / 504 | api 未起、崩溃、迁移失败 | `$COMPOSE ps api`、`$COMPOSE logs --since 10m api` | 按 `docs/70` §9；迁移失败先看 postgres |
| 503 + `{"code":"INTERNAL_ERROR"...}` | DB 不可用（契约语义） | `$COMPOSE ps postgres`、`logs postgres` | 修 DB；不要改探针绕过 |
| 200 但 `db` 不是 `ok` | 极罕见（代理缓存/假响应） | `curl -v` 看是否真到 api | 清缓存/核对 nginx 反代 |
| `health_probe` exit=1 | 采样失败但未达阈值 | `health_probe.state` 的 `fail_count` | 再观察；连续失败会转 exit=3 |
| `health_probe` exit=3 | 已告警 | 告警文件 + §8.2 上述步骤 | 修复后脚本自动恢复；确认后可 `--reset` |

### 8.3 `/nginx-health` 正常但 `/api/healthz` 失败

说明 nginx 活着、反代或 api 有问题：

```bash
$COMPOSE logs --no-color --since 10m nginx | tail -n 50
$COMPOSE logs --no-color --since 10m api | tail -n 100
$COMPOSE exec -T api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).read().decode())"
```

容器内直连成功、经 nginx 失败 → 查 `upstream exam_api` / 网络 `edge` / 超时；容器内也失败 → 查 api 与 DB。

### 8.4 错误率（5xx）

```bash
# nginx combined 文本（当前默认）
total=$($COMPOSE logs --no-color --no-log-prefix --since 15m nginx | grep -cE '" (2|3|4|5)[0-9][0-9] ' || true)
err=$($COMPOSE logs --no-color --no-log-prefix --since 15m nginx | grep -cE '" 5[0-9][0-9] ' || true)
echo "5xx=${err}/${total}"

# api JSON 落地后（推荐）
$COMPOSE logs --no-color --no-log-prefix --since 15m api \
  | jq -R 'fromjson? | select(.status >= 500) | {request_id, method, path, status, latency_ms}' 2>/dev/null | head -n 20
```

判定：>5% 先看是否有发布/迁移/依赖故障；>20% 按 P2 升级（§9）。常见根因见 `docs/70` §9 表。

### 8.5 慢请求

```bash
bash deploy/monitoring/slow_requests.sh --since 2h
tail -n 80 deploy/evidence/monitoring/slow-requests-$(date -u +%Y%m%d).log
```

- `slow=0` 但 `unknown_latency` 很大 → 日志格式还没按 §5.2/§5.3 应用；先补格式，别误判“没有慢请求”。
- Top 路径集中在导入/导出/报表 → 对照 nginx `proxy_read_timeout=60s`（`deploy/nginx.conf`），确认是否接近超时。
- 单个请求 >10s 且伴随 504 → 查 api 日志同一 `request_id`，确认是 DB 慢查询还是外部 IO。

### 8.6 磁盘告警（`alert_disk_high.log`）

```bash
df -h
docker system df
du -sh /var/lib/docker/volumes/* 2>/dev/null | sort -h | tail
$COMPOSE exec -T backup ls -lh /backups | tail
```

处置顺序：清理无用镜像/构建缓存（`docker image prune`，不要 `volume prune`）→ 检查日志轮转（§5.4）→
按 `docs/71` §2.2 的保留策略清理过期备份（由 backup 脚本自动做，人工只处理异常大文件）→ 扩容。
**禁止**为了降水位删除 `pgdata`/`backups` 卷。

### 8.7 备份陈旧/失败（`alert_backup_stale.log`、`.alert_*`）

```bash
bash deploy/cron/exam-backup-cron.sh status
$COMPOSE exec -T backup cat /backups/.last_backup_ok
$COMPOSE exec -T backup /bin/sh /backup/freshness_check.sh; echo "exit=$?"
$COMPOSE logs --no-color --tail=100 backup
```

- 容器没跑 → `$COMPOSE up -d backup`，观察首轮备份；不要手工伪造 `.last_backup_ok`。
- 备份失败 → 按 `docs/71` §2.4 手工触发一次，看 `pg_dump`/磁盘/密码；修好后重新验证新鲜度。
- 陈旧但容器正常 → 看 loop 间隔（`BACKUP_INTERVAL_SECONDS`）与保留策略；必要时手工备份。
- 恢复流程与演练见 `docs/71` §3/§4，不要在本卡范围内改备份脚本。

### 8.8 容器异常（`alert_container_unhealthy.log`）

```bash
$COMPOSE ps
docker inspect --format '{{.Name}} {{.State.Status}} exit={{.State.ExitCode}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} restarts={{.RestartCount}}' \
  "$($COMPOSE ps -q api)"
$COMPOSE logs --no-color --since 30m api | tail -n 120
```

先留日志再重启（`$COMPOSE logs > /tmp/incident-...`）。反复重启通常是启动配置/迁移/DB 问题，按 `docs/70` §9；
单个容器 unhealthy、其余正常时，优先看该服务的 healthcheck 是否过严。

### 8.9 一次故障要保留的证据

`$COMPOSE ps`、相关服务 `logs --since`、`deploy/evidence/monitoring/alert_*.log`、`health_probe.state`、
慢请求日报、`deploy/evidence/` 下发布证据；文件名建议 `deploy/evidence/incident-<YYYYMMDD>-<NN>.log`。

### 8.10 示例：用户报 502 的完整定位（约 5 分钟）

```bash
# ① 分层：nginx 自身 vs 应用链路
curl -fsS -m 3 http://127.0.0.1:8080/nginx-health; echo   # ok  → nginx 活着
curl -sS -m 5 -o /dev/null -w 'healthz=%{http_code}\n' \
  http://127.0.0.1:8080/api/healthz                       # 502 → 反代后的 api 不可用

# ② 看容器（不要先重启）
$COMPOSE ps
# 典型：api  Restarting (1) ...；nginx/postgres healthy

# ③ 看 api 为什么退出（迁移失败/DB 连接/依赖缺失）
$COMPOSE logs --no-color --since 10m api | tail -n 120
# 若最后是 alembic/psycopg/OperationalError → 转 ④；若是 OOM/退出码 137 → 查磁盘/内存

# ④ 看数据库与磁盘
$COMPOSE ps postgres
$COMPOSE logs --no-color --since 10m postgres | tail -n 80
df -h

# ⑤ 认证告警与趋势
cat deploy/evidence/monitoring/alert_container_unhealthy.log 2>/dev/null || true
cat deploy/evidence/monitoring/health_probe.state 2>/dev/null || true
```

判定：若 api 因迁移/DB 故障无法启动，按 `docs/70` §9 与 §7.2 处理（必要时恢复备份）；
若只是单次重启且已恢复，保留日志并在复盘里说明根因。修复后：

```bash
curl -fsS http://127.0.0.1:8080/api/healthz; echo
bash deploy/monitoring/health_probe.sh; echo "exit=$?"   # 期望 0，告警文件自动清除
```

---

## 9. 告警升级路径

### 9.1 分级与时限

| 级别 | 触发条件（示例） | 确认时限 | 升级时限 | 升级对象 |
|---|---|---|---|---|
| **P1** | `/api/healthz` 连续失败 ≥3 分钟；DB 不可用；磁盘 >95%；备份 >48h 或 backup 容器停止 >1h；无法登录 | 15 分钟 | 30 分钟 | R0/Lead + 备份负责人；必要时公告维护窗口 |
| **P2** | 磁盘 >85%；备份 26–48h；容器 unhealthy/反复重启；5xx >20%（15 分钟） | 1 小时 | 4 小时 | R0/Lead |
| **P3** | 5xx 5–20%；慢请求 >20 条/日或单路径 >10 条/日；单次告警但已自动恢复 | 次一工作日 | 3 个工作日 | 模块负责人（BE/FE） |

> 单机构、1–2 人项目：升级对象就是 R0/Lead 与备份负责人；没有 24×7 值班台时，
> **P1 的 15 分钟确认**必须由 `ALERT_CMD` 推到手机（webhook/短信/电话），否则本方案只能做到“事后发现”。

### 9.2 处置流程

1. **收到告警**：先看 `ALERT_REASON`/告警文件，别急着重启。
2. **确认影响面**：§8.1 的 3 个探针 + `$COMPOSE ps`；判断是单服务还是全站。
3. **止血**：优先回滚（`docs/70` §7.1）或恢复（`docs/71` §3）；需要改配置时走审批。
4. **记录**：时间线、命令输出、证据文件路径；P1/P2 必须写事故记录。
5. **恢复判定**：探针连续 3 次成功、告警文件自动消失、冒烟（`docs/70` §5）通过。
6. **复盘**：更新阈值/查询/文档；如属代码缺陷，建 BE/FE 任务，不要只改监控阈值掩盖。

### 9.3 告警抑制与误报

- 同一告警在冷却窗口内只通知一次（§6.1）；持续未恢复会按窗口重复提醒，不会“只响一次就消失”。
- 维护窗口内先 `crontab -e` 注释掉对应行，或 `ALERT_CMD=` 临时置空（**必须**在窗口结束恢复并记录）。
- 误报处理：保留告警文件与状态，调整 `monitoring.env` 阈值，不要直接删 `health_probe.state` 掩盖计数。

### 9.4 已知盲区

- 宿主整体宕机时，本机 cron 不会告警（没有 dead-man's switch）。建议（可选）用外部 uptime 服务每分钟探
  `https://域名/nginx-health` 与 `/api/healthz`，它不依赖本机；这仍是轻量方案，不需要 Prometheus。
- `ALERT_CMD` 依赖网络与外部服务；通道失败只 WARN，不会重试/排队。
- 监控脚本本身没有被监控；cron 是否还在跑，靠 `cron-*.log` 的 mtime 与外部 uptime 双重确认。

---

## 10. 验收与自测清单

在可执行环境（Linux 宿主 + bash/curl/awk/df）按顺序执行：

| # | 命令 | 预期 | 说明 |
|---|---|---|---|
| 1 | `bash deploy/monitoring/health_probe.sh --dry-run` | exit 0 | 不联网、不写文件 |
| 2 | `bash deploy/monitoring/resource_alerts.sh --dry-run` | exit 0 | 不写告警 |
| 3 | `bash deploy/monitoring/slow_requests.sh --dry-run` | exit 0 | 不写报告 |
| 4 | `bash deploy/monitoring/smoke_alert.sh` | exit 0，`failed=0` | 真实触发三类告警；证据落 `smoke-alert-*.log` |
| 5 | §3.4 手工失败探测 | exit 3，告警文件含 `reason=health_probe_failed` | staging 验收动作 |
| 6 | `bash deploy/monitoring/health_probe.sh --reset` | exit 0，告警文件消失 | 清理演练痕迹 |
| 7 | 安装 §7 cron 后 `crontab -l \| grep deploy/monitoring` | 3 行 | 定时任务生效 |
| 8 | 停 api（staging）≥3 分钟再恢复 | `alert_health_probe.log` 出现后自动消失 | 端到端验证（会中断服务，选窗口） |

证据与回填：

- 自测输出：`deploy/evidence/monitoring/smoke-alert-<UTC>.log`；
- 如做第 8 项，把时间线/命令/结果追加到任务卡或 `deploy/evidence/`；
- 本卡执行环境 shell 不可用（0xC0000142），**以上 1–8 未实跑**（`tests.verified=false`），
  因此“验收通过”必须以可执行环境的输出为准，不能以本文替代。

> 非作者验收标准：只读本文 §8，能否在 10 分钟内从“用户报 502”走到“定位到 api 或 DB 的具体日志”。
> 若不能，说明 §8 还需要补命令，而不是补监控系统。

---

## 11. 已知限制与后续

| 编号 | 限制 | 影响 | 缓解/归属 |
|---|---|---|---|
| M-01 | 无集中指标存储/dashboard | 无法看历史曲线、容量趋势 | 日报 + `docker system df`；规模上来再评估 exporter |
| M-02 | ✅ 已关闭（TASK-BE-008） | `AccessLogMiddleware` 输出 `latency_ms`/`actor_id` 结构化字段，`slow_requests.sh` 可直接统计 | 已实现并补测试，CI 验证后归档 |
| M-03 | nginx 默认 combined 无耗时 | 慢请求的 nginx 侧不可见 | §5.3 `log_format`（需改 `deploy/nginx.conf`） |
| M-04 | compose 仍是 `max-file: 5` | 日志占用比目标多 20 MB/服务 | §5.4 片段（需重建容器） |
| M-05 | cron 未安装 | 本卡只交付脚本与模板 | §7 片段；安装后按 §10 验证 |
| M-06 | 宿主宕机无告警 | 整机故障只能事后发现 | §9.4 外部 uptime 检查（可选） |
| M-07 | 阈值静态、无自适应 | 流量变化后可能误报/漏报 | 月度复盘调整 `monitoring.env` |
| M-08 | 磁盘检查只看宿主路径 | Docker 卷配额/镜像层增长可能漏 | `docker system df` + §8.6 |
| M-09 | `ALERT_CMD` 无重试/队列 | 通道短暂故障会丢通知 | 冷却窗口内会重复尝试；关键期加外部 uptime |
| M-10 | 备份 `BACKUP_ALERT_CMD` 与宿主 `ALERT_CMD` 两套接口 | 配置不当会重复通知 | §6.2/§7.3 明确分工 |

与既有文档的缺口对应：

- `docs/70-runbook.md` §12 的 **GAP-06**（无健康/备份指标与告警通道）：本卡交付脚本、指标、阈值与通道接口；
  但“在 staging 真实触发并留证”仍需在可执行环境完成（§10 第 5/8 项）。在该证据产生前，GAP-06 只能算**部分关闭**。
- `docs/71-backup-restore.md` §5 的 **B-03**（告警通道未内置）：本卡提供 `ALERT_CMD` 接入点与自测脚本；
  实际 webhook 地址由运维在 `monitoring.env` 配置，不进仓库。

---

## 12. 文件与命令速查

```bash
# 三个脚本
bash deploy/monitoring/health_probe.sh [--dry-run] [--reset] [--base-url URL]
bash deploy/monitoring/resource_alerts.sh [--dry-run] [--skip-freshness] [--skip-containers] [--disk-path PATH]
bash deploy/monitoring/slow_requests.sh [--dry-run] [--file PATH] [--since 24h] [--latency-ms 1000] [--top 10]

# 自测
bash deploy/monitoring/smoke_alert.sh [--keep]

# 退出码
# health_probe     : 0=健康 1=未达阈值 2=配置错误 3=已告警
# resource_alerts  : 0=通过 1=有告警 2=配置/依赖错误
# slow_requests    : 0=报告成功 1=读日志失败 2=配置错误
# smoke_alert      : 0=全部通过 1=有失败 2=环境/参数错误
```

配置与证据：

- 配置模板：`deploy/monitoring/monitoring.env.example` → `monitoring.env`（600，禁止提交）；
- 告警/状态/日报：`deploy/evidence/monitoring/`；
- 相关 runbook：`docs/70-runbook.md`（部署/回滚/日志）、`docs/71-backup-restore.md`（备份/恢复/演练）。
