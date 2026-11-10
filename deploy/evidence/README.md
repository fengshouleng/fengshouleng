# deploy/evidence/ — 发布证据留档

本目录存放**可复核的发布/冒烟/演练证据**，用于 G4/G5 门控与事后追溯。
由 `deploy/scripts/smoke.sh` 自动写入，也可手工追加。

## 命名规则

| 场景 | 文件名 | 生成方式 |
|---|---|---|
| staging 冒烟 | `rel-001-<env>-<UTC时间戳>.log` | `bash deploy/scripts/smoke.sh` |
| 发布记录 | `release-<env>-<UTC时间戳>.log` | 人工（`docker compose images` + `ps` + 冒烟输出） |
| 备份恢复演练 | `dr-<YYYYMMDD>-<NN>.log` | 人工（`docs/71-backup-restore.md` §4） |

示例：`rel-001-staging-20261109T031500Z.log`

## 每次冒烟至少包含

1. 时间（UTC）、`git rev-parse --short HEAD`、镜像 tag（`docker compose images`）
2. 健康检查输出（`/api/healthz`）
3. 登录 → `/auth/me` → 建题 → 列表回读 → 删除 的完整输出
4. 失败时的原始错误与 HTTP 状态码（不要只留“失败”二字）

## 保留与提交

- 日志保留 ≥ 90 天（G5 之后可归档压缩）；
- 是否入库由 R0 决定：若入库，**禁止**包含密码、Cookie、CSRF token、真实用户姓名等敏感值；
  当前约定为**仅保留本地/CI artifact**，不提交敏感日志。
- 本 README 之外的文件默认不进入版本库（发布时另行归档）。
