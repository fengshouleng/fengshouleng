# 71 · 备份与恢复（策略 + 恢复步骤 + 恢复演练记录）

- 版本：v1.1（策略完整化 + 演练脚本；v1.0 为 REL-001 骨架）　作者：R6 Release（TASK-REL-002）　日期：2026-11-09
- 状态：**脚本已就绪（每日备份/校验/保留/异地开关/新鲜度告警/一键演练）；演练实跑待有 shell 的环境执行并回填 §4.2**（本卡执行环境 shell 不可用，见 §4.2 备注）
- 相关：`deploy/backup/backup.sh`、`deploy/backup/freshness_check.sh`、`deploy/backup/restore.sh`、`deploy/backup/restore_drill.sh`、`deploy/backup/loop.sh`、`deploy/cron/exam-backup-cron.sh`、`deploy/docker-compose.yml`（`backup` 服务）、`docs/70-runbook.md`
- 目标：RPO ≤ 24 小时（每日备份）、**RTO < 30 分钟**（G4 准入条件；> 30 分钟即 G4 不通过，见 §4.1）
- 读者假设：有 Docker 基础、未参与本项目；按 §2 → §3 → §4 顺序执行即可完成“备份核查 / 恢复 / 演练”。

> 所有命令都在**仓库根目录**执行。`$COMPOSE` 一律指
> `docker compose -f deploy/docker-compose.yml --env-file deploy/.env.staging -p exam-staging`，
> 生产改为 `.env.prod` + `-p exam-prod`。本文不出现任何真实口令。

---

## 1. 备份范围与不备份的内容

| 对象 | 是否备份 | 说明 |
|---|---|---|
| PostgreSQL 业务库（全部 schema/表） | ✅ 每日 `pg_dump -Fc` | 含用户、题库、试卷、考试、作答、成绩、审计 |
| 备份文件本身 | ✅ 命名卷 `backups`（容器内 `/backups`） | 与 `pgdata` **同一台宿主**，属单点（§5 B-01） |
| 备份校验文件 | ✅ 同上，同名 `.sha256` | 演练强制校验（缺失即拒绝）；整库恢复存在时必须匹配 |
| `deploy/.env.*`（含数据库密码） | ❌ 不自动备份 | 必须由运维单独存密码管理器；否则恢复后 api 连不上库 |
| `deploy/nginx.conf`、`deploy/certs/` | ❌ 不自动备份 | 由 Git 与证书签发流程管理 |
| 数据库 WAL / PITR | ❌ V1 不启用 | 只支持恢复到最近一次备份点（RPO ≤ 24h） |
| 上传的题库原始 Excel/CSV | ❌ 不入库不落地 | 导入即解析入库，源文件由使用者自留 |

> 结论：V1 的“可恢复”= 最近一次**通过校验**的 `*.dump` + 对应的 `.env` 密码。两者缺一不可。

---

## 2. 备份策略

### 2.1 参数（全部可用环境变量覆盖，见 §8）

