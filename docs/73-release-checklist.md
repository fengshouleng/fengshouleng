# 73 · 生产上线检查单（G4 通过后执行）

- 版本：v1.0　作者：R6 Release（TASK-REL-004）　日期：2026-11-09
- 状态：**可执行检查单 + 演练脚本已交付；staging 演练与本单的勾选需在可执行环境完成**（本卡执行环境 shell 不可用，`tests.verified=false`）
- 适用：`deploy/release/deploy.sh`（上线）、`deploy/release/rollback.sh`（回滚）、`deploy/release/staging_drill.sh`（演练）
- 配套：`docs/70-runbook.md` §14（上线与回滚）、`docs/71-backup-restore.md`（备份/恢复）、`docs/72-monitoring.md`（监控/告警）、`docs/74-incident-template.md`（故障分级/复盘）、`docs/61-gate-report.md`（门报告模板）
- 读者假设：有 Docker 基础、能读中文、**未参与本项目**。按 §1 → §8 顺序执行；§9 签字后本单即为发布记录的一部分。

> **硬规则（先读再动手）**
>
> 1. **G4 未通过，禁止上线生产**。G4 报告必须已归档、结论 `PASS`，且报告内有报告编号与单独一行 `G4: PASS`（`deploy.sh --env production` 会机器校验这两项，缺一即拒绝，退出码 3）。
> 2. **生产上线必须人工审批**（DSH approval = ask）。执行人必须拿到人类 PO/Lead 的批准，再自己输入 `--confirm-production`；**脚本不自批、不代签**（缺该参数退出码 4）。
> 3. 本单任一检查项为 ✗ → **NO-GO**。不要用“先上线再补”绕过；把缺口写成任务/缺陷再重排窗口。
> 4. 本单不含任何真实口令：`ADMIN_PASSWORD`、数据库口令只从环境变量/密码管理器读取，禁止粘贴进本单、证据日志或聊天记录。
> 5. 所有命令都在**仓库根目录**执行；staging 用 `deploy/.env.staging` + `-p exam-staging`，生产用 `deploy/.env.prod` + `-p exam-prod`（脚本已封装，不要手工拼）。

---

## 0. 一页速查（老手路径，仍需逐项勾选 §1–§6）

```bash
# 0.1 演练（staging，产出 G4 证据；先 export ADMIN_USER/ADMIN_PASSWORD）
bash deploy/release/staging_drill.sh --env staging --g4-report-id G4-20261109-01

# 0.2 预检：确认 G4 报告、备份、回滚 tag 三项（对应 §1–§5）
ls -l reports/release-drill-*.json deploy/evidence/release-drill-*.log
grep -nE 'G4: PASS|G4-2026' docs/61-gate-report.md

# 0.3 生产上线（人工审批后，由执行人输入 --confirm-production）
bash deploy/release/deploy.sh --env production --confirm-production \
  --g4-report-id G4-20261109-01 \
  --g4-report docs/61-gate-report.md \
  --base-url https://exam.example.com \
  --approved-by "张三（人类 PO）"

# 0.4 上线后 24h 观察（§8）
bash deploy/monitoring/health_probe.sh; echo "exit=$?"
```

判定：`0.1` 退出码 0 且 JSON 中 `"result":"PASS"`、`"rollback_within_15min":true`；`0.3` 退出码 0 且证据日志含 `result=SUCCESS`。

---

## 1. 门控与授权（不过此项直接停）

| # | 检查项 | 判据 | 证据 / 命令 | ☐ |
|---|---|---|---|---|
| 1.1 | G4 报告已归档且结论 PASS | 报告内有 `G4-YYYYMMDD-NN` 编号；有单独一行 `G4: PASS` | `grep -nE '^[[:space:]]*G4:[[:space:]]*PASS[[:space:]]*$' docs/61-gate-report.md` | ☐ |
| 1.2 | G4 报告 §1 第 8/9 行（备份恢复/回滚演练）已回填实测 | 有命令、退出码、耗时、日志路径；回滚耗时 < 15min | 报告 `reports/gate-g4/rollback.md`（或本单 §5 引用的 drill JSON） | ☐ |
| 1.3 | 演练数据与 G4 报告编号挂钩 | `reports/release-drill-<UTC>.json` 的 `g4` 字段 == 本次 G4 报告编号，`result=PASS` | `jq '{g4,result,rollback_seconds,rollback_within_15min,total_seconds}' reports/release-drill-*.json` | ☐ |
| 1.4 | 人工审批已记录 | 审批人姓名/角色、时间、批准范围（commit + 镜像 tag）、approval 记录链接 | 本单 §9 签字位 + DSH approval 记录；`deploy.sh --approved-by` 写入发布日志 | ☐ |
| 1.5 | 发布内容明确 | commit、镜像 tag、变更摘要、关联任务/缺陷单 | `git rev-parse --short HEAD`；`deploy/evidence/release-production-*.log` | ☐ |
| 1.6 | 本次不是“首次生产部署”或首次部署已有人工方案 | 首次部署无回滚镜像 → 自动回滚不可用，必须另行批准 | 见 §5.1；`cat deploy/release/.state/production.env`（首次不存在） | ☐ |
| 1.7 | 维护窗口与公告已发出 | 低峰窗口、预计中断时长、回滚决策人 | 公告链接/截图，记录到 §9 | ☐ |

