# 70 · 部署与运维 Runbook

- 版本：v1.0　作者：R6 Release（TASK-REL-001）　日期：2026-11-09
- 适用：`deploy/docker-compose.yml`（ADR-008 四服务：nginx / api / postgres / backup）
- 配套：`deploy/nginx.conf`、`deploy/Dockerfile.api`、`deploy/Dockerfile.web`、`deploy/.env.example`、`docs/71-backup-restore.md`
- 读者假设：有 Docker 基础、能读中文、**未参与本项目**。按 §1→§5 顺序执行即可完成首次部署与验收。

> 本文所有命令都在**仓库根目录**执行；`deploy/docker-compose.yml` 的构建上下文是仓库根，
> 因此不要 `cd deploy` 后再用相对路径引用 `server/` 或 `web/`。

---

## 0. 前置条件（缺一不可）

| 项 | 要求 | 检查命令 |
|---|---|---|
| Docker Engine | ≥ 24.0 | `docker version --format '{{.Server.Version}}'` |
| Docker Compose | v2（支持 `depends_on.condition`、`name:`） | `docker compose version` |
| 仓库 | 已检出本项目，含 `server/`、`web/`、`deploy/` | `ls deploy/docker-compose.yml` |
| 客户端工具 | `curl`、`jq`（解析登录响应）、`openssl`（生成密码） | `curl --version; jq --version` |
| 端口 | staging 默认 8080 空闲；prod 需 80/443 | `ss -ltnp \| grep -E ':(80\|443\|8080)\b'` |
| 磁盘 | 数据库 + 备份卷合计预留 ≥ 2×当前库大小 | `df -h` |
| 域名/证书 | 仅 TLS 场景需要（§8）；纯 HTTP 内网 staging 可跳过 | — |

---

## 1. 准备环境变量（一次）

```bash
# 1.1 从模板创建环境文件（staging / prod 各一份，均不入库）
cp deploy/.env.example deploy/.env.staging
chmod 600 deploy/.env.staging

# 1.2 生成强密码并写入（示例用 sed 就地替换；也可手工编辑）
SECRET="$(openssl rand -hex 24)"
sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=${SECRET}/" deploy/.env.staging

# 1.3 确认项目名与端口互不冲突
grep -E '^(COMPOSE_PROJECT_NAME|APP_ENV|NGINX_HTTP_PORT|SESSION_COOKIE_SECURE)=' deploy/.env.staging
```

要点：

- `COMPOSE_PROJECT_NAME` 决定容器/卷/网络前缀。**同一台机器上 staging 与 prod 必须不同**（例如 `exam-staging` / `exam-prod`），否则会互相覆盖容器。
- `POSTGRES_PASSWORD` 会被拼进 `DATABASE_URL`（`postgresql+psycopg://...`）。密码含 `@ : / % # ? &` 时必须 URL 编码，否则 api 连接串解析失败；最省事的做法是只使用 `[A-Za-z0-9]`（`openssl rand -hex` 已满足）。
- `SESSION_COOKIE_SECURE`：prod/TLS 必须 `true`；**仅当 staging 用纯 HTTP 冒烟时**临时改 `false`，否则浏览器/curl 不会回传 Secure Cookie，表现为“登录返回 200，随后请求 401”。冒烟结束必须改回 `true`。
- 不要提交 `deploy/.env.staging`、`deploy/.env.prod` 到版本库（模板只保留占位值）。

---

## 2. 部署前结构自检（不启动容器，30 秒）

```bash
# 2.1 语法与变量解析：缺变量、YAML 结构错误都会在此失败（退出码非 0）
docker compose -f deploy/docker-compose.yml --env-file deploy/.env.staging -p exam-staging config -q
echo "compose config OK (exit=$?)"

# 2.2 确认四服务与卷/网络齐备
docker compose -f deploy/docker-compose.yml --env-file deploy/.env.staging -p exam-staging config --services
# 期望输出（顺序不限）：postgres api nginx backup

docker compose -f deploy/docker-compose.yml --env-file deploy/.env.staging -p exam-staging config --volumes
# 期望：pgdata backups

# 2.3 抽查健康检查渲染结果（api 必须是 /healthz，postgres 必须是 pg_isready）
docker compose -f deploy/docker-compose.yml --env-file deploy/.env.staging -p exam-staging config \
  | grep -E 'test:|healthcheck:|condition:|pg_isready|/healthz'
```

`config -q` 失败时的常见原因：`.env.staging` 缺少 `POSTGRES_PASSWORD`/`POSTGRES_DB`（compose 用 `${VAR:?}` 强制），或 YAML 缩进用了 TAB（compose 只接受空格）。

---

## 3. 首次部署（staging）

```bash
# 3.1 构建镜像（api 与 web 都从仓库根构建；首次约 3–8 分钟）
docker compose -f deploy/docker-compose.yml --env-file deploy/.env.staging -p exam-staging build

# 3.2 启动（postgres 健康后才启动 api；api 健康后才启动 nginx）
docker compose -f deploy/docker-compose.yml --env-file deploy/.env.staging -p exam-staging up -d

# 3.3 观察启动顺序与迁移日志（api 入口会执行 alembic upgrade head）
docker compose -f deploy/docker-compose.yml --env-file deploy/.env.staging -p exam-staging ps
docker compose -f deploy/docker-compose.yml --env-file deploy/.env.staging -p exam-staging logs --tail=80 api

# 3.4 等待全部 healthy（最多约 2 分钟）
watch -n 3 "docker compose -f deploy/docker-compose.yml --env-file deploy/.env.staging -p exam-staging ps"
```

判定标准：

