# deploy/cron/ — 备份的宿主机侧定时任务（TASK-REL-002 补充）

容器内的 `deploy/backup/loop.sh` 已经负责“每日备份 + 每次备份后自检”，但有两个它覆盖不到的场景：

1. **容器整体挂掉/被误停**：容器内的循环和自检一起停，没人会发现“备份已经停止”；
2. **演练与异地副本**：恢复演练需要临时库权限，异地导出需要宿主机磁盘/对象存储凭据，都不应常驻在数据库容器里。

因此本目录提供宿主机侧入口，由 cron 调用：

| 文件 | 作用 |
|---|---|
| `exam-backup-cron.sh` | 宿主机入口：`freshness` / `backup` / `drill` / `export` / `status` |
| `exam-backup.cron` | crontab 模板（每小时新鲜度检查、每周导出、每月演练） |

## 1. 安装

```bash
# 先手工验证（仓库根执行；staging 换成 .env.prod 即为生产）
bash deploy/cron/exam-backup-cron.sh status
bash deploy/cron/exam-backup-cron.sh freshness; echo "exit=$?"

# 安装 crontab（记得把模板里的 /opt/exam 改成真实路径）
crontab deploy/cron/exam-backup.cron
crontab -l | grep exam-backup
```

> `crontab deploy/cron/exam-backup.cron` 会**替换**当前用户的整个 crontab。
> 已有任务时改用 `crontab -e` 手工追加模板中的四行（含 `SHELL`/`PATH`/`MAILTO`）。

cron 的 PATH 很短，模板里已固定 `SHELL=/bin/bash`、`PATH=...`，并全部使用绝对路径。

## 2. 各子命令

| 命令 | 频率建议 | 行为 | 非 0 退出的含义 |
|---|---|---|---|
| `freshness` | 每小时 | 在 backup 容器内读 `/backups/.last_backup_ok`，>26h → 告警 | 备份过期或从未成功 |
| `backup` | 手工/发布前 | 立即备份一次（含校验、保留策略、异地开关） | 备份失败（见容器日志） |
| `drill` | 每月 | 恢复到临时库、比对 5 张关键表、测耗时 | 演练失败或恢复 >30 分钟（G4 不通过） |
| `export` | 每周 | `docker cp` 最新 `.dump` + `.sha256` 到宿主机目录并校验 | 导出/校验失败 |
| `status` | 排障 | 最近成功备份、清单、告警文件、新鲜度 | 容器未运行 |

## 3. 环境变量（默认值不写死任何密钥）

| 变量 | 默认 | 说明 |
|---|---|---|
| `COMPOSE_FILE` | `deploy/docker-compose.yml` | compose 文件绝对路径 |
| `ENV_FILE` | `deploy/.env.staging` | 变量文件；**密码只在 compose 侧使用**，本目录脚本不读取、不回显 |
| `COMPOSE_PROJECT_NAME` | 从 `ENV_FILE` 读 | staging/prod 必须不同 |
| `LOG_DIR` | `deploy/evidence/cron` | 宿主机日志与告警文件 |
| `BACKUP_REMOTE_DIR` | `deploy/evidence/offsite` | `export` 目标目录（建议指向另一块盘/NAS） |
| `EXPORT_RETENTION_DAYS` | `7` | `export` 目标目录按天清理 |
| `HOST_ALERT_CMD` | 空 | 告警命令（REL-003 接口），从 `HOST_ALERT_REASON`/`HOST_ALERT_MESSAGE`/`HOST_ALERT_FILE` 读上下文 |
| `BACKUP_*` / `DRILL_*` | 见 `deploy/backup/*.sh` | 设置后由 `docker exec -e` 透传进容器 |

## 4. 已知边界（诚实说明）

- **容器内无法直接做异地副本**：compose 的 `data` 网络是 `internal: true`（无出网），且没有挂载宿主异地目录。
  因此本卡的 `BACKUP_REMOTE_DIR` / `BACKUP_REMOTE_CMD` 默认只在容器内生效需先改 compose（挂载目录或临时加网络）。
  当前可用的“换盘副本”路径是宿主机 `export` 子命令（`docker cp` + sha256 校验），不依赖容器网络。
- **02:00 定点备份**：`loop.sh` 是“启动后按间隔”而非 cron 定点，`BACKUP_INTERVAL_SECONDS=86400` 时备份时刻
  取决于容器启动时间。需要严格 02:00 时，见 `docs/71-backup-restore.md` §7 的 compose 改法（本卡写作用域不含 compose）。
- cron 的 `MAILTO` 默认置空，避免把容器日志当邮件发；告警请走 `HOST_ALERT_CMD`。
