# 74 · 故障响应与复盘模板（P0 / P1 / P2）

- 版本：v1.0　作者：R6 Release（TASK-REL-004）　日期：2026-11-09
- 适用：生产（`exam-prod`）故障与发布事故；staging 演练失败也按本文定级（不对外公告）
- 配套：`docs/73-release-checklist.md`（上线/回滚前检查）、`docs/70-runbook.md` §14（上线与回滚）、`docs/71-backup-restore.md`（恢复）、`docs/72-monitoring.md`（告警与阈值）
- 用法：**复制 §5 模板**为 `deploy/evidence/incident-<YYYYMMDD>-<NN>.md`（或事故系统），边处理边填；P0/P1 必须在恢复后 48 小时内出复盘。
- 原则：**先止损、后定位、再复盘**。不要在故障中改代码；不要删日志/删卷/伪造备份标记。

> 记录纪律：时间统一写 UTC（括号内可注本地时间）；每条命令记退出码；截图/日志放 `deploy/evidence/`；口令、Cookie、CSRF token、真实用户答案**禁止**写入事故记录。

---

## 1. 故障分级（先定级，再决定动不动生产）

| 级别 | 判据（满足任一） | 典型现象 | 确认时限 | 升级时限 | 通知对象 | 是否回滚 |
|---|---|---|---|---|---|---|
| **P0** | 全站不可用 > 5 分钟；数据丢失/损坏；数据泄露或越权；数据库不可用 | `/api/healthz` 持续 5xx/超时；登录全军覆没；库连不上；备份卷损坏 | 5 分钟 | 15 分钟 | 人类 PO + R0/Lead + 备份负责人；对外公告 | 是（优先） |
| **P1** | 核心链路（登录/答题/交卷/成绩）不可用或错误率 > 20%；单环境整体降级；备份 > 48h | 教师无法建题；学生交卷 500；5xx 15 分钟 > 20%；磁盘 > 95% | 15 分钟 | 30 分钟 | R0/Lead + 模块负责人 | 是（30 分钟内未定位即回滚） |
| **P2** | 局部功能异常/性能退化；5xx 5–20%；备份 26–48h；容器 unhealthy/反复重启；单次告警已自愈 | 导入偶发失败；慢请求增多；某个容器重启一次 | 1 小时 | 4 小时 | 模块负责人（BE/FE） | 视情况（可先观察） |

补充规则：

- **发布关联**：上线后 30 分钟内出现的 P0/P1，默认按“发布引入”处理 → **优先回滚**（§3.1），回滚后再定位；不要在生产上边修边看。
- **单机构、1–2 人项目**：升级对象就是人类 PO、R0/Lead、备份负责人；没有 24×7 值班台时，P0/P1 必须由 `ALERT_CMD` 推到手机（见 `docs/72` §9.1）。
- **与 `docs/72` §9.1 的映射**（两套编号并存时以影响面为准，不按字面）：

  | 本模板 | `docs/72` §9.1 | 说明 |
  |---|---|---|
  | P0 | P1（健康/DB/磁盘 >95%/备份 >48h） | 全站或数据级 → 本模板按 P0 处理 |
  | P1 | P2（磁盘 >85%/备份 26–48h/5xx >20%/容器 unhealthy） | 核心链路受损 → P1 |
  | P2 | P3（5xx 5–20%/慢请求/单次已自愈） | 局部降级 → P2 |

- 定级可以**升级**（P2→P1）但不要为了省事降级；降级必须写理由并由复核人确认。

---

## 2. 首 5 分钟止损清单（按顺序，边做边记时间）

| # | 动作 | 命令/判据 | 记录 |
|---|---|---|---|
| 2.1 | 确认时间与影响面 | 记下发现时间（UTC）、谁发现、影响谁（教师/学生/全部） | 时间线第 1 行 |
| 2.2 | 保留现场 | `docker compose -f deploy/docker-compose.yml --env-file deploy/.env.prod -p exam-prod logs --since 30m > deploy/evidence/incident-logs-$(date -u +%Y%m%dT%H%M%SZ).log 2>&1` | 日志路径 |
| 2.3 | 分层定位（60 秒） | `/nginx-health` → `/api/healthz` → `compose ps` → `logs api`（`docs/72` §8.1） | 每步结果 |
| 2.4 | 决定止损方式 | 回滚镜像（§3.1）/ 恢复备份（§3.2）/ 修配置（§3.3） | 决策 + 决策人 |
| 2.5 | 对外公告（P0/P1） | 用 §4 模板，给出“已知影响 + 预计下次更新” | 公告链接 |
| 2.6 | 升级 | 按 §1 时限通知对应角色；P0 同时通知备份负责人 | 通知记录 |
| 2.7 | 建事故记录 | 复制 §5 模板到 `deploy/evidence/incident-<YYYYMMDD>-<NN>.md` | 文件路径 |