- `postgres`、`api`、`nginx` 三行 `STATUS` 出现 `(healthy)`；`backup` 为 `Up`（其 healthcheck 只校验备份卷可写）。
- `api` 日志含 `[entrypoint] 迁移完成` 与 `Uvicorn running on http://0.0.0.0:8000`。
- 任一服务 `restarting`：按 §9 查日志，不要反复 `up -d` 掩盖问题。

失败即停：`docker compose ... down` 只删容器不删卷；`down -v` 会删数据库卷，**首次部署前可接受，之后严禁**。

### 3.5 首次管理员账号（空库必需，已提供运维 CLI）

空库无法登录（`/api/auth/login` 需要已存在账号）。用 `server/app/cli.py` 提供的 `python -m app.cli` 创建首个 admin：

- 密码**只从 stdin 或环境变量**读取（**没有** `--password` 参数），不会进入 shell 历史或进程参数列表；
- 创建成功会同时写审计日志（`audit_logs`，actor 为空 = 系统操作）。

```bash
COMPOSE="docker compose -f deploy/docker-compose.yml --env-file deploy/.env.staging -p exam-staging"

# 方式 A（推荐）：密码走环境变量；exec 必须带 -T，否则 stdin/TTY 行为不一致
read -rsp '初始管理员密码（≥8 位，输入不回显）: ' ADMIN_PASSWORD; echo
$COMPOSE exec -T -e BOOTSTRAP_PASSWORD="$ADMIN_PASSWORD" api \
  python -m app.cli create-admin \
  --username admin --display-name 初始管理员 --password-env BOOTSTRAP_PASSWORD

# 方式 B（二选一，不要与方式 A 同时执行）：密码从 stdin 读入（同样需要 -T）
printf '%s\n' "$ADMIN_PASSWORD" | $COMPOSE exec -T api \
  python -m app.cli create-admin --username admin --display-name 初始管理员
unset ADMIN_PASSWORD

# 验证：应打印 alembic 版本、数据库连通性 ok、管理员数量 ≥ 1
$COMPOSE exec -T api python -m app.cli check
```

退出码：`0` 成功；`1` 数据库/自检失败；`2` 用法或前置条件不满足（用户名已存在、密码缺失或短于 8 位、用户名长度不在 3–64、显示名为空）。
`create-admin` **不是**“已存在即跳过”：重复执行会以 `2` 退出并打印“用户名已存在”，按此判断初始化是否已完成。

本地开发（容器外，工作目录 `server/`）：

```bash
python -m app.cli create-admin --username admin --display-name 管理员   # 交互输入密码
python -m app.cli seed-demo    # 仅空库；演示数据。staging/prod 拒绝使用开发默认密码
python -m app.cli check
```

> 旧版此处的一次性 heredoc 脚本已废弃（原 GAP-07 已关闭），统一走 CLI；`seed-demo` 只允许空库且幂等。

---

## 4. 健康检查与判据

四层探针，从内到外：

```bash
BASE="http://127.0.0.1:8080"   # 对应 .env 的 NGINX_HTTP_PORT
COMPOSE="docker compose -f deploy/docker-compose.yml --env-file deploy/.env.staging -p exam-staging"

# 4.1 nginx 自身存活（不经过 api）
curl -fsS "$BASE/nginx-health"; echo        # 期望：ok

# 4.2 应用健康检查（ADR-008 的 /healthz，经 nginx 反代到 api）
curl -fsS "$BASE/healthz"; echo             # 期望：{"status":"ok","version":"1.0.0","db":"ok"}

# 4.3 契约路径（OpenAPI servers: /api → /api/healthz）
curl -fsS "$BASE/api/healthz"; echo         # 期望同上

# 4.4 数据库可用（postgres 健康检查）
$COMPOSE exec -T postgres pg_isready -U "$(grep -E '^POSTGRES_USER=' deploy/.env.staging | cut -d= -f2)"

# 4.5 备份最近一次成功时间（文件由 backup.sh 成功后写入）
$COMPOSE exec -T backup cat /backups/.last_backup_ok
```

判据与含义：

| 现象 | 含义 | 处置 |
|---|---|---|
| `/nginx-health` 失败 | nginx 进程/配置问题 | `$COMPOSE logs nginx`，改配置后 `$COMPOSE restart nginx` |
| `/nginx-health` 正常、`/healthz` 502/504 | api 未启动或已崩溃 | `$COMPOSE ps api` + `logs api` |
| `/healthz` 503 | 契约定义：DB 不可用/迁移未就绪 | 查 postgres 健康与 api 迁移日志 |
| `api` 反复 unhealthy 但日志正常 | 探针超时/venv python 缺失 | `$COMPOSE exec api python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')"` |
| `backup` 无 `.last_backup_ok` | 首轮备份失败 | `$COMPOSE logs backup`，按 §9 排查 |

> **数据库探针已接入（GAP-02 已关闭）**：`/healthz` 每次请求执行 `SELECT 1`，正常返回
> `{"status":"ok","version":"1.0.0","db":"ok"}`；数据库不可用时返回 **503**（统一错误体
> `{code,message,request_id}`，`code=INTERNAL_ERROR`）。因此 `/healthz` 同时是存活探针与 DB 探针，
> `api` 在 DB 故障时会被标记 unhealthy。需要更细的自检（alembic 版本、管理员数量）用
> `$COMPOSE exec -T api python -m app.cli check`。

---

## 5. staging 冒烟（发布必跑，含证据留档）

前置：§3 完成、管理员已创建、`jq` 已安装。证据统一写入 `deploy/evidence/`（命名与保留规则见该目录 README）。

推荐一键执行（脚本会做完整断言并自动落证据，失败即非 0 退出）：