| 参数 | 默认值 | 配置位置 | 说明 |
|---|---|---|---|
| 触发方式 | 容器启动即备一次，之后每 `BACKUP_INTERVAL_SECONDS` 秒一次 | `deploy/.env.*` | 默认 `86400`（每日）；定点 02:00 需改 compose，见 §7.3 |
| 备份工具 | `pg_dump --format=custom`（`-Fc`） | `backup.sh` | 压缩、可并行恢复、可 `pg_restore --list` 校验 |
| 一致性 | 单事务快照 | `pg_dump` 默认 | 备份期间业务可继续读写 |
| 输出路径 | `${BACKUP_DIR}/<db>_<UTC时间戳>.dump` | `BACKUP_DIR`（默认 `/backups`） | 例：`exam_20261109T020000Z.dump` |
| 原子性 | 先写 `.partial`，校验通过后 `mv` 改名 | `backup.sh` | 半截文件永远不会被当成有效备份 |
| 完整性校验 | 体积下限 → `pg_restore --list` → `sha256sum` 生成并**立即回验** | `backup.sh` / `lib.sh` | 任一失败 → 删除产物 + 告警 + 非 0 退出 |
| 最小体积阈值 | 1 KiB（`BACKUP_MIN_BYTES`） | `backup.sh` | 拦截 0 字节 / 空库 / 写盘失败 |
| 日备保留 | 7 天（`BACKUP_RETENTION_DAYS`） | `backup.sh` | 按 mtime 清理 `*.dump` + `.sha256` |
| 周备保留 | 4 份（`BACKUP_RETENTION_WEEKS`） | `backup.sh` | **只打标记不复制**，见 §2.2 |
| 周备标记日 | 周日（`BACKUP_WEEKLY_WEEKDAY=7`，ISO 周几） | `backup.sh` | UTC 周日成功备份后写 `.weekly/<base>` 标记 |
| 异地副本 | 关闭（`BACKUP_REMOTE_DIR` / `BACKUP_REMOTE_CMD` 均为空） | `backup.sh` | 未配置只打 NOTICE，不阻塞 |
| 新鲜度告警 | > 26h 无成功备份即告警 | `freshness_check.sh` | 写 `.alert_stale_backup` + 非 0 退出 |
| 失败语义 | 非 0 退出 + `[backup] ERROR/ALERT` 日志 + 告警文件 | `backup.sh` / `loop.sh` | 循环不因单次失败终止，但失败一定可见 |
| 成功标记 | `${BACKUP_DIR}/.last_backup_ok`（UTC 时间 + 文件名 + 字节数）与 `.last_backup_ok.epoch` | `backup.sh` | 新鲜度检查与发布检查单读取 |
| 日志 | `${BACKUP_DIR}/logs/backup-YYYYMMDD.log` | `BACKUP_LOG_DIR` | 按 UTC 日期滚动，容器 stdout 同步输出 |

**退出码约定**（`loop.sh` 与宿主机 cron 都据此判断）：

| 脚本 | 0 | 1 | 2 | 3 |
|---|---|---|---|---|
| `backup.sh` | 成功 | 本地备份或其校验失败 | 参数/环境错误（如保留天数为非数字） | 本地成功但异地副本失败 |
| `freshness_check.sh` | 新鲜 | 过期 / 从未成功 / 时钟异常 | 阈值配置非法 | — |
| `restore_drill.sh` | 演练通过且 G4 达标 | 演练失败或恢复 >30 分钟 | 参数/前置条件不满足 | — |

### 2.2 保留策略：7 日备 + 4 周备（周备不复制）

```
/backups/
  exam_20261101T020000Z.dump          日备（超过 BACKUP_RETENTION_DAYS=7 天后清理）
  exam_20261101T020000Z.dump.sha256
  exam_20261102T020000Z.dump          周日备份 → 同时被 .weekly/ 标记
  ...
  .weekly/exam_20261102T020000Z       标记文件（0 字节，仅表示“这份算周备”）
  .last_backup_ok / .last_backup_ok.epoch
  logs/backup-20261109.log
  .alert_backup_failed / .alert_stale_backup / .alert_remote_copy_failed   （存在即异常）
```

规则（`backup.sh` 每次成功后执行）：

1. **日备**：删除 mtime 超过 `BACKUP_RETENTION_DAYS`（默认 7）天的 `*.dump` 及其 `.sha256`；
   被 `.weekly/` 标记的文件**跳过**，不会因“超过 7 天”被删。
2. **周备**：UTC 周日（`date -u +%u` = 7）成功备份后写 `.weekly/<base>` 标记（不复制文件、不额外占空间）。
3. 标记数超过 `BACKUP_RETENTION_WEEKS`（默认 4）时，删除**最旧**的标记及其 `.dump`/`.sha256`，
   保证“周备滚存 4 周”；标记对应的 dump 被人工删除时，孤儿标记会被自动清理。
4. 预期磁盘占用上限 ≈ 7 份日备 + 4 份周备（重叠部分不重复计数），约 11 份 dump。

> 为什么不复制周备：`pg_dump -Fc` 已是压缩归档，复制一份只翻倍占盘却不提高可靠性；
> “标记”同样能保证“周日那份不被日备清理掉”，且清理逻辑可审计（`ls /backups/.weekly`）。

### 2.3 日常核查（每日 1 分钟，或交给宿主机 cron）

