# 78 · G5 上线验收预检：现状差距清单（2026-11-11）

> G5 = 上线验收（`docs/61 §0` 的四个门之一）。本文不是 G5 报告，而是**预检**：
> 把「不依赖生产环境就能判」的项自动判掉，把「必须人/生产环境」的项列清楚，
> 让真正上线时的签字只需处理剩下的部分。
>
> 自动预检脚本：`scripts/g5_precheck.ps1`（证据写 `reports/ci/g5-precheck.json`，不入库）。

## 1. 自动预检结果（2026-11-11 实测）

```text
ID      检查项                                     状态    明细
1.1     G4 报告结论 PASS                            FAIL    G4 仍为 CONDITIONAL（性能待 ≥8 vCPU 销项，docs/76）
1.3     演练 JSON result=PASS 且回滚 < 15min        PASS    g4=G4-20261110-1300 result=PASS rollback=11s total=31s
3.6/§9  compose config -q                           PASS    退出码 0
4.2     TLS 证书存在                                SKIP    deploy/certs/fullchain.pem 不存在
4.5     deploy/.env.prod 存在                       SKIP    尚无生产 env
P-005   main 分支保护                               FAIL    token 无 Administration 权限（docs/77 §2）
§2/§5   发布/备份/监控脚本存在                      PASS    全部存在
G5 预检：PASS=3 FAIL=2 SKIP=2
```

**判读**：
- **FAIL 1.1** 是 G4 未转 PASS 的直接后果 → 性能销项（等 ≥8 vCPU）是 G5 的前置；
- **FAIL P-005** 是仓库权限问题 → 管理员按 `docs/77 §3.1` 勾选即消；
- **SKIP 4.2 / 4.5** 需要真实的域名、证书与生产 env（`docs/73 §4`）；
- 演练 JSON 与 compose 配置已 PASS，脚本齐全，说明「发布/回滚/备份/监控」的工具链本身可用。

## 2. 剩余人工项（按 docs/73 章节，逐条给「怎么关」）

| § | 检查项 | 当前状态 | 关门动作 | 责任角色 |
|---|---|---|---|---|
| 1.1 | G4 结论 PASS | ❌ CONDITIONAL | ≥8 vCPU runner 跑 `perf` job 销项（或 R0/QA 批准 `docs/76` 口径） | R0 + QA |
| 1.4 | 人工审批记录 | ❌ 无 | 上线前由人类 PO 在 `docs/61 §7` 签字（姓名/时间/范围：commit + 镜像 tag） | PO |
| 1.5 | 发布内容明确 | ⚠️ 部分 | `deploy.sh` 会写 `release-production-*.log`；上线时用同一 commit 跑 G4 | R0 |
| 1.6 | 是否首次部署 | ⚠️ 未定 | 首次部署无回滚镜像 → 必须另行批准「无自动回滚」并在 §5 记录人工回滚方案 | PO + SRE |
| 1.7 | 维护窗口与公告 | ❌ 无 | 定窗口（建议低峰）+ 公告 + 指定回滚决策人 | PO |
| 2.1–2.7 | 发布前 60min 内备份 / 新鲜度 / 恢复预案 | ⚠️ 备份与恢复演练已 PASS（`docs/71`），生产实例尚未运行 | 生产首次部署后：`deploy.sh` 自动触发备份；手工核验 `freshness_check.sh` 退出 0 | SRE |
| 3.1–3.6 | 迁移 dry-run / 兼容性 / 回滚策略 | ⚠️ 迁移 0001→0006 已在 staging 与 CI 验证 | 生产首次部署：记录 `alembic current/heads`、审阅 `release-production-*-migration.sql`、在 §3.4 写「向前兼容=可」 | SRE |
| 4.1–4.7 | DNS / 证书 / TLS / Cookie Secure / 防火墙 / 续期责任人 | ❌ 全缺（无域名与证书） | 购买/解析域名 → 签发证书放 `deploy/certs/` → `.env.prod` 设 `SESSION_COOKIE_SECURE=true` → `curl https://<域名>/api/healthz` 200 + 80→301；证书到期前 30 天日历提醒 | PO + SRE |
| 5.1–5.7 | 回滚镜像 tag / commit / 演练 / 决策人 / 命令 | ⚠️ staging 演练 PASS（rollback 11s），生产回滚镜像尚不存在 | 首次生产发布后立刻把 `exam-api:<tag>`/`exam-web:<tag>` 标为 PREV，并做一次「发布→回滚」生产演练 | SRE |
| 6 | 联系人 / 窗口 / 观察期 | ❌ 空表 | 填 `docs/73 §6` 五个角色 + 24h 观察窗口（密集 0–2h） | PO |
| 8 | 上线后 24h 观察指标 | ⚠️ 阈值已有（`docs/73 §8`），无生产数据 | 上线后按 `docs/72` 告警脚本 + `slow_requests.sh` 每日复盘 | SRE |

## 3. 上线前的最小动作序列（建议）

1. **性能销项**（唯一代码侧阻断）：≥8 vCPU runner → `workflow_dispatch` 跑 `perf` → G4 转 PASS；
2. **开分支保护**（`docs/77`）→ 之后所有变更走 PR + `ci-gate`；
3. **准备生产环境**：域名 + 证书 + `.env.prod`（强口令、`SESSION_COOKIE_SECURE=true`、`WARMUP_ENABLED=true`）；
4. **首部署**：`deploy.sh --env production --confirm-production`（自动备份/迁移/冒烟），记录镜像 tag 与 commit；
5. **首部署后的回滚演练**：立刻做一次发布→回滚，把 `rollback_seconds` 填进 `docs/73 §5`；
6. **观察 24h**：`docs/72` 告警 + 慢请求报告；无异常后按 `docs/73 §9` 签字归档。

## 4. 与 G4 的关系（避免混淆）

| 门 | 含义 | 当前结论 |
|---|---|---|
| G4 发布候选 | 代码/契约/安全/E2E/备份/演练/CI 全部有证据 | **CONDITIONAL**（仅性能容量待 ≥8 vCPU） |
| G5 上线验收 | 真实生产环境的部署、观察、签字 | **未开始**（缺环境与人工审批，本文给出差距） |

> G5 的预检脚本可在每次发布前重跑；`FAIL 1.1` 消失即代表 G4 已转 PASS，可进入 §3 的序列。

## 5. 追溯

| 文档 | 关系 |
|---|---|
| `docs/73-release-checklist.md` | G5 原始清单（本文是其差距视图） |
| `docs/70-runbook.md` §14 | 发布/回滚操作手册 |
| `docs/71-backup-restore.md` | 备份与恢复（含演练台账） |
| `docs/72-monitoring.md` | 告警与 24h 观察 |
| `docs/77-ci-branch-protection.md` | P-005 分支保护（G5 的前置之一） |
| `scripts/g5_precheck.ps1` | 本文件 §1 的自动预检 |