```bash
export ADMIN_USER=admin ADMIN_PASSWORD='至少8位，见 §3.5'
bash deploy/scripts/smoke.sh
# 可选覆盖：BASE=http://127.0.0.1:8080 ENV_FILE=deploy/.env.staging EVID_DIR=deploy/evidence
```

以下为脚本内的**等价命令**，用于逐条理解或手工复现（注意：手工执行时不会自动落证据）：

```bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

BASE="${BASE:-http://127.0.0.1:8080}"
COMPOSE="docker compose -f deploy/docker-compose.yml --env-file deploy/.env.staging -p exam-staging"
COOKIE="$(mktemp)"
EVID="deploy/evidence/rel-001-staging-$(date -u +%Y%m%dT%H%M%SZ).log"
mkdir -p deploy/evidence
exec > >(tee -a "$EVID") 2>&1

echo "== [1/6] 健康检查 =="
curl -fsS "$BASE/api/healthz"; echo

echo "== [2/6] 登录（拿 Cookie + CSRF）=="
: "${ADMIN_USER:?请先 export ADMIN_USER}"
: "${ADMIN_PASSWORD:?请先 export ADMIN_PASSWORD}"
LOGIN_JSON="$(curl -fsS -c "$COOKIE" -X POST "$BASE/api/auth/login" \
  -H 'Content-Type: application/json' \
  --data "{\"username\":\"${ADMIN_USER}\",\"password\":\"${ADMIN_PASSWORD}\"}")"
echo "$LOGIN_JSON" | jq -c '{user: .user.username, role: .user.role, csrf: (.csrf_token | length)}'
CSRF="$(printf '%s' "$LOGIN_JSON" | jq -r '.csrf_token')"
test -n "$CSRF" -a "$CSRF" != "null"

echo "== [3/6] 当前用户（验证 Cookie 生效、取 CSRF）=="
curl -fsS -b "$COOKIE" "$BASE/api/auth/me" | jq -c '{username: .user.username, role: .user.role}'

echo "== [4/6] 建题（写接口必须带 X-CSRF-Token）=="
Q_JSON="$(curl -fsS -b "$COOKIE" -X POST "$BASE/api/questions" \
  -H 'Content-Type: application/json' -H "X-CSRF-Token: ${CSRF}" --data @- <<'JSON'
{"type":"single","stem":"REL-001 冒烟题：1+1=?","options":[{"key":"A","text":"2"},{"key":"B","text":"3"}],"answer":["A"],"explanation":"基础算术","difficulty":1,"tags":["smoke"]}
JSON
)"
echo "$Q_JSON" | jq -c '{id, type, stem, points: .points}'
QID="$(printf '%s' "$Q_JSON" | jq -r '.id')"

echo "== [5/6] 列表回读（验证落库与分页）=="
curl -fsS -b "$COOKIE" "$BASE/api/questions?page=1&page_size=5" | jq -c '{total, ids: [.items[].id]}'

echo "== [6/6] 清理冒烟数据（软删除）=="
curl -fsS -o /dev/null -w 'delete status=%{http_code}\n' -b "$COOKIE" \
  -X DELETE "$BASE/api/questions/${QID}" -H "X-CSRF-Token: ${CSRF}"

echo "== 冒烟全部通过 =="
echo "证据文件：$EVID"
rm -f "$COOKIE"
```

判定：任一 `curl -f` 失败即中止，证据文件保留失败现场。

- 期望 `[4/6]` 返回 HTTP 201 且含 `id`（契约 `createQuestion`）。
- 若 `[4/6]` 返回 **403 `CSRF_INVALID`**：确认请求头名是 `X-CSRF-Token` 且值来自本次登录（会话绑定，换会话即失效）。
- 若 `[4/6]` 返回 **422**：`stem/explanation` 必填、单选恰好 1 个答案、`options` 至少 2 项（契约 v1.0.1）。
- 若 `[2/6]` 返回 **401**：账号不存在（先做 §3.5）或密码错（口径统一为“用户名或密码错误”）。
- 若 `[2/6]` 200 但 `[3/6]` 401：Cookie 未回传，检查 `SESSION_COOKIE_SECURE` 与是否 HTTPS。

证据留档要求（G4/G5 准入）：

1. 文件路径：`deploy/evidence/rel-001-<env>-<UTC>.log`（本命令自动生成）；
2. 附一行环境指纹：`$COMPOSE images` 输出（镜像 tag 必须可追溯）；
3. 失败时保留完整日志，并在任务卡/decision-log 登记失败结论与重跑结果。

---

## 6. 升级发布（staging 先跑，prod 需审批）

```bash
cd "$(git rev-parse --show-toplevel)"
COMPOSE="docker compose -f deploy/docker-compose.yml --env-file deploy/.env.staging -p exam-staging"

# 6.1 记录当前状态（回滚基线）
git rev-parse --short HEAD | tee /tmp/rel001-before.txt
$COMPOSE images | tee -a /tmp/rel001-before.txt

# 6.2 取新代码并自检 compose
git pull --ff-only
$COMPOSE config -q

# 6.3 构建新镜像（IMAGE_TAG 建议用 commit SHA 或版本号，便于回滚定位）
export IMAGE_TAG="$(git rev-parse --short HEAD)"
$COMPOSE build
$COMPOSE up -d

# 6.4 等待健康并跑冒烟（§5；脚本会自动落证据）
$COMPOSE ps
bash deploy/scripts/smoke.sh
```

要点：

- **迁移只能前进**：api 启动自动 `alembic upgrade head`。发布前确认新迁移对旧代码向后兼容（加列/加表可，改列语义/删列不可），否则回滚会失败。
- 单实例 api 在 `up -d` 时会有数秒中断（构建/重启期间 502）。若不可接受，先在 staging 验证维护窗口，prod 选低峰并公告。
- `backup` 服务不随业务变更重启，但 `up -d` 会重建它的容器（卷保留）。