```bash
COMPOSE="docker compose -f deploy/docker-compose.yml --env-file deploy/.env.staging -p exam-staging"

# 最近一次成功备份（时间应在 26h 内）
$COMPOSE exec -T backup cat /backups/.last_backup_ok

# 新鲜度自检（不是“看一眼”，而是断言：失败即非 0）
$COMPOSE exec -T backup /bin/sh /backup/freshness_check.sh; echo "exit=$?"

# 备份清单与体积趋势（体积突降通常意味着数据异常/备份截断）
$COMPOSE exec -T backup ls -lh /backups

# 周备标记（应 ≤ 4 个）
$COMPOSE exec -T backup ls -1 /backups/.weekly

# 告警文件（存在即说明有未处理异常）
$COMPOSE exec -T backup sh -c 'ls -1 /backups/.alert_* 2>/dev/null || echo "无告警文件"'
```

期望：`freshness_check.sh` 退出 0；`.last_backup_ok` 时间在 26h 内；`*.dump` 数量 ≤ 11；
`.weekly` ≤ 4 项；无 `.alert_*` 文件。

宿主机侧（容器挂了也能发现）：

```bash
bash deploy/cron/exam-backup-cron.sh status
bash deploy/cron/exam-backup-cron.sh freshness; echo "exit=$?"
```

### 2.4 手工触发一次备份（发布前必做）

```bash
$COMPOSE exec -T backup /bin/sh /backup/backup.sh; echo "exit=$?"
$COMPOSE exec -T backup cat /backups/.last_backup_ok
```

退出码 0 = 已通过 `pg_restore --list` + sha256 双校验；非 0 时先看容器日志
（`$COMPOSE logs --tail=100 backup`）与 `/backups/logs/backup-<日期>.log`，**不要**带着失败继续发布。

### 2.5 异地副本（可选开关，默认关闭）

命名卷与数据库同盘，磁盘/宿主损坏会同时丢两者。三种方式（A/B 在容器内执行，C 在宿主机；默认都关闭）：

**方式 A · 目录副本（`BACKUP_REMOTE_DIR`）**——容器内执行，要求该路径已挂载到**不同物理盘/NAS**：

```bash
$COMPOSE exec -T -e BACKUP_REMOTE_DIR=/mnt/offsite backup /bin/sh /backup/backup.sh
# 成功日志：异地副本完成（并回验 sha256）：/mnt/offsite/exam_....dump
```

脚本行为：`cp` 到目标目录 → 在目标目录执行 `sha256sum -c` 回验 → 失败写 `.alert_remote_copy_failed` 并以 **exit 3** 结束
（本地备份仍然有效，因此不删除本地产物）。

**方式 B · 对象存储命令（`BACKUP_REMOTE_CMD`，占位接口）**——由命令自行上传，脚本只负责调用与判定退出码：

```bash
# 示例（占位，按实际对象存储替换；凭据走 CI/宿主 secret，绝不写进仓库）
export BACKUP_REMOTE_CMD='aws s3 cp "$BACKUP_FILE" "s3://exam-backup/$(date -u +%Y/%m/%d)/$BACKUP_BASE.dump" --only-show-errors'
$COMPOSE exec -T -e BACKUP_REMOTE_CMD="$BACKUP_REMOTE_CMD" backup /bin/sh /backup/backup.sh
```

命令可用环境变量：`BACKUP_FILE`（dump 绝对路径）、`BACKUP_SHA256_FILE`、`BACKUP_BASE`（不含扩展名）。
退出码 0 视为成功；非 0 → `.alert_remote_copy_failed` + `backup.sh` 以 exit 3 结束。

**方式 C · 宿主机导出（当前 compose 下唯一开箱可用，推荐）**——容器 `data` 网络是 `internal: true`（无出网）
且未挂载宿主异地目录，因此容器内方式 A/B 需先改 compose（§7.4）；宿主机侧用 `docker cp` 绕过网络限制：

```bash
# 复制最新 dump + sha256 到另一块盘，并校验
BACKUP_REMOTE_DIR=/mnt/backup-offsite bash deploy/cron/exam-backup-cron.sh export
```

> 未配置异地副本（`BACKUP_REMOTE_DIR` 与 `BACKUP_REMOTE_CMD` 都为空）时**不影响备份结论**：`backup.sh` 只打印
> `NOTICE 未配置异地副本（BACKUP_REMOTE_DIR / BACKUP_REMOTE_CMD 均为空）：本次仅本地备份，不阻塞`。

