# deploy/release/ — 上线、回滚与演练脚本（TASK-REL-004）

四个文件，一条主线：**演练 → 检查单 → 生产上线 → （必要时）回滚**。

| 文件 | 作用 | 谁会跑 |
|---|---|---|
| `lib.sh` | 共用函数（compose 封装、健康重试、备份门禁、alembic、state、G4 门控）；被 source，不单独执行 | 其余三个脚本 |
| `deploy.sh` | 上线：构建 → 备份确认 → 迁移 → 滚动重启 → 健康 → 冒烟 → 写状态 | 执行人（production 需人工审批） |
| `rollback.sh` | 回滚：切上一镜像 + 迁移策略 + 健康 + 冒烟 + 15 分钟计时 | 执行人 / `deploy.sh` 失败时自动调用 |
| `staging_drill.sh` | 在 staging 完整演练「部署→冒烟→回滚→冒烟」，输出 JSON 证据 | 发布前（G4 证据） |

- 检查单：`docs/73-release-checklist.md`（G4 未通过不得上线；逐项勾选 + 签字）
- 故障分级/复盘：`docs/74-incident-template.md`
- Runbook 章节：`docs/70-runbook.md` §14

> 所有脚本 `set -euo pipefail`；不写、不打印任何真实口令；`ADMIN_USER`/`ADMIN_PASSWORD` 只从环境变量读取。

---

## 1. 安全门（脚本强制，不能靠自觉）

| 门 | 触发条件 | 行为 |
|---|---|---|
| G4 门控 | `--env production` | 必须 `--g4-report-id` + `--g4-report`；报告内要有该编号，且有一行 `G4: PASS`。否则 **exit 3** |
| 人工审批 | `--env production` | 必须显式 `--confirm-production`（由执行人在拿到审批后输入）。缺则 **exit 4** |
| 生产禁跳过 | `--env production` | `--skip-backup` / `--skip-smoke` 直接拒绝（exit 2） |
| 备份门禁 | 上线 | `backup.sh` → `freshness_check.sh` → 距现在 ≤ `--backup-max-age`（默认 60 分钟）；失败 exit 5，**不会**进入迁移 |
| 迁移校验 | 上线 | `alembic upgrade head` 后必须 `current == heads`，否则触发自动回滚 |
| 回滚目标 | 回滚 | 目标镜像必须本机存在；迁移降级必须 `--allow-downgrade` 且 `--sql` 预检通过，否则 **exit 3** + 恢复预案 |

`docs/73` §1 要求在 G4 报告（`docs/61-gate-report.md` 的 G4 区块）里**单起一行**写：

```text
G4: PASS
```

并确保报告内出现报告编号（如 `G4-20261109-01`）。这两项就是“脚本不自批”的机器兜底。

---

## 2. 快速用法

```bash
cd "$(git rev-parse --show-toplevel)"
export ADMIN_USER=admin
read -rsp 'ADMIN_PASSWORD: ' ADMIN_PASSWORD; echo
export ADMIN_PASSWORD

# ① staging 演练（产出 G4 证据；只允许 staging）
bash deploy/release/staging_drill.sh --env staging --g4-report-id G4-20261109-01

# ② staging 上线（日常验证）
bash deploy/release/deploy.sh --env staging

# ③ 生产上线（人工审批后）
bash deploy/release/deploy.sh --env production --confirm-production \
  --g4-report-id G4-20261109-01 \
  --g4-report docs/61-gate-report.md \
  --base-url https://exam.example.com \
  --approved-by "张三（人类 PO）"

# ④ 回滚（默认切回上一次成功发布的镜像）
bash deploy/release/rollback.sh --env production --confirm-production --reason "P1 发布后 502"

# ⑤ 只回滚代码、保留新库（默认行为，迁移向前兼容时）
#    与 ④ 相同，不需要额外参数

# ⑥ 需要迁移降级（仅当 docs/73 §3.5-B 已评审批准）
bash deploy/release/rollback.sh --env production --confirm-production --allow-downgrade --reason "..."
```

全部参数见 `--help`。常用覆盖：`--image-tag`、`--project`、`--env-file`、`--base-url`、`--health-retries`、`--health-interval`、`--backup-max-age`、`--git-pull`、`--no-auto-rollback`（演练用）。

---

## 3. 退出码（三个脚本统一）