### prod 发布检查单

- [ ] staging 冒烟（§5）通过且证据已归档
- [ ] 人工审批已记录（谁批准、何时、发布内容）——禁止自批自执行
- [ ] 已手动触发一次备份并验证 `.last_backup_ok` 时间在发布前 1 小时内
- [ ] 回滚目标镜像 tag 与 §7 步骤已确认
- [ ] 发布后 10 分钟内完成 §4 健康检查与 §5 冒烟

---

## 7. 回滚

### 7.1 代码/镜像回滚（无数据库变更时首选，通常 < 5 分钟）

```bash
cd "$(git rev-parse --show-toplevel)"
COMPOSE="docker compose -f deploy/docker-compose.yml --env-file deploy/.env.staging -p exam-staging"

# 7.1.1 回到上一个已验证 commit
git checkout <上一个good-commit>
export IMAGE_TAG=<该次发布的 tag 或新构建的 SHA>

# 7.1.2 用同一份 .env 重建并启动（不要 down -v）
$COMPOSE build api nginx
$COMPOSE up -d --no-deps api nginx

# 7.1.3 验证
$COMPOSE ps
curl -fsS http://127.0.0.1:8080/api/healthz; echo
bash deploy/scripts/smoke.sh
```

`--no-deps` 避免顺带重启 postgres；若本次发布改过 compose 本身，去掉 `--no-deps`。

### 7.2 数据库回滚（本次发布含不可逆迁移时）

迁移不可逆时**不要**执行 `alembic downgrade`（可能丢数据）。按 `docs/71-backup-restore.md` §3 从发布前备份恢复（以下沿用 §7.1 已定义的 `$COMPOSE`；在新终端执行时先重新定义）：

1. 停 api：`$COMPOSE stop api`
2. 恢复备份（restore.sh，需 `--yes`）
3. 启动 api（自动迁移到旧代码所需版本）
4. 冒烟并登记 RTO/RPO 实测值

### 7.3 回滚演练要求

每次 G4 前在 staging 演练一次并留档：commit、镜像 tag、开始/结束时间、耗时、冒烟结果（模板见 `docs/71-backup-restore.md` §4）。

---

## 8. 启用 TLS（prod 前必须）

1. 证书放宿主 `deploy/certs/fullchain.pem`、`deploy/certs/privkey.pem`（目录需自行创建，`chmod 600`，**不要提交**）。
2. `deploy/docker-compose.yml` 的 `nginx` 服务增加卷与端口映射：

   ```yaml
       volumes:
         - ./certs:/etc/nginx/certs:ro
       ports:
         - "${NGINX_HTTP_PORT:-8080}:80"
         - "${NGINX_HTTPS_PORT:-8443}:443"
   ```

3. `deploy/nginx.conf` 末尾取消 TLS `server` 块注释，替换 `server_name`，并将 80 端口的 server 改为 301 跳转 https。
4. `.env.*` 中 `SESSION_COOKIE_SECURE=true`（ADR-004 硬要求）。
5. 重建并验证：

   ```bash
   docker compose -f deploy/docker-compose.yml --env-file deploy/.env.prod -p exam-prod build nginx
   docker compose -f deploy/docker-compose.yml --env-file deploy/.env.prod -p exam-prod up -d nginx
   curl -fsS https://exam.example.com/api/healthz; echo
   ```

> 证书续期（certbot/ACME）不在 REL-001 范围；到期前 30 天必须有人处理，建议登记为运维日历项（REL-003）。

---

## 9. 日志与排障

```bash
COMPOSE="docker compose -f deploy/docker-compose.yml --env-file deploy/.env.staging -p exam-staging"

$COMPOSE logs -f --tail=200 api          # 应用日志（uvicorn + alembic）
$COMPOSE logs -f --tail=200 nginx        # 访问日志/错误日志（stdout）
$COMPOSE logs -f --tail=200 postgres     # 数据库启动/连接错误
$COMPOSE logs -f --tail=200 backup       # 备份成功/失败
$COMPOSE ps                              # 状态与健康
$COMPOSE exec api env | sort            # 确认注入的变量（会打印密码，注意终端录屏）
```

常见故障速查：

| 症状 | 最可能原因 | 处置 |
|---|---|---|
| api 启动即退出，日志 `sqlalchemy.exc.OperationalError` / `ModuleNotFoundError: psycopg` | 数据库未就绪或驱动缺失（镜像已内置 psycopg，需确认用的新镜像） | 确认 postgres healthy；`$COMPOSE build --no-cache api` |
| `alembic.util.exc.CommandError: Can't locate revision` | 数据库版本比代码新（从新版本回滚了代码） | 按 §7.2 恢复备份，勿强改版本表 |
| nginx 502 | api 未健康/被重启 | `$COMPOSE ps api`；`logs api` |
| nginx 413 | 上传 > 20 MiB（`client_max_body_size`） | 拆分导入文件；确需更大则改 nginx.conf 并重建 |
| nginx 504 | 导入/导出超过 60s | 看 api 日志是否慢查询；临时调 `proxy_read_timeout` |
| 登录 200 但后续 401 | Cookie Secure/HTTPS 不匹配 | §4 表；“SESSION_COOKIE_SECURE” |
| 写接口 403 `CSRF_INVALID` | 缺 `X-CSRF-Token` 或会话不匹配 | 用同一次登录返回的 token |
| `backup` 日志 `pg_dump 失败` | 密码/库名错、磁盘满 | 校验 `.env`、`$COMPOSE exec backup df -h /backups` |
| postgres 起不来 `database files are incompatible` | 卷来自不同 major 版本 | 用备份恢复（docs/71），**不要**删卷了事 |