### 2.6 失败与告警（对接 TASK-REL-003）

脚本本身不绑定具体告警通道，只提供两个稳定接口：

1. **告警文件**（容器内，随备份卷保留）：
   - `.alert_backup_failed`：任何本地备份/校验失败
   - `.alert_remote_copy_failed`：本地成功但异地副本失败
   - `.alert_stale_backup`：>26h 无成功备份（新鲜度检查写入）
   - 文件内容为 `reason/time/host/database/message` 的 key=value，监控可 `cat` 后解析；成功备份会自动清除对应文件。
2. **告警命令**：设置 `BACKUP_ALERT_CMD` 后，每次告警执行 `sh -c "$BACKUP_ALERT_CMD"`，并从环境读取
   `BACKUP_ALERT_REASON`（`backup_failed|stale_backup|remote_copy_failed|drill_failed`）、
   `BACKUP_ALERT_MESSAGE`、`BACKUP_ALERT_FILE`、`BACKUP_DB_NAME`。告警通道自身失败只打 WARN，不改变结论。

REL-003 接入建议：在 `deploy/cron/README.md` 的 `HOST_ALERT_CMD`（宿主侧）或 compose 的 `BACKUP_ALERT_CMD`
（容器侧）填一条 webhook 命令即可，无需改本卡脚本。

---

## 3. 恢复步骤

### 3.1 前置检查（全部满足才继续）

- [ ] 已确认要恢复到的**时间点**（选哪一份 `*.dump`），并知悉该点之后的数据会丢失
- [ ] `deploy/.env.*` 的 `POSTGRES_USER/POSTGRES_PASSWORD` 与备份来源一致（否则恢复后应用连不上）
- [ ] 目标库不是 `postgres/template*`（`restore.sh` 会拒绝）
- [ ] staging 先演练；prod 恢复需人工审批并记录（谁批准、何时、原因）
- [ ] 磁盘剩余空间 ≥ 备份文件解压后体积的 2 倍（`df -h`）
- [ ] `$COMPOSE ps` 中 postgres/backup 为 healthy

### 3.2 整库恢复（staging 示例；prod 同流程但需审批）

```bash
COMPOSE="docker compose -f deploy/docker-compose.yml --env-file deploy/.env.staging -p exam-staging"

# 1) 停写：恢复期间禁止 api 连接（restore.sh 的 --clean 会 DROP 对象）
$COMPOSE stop api

# 2) 选备份（先看清单，再抄文件名）
$COMPOSE exec -T backup ls -1t /backups/*.dump | head -n 5

# 3) 执行恢复（必须显式确认；脚本会校验 sha256 + pg_restore --list，再 --clean --if-exists --exit-on-error）
$COMPOSE exec -T -e RESTORE_CONFIRM=yes backup \
  /bin/sh /backup/restore.sh /backups/<选定文件>.dump

# 4) 启动 api（入口自动 alembic upgrade head，把库升到当前代码所需版本）
$COMPOSE start api
$COMPOSE ps

# 5) 健康 + 冒烟并留证（docs/70-runbook.md §4、§5）
curl -fsS http://127.0.0.1:8080/api/healthz; echo     # 期望含 "db":"ok"
export ADMIN_USER=admin ADMIN_PASSWORD='<从密码管理器取，不要提交>'
bash deploy/scripts/smoke.sh
```

`restore.sh` 的既有行为（TASK-REL-001）：拒绝无 `--yes`/`RESTORE_CONFIRM=yes` 的调用；`.sha256` 存在时必须匹配
（缺失只告警并继续，整库恢复请自行确认来源可信）；`pg_restore --list` 验证归档；
`--clean --if-exists --exit-on-error` 恢复；失败非 0 退出并提示“库可能处于部分恢复状态”。
（`restore_drill.sh` 更严格：缺 `.sha256` 直接拒绝，见 §3.4。）

### 3.3 恢复到临时库（手工版，推荐用于“先看看数据对不对”）