> `staging_drill.sh` 生成的 JSON 是本单 1.3 与 §5 的唯一机器证据；**没有它不得签字**。
> JSON 字段：`env / steps[].name / steps[].seconds / steps[].result / rollback_seconds / rollback_within_15min / total_seconds / g4 / result`。

---

## 2. 备份与恢复（缺一不可）

| # | 检查项 | 判据 | 证据 / 命令 | ☐ |
|---|---|---|---|---|
| 2.1 | 发布前 60 分钟内有一份成功备份 | `backup.sh` 退出 0，`.last_backup_ok` 时间在 60 分钟内（有新鲜备份则复用，否则 `deploy.sh` 自动触发一次并校验） | `bash deploy/release/deploy.sh` 会自动执行；手工核验见下行 | ☐ |
| 2.2 | 备份新鲜度检查通过 | `freshness_check.sh` 退出 0（阈值 26h） | `docker compose -f deploy/docker-compose.yml --env-file deploy/.env.prod -p exam-prod exec -T backup /bin/sh /backup/freshness_check.sh; echo "exit=$?"` | ☐ |
| 2.3 | 备份恢复演练通过（G4 准入） | `restore_drill.sh` 退出 0；`g4=pass`；恢复 < 30min | `docs/71` §4.2 台账；证据 `deploy/evidence/dr-*.log` | ☐ |
| 2.4 | 备份点已记录（可追溯） | dump 文件名、大小、sha256 前 12 位、UTC 时间 | `... exec -T backup ls -lh /backups/*.dump \| tail -n 3`；`... exec -T backup cat /backups/.last_backup_ok` | ☐ |
| 2.5 | 恢复所需口令可用 | `deploy/.env.prod` 的 `POSTGRES_USER/PASSWORD` 能从密码管理器取到 | 密码管理器条目（不在本单写值） | ☐ |
| 2.6 | 磁盘余量足够 | 宿主剩余 ≥ 2× 当前库大小；备份卷同盘 | `df -h`；`... exec -T backup du -sh /backups` | ☐ |
| 2.7 | 恢复预案已知悉 | 执行人能复述 `docs/71` §3.2 的 4 步（停 api → restore.sh → 启 api → 冒烟） | 本单 §9 复核人确认 | ☐ |

> **禁止**跳过备份直接迁移（`deploy.sh` 在 production 拒绝 `--skip-backup`）。备份失败时按 `docs/71` §2.4 排查后重跑，不要带着失败继续。
>
> **首次部署例外**：`deploy.sh` 检测到“无 state 且无运行中的 api”时会先启动 postgres、迁移建 schema，再做**迁移后基线备份**——空库的 `pg_dump` 没有可校验对象，迁移前备份没有意义（`deploy.sh` 日志会打印 `首次部署` 与 `基线备份`）。该例外只适用于空库；已有数据的升级一律“先备份、后迁移”。

---

## 3. 数据库迁移（dry-run 必看，回滚策略必选）