一次性排障容器（不改状态）：

```bash
$COMPOSE run --rm --no-deps api sh          # 进出后即销毁；镜像内为非 root
# 数据库内直接用容器自身的 POSTGRES_* 变量，避免在宿主 shell 里再读 .env
$COMPOSE exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\dt"'
```

---

## 10. 停止 / 清理

```bash
COMPOSE="docker compose -f deploy/docker-compose.yml --env-file deploy/.env.staging -p exam-staging"
$COMPOSE stop                 # 停止，保留容器与卷
$COMPOSE down                 # 删除容器/网络，保留命名卷（pgdata、backups）
$COMPOSE down -v              # ⚠️ 同时删除数据库与备份卷：仅限一次性 staging 重装
```

`down -v` 前必须确认 `COMPOSE_PROJECT_NAME` 指向 staging 且备份已导出到宿主。

---

## 11. 目录与文件对照（排障时定位用）

| 路径 | 作用 |
|---|---|
| `deploy/docker-compose.yml` | 四服务拓扑、健康检查、卷与网络 |
| `deploy/.env.example` | 变量模板（占位，不含真实密钥） |
| `deploy/Dockerfile.api` | 后端多阶段镜像（python 3.12-slim，非 root） |
| `deploy/docker-entrypoint.sh` | 迁移 + uvicorn 启动 |
| `deploy/Dockerfile.web` | 前端构建 + nginx 运行镜像 |
| `deploy/nginx.conf` | 反代 `/api`、静态缓存、探针、TLS 占位 |
| `deploy/backup/backup.sh` | 单次 `pg_dump -Fc` + 保留 N 天 |
| `deploy/backup/loop.sh` | backup 容器主进程（周期执行） |
| `deploy/backup/restore.sh` | 恢复（带确认与校验） |
| `deploy/scripts/smoke.sh` | staging 冒烟（登录/建题/清理，自动落证据） |
| `deploy/evidence/` | 冒烟/演练证据（命名规则见该目录 README） |
| `server/pyproject.toml` | 后端依赖唯一来源；`alembic.ini`+`migrations/` 随镜像复制 |

---

## 12. 已知缺口与后续任务（未关闭项不要当成“已解决”）

| 编号 | 缺口 | 影响 | 状态 | 归属 / 证据 |
|---|---|---|---|---|
| GAP-01 | `server/pyproject.toml` 未声明 `psycopg`（Postgres 驱动），由 `Dockerfile.api` 临时补装 | 本地/CI 装 `.[dev]` 后无法连 Postgres；依赖声明与运行时不符 | ✅ **已关闭**（2026-11-09） | `server/pyproject.toml` 已声明 `psycopg[binary]>=3.2`；`Dockerfile.api` 的补装行现为冗余（清理属 deploy 其他卡的写作用域，非阻塞） |
| GAP-02 | `/healthz` 只报进程存活，不校验 DB（契约允许 503） | DB 故障时探针仍 200，容器不会被标记 unhealthy | ✅ **已关闭**（2026-11-09） | `server/app/api/health.py` 每次请求 `SELECT 1`，正常返回 `db:"ok"`，DB 不可用返回 503；§4.2 已同步 |
| GAP-03 | 仓库根无 `.dockerignore`（本卡写作用域仅 `deploy/**`） | 构建上下文可能包含 `node_modules/.git`，构建变慢 | ⏳ 未关闭 | REL-002 或 R0 |
| GAP-04 | `web/pnpm-lock.yaml` 缺失（P-004） | 前端镜像安装不可复现（`--no-frozen-lockfile`） | ⏳ 未关闭 | FE + REL-002 |
| GAP-05 | 备份仅落在本机命名卷，无异地/对象存储副本与告警 | 宿主磁盘损坏即同时丢库与备份 | ⏳ 未关闭 | REL-002 |
| GAP-06 | 无备份/健康指标与告警通道 | 故障靠人肉发现 | ⏳ 未关闭 | REL-003 |
| GAP-07 | 无首启种子/CLI，首个管理员靠 §3.5 一次性脚本 | 新环境初始化多一步手工操作 | ✅ **已关闭**（2026-11-09） | `server/app/cli.py`（`create-admin` / `seed-demo` / `check`）；§3.5 已改用 CLI |

---

## 13. 禁止事项（发布纪律）

- 禁止未经审批直接部署 production；禁止自批自执行。
- 禁止把 `.env.*`、证书、真实密钥提交到仓库（`.env.example` 只允许占位）。
- 禁止在 production 执行 `down -v`、`docker volume rm`、`alembic downgrade`（除审批后的恢复流程）。
- 禁止跳过 staging 直接在 prod 验证新 compose/Dockerfile 变更。
- 禁止用 `docker compose up` 覆盖正在运行的 prod 而不留回滚 tag。

---

## 14. 上线与回滚（TASK-REL-004）

> 本节是**正式发布入口**。§6/§7 的手工命令仍可用于排障与理解，但上线/回滚必须走本节脚本：
> 它们内置了备份门禁、迁移校验、健康重试、冒烟、自动回滚与证据落盘。
> 配套：`docs/73-release-checklist.md`（逐项检查单 + 签字）、`docs/74-incident-template.md`（P0/P1/P2 与复盘）、
> `deploy/release/README.md`（脚本速查）。

### 14.0 硬规则（先读）