```bash
DUMP="/backups/<选定文件>.dump"

# 1) 建临时库（用容器内 POSTGRES_USER，避免在宿主 shell 重复配置口令）
$COMPOSE exec -T postgres sh -c 'createdb -U "$POSTGRES_USER" exam_restore_check'

# 2) 恢复到临时库（覆盖 PGDATABASE；目标库必须已存在）
$COMPOSE exec -T -e PGDATABASE=exam_restore_check -e RESTORE_CONFIRM=yes \
  backup /bin/sh /backup/restore.sh "$DUMP"

# 3) 抽查关键表行数（按实际 schema 调整表名）
$COMPOSE exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d exam_restore_check \
  -c "select count(*) from users; select count(*) from questions; select count(*) from papers; \
      select count(*) from exams; select count(*) from exam_sessions;"'

# 4) 清理
$COMPOSE exec -T postgres sh -c 'dropdb -U "$POSTGRES_USER" exam_restore_check'
```

> 手工版只证明“备份可解析可恢复”。**正式演练请用 §3.4 的 `restore_drill.sh`**：
> 它把上面 4 步（选备份 → 校验 → 建临时库 → 恢复 → 行数比对 → 清理）做成一条命令，并自动计时。

### 3.4 一键恢复演练（`restore_drill.sh`，G4 证据来源）

```bash
# 默认：取最新备份 → 恢复到临时库 → 比对 5 张关键表 → 输出耗时与结论 → 删除临时库
$COMPOSE exec -T backup /bin/sh /backup/restore_drill.sh; echo "exit=$?"

# 指定备份点 / 保留临时库排查
$COMPOSE exec -T backup /bin/sh /backup/restore_drill.sh --dump /backups/exam_20261109T020000Z.dump
$COMPOSE exec -T backup /bin/sh /backup/restore_drill.sh --keep
```

脚本保证：

- **不碰生产库**：源库只执行 `SELECT count(*)`（5 张表），所有写入发生在临时库 `exam_restore_drill_<UTC时间戳>`；
  临时库名与源库相同则拒绝执行；源库是 `postgres/template*` 直接拒绝。
- 恢复前强制 `sha256` + `pg_restore --list` 双校验（校验不过就不建临时库）。
- 恢复到临时库用 `--single-transaction`：失败则临时库无半截数据。
- 结论落在输出与 `${BACKUP_DIR}/drill-logs/drill-<UTC时间戳>.log`，包含 `dump / restore_seconds / total_seconds / row_check / result / g4`。
- 默认删除临时库；`--keep` 或演练失败时保留，便于排查（输出里给出手工 `dropdb` 命令，口令不回显）。
- 退出码：0 通过且 G4 达标；1 失败或恢复 >30 分钟；2 参数/前置条件不满足。

宿主机入口（免记 compose 命令，等价）：

```bash
bash deploy/cron/exam-backup-cron.sh drill            # 每月 cron 也调它
bash deploy/cron/exam-backup-cron.sh drill --keep
```

### 3.5 恢复后必须做的事

1. `docs/70-runbook.md` §4 健康检查 + §5 冒烟全部通过（`/healthz` 现在包含 `"db":"ok"`）；
2. 记录实际 RTO（从停 api 到冒烟通过）与 RPO（备份时间戳 → 故障时刻）；
3. 在 `docs/decision-log.md` 登记事故/恢复结论（由 R0/Release 维护，本卡不改该文件）；
4. 确认 `backup` 服务恢复后仍能产出一份新备份（`$COMPOSE exec -T backup /bin/sh /backup/backup.sh`），
   避免“恢复后忘了备份”；
5. 把演练/恢复证据复制到 `deploy/evidence/dr-<YYYYMMDD>-<NN>.log`（命名规则见该目录 README）。

---

## 4. 恢复演练记录（G4 证据）

### 4.1 演练判据（先看判据，再填表）

| # | 判据 | 判定方式 |
|---|---|---|
| 1 | `restore_drill.sh` 退出码 0，无非预期报错 | `echo $?` |
| 2 | 关键表行数 `restored == source`，或 `restored < source` 且差额能由“备份点后有写入”解释 | 脚本 `row_check` 输出 |
| 3 | 恢复后 `alembic upgrade head` 成功、api 正常启动（整库恢复时必查；临时库演练可跳过） | `$COMPOSE ps` + §3.2 第 4 步 |
| 4 | `docs/70-runbook.md` §5 冒烟通过（整库恢复时必查） | `bash deploy/scripts/smoke.sh` |
| 5 | **恢复耗时 < 30 分钟（1800s）** | `restore_seconds`（G4 硬判据） |
| 6 | 备份 sha256 校验通过，且已有一份副本在宿主/异地 | 脚本输出 + §2.5 |
| 7 | 记录已回填 §4.2，偏差已建任务 | 本文档 |