| # | 检查项 | 判据 | 证据 / 命令 | ☐ |
|---|---|---|---|---|
| 3.1 | 记录升级前版本 | `alembic current` 有值（或空库 = 无版本） | `... run -T --rm --no-deps -e RUN_MIGRATIONS=false api alembic current` | ☐ |
| 3.2 | 记录目标版本 | `alembic heads` 只有 1 个 head（多 head 会失败） | `... run -T --rm --no-deps -e RUN_MIGRATIONS=false api alembic heads` | ☐ |
| 3.3 | dry-run SQL 已生成并人工审阅 | 无 `DROP TABLE/DROP COLUMN/NOT NULL 无默认值` 等破坏性变更；有则必须 §3.5 评审 | `deploy/evidence/release-production-<ts>-migration.sql`（deploy.sh 自动生成） | ☐ |
| 3.4 | 向前兼容结论 | 本次迁移对**旧代码**兼容（加表/加可空列/加默认值 = 可；改列语义/删列/重命名 = 不可） | 在本行写结论：`可 / 不可（原因）`：__________ | ☐ |
| 3.5 | 迁移可回滚性结论 + 授权 | 二选一：<br>A. 向前兼容，回滚不降级（默认，推荐）<br>B. 需降级：已用 `alembic downgrade <cur>:<prev> --sql` 预检成功，且经评审批准 `--allow-downgrade`<br>C. 不可回滚：已批准“恢复备份”预案 | 预检 SQL：`deploy/evidence/rollback-production-*-downgrade.sql`（仅 B 场景）；评审记录链接：__________ | ☐ |
| 3.6 | 迁移由脚本执行（不手工 psql） | `deploy.sh` 在 api 容器内执行 `alembic upgrade head` 并校验 `current == heads` | `deploy/evidence/release-production-*.log` 中 `alembic current（升级后）` 行 | ☐ |

**选型说明（写进 §9 结论）**

- **A（默认）**：本次迁移只增不改 → 回滚只切镜像，数据库保持新版本；`rollback.sh` 默认即此策略（不会自动 downgrade）。
- **B（需评审）**：必须能机械回滚，且接受降级可能丢数据；执行时显式 `--allow-downgrade`。
- **C（不可回滚）**：唯一恢复路径是 `docs/71` §3.2 整库恢复（RTO < 30min，RPO = 备份点）；必须事先批准并接受数据丢失范围。

---

## 4. 域名 / 证书 / TLS（生产必须）

| # | 检查项 | 判据 | 证据 / 命令 | ☐ |
|---|---|---|---|---|
| 4.1 | DNS 解析正确 | 域名 A/AAAA 指向本机；`dig +short <域名>` 与预期一致 | `dig +short exam.example.com` | ☐ |
| 4.2 | 证书有效期 > 30 天 | `notAfter` 距今 ≥ 30 天 | `openssl x509 -in deploy/certs/fullchain.pem -noout -enddate` | ☐ |
| 4.3 | 证书链与私钥匹配 | 公钥指纹一致 | `openssl x509 -noout -pubkey -in deploy/certs/fullchain.pem \| openssl sha256`；对私钥重复 | ☐ |
| 4.4 | TLS 已启用且 HTTP 跳转 | `https://<域名>/api/healthz` 200；80 → 301 | `curl -fsS https://exam.example.com/api/healthz`；`curl -sI http://exam.example.com \| head -n 1` | ☐ |
| 4.5 | Cookie 安全属性 | `SESSION_COOKIE_SECURE=true` | `grep -E '^SESSION_COOKIE_SECURE=' deploy/.env.prod` | ☐ |
| 4.6 | 防火墙/端口 | 80/443 对公网开放，8000/5432 不暴露 | `ss -ltnp \| grep -E ':(80\|443\|8000\|5432)\b'` | ☐ |
| 4.7 | 证书续期责任人 | 到期前 30 天有人处理（日历项） | 记录到 §6 联系人表 | ☐ |

---

## 5. 回滚准备（15 分钟判据）

| # | 检查项 | 判据 | 证据 / 命令 | ☐ |
|---|---|---|---|---|
| 5.1 | 回滚镜像 tag 已确定且本机存在 | `exam-api:<tag>` 与 `exam-web:<tag>` 都能 `docker image inspect` | `cat deploy/release/.state/production.env`（`IMAGE_TAG=PREV` 见 `PREV_IMAGE_TAG`） | ☐ |
| 5.2 | 回滚点 commit 已记录 | state 的 `COMMIT`/`PREV_COMMIT` 与实际一致 | 同上 | ☐ |
| 5.3 | 回滚脚本演练过 | staging 演练 `rollback` 步骤 PASS；`rollback_seconds <= 900` | `jq '.steps[] | select(.name=="rollback")' reports/release-drill-*.json` | ☐ |
| 5.4 | 回滚决策人与触发条件明确 | 谁有权说“回滚”、什么现象触发（见 §8 阈值） | 本单 §9 + `docs/74` §1 分级表 | ☐ |
| 5.5 | 迁移回滚策略已选 | 与 §3.5 的 A/B/C 一致 | 本单 §3.5 | ☐ |
| 5.6 | 回滚命令已抄好 | 执行人能直接粘贴（见下方模板） | 终端历史/本单 | ☐ |
| 5.7 | 回滚后验证手段可用 | `ADMIN_USER/ADMIN_PASSWORD` 可用（冒烟要登录） | `echo "${ADMIN_USER:-未设置}"`（不打印密码） | ☐ |