1. **G4 未通过，禁止上线生产**。`deploy.sh --env production` 会校验 `--g4-report-id` 与报告内的机器判据行 `G4: PASS`，缺一即退出码 3。
2. **生产上线必须人工审批**（DSH approval = ask）：审批人、时间、范围记入 `docs/73` §9，执行人再输入 `--confirm-production`；脚本不自批（缺参数退出码 4）。
3. 生产禁止 `--skip-backup` / `--skip-smoke`（脚本直接拒绝）。
4. 口令只走环境变量：`export ADMIN_USER=... ADMIN_PASSWORD=...`，不写进 `.env`、不写进证据、不发聊天。
5. 所有命令在**仓库根目录**执行；脚本内部统一 `docker compose -f deploy/docker-compose.yml --env-file <env> -p <project>`，不要手工拼 `-p`。

### 14.1 上线一次做了什么（步骤固定，任一步失败即停）

```bash
# 查看帮助
bash deploy/release/deploy.sh --help
bash deploy/release/rollback.sh --help
bash deploy/release/staging_drill.sh --help
```

| # | 步骤 | 判据 | 证据 |
|---|---|---|---|
| 1 | 记录基线（state 中的 IMAGE_TAG / commit / 迁移版本） | 日志有 `回滚基线：...` | `deploy/evidence/release-<env>-<ts>.log` |
| 2 | 构建 `exam-api:<tag>` / `exam-web:<tag>` | `compose build` 退出 0 | 同上 |
| 3 | 备份确认（有 60 分钟内成功备份则复用，否则 `backup.sh` → `freshness_check.sh`） | 日志 `备份确认通过` | 同上；dump 文件名 |
| 4 | 迁移 dry-run（`alembic upgrade <cur>:head --sql`） | SQL 落盘（失败只告警，需人工审阅） | `release-<env>-<ts>-migration.sql` |
| 5 | `alembic upgrade head`（api 容器内） | `current == heads` | 日志迁移前后版本 |
| 6 | 滚动重启 api → nginx（`--no-deps`，不动 postgres/backup） | `compose ps` 正常 | 日志 |
| 7 | 健康检查（`/api/healthz` 需 `status=ok` 且 `db=ok`；`/nginx-health` = ok） | 重试内通过 | 日志 |
| 8 | 冒烟（`deploy/scripts/smoke.sh`：登录→建题→回读→清理） | 退出 0 | 冒烟自身证据 `deploy/evidence/rel-001-*.log` |
| 9 | 写状态（下次的回滚点）与发布记录 | `result=SUCCESS` | `deploy/release/.state/<env>.env` |

> **首次部署**（无 state 且无运行中的 api）：空库没有可保护的数据，顺序变为「启动 postgres → 迁移建 schema → 基线备份 → 重启 api/nginx」；日志会打印 `首次部署` 与 `基线备份`。已有数据的升级永远是「先备份、后迁移」。失败：迁移之后（已改动服务）任一步失败会自动执行 `rollback.sh` 切回上一 `IMAGE_TAG`，再以**非 0**退出；门禁/备份等**前置**失败（退出码 2/3/4/5）线上未被改动，不回滚。首次部署无基线时不自动回滚，只打印恢复预案。

### 14.2 staging 演练（G4 证据，必须先做）

```bash
cd "$(git rev-parse --show-toplevel)"
export ADMIN_USER=admin
read -rsp 'ADMIN_PASSWORD: ' ADMIN_PASSWORD; echo
export ADMIN_PASSWORD

bash deploy/release/staging_drill.sh --env staging --g4-report-id G4-<YYYYMMDD>-<NN>
rc=$?; echo "drill exit=$rc"
```

产出：

- `reports/release-drill-<UTC>.json`：`env / steps[]（name/seconds/result/exit_code） / deploy_seconds / rollback_seconds / rollback_within_15min / total_seconds / g4 / result`；
- `deploy/evidence/release-drill-staging-<UTC>.log`：完整过程；
- 两次冒烟各自写 `deploy/evidence/rel-001-staging-*.log`。

判据：`exit=0`、`"result":"PASS"`、`"rollback_within_15min":true`、`"g4"` 等于 G4 报告编号。
不满足时**不得**把演练数据填进 G4 报告；先按日志修问题再重跑（重跑会生成新 JSON，旧 JSON 保留作对比）。
只允许 `--env staging`：production 演练就是真实故障，必须走 `docs/74` 的人工流程。

**Windows（Git bash）执行注意**（2026-11-10 本机实测）：

```bash
export MSYS2_ARG_CONV_EXCL="/bin/sh;/backup"   # 否则容器 exec 的 /bin/sh 会被转成 C:/Program Files/Git/usr/bin/sh
# 需要 jq 1.7+（Git bash 不自带）；可下载 jq-windows-amd64.exe 放入 PATH
# 全新 staging 库必须先建管理员，否则两次冒烟都会 401：
docker compose -f deploy/docker-compose.yml --env-file deploy/.env.staging -p exam-staging exec -T \
  -e EXAM_ADMIN_PASSWORD="$ADMIN_PASSWORD" api \
  python -m app.cli create-admin --username "$ADMIN_USER" --display-name "演练管理员" --password-env EXAM_ADMIN_PASSWORD
```

演练会真实执行 `deploy.sh` → 冒烟 → `rollback.sh` → 冒烟；`finalize()` 只在这四步全 PASS 且回滚 ≤900s 时写 `result=PASS`（`DEF-002` 已修）。

说明：演练验证“镜像回滚 + 健康 + 冒烟 + 15 分钟”，**不验证迁移降级**（降级演练需要两个含迁移的版本，见 §14.4 选项 B；当前 V1 默认不允许降级）。
演练结束后 staging 会停在“回滚后的旧镜像”上（这是演练的预期终态）；要验证新代码，随后单独执行 `bash deploy/release/deploy.sh --env staging`。

### 14.3 生产上线（人工审批后）