> **>30 分钟即 G4 不通过**：`restore_drill.sh` 在 `restore_seconds > DRILL_RTO_LIMIT_SECONDS`（默认 1800s）时
> 直接 `result=FAIL / g4=fail` 并以退出码 1 结束，**不允许**以“数据是对的”绕过耗时判据。
> 若确因数据量增长导致超时，必须新建优化任务（并行恢复 `-j`、增量/WAL、更大 IO），并在下次演练前复测。

### 4.2 演练台账（每次演练追加一行）

| 编号 | 时间（UTC） | 备份点（dump） | 环境 | 恢复耗时 | 行数比对（source/restored） | 结论 | 执行人 |
|---|---|---|---|---|---|---|---|
| DR-20261109-01 | 待执行 | — | staging | — | — | **未执行**：本卡执行环境 shell 不可用（0xC0000142），脚本已就绪，需在可执行环境跑 `restore_drill.sh` 后回填 | R6 Release（脚本交付） |
| DR-20261110-01 | 2026-11-09T17:53:55Z | `exam_20261109T175350Z.dump`（33900B，mtime 17:53:50Z，TOC 90） | 演练用 PG16.4 容器 + 本地临时库 | **0s** | users=4/4、questions=3/3、papers=1/1、exams=0/0、exam_sessions=0/0 | **通过（RTO 达标；临时库已删）** | R0 Lead（脚本执行；人工复核待补） |

> 填表口径：
> - **时间**：演练开始时间（UTC，`date -u`）；**备份点**：`/backups/<name>.dump` 与文件 mtime；
> - **恢复耗时**：只填 `restore_seconds`（pg_restore 本体），不是总耗时；总耗时另记在证据日志；
> - **行数比对**：填 `users/questions/papers/exams/exam_sessions` 的 `source/restored`，有差额必须写原因；
> - **结论**：`通过（RTO 达标）` / `不通过（>30min）` / `不通过（数据不符）`；
> - **执行人**：真人姓名或工号 + 复核人（prod 演练必须有复核人）。

### 4.3 单次演练记录模板（长版，粘贴到 `deploy/evidence/dr-YYYYMMDD-NN.log` 头部）

```markdown
### 演练记录 DR-YYYYMMDD-NN

- 日期/时间（UTC）：
- 环境：staging / prod（演练禁止在 prod 做破坏性恢复）
- 执行人 / 复核人：
- 演练类型：□ 临时库校验（restore_drill.sh） □ 整库恢复（restore.sh） □ 回滚+恢复组合
- 代码版本（git commit）/ 镜像 tag：
- 备份文件：/backups/<name>.dump
- 备份时间（UTC）/ 大小 / sha256 校验结果：
- 开始时间 / 结束时间 / restore_seconds / total_seconds：
- 实测 RTO（停 api → 冒烟通过）：
- 实测 RPO（备份时间戳 → 故障假设时间）：
- 行数比对：users __/__　questions __/__　papers __/__　exams __/__　exam_sessions __/__
- 步骤是否与 §3 一致（若偏离，写明原因）：
- 冒烟结果：□ 通过 □ 失败（证据路径 deploy/evidence/...）
- 发现的问题与偏差：
- 后续任务（编号/负责人/截止）：
- 结论：□ 通过（RTO<30min，数据完整） □ 不通过（>30min 或数据不符，需重练）
```

---

## 5. 已知限制（V1 明确接受）