**禁止动作**（做了会扩大事故）：

- 在生产执行 `docker compose down -v`、`docker volume rm`、删 `pgdata`/`backups` 卷；
- 未经评审执行 `alembic downgrade` / 手工改 `alembic_version` 表；
- 删除日志、删除告警文件、伪造 `.last_backup_ok`；
- 同时改多个变量/多个服务（无法归因）；先只做一件事并记录。

---

## 3. 止损决策（三条路，先选一条）

### 3.1 回滚镜像（发布引入的问题首选，目标 < 15 分钟）

适用：发布后 30 分钟内出现 P0/P1；错误来自新代码/新镜像；数据库迁移**向前兼容**（`docs/73` §3.5-A）。

```bash
# 生产回滚（人工审批后由执行人输入 --confirm-production）
bash deploy/release/rollback.sh --env production --confirm-production \
  --reason "P1 发布后 502（incident-YYYYMMDD-NN）"
rc=$?
echo "rollback exit=$rc"
```

判据：退出码 0；`deploy/evidence/rollback-production-<ts>.log` 含 `within_15min=yes` 与 `verification=smoke_passed`。
退出码 1 = 已切回但验证失败/不完整（继续人工核）；3 = 拒绝（无目标镜像/迁移不可回滚）→ 转 §3.2。

> 不要为了“快”而跳过冒烟：`rollback.sh` 的健康检查与冒烟就是“止损是否成功”的判据。

### 3.2 恢复备份（数据库损坏/迁移不可回滚时）

适用：`alembic` 报 `Can't locate revision`；迁移写坏数据；`rollback.sh` 判定迁移不可机械回滚且业务不可用。

按 `docs/71-backup-restore.md` §3.2 执行（**先停 api 再恢复**）：

```bash
COMPOSE="docker compose -f deploy/docker-compose.yml --env-file deploy/.env.prod -p exam-prod"
$COMPOSE stop api
$COMPOSE exec -T backup ls -1t /backups/*.dump | head -n 5      # 选备份点
$COMPOSE exec -T -e RESTORE_CONFIRM=yes backup /bin/sh /backup/restore.sh /backups/<选定的>.dump
$COMPOSE start api
curl -fsS https://exam.example.com/api/healthz; echo
bash deploy/scripts/smoke.sh
```

判据：RTO < 30 分钟（`docs/71` §4.1）；恢复后必须补一次新备份并记录 RTO/RPO 到 `docs/71` §4.2。**恢复会丢失备份点之后的数据**，必须先向审批人说明丢失范围。

### 3.3 修复配置/证书/资源（不改代码、不改数据）

适用：证书过期、磁盘满、nginx 配置错误、容器 OOM。修复后必须重跑 `/api/healthz` + `/nginx-health` + 冒烟三件套，并在时间线记录“改了什么、谁批的”。

---

## 4. 沟通模板（对外公告 / 对内同步）

**对外（P0/P1，30 分钟内首发）**

```text
【故障通告】考试系统访问异常（P1）
- 影响：<教师/学生/全部>，<登录/答题/交卷/成绩> 不可用或变慢
- 开始时间：YYYY-MM-DD HH:mm（UTC+8）
- 当前状态：已定位/处理中；已采取 <回滚/恢复/扩容> 措施
- 预计下次更新：HH:mm
- 临时建议：<暂缓交卷/改用纸笔/稍后重试>
```

**对内（每次更新时间线，不写猜测当结论）**

```text
[HH:mmZ] 现象=<探针/错误率/用户反馈> 判据=<命令与结果> 动作=<做了什么> 结果=<退出码/现象变化> 下一步=<谁在做什么>
```

---

## 5. 复盘模板（复制本段填写）