```bash
cd "$(git rev-parse --show-toplevel)"
export ADMIN_USER=admin
read -rsp 'ADMIN_PASSWORD: ' ADMIN_PASSWORD; echo
export ADMIN_PASSWORD

bash deploy/release/deploy.sh --env production --confirm-production \
  --g4-report-id G4-20261109-01 \
  --g4-report docs/61-gate-report.md \
  --base-url https://exam.example.com \
  --approved-by "张三（人类 PO）"
rc=$?; unset ADMIN_PASSWORD; echo "deploy exit=$rc"
```

参数要点：

| 参数 | 说明 |
|---|---|
| `--env production` | 使用 `deploy/.env.prod` + 项目名 `exam-prod`（必须与文件内 `COMPOSE_PROJECT_NAME` 一致） |
| `--confirm-production` | 人工审批后的显式确认；缺省退出码 4 |
| `--g4-report-id` / `--g4-report` | G4 报告编号与文件；报告须含该编号 + 一行 `G4: PASS`，否则退出码 3 |
| `--base-url` | 生产必须 `https://`；不猜域名（staging 默认 `http://127.0.0.1:8080`） |
| `--image-tag` | 默认 `git short SHA`；用于回滚定位，建议保持默认 |
| `--approved-by` | 审批人；写入发布记录（缺省告警，需在 `docs/73` §9 补签） |
| `--git-pull` | 构建前 `git pull --ff-only`（生产慎用：先确认工作区干净） |

退出码：`0` 成功；`1` 失败（已尝试自动回滚）；`2` 用法/配置；`3` G4 拒绝；`4` 缺生产确认；`5` 前置不满足（备份等）。
证据：`deploy/evidence/release-production-<ts>.log`（含 `result=SUCCESS`、commit、镜像 tag、迁移前后版本、回滚点、备份 dump、冒烟输出）
与 `release-production-<ts>-images.txt`、`release-production-<ts>-migration.sql`。

### 14.4 回滚（目标 < 15 分钟）

```bash
# 默认：切回 state 的 PREV_IMAGE_TAG（上一次成功发布的镜像）
bash deploy/release/rollback.sh --env production --confirm-production --reason "P1 发布后 502"

# 指定目标（例如手工确认过的 tag）
bash deploy/release/rollback.sh --env production --confirm-production --to-tag <tag> --reason "..."

# 需要降级迁移（仅当 docs/73 §3.5-B 已评审批准）
bash deploy/release/rollback.sh --env production --confirm-production --allow-downgrade --reason "..."
```

迁移策略（默认最保守）：

| 选项 | 条件 | 行为 |
|---|---|---|
| A（默认） | 迁移向前兼容 | **不执行** `alembic downgrade`，数据库保持新版本，只回滚镜像 |
| B | 显式 `--allow-downgrade` 且 `alembic downgrade <cur>:<prev> --sql` 预检通过 | 停 api → 真实 `downgrade` → 切镜像 |
| C | 迁移不可机械回滚 / 未授权 | 拒绝 downgrade 并打印恢复预案：`docs/71` §3.2 从备份整库恢复（RTO < 30min） |

判定与证据：

- 退出码 `0` = 回滚完成且健康 + 冒烟通过；`1` = 已切换但验证失败/不完整（继续人工核）；`3` = 拒绝执行（无目标镜像/迁移不可回滚）；`4` = 缺生产确认。
- `deploy/evidence/rollback-production-<ts>.log` 末尾：`回滚耗时 <n>s（目标 < 900s）`、`within_15min=yes/no`、`db_action=...`、`verification=smoke_passed`。
- **15 分钟判据的证明**：staging 演练 JSON 的 `rollback_seconds` + `rollback_within_15min`；生产回滚日志的实测耗时。超过 900s 即判据不达标，必须进复盘（`docs/74`）并优化，不能用“服务已恢复”掩盖。
- 回滚后立即补一次备份（`... exec -T backup /bin/sh /backup/backup.sh`），避免“回滚后忘了备份”。

### 14.5 上线后 24 小时观察窗口

密集观察 0–2h（每 15 分钟），2–24h（每小时）。指标与阈值与 `docs/72` §4 一致：

| 指标 | 判据（越线动作） | 命令 | 频率 |
|---|---|---|---|
| `/api/healthz` | 连续 3 次非 200 或 `db!=ok` → P0/P1 | `bash deploy/monitoring/health_probe.sh; echo "exit=$?"`（3 = 已告警） | 每分钟（cron） |
| `/nginx-health` | 非 `ok` → 查 nginx | `curl -fsS https://<域名>/nginx-health` | 每分钟 |
| 5xx 错误率 | 15min >5% 关注；>20% → P1 | `... logs --since 15m nginx \| grep -cE '" 5[0-9][0-9] '`（`docs/72` §8.4） | 15 分钟 |
| 慢请求 | 单请求 >10s 立即查；>20 条/日立项 | `bash deploy/monitoring/slow_requests.sh --since 2h` | 每日 |
| 备份新鲜度 | >26h 无成功备份 → P1；`.alert_*` 存在即异常 | `... exec -T backup /bin/sh /backup/freshness_check.sh` | 每小时 |
| 容器状态 | 非 running / unhealthy / 反复重启 | `... ps` + `docker inspect`（`docs/72` §8.8） | 每 5 分钟 |
| 磁盘 | >85% 关注；>95% → P1 | `df -h`、`docker system df` | 每小时 |
| 业务抽检 | 登录/建题/回读通过 | `bash deploy/scripts/smoke.sh` | 30min、2h、24h |

观察记录与 24h 结论写进 `docs/73` §8 表格；结论同时回填 `docs/61-gate-report.md` 的 G5 区块。
触发 P0/P1 时按 `docs/74` 定级、止损（优先 §3.1 回滚）、复盘。