| 编号 | 限制 | 风险 | 缓解/归属 |
|---|---|---|---|
| B-01 | 备份与数据库同宿主同盘（命名卷） | 宿主/磁盘故障同时丢失两者 | §2.5 宿主机 `export` / 容器副本开关；异地机房副本需 REL-003 或宿主存储方案 |
| B-02 | 仅每日一次 → RPO 最长 24h | 最坏丢一天数据 | 关键操作前手工备份（§2.4）；增量/WAL 未启用（B-04） |
| B-03 | 告警通道未内置（仅告警文件 + 命令钩子） | 没人看文件时故障仍可能漏报 | §2.6 接口已留；REL-003 接入 webhook/邮件 |
| B-04 | 无 PITR（未归档 WAL） | 无法恢复到任意时间点 | 明确不承诺；如需要走 ADR |
| B-05 | `restore.sh` 不校验“api 已停” | 恢复期间写入会造成不一致 | §3.2 流程强制先 `stop api`；`restore_drill.sh` 走临时库不受影响 |
| B-06 | 未加密备份文件（卷内明文） | 宿主被入侵即数据泄露 | 卷权限 + 宿主加固；加密（age/gpg）未实现 |
| B-07 | 容器内异地副本受 `data` 网络 `internal: true` 限制 | 容器内无法直连对象存储 | 用宿主机 `export`（§2.5 方式 C）；若必须容器内上传，按 §7.4 改 compose |

---

## 6. 后续任务（不在本卡）

- [ ] REL-003：把 §2.6 的告警文件/`*_ALERT_CMD` 接到实际通道，并做一次“人为停掉 backup 容器 → 26h 内必须告警”的验证
- [ ] 备份文件加密与密钥管理（B-06）
- [ ] 备份体积/耗时趋势与容量规划（评估 `pg_restore -j` 并行恢复对 RTO 的收益）
- [ ] 异地机房级副本（当前只有“换盘/宿主目录”级，B-01）
- [ ] 若数据量增长导致恢复 >30min：见 §4.1 的硬判据，先建优化任务再复测

---

## 7. `docker-compose.yml` 的 `backup` 服务说明（现状与待改点）

### 7.1 现状（本卡未改 compose，写作用域仅 `deploy/backup/**`、`deploy/cron/**`）

| 项 | 现状 | 对本卡脚本的影响 |
|---|---|---|
| 镜像 | `postgres:16.4-alpine` | 与服务器同版本，`pg_dump/pg_restore/psql/createdb/dropdb` 均可用 |
| 入口 | `/bin/sh /backup/loop.sh` | 循环日备；每轮后调用 `freshness_check.sh` |
| 挂载 | `backups:/backups`（命名卷）+ `./backup:/backup:ro`（脚本只读挂载） | 新增脚本随仓库更新即可生效，无需重建镜像 |
| 环境 | 仅传 `PG*`、`BACKUP_DIR`、`BACKUP_RETENTION_DAYS`、`BACKUP_INTERVAL_SECONDS` | **新增变量未在 compose 里声明**：本卡脚本全部有默认值，可直接工作；要改默认值需按 §7.2 注入 |
| 网络 | 仅 `data`（`internal: true`，无出网） | 容器内无法访问对象存储（B-07） |
| 健康检查 | `test -w /backups` | 只代表卷可写，不代表备份新鲜；新鲜度看 §2.3 |
| 依赖 | `postgres` healthy + `api` healthy | 避免首轮把“空 schema”当有效存档 |

### 7.2 新增环境变量如何生效（三种方式，按侵入性排序）

1. **单次注入**（不改文件，最常用）：
   ```bash
   $COMPOSE exec -T -e BACKUP_RETENTION_WEEKS=8 backup /bin/sh /backup/backup.sh
   ```
2. **宿主机 cron 透传**：`deploy/cron/exam-backup-cron.sh` 会把 `BACKUP_*` / `DRILL_*` 用 `docker exec -e` 传进容器，
   适合把“节假日多留几份”这类临时策略挂在 cron 上。
3. **改进 compose 默认值**（需 REL-001/R0 授权，本卡不写）：在 `backup.environment` 增补
   ```yaml
       BACKUP_RETENTION_WEEKS: ${BACKUP_RETENTION_WEEKS:-4}
       BACKUP_WEEKLY_WEEKDAY: ${BACKUP_WEEKLY_WEEKDAY:-7}
       BACKUP_FRESHNESS_MAX_HOURS: ${BACKUP_FRESHNESS_MAX_HOURS:-26}
       BACKUP_REMOTE_DIR: ${BACKUP_REMOTE_DIR:-}
       BACKUP_REMOTE_CMD: ${BACKUP_REMOTE_CMD:-}
       BACKUP_ALERT_CMD: ${BACKUP_ALERT_CMD:-}
   ```
   并同步 `deploy/.env.example`。改完必须按 `docs/70-runbook.md` §2 跑 `docker compose config -q`
   再在 staging 验证一次备份（`exit=0` 且 `.last_backup_ok` 更新）。