| 码 | 含义 | 处置 |
|---|---|---|
| 0 | 成功（部署/回滚 + 健康 + 冒烟通过） | 按 `docs/73` §8 观察 24h |
| 1 | 失败（deploy 已尝试自动回滚；rollback 已切换但验证失败/不完整/超时） | 看证据日志末尾 `result=`；按 `docs/74` 定级 |
| 2 | 用法/配置错误（缺参数、环境文件缺失、compose 配置非法） | 修参数/环境文件，线上未变 |
| 3 | 拒绝执行（G4 未过 / 无回滚目标 / 目标镜像不存在 / 迁移不可机械回滚） | 不要绕过；按打印的恢复预案处理 |
| 4 | 生产确认缺失（`--confirm-production`） | 拿到人工审批后重跑 |
| 5 | 前置条件不满足（备份失败/不新鲜等） | 按 `docs/71` §2.4 修复后重跑 |

`--strict-time`（rollback）：耗时 > 15 分钟时返回 1（G4 判据，演练/验收用）。
`--no-smoke`（rollback）：跳过冒烟，**退出码仍为 1**（表示“已恢复但未完整验证”）。

---

## 4. 状态文件（回滚基线）

路径：`deploy/release/.state/<staging|production>.env`（运行期产物，**不要提交**）。

| 字段 | 含义 |
|---|---|
| `IMAGE_TAG` | 当前已部署且验证通过的镜像 tag |
| `PREV_IMAGE_TAG` | 上一次成功发布的 tag（手工回滚的默认目标） |
| `COMMIT` / `PREV_COMMIT` | 对应 commit |
| `MIGRATION_VERSION` / `PREV_MIGRATION_VERSION` | 对应 alembic 版本 |
| `BASE_URL` | 该环境的入口 URL（回滚冒烟用） |
| `RESULT` | `deployed` / `rolled_back` |
| `ROLLBACK_SECONDS` / `ROLLBACK_REASON` | 最近一次回滚的耗时与原因 |

写时机：`deploy.sh` **成功末尾**写一次；`rollback.sh` **成功末尾**写一次。失败时 deliberately 不写，保证“回滚点”始终是最后一个验证通过的版本。

---

## 5. 证据

| 文件 | 内容 |
|---|---|
| `deploy/evidence/release-<env>-<UTC>.log` | 发布全过程 + 发布记录（commit / 镜像 tag / 迁移前后版本 / 回滚点 / 备份 dump / 冒烟输出 / `result=`） |
| `deploy/evidence/release-<env>-<UTC>-migration.sql` | 迁移 dry-run SQL（`<current>:head`） |
| `deploy/evidence/release-<env>-<UTC>-images.txt` | `docker compose images` 指纹 |
| `deploy/evidence/rollback-<env>-<UTC>.log` | 回滚记录（`within_15min` / `db_action` / `verification` / 耗时） |
| `deploy/evidence/rollback-<env>-<UTC>-downgrade.sql` | 降级预检 SQL（仅 `--allow-downgrade`） |
| `deploy/evidence/release-drill-<env>-<UTC>.log` | 演练全过程 |
| `reports/release-drill-<UTC>.json` | 演练机器可读结论（G4 证据，见下） |

演练 JSON 关键字段：

```json
{
  "schema": "release-drill/1",
  "env": "staging",
  "g4": "G4-20261109-01",
  "steps": [{"name": "deploy", "seconds": 0, "result": "PASS", "exit_code": 0}],
  "deploy_seconds": 0,
  "smoke_seconds_total": 0,
  "rollback_seconds": 0,
  "rollback_limit_seconds": 900,
  "rollback_within_15min": true,
  "total_seconds": 0,
  "result": "PASS",
  "evidence_log": "deploy/evidence/release-drill-staging-*.log"
}
```

`g4` 必须等于 `docs/61-gate-report.md` 里的 G4 报告编号；`rollback_within_15min` 是“回滚 < 15 分钟”的机器判据。

---

## 6. 行为细节（排障时看）