### 14.6 事故响应（引用 74）

- 分级、首 5 分钟止损清单、回滚/恢复/修复三条路、沟通模板、复盘模板：`docs/74-incident-template.md`。
- 发布后 30 分钟内出现 P0/P1 → **优先回滚**，不要在生产边修边看。
- 回滚被拒绝（退出码 3）或迁移不可回滚 → 转 `docs/71` §3.2 整库恢复，并在 `docs/74` 记录 RTO/RPO 与数据丢失范围。
- 事故记录文件：`deploy/evidence/incident-<YYYYMMDD>-<NN>.md`。

### 14.7 文件对照与已知限制

| 路径 | 作用 |
|---|---|
| `deploy/release/lib.sh` | 共用函数（compose/健康/备份门禁/alembic/状态/G4 门控） |
| `deploy/release/deploy.sh` | 上线（构建→备份→迁移→重启→健康→冒烟→状态） |
| `deploy/release/rollback.sh` | 回滚（切镜像 + 迁移策略 + 健康 + 冒烟 + 15 分钟计时） |
| `deploy/release/staging_drill.sh` | staging 演练（deploy→smoke→rollback→smoke，输出 JSON） |
| `deploy/release/.state/<env>.env` | 回滚基线（运行期产物，不入库） |
| `deploy/evidence/release-*.log` | 发布记录（commit/镜像 tag/迁移版本/回滚点/冒烟） |
| `deploy/evidence/rollback-*.log` | 回滚记录（含耗时与 `within_15min`） |
| `deploy/evidence/release-drill-*.log`、`reports/release-drill-*.json` | 演练证据（G4/G5） |

已知限制（V1 明确接受）：

1. 单实例 api：`up -d --force-recreate` 期间有数秒 502；生产选低峰窗口（`docs/73` §6）。
2. 迁移降级默认不执行：V1 只承诺“向前兼容 + 恢复备份”，`--allow-downgrade` 必须事前评审。
3. 演练不覆盖迁移降级：需要两个含迁移的版本，属后续任务；当前用 `--sql` 预检 + 恢复预案兜底。
4. 后台执行（drill/deploy）需要 Docker 环境与 `ADMIN_*`；本仓库当前执行环境 shell 不可用（0xC0000142），脚本已交付但**未实跑**（`tests.verified=false`），首次执行以真实输出为准。

## 15. 容量与并发配置（性能基线，PERF-001）

> 完整实测矩阵与复现手册：`docs/61-gate-report.md §10.2`、`docs/75-perf-baseline.md`。
> 一句话：**单 worker 部署不满足 100 并发**；worker 数要按核数配，连接池要按 worker 摊分，且**不要用 `synchronous_commit=off` 冒充达标**。

### 15.1 两个变量

| 变量 | 现象 | 结论 |
|---|---|---|
| `WEB_CONCURRENCY`（uvicorn worker 数） | 1 worker 时读路径 p95 596ms；4 worker 82ms、8 worker 62ms、12 worker 38ms | 读路径是 CPU 容量问题，随 worker 近似线性改善 |
| PostgreSQL 提交落盘（WAL fsync） | 100 并发开考 burst 的 p95：fsync=on 593→523ms（4→12 worker 几乎不变），`synchronous_commit=off` 后 217ms | 写尾/开场尾延迟由存储 fsync 主导；需在 Linux/SSD 复核，**生产必须保持 fsync=on** |

### 15.2 配置与连接预算（必须满足）

```
WEB_CONCURRENCY × (DB_POOL_SIZE + DB_MAX_OVERFLOW) < postgres max_connections（默认 100）
```

| 场景 | 建议值 |
|---|---|
| 4 vCPU staging | `WEB_CONCURRENCY=4`、`DB_POOL_SIZE=10`、`DB_MAX_OVERFLOW=10`（4×20=80） |
| 8+ 核 / 100 并发正式考场 | `WEB_CONCURRENCY=8`~`12`、`DB_POOL_SIZE=4`、`DB_MAX_OVERFLOW=4`（12×8=96） |
| 单 worker 排障/低负载 | `WEB_CONCURRENCY=1`（默认），池可回到 20/30 |

- 这些值都在 `deploy/.env.staging` / `.env.prod` 里配，compose 已支持同名变量透传；
- 调大 worker 后必须复查 `pg_stat_activity` 连接数，避免打满 `max_connections`（打满表现为请求排队或 `QueuePool timeout` 500）。

### 15.3 日志管道的坑（RISK-15）

`app/middleware/access_log.py` 用模块级锁 + 每行 `flush()` 写 stdout。**日志消费者慢时（PowerShell 管道、前台 `docker compose up`、慢 journald）会把所有请求串行化**：实测同一负载 p95 从 ~700ms 放大到 20–31s，并诱发 `QueuePool` 超时 500。

- 压测与生产一律 detached 启动（`compose up -d`），不要 `| tee`、不要前台跟踪；
- 需要长时间跟踪日志用 `docker compose logs -f` 另开会话，但**不要在压测期间把容器 stdout 接到慢管道**；
- json-file 驱动保留 `max-size/max-file` 限额（已配置）。

### 15.4 性能回归怎么跑

1. Linux/CI：`docs/75-perf-baseline.md §6`（compose 同网络 + k6）；
2. 本机无 Docker：`docs/75-perf-baseline.md §2–§4`（原生 PG + 原生 uvicorn + 同机 k6）；
3. 判据只看 k6 `summary.json`：两个场景 `p(95)<300ms`、`http_req_failed rate<0.001`；
4. 结论回填 `docs/61 §10.1` 与 `docs/decision-log.md`，不要在 CI 外手工改「通过」。