### 7.3 若要求“每日 02:00 定点备份”

`loop.sh` 是“启动即备 + 固定间隔”，备份时刻取决于容器启动时间，**不是** cron 定点。二选一：

- **容器内定点**（需改 compose，本卡不写）：把 `backup` 服务的 entrypoint 换成
  `/bin/sh -c 'while :; do sleep $(( ... )); /bin/sh /backup/backup.sh; ...; done'`，或在镜像内装 cron；
  推荐做法是保留 `loop.sh`，把 `BACKUP_INTERVAL_SECONDS=86400` 并在每天 02:00（UTC）重启容器：
  `docker compose ... restart backup`（需宿主 cron 在 02:00 执行）。
- **宿主机定点**（不改 compose）：停用容器 loop（或把间隔设得很大），启用 `deploy/cron/exam-backup.cron`
  末行的 02:00 `backup`。**不要同时启用两套**，否则一天会备两份、且保留策略会更快触发。

### 7.4 若必须启用容器内异地副本（B-07）

需要同时满足（均属 compose/基础设施变更，本卡写作用域外）：

1. 挂载异地目录：`- ${BACKUP_REMOTE_DIR:?}:/offsite`，并设 `BACKUP_REMOTE_DIR=/offsite`；
2. 或给 `backup` 服务加一个有出网的网络（对象存储场景），把凭据以 secret/只读文件注入，
   并设 `BACKUP_REMOTE_CMD='...'`；
3. 改完在 staging 跑一次 `backup.sh`，确认日志出现“异地副本完成（并回验 sha256）”，
   再人为改名目标目录验证失败路径（应出现 `.alert_remote_copy_failed` 且退出码 3）。

---

## 8. 环境变量速查（脚本全部支持，均有安全默认值）

| 变量 | 默认 | 脚本 | 说明 |
|---|---|---|---|
| `PGHOST` `PGPORT` `PGDATABASE` `PGUSER` `PGPASSWORD` | 无（compose 注入） | 全部 | 连接信息；`PGPASSWORD` 不落盘、不打印 |
| `BACKUP_DIR` | `/backups` | backup/freshness/drill | 产物与标记目录 |
| `BACKUP_RETENTION_DAYS` | `7` | backup | 日备保留天数（0 = 不按天清理） |
| `BACKUP_RETENTION_WEEKS` | `4` | backup | 周备保留份数（0 = 不打周标记） |
| `BACKUP_WEEKLY_WEEKDAY` | `7` | backup | ISO 周几打周标记（7 = 周日） |
| `BACKUP_MIN_BYTES` | `1024` | backup | 体积下限 |
| `BACKUP_LOG_DIR` | `${BACKUP_DIR}/logs` | 全部 | 日志目录 |
| `BACKUP_REMOTE_DIR` | 空 | backup | 异地目录副本（失败 → exit 3） |
| `BACKUP_REMOTE_CMD` | 空 | backup | 对象存储命令（`$BACKUP_FILE` 等可用） |
| `BACKUP_ALERT_CMD` | 空 | 全部 | 告警命令（REL-003 接口） |
| `BACKUP_FRESHNESS_MAX_HOURS` | `26` | freshness | 新鲜度阈值 |
| `DRILL_DB_PREFIX` | `exam_restore_drill` | drill | 临时库前缀（`[a-z0-9_]`） |
| `DRILL_KEY_TABLES` | `users questions papers exams exam_sessions` | drill | 比对表清单 |
| `DRILL_RTO_LIMIT_SECONDS` | `1800` | drill | G4 阈值（30 分钟） |
| `DRILL_KEEP` | `no` | drill | 等价 `--keep` |
| `DRILL_LOG_DIR` | `${BACKUP_DIR}/drill-logs` | drill | 演练日志目录 |