```markdown
# 事故复盘 INC-YYYYMMDD-NN

## 1. 基本信息
- 事故编号：INC-YYYYMMDD-NN
- 级别：P0 / P1 / P2（定级依据：§1 哪条判据）
- 发现时间（UTC）/ 恢复时间（UTC）/ 总时长：
- 发现人 / 处理人 / 复核人 / 审批人：
- 关联发布：commit ______　镜像 tag ______　迁移版本 ______
- 关联事故单/工单：
- 是否回滚：是（rollback 退出码 __，耗时 __s）/ 否（原因：__）
- 是否恢复备份：是（备份点 ______，RTO __min，RPO __h）/ 否

## 2. 影响
- 受影响用户/角色与规模：
- 受影响功能（登录/建题/考试/交卷/成绩/导入导出）：
- 数据影响：丢失/不一致/无（范围与条数；如恢复，写丢失区间）
- 对外影响与公告记录：
- 是否触发 SLA/合同条款：

## 3. 时间线（UTC，一行一件事，含命令与退出码）
| 时间 | 事件/动作 | 执行人 | 证据（日志/命令退出码） |
|---|---|---|---|
|  |  |  |  |

## 4. 根因（5 Why，到“可验证的机制”为止）
- 直接原因：
- 为什么没被更早发现（监控/演练/评审哪一环缺失）：
- 为什么影响被放大（单点/无重试/无降级/文档缺失）：
- 5 Why：
  1. 为什么…
  2. 为什么…
  3. 为什么…
  4. 为什么…
  5. 为什么…
- 结论（一句话，可被证据支持）：

## 5. 处置评估（做对了什么 / 做错了什么）
- 有效的：
- 无效或加重的（含浪费时间的方向）：
- 与 runbook/检查单的偏差（哪一步没按 docs/70 §14、docs/71 §3、docs/73 执行，为什么）：

## 6. 改进项（每条必须有责任人 + 截止 + 验收方式）
| # | 改进项 | 类型（代码/配置/监控/文档/流程） | 责任人 | 截止（UTC） | 验收方式 | 关联任务/缺陷 ID |
|---|---|---|---|---|---|---|
| 1 |  |  |  |  |  |  |
| 2 |  |  |  |  |  |  |

## 7. 证据清单
- 事故记录：deploy/evidence/incident-YYYYMMDD-NN.md
- 发布/回滚日志：deploy/evidence/release-production-*.log、rollback-production-*.log
- 容器日志快照：deploy/evidence/incident-logs-*.log
- 告警文件：deploy/evidence/monitoring/alert_*.log
- 监控/慢请求日报：deploy/evidence/monitoring/slow-requests-*.log
- 备份/恢复记录：deploy/evidence/dr-*.log、docs/71 §4.2 台账
- 其他（截图/工单/公告）：

## 8. 签字
| 角色 | 姓名 | 结论 | 时间 |
|---|---|---|---|
| 处理人 |  |  |  |
| 复核人 |  |  |  |
| 人类 PO/Lead（P0/P1 必签） |  |  |  |
```

---

## 6. 改进项跟踪规则（防止“复盘完就忘”）

| 级别 | 复盘截止 | 改进项最晚关闭 | 跟踪方式 |
|---|---|---|---|
| P0 | 恢复后 48h | 10 个工作日 | 每条进 `contracts/tasks/` 或缺陷单；每周复查 |
| P1 | 恢复后 48h | 15 个工作日 | 同上；下一次发布前必须关闭阻塞项 |
| P2 | 次一工作日 | 30 个自然日 | 进任务列表；月度运维复盘统一看 |

规则：

1. 改进项必须**可验收**（有命令/判据），不接受“加强意识”“注意一下”这类不可验收项。
2. 监控阈值调整不能替代根因修复：因误报调阈值要写理由；因漏报补指标要给出验证方式。
3. 同类事故（同一根因）第二次发生 → 级别自动升一级，并在复盘里说明第一次改进项为何无效。
4. 复盘归档：`deploy/evidence/incident-<YYYYMMDD>-<NN>.md`；结论摘要回填 `docs/decision-log.md`（由 R0/Release 维护）。

---

## 7. 与发布流程的接口

| 场景 | 用哪份文档 | 关键判据 |
|---|---|---|
| 上线前检查 | `docs/73` §1–§6 | G4 `G4: PASS`、备份新鲜、演练 JSON `rollback_within_15min=true` |
| 上线执行/失败自动回滚 | `docs/70` §14、`deploy/release/deploy.sh` | 退出码 0；证据日志 `result=SUCCESS` |
| 回滚 | §3.1、`deploy/release/rollback.sh` | 退出码 0；`within_15min=yes` |
| 恢复备份 | §3.2、`docs/71` §3.2 | RTO < 30min；恢复后补备份 |
| 告警与阈值 | `docs/72` §4/§8/§9 | M1–M9；P0/P1 15 分钟确认 |
| 复盘与改进 | §5/§6 | 改进项有责任人、截止、验收方式 |

---

## 8. 演练与评审（让模板不过期）

- 每次 staging 发布演练（`deploy/release/staging_drill.sh`）后，用 §5 的空模板走一遍“假想事故”，检查时间线字段是否够用（10 分钟）。
- 每季度用一次真实告警（可用 `HEALTH_FAIL_THRESHOLD=1` 在 staging 触发）验证：告警 → 定级 → 止损 → 复盘 全链路（`docs/72` §3.4）。
- 若某次事故发现模板缺字段/命令失效，**先改本文再复盘**，并在改动处注明日期与原因。