1. **不重启 postgres/backup**：重启用 `up -d --no-deps --no-build --force-recreate api nginx`；`postgres`/`backup` 的容器与卷不动。
2. **迁移在 api 容器内执行**：`docker compose run -T --rm --no-deps -e RUN_MIGRATIONS=false api alembic ...`；容器入口也会迁移（`RUN_MIGRATIONS=true`），普通启动无需手工跑 alembic。
3. **健康判据**：`/api/healthz` 必须 `status=ok` 且 `db=ok`（契约 503 语义见 `docs/70` §4）；`/nginx-health` 必须是 `ok`。
4. **自动回滚**：迁移之后（已改动服务）任一步失败都会调 `rollback.sh --auto --to-tag <上一 IMAGE_TAG>`；门禁/备份/构建等**前置**失败（退出码 2/3/4/5，服务未改动）不回滚；无基线（首次部署）时不自动回滚，打印恢复预案。演练用 `--no-auto-rollback` 保留现场。
5. **回滚不猜迁移**：默认不降级；不同版本且无 `--allow-downgrade` 时保持数据库新版本并打印恢复预案（`docs/71` §3.2）。
6. **状态只在成功时更新**：避免把失败的 tag 当成下一次的回滚点。
7. **首次部署例外**：检测到“无 state 且无运行中的 api”时，先启动 postgres → 迁移建 schema → 基线备份 → 重启 api/nginx（空库 `pg_dump` 无可校验对象；已有数据的升级永远先备份再迁移）。
8. **失败自动回滚的触发边界**：只有退出码 1 且已开始改动服务（迁移/重启之后）才回滚；用法/门控/备份门禁失败（2/3/4/5）不回滚，线上未被改动。

---

## 7. 已知限制

| # | 限制 | 影响 | 缓解 |
|---|---|---|---|
| 1 | 单实例 api，重建期间数秒 502 | 上线有短暂中断 | 低峰窗口 + 公告（`docs/73` §6） |
| 2 | 默认不执行迁移降级 | 不可回滚迁移只能恢复备份（RTO < 30min） | `docs/73` §3.5 事前选型 + 评审；`--allow-downgrade` 仅评审后使用 |
| 3 | 演练不覆盖迁移降级 | 降级路径仅靠 `--sql` 预检 | 后续任务：构造两个含迁移的版本做专项演练 |
| 4 | 脚本依赖 Docker/curl/jq/git 与 `ADMIN_*` | CI 无 Docker 时无法跑 | 在 staging 宿主执行；CI 只做静态检查 |
| 5 | 本仓库当前执行环境 shell 不可用（0xC0000142） | 脚本**未实跑**（`tests.verified=false`） | 首次在可执行环境按 `docs/73` §0/§7 执行并回填证据 |

---

## 8. 运行前自检与排障

```bash
# 语法检查（不执行）
bash -n deploy/release/lib.sh
bash -n deploy/release/deploy.sh
bash -n deploy/release/rollback.sh
bash -n deploy/release/staging_drill.sh
```

| 现象 | 原因 | 处置 |
|---|---|---|
| `$'\r': command not found` / 奇怪的 `syntax error` | Windows 检出后脚本被转成 CRLF | `sed -i 's/\r$//' deploy/release/*.sh`（或 `dos2unix`）；提交保持 LF |
| deploy 退出 3 | G4 报告缺编号或 `G4: PASS` 行 | 在 G4 区块**单起一行**写 `G4: PASS`，编号与 `--g4-report-id` 一致 |
| deploy 退出 4 | 未提供 `--confirm-production` | 拿到人工审批后再输入（脚本不自批） |
| deploy 退出 5 | 备份失败/不新鲜（尚未改动线上） | 按 `docs/71` §2.4 修复后重跑 |
| 健康检查重试耗尽（exit 1） | api 未起/迁移失败/DB 不可用 | `docs/70` §9、`docs/72` §8；查看 `release-*.log` 最后 50 行 |
| rollback 退出 3 | 目标镜像不存在，或迁移不可机械回滚 | 在目标 commit 重建镜像；否则按 `docs/71` §3.2 恢复备份 |
| rollback 退出 1 且 `verification≠smoke_passed` | 服务已切回但未冒烟（缺 `ADMIN_*` 或 `--no-smoke`） | 补 `ADMIN_*` 后手工跑 `deploy/scripts/smoke.sh` |

---

## 9. 相关文档

- `docs/70-runbook.md` §14：上线与回滚（本目录脚本的 runbook 版）
- `docs/73-release-checklist.md`：上线前逐项检查 + 24h 观察 + 签字
- `docs/74-incident-template.md`：P0/P1/P2、止损、复盘
- `docs/71-backup-restore.md`：备份/恢复/演练（RTO < 30min）
- `docs/72-monitoring.md`：healthz/错误率/慢请求/备份新鲜度告警