**15 分钟判据怎么证明（必须能指出来）**

1. 演练 JSON：`rollback_seconds` 是 `rollback.sh` 从开始到「健康 + 冒烟通过」的实测秒数；`rollback_within_15min=true` 才算达标。
2. 生产实测：`deploy/evidence/rollback-production-<ts>.log` 末尾 `回滚耗时 <n>s（目标 < 900s）`；超过 900s 即 G4 判据不达标，需在复盘登记并优化（这不是“可以接受”的偏差）。
3. 回滚不包含“从备份整库恢复”：走 C 方案时 RTO 判据是 30 分钟（`docs/71` §4.1），不要混用两个数字。

```bash
# 回滚命令模板（生产；把 <原因> 换成事故/变更编号）
bash deploy/release/rollback.sh --env production --confirm-production \
  --reason "<事故编号/原因>"

# 需要降级迁移时（仅 §3.5-B，已评审）
bash deploy/release/rollback.sh --env production --confirm-production \
  --allow-downgrade --reason "<事故编号/原因>"
```

---

## 6. 联系人与窗口

| 角色 | 姓名/工号 | 联系方式 | 备份联系人 | ☐ |
|---|---|---|---|---|
| 发布执行人（值班 SRE/Release） |  |  |  | ☐ |
| 复核人（第二双眼睛） |  |  |  | ☐ |
| 审批人（人类 PO/Lead） |  |  |  | ☐ |
| 数据库/备份负责人 |  |  |  | ☐ |
| 业务公告/客服接口人 |  |  |  | ☐ |

| 项 | 值 | ☐ |
|---|---|---|
| 维护窗口（本地时间/UTC） |  | ☐ |
| 预计中断 | 单实例 api 重建期间约 5–30s 502（`docs/70` §6） | ☐ |
| 回滚决策截止时间 | 上线后 30 分钟内出现问题优先回滚，不做“边修边看” | ☐ |
| 观察窗口 | 上线后 24h（密集观察 0–2h，见 §8） | ☐ |

---

## 7. 执行（命令与退出码）

```bash
cd "$(git rev-parse --show-toplevel)"
export ADMIN_USER=admin
read -rsp 'ADMIN_PASSWORD（不回显、不落盘）: ' ADMIN_PASSWORD; echo
export ADMIN_PASSWORD

# 生产上线（人工审批后；脚本会：构建 → 备份确认 → 迁移 → 重启 → 健康 → 冒烟）
bash deploy/release/deploy.sh --env production --confirm-production \
  --g4-report-id G4-20261109-01 \
  --g4-report docs/61-gate-report.md \
  --base-url https://exam.example.com \
  --approved-by "张三（人类 PO）"
rc=$?; unset ADMIN_PASSWORD
echo "deploy exit=$rc"
```

| 退出码 | 含义 | 处置 |
|---|---|---|
| 0 | 成功（部署 + 健康 + 冒烟通过） | 进入 §8 观察 |
| 1 | 失败（已改动服务时已自动回滚；构建/前置阶段失败则线上未改动） | 看证据日志末尾 `result=` 与 `step=`；按 `docs/74` 定级并复盘 |
| 2 | 用法/配置错误 | 按报错修参数/环境文件，不改变线上 |
| 3 | 拒绝执行（G4 未通过/报告缺 `G4: PASS`） | 回到 §1，**不得**绕过 |
| 4 | 缺 `--confirm-production` | 拿到人工审批后重跑 |
| 5 | 前置不满足（备份失败/不新鲜） | 按 `docs/71` §2.4 修复后重跑 |

证据与记录：

