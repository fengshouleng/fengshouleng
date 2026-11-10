# 77 · CI 门禁加固：main 分支保护（P-005）

> 目标：让「绿」这件事无法绕过——`main` 只接受 PR + 必过 `ci-gate`，杜绝直接 push 造成的假绿/跳检。
> 状态：**文档 + 可执行脚本已就绪；实际开关待仓库管理员执行**（当前 token 权限不足，见 §2 证据）。

## 1. 为什么需要

G4 期间已经踩过一次真实事故：`DEF-005` 首推时 `changes` 判定错误让三个 job 全 `skipped`，
而 `ci-gate` 仍然 success（**假绿**）。修复靠的是代码（force_all 兜底），但**流程上没有第二道闸**：

- 现在任何人都能 `git push origin main`，绕过 PR 与 review；
- `ci-gate` 是汇总门，但如果 push 时 job 被跳过，它仍可能绿（已有兜底，但兜底本身也可能被未来改动破坏）；
- 发布前「同一 commit 必须既有 CI 绿又有 G4 证据」这条要求，只能靠人记。

分支保护把这三条变成仓库设置层面的硬约束。

## 2. 现状与阻塞证据（2026-11-11 实测）

本会话使用的 fine-grained PAT 具备 `Contents: RW` / `Actions: RW` / `Workflows: RW`，
但**没有 `Administration: RW`**，因此调用 GitHub 分支保护 API 全部 403：

```text
GET  /repos/fengshouleng/fengshouleng/branches/main/protection  -> 403
PUT  /repos/fengshouleng/fengshouleng/branches/main/protection  -> 403（body 为空）
```

→ 这一步**必须由仓库管理员**（human，或用带 Administration 权限的 token）执行。
其余工作（脚本、校验、回滚方法）已在本文件 §3/§4 备好，执行后再把结果回填 §5。

## 3. 怎么开（二选一）

### 3.1 GitHub 网页（推荐，1 分钟）

`https://github.com/fengshouleng/fengshouleng/settings/branches` →

1. **Add branch protection rule**
2. Branch name pattern：`main`
3. 勾选：
   - ✅ Require a pull request before merging（建议 Required approvals = 0，单人项目先不强制 review）
   - ✅ Require status checks to pass before merging
     - ✅ Require branches to be up to date before merging
     - 在 checks 列表里勾选 **`CI 汇总门`**（即 workflow 里 `ci-gate` job 的 name；也可同时勾 `backend (ruff / mypy / pytest)`、`frontend (lint / tsc / test / build)`、`contract (openapi-spec-validator / pytest tests/contract)`）
   - ✅ Do not allow bypassing the above settings（如仓库仅你一人，可同时接受对 admin 也生效）
   - ❌ 不要勾 Require deployments / Require signed commits（当前无此要求，勾了会阻塞）
4. Save changes

> **注意**：`perf (k6 100+100 VU)` 只在 `workflow_dispatch` 运行，**不要**把它设为 required，
> 否则 PR 会因为该 job 被跳过而永远 pending。

### 3.2 API（需要 `Administration: write`）

```bash
curl -X PUT \
  -H "Authorization: Bearer $GH_ADMIN_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/fengshouleng/fengshouleng/branches/main/protection \
  -d '{
    "required_status_checks": {"strict": true, "contexts": ["CI 汇总门"]},
    "enforce_admins": true,
    "required_pull_request_reviews": {"required_approving_review_count": 0},
    "restrictions": null
  }'
```

PowerShell 等价写法（`Invoke-RestMethod -Method Put`）见 `scripts/branch_protection.ps1`：

```powershell
$env:GH_ADMIN_TOKEN = "<admin token>"
pwsh -File scripts/branch_protection.ps1            # 只看现状
pwsh -File scripts/branch_protection.ps1 -Apply     # 开启保护（required: CI 汇总门）
```

脚本会先 GET 打印现状，再 PUT，并把结果写进 `reports/ci/branch-protection-<UTC>.json`（证据目录不入库）。

## 4. 开关之后怎么验证（必做）

| 验证 | 命令 | 期望 |
|---|---|---|
| 保护规则生效 | `curl -H "Authorization: Bearer $TOKEN" https://api.github.com/repos/fengshouleng/fengshouleng/branches/main/protection` | 返回 `required_status_checks.contexts` 含 `CI 汇总门` |
| 直接 push 被拒 | `git push origin main`（在本地多一个空提交时） | 被拒绝：`protected branch hook declined` / `Required status check ... is expected` |
| PR 流程可用 | 建一个测试分支 → 开 PR → 等 CI | PR 上出现 `CI 汇总门` 必过检查；绿了才能 merge |
| 绕过被拒 | 用 admin 账号尝试 force push | 被拒绝（`enforce_admins: true` 时） |

## 5. 执行记录（管理员执行后回填）

| 项 | 值 |
|---|---|
| 执行人 | |
| 时间（UTC+8） | |
| 方式 | 网页 / API |
| required checks | |
| enforce_admins | |
| 验证结果（§4 四行） | |
| 证据 | `reports/ci/branch-protection-*.json`（或网页截图路径） |

## 6. 顺序建议

1. 先按本文件开启保护并验证 §4；
2. 再跑一次 `workflow_dispatch` 的完整 CI（含 perf）作为「保护生效后的第一次发布候选」；
3. 性能项（`docs/76`）等 ≥8 vCPU runner 到位后一并销项，届时 G4 可正式转 PASS。

## 7. 追溯

| 文档 | 关系 |
|---|---|
| `.github/workflows/ci.yml` | `ci-gate` 汇总门与四个 job |
| `docs/61-gate-report.md §10.3` | DEF-005 假绿事故与修复 |
| `docs/73-release-checklist.md §1` | 发布前的门控与授权检查 |
| `docs/76-perf-gate-scope-proposal.md` | 性能门槛口径提案（未生效） |