- 发布日志：`deploy/evidence/release-production-<UTC>.log`（含 commit、镜像 tag、迁移前后版本、回滚点、备份 dump、冒烟输出、`result=`）；
- 镜像指纹：`deploy/evidence/release-production-<UTC>-images.txt`；
- 迁移 dry-run SQL：`deploy/evidence/release-production-<UTC>-migration.sql`；
- 状态（下次回滚基线）：`deploy/release/.state/production.env`（运行期产物，勿提交）。

---

## 8. 上线后 24 小时观察窗口

密集观察 0–2h（每 15 分钟），之后 2–24h（每小时）。任一项越线 → 按 `docs/74` 定级；发布后 30 分钟内出现 P0/P1 现象 → **优先回滚**（§5），不要先改代码。

| # | 指标 | 判据（越线即动作） | 采集命令 | 频率 | 责任人 |
|---|---|---|---|---|---|
| 8.1 | `/api/healthz` | 连续 3 次非 200 或 `db!=ok` → P0/P1 | `bash deploy/monitoring/health_probe.sh; echo "exit=$?"`（exit 3 = 已告警） | 每分钟（cron） | 值班 |
| 8.2 | `/nginx-health` | 非 `ok` → 先查 nginx | `curl -fsS https://<域名>/nginx-health` | 每分钟 | 值班 |
| 8.3 | 5xx 错误率 | 15 分钟 >5% 关注；>20% → P1 | `docker compose ... logs --since 15m nginx \| grep -cE '" 5[0-9][0-9] '`（见 `docs/72` §8.4） | 15 分钟 | 值班 |
| 8.4 | 慢请求 | 单请求 >10s 立即查；>20 条/日或单路径 >10 条/日 立项 | `bash deploy/monitoring/slow_requests.sh --since 2h` | 每日 | 值班/BE |
| 8.5 | 备份新鲜度 | `>26h` 无成功备份 → P1；`.alert_*` 存在即未处理 | `... exec -T backup /bin/sh /backup/freshness_check.sh` | 每小时 | 备份负责人 |
| 8.6 | 容器状态 | 非 running / unhealthy / 反复重启 | `docker compose ... ps`；`docker inspect`（`docs/72` §8.8） | 每 5 分钟 | 值班 |
| 8.7 | 磁盘 | >85% 关注，>95% → P1 | `df -h`；`docker system df` | 每小时 | 值班 |
| 8.8 | 业务抽检 | 登录 → 建题 → 列表回读 通过 | `bash deploy/scripts/smoke.sh`（复用 §7 的 `ADMIN_*`） | 上线后 30 分钟、2h、24h | 执行人 |

观察记录（每条一行）：

| 时间（UTC） | 指标 | 实测值 | 结论（正常/关注/越线） | 处置/工单 | 记录人 |
|---|---|---|---|---|---|
|  |  |  |  |  |  |

**24h 结束判定**：8.1–8.8 全部正常、无未关闭 P0/P1、备份连续成功 ≥ 2 次 → 在本单 §9 写「观察通过」，把结论回填 `docs/61-gate-report.md` 的 G5 区块。

---

## 9. 签字（发布记录，随证据归档）

| 项 | 值 |
|---|---|
| G4 报告编号 | `G4-__________` |
| G4 报告路径 / sha256 前 12 位 | `docs/61-gate-report.md` / `__________` |
| 演练 JSON 路径（`g4` 字段须等于上方编号） | `reports/release-drill-__________.json` |
| 发布 commit | `__________` |
| 发布镜像 tag | `exam-api:__________` / `exam-web:__________` |
| 回滚点（commit / 镜像 tag / 迁移版本） | `__________ / __________ / __________` |
| 迁移策略（A 向前兼容 / B 降级 / C 恢复备份） | `____` |
| 开始时间 / 结束时间（本地 + UTC） | `__________` |
| 上线结论 | `GO / NO-GO` |
| 24h 观察结论 | `通过 / 不通过（原因）` |

| 角色 | 姓名 | 结论 | 签字 | 时间 |
|---|---|---|---|---|
| 执行人（Release/SRE） |  |  |  |  |
| 复核人（第二双眼睛） |  |  |  |  |
| 审批人（人类 PO/Lead，G4/G5 审批） |  |  |  |  |
| 备份/恢复负责人（确认 §2） |  |  |  |  |

> 签字即表示：已按本单逐项核对、证据路径可复核、G4 已通过、人工审批已完成。
> 若中途变更发布内容（换 commit/镜像 tag/迁移策略），本单作废重签，并在 `docs/decision-log.md` 登记原因。
