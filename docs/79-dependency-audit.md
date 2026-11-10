# 79 · 依赖漏洞扫描台账（2026-11-11）

> 目的：把「依赖有没有已知 CVE」从口头保证变成可复跑的动作，并把**生产运行时**与**开发/构建期**
> 分开评估（很多告警只影响 dev server / CI 构建，不进生产镜像）。
> 风险登记：`docs/02-risk.md` RISK-16（前端 dev-only）、RISK-17（react-router，已代码层缓解）。

## 1. 怎么跑（可复跑）

```powershell
# 后端（审计当前 venv；pyproject 不能直接当 requirements 用）
.venv\Scripts\python.exe -m pip_audit --cache-dir reports\tmp\pip-audit-cache `
  --progress-spinner off --format json --output reports\pip-audit.json

# 前端（全量 + 仅生产依赖）
cd web
pnpm audit --json          | Out-File -Encoding UTF8 ..\reports\pnpm-audit.json
pnpm audit --prod --json   | Out-File -Encoding UTF8 ..\reports\pnpm-audit-prod.json
```

- 首次使用需 `pip install pip-audit`（本机已装 2.10.1）；`pip_audit` 默认缓存目录在
  `%LOCALAPPDATA%` 下可能无写权限，**必须带 `--cache-dir`**（本次踩到 `PermissionError`）。
- 扫描需要访问 OSV/PyPI 与 npm registry；本轮 `pip install` 走镜像成功，`pip-audit` 用缓存目录跑通。
- 证据文件在 `reports/`（按 `.gitignore` 不入库），台账结论记录在本文件。

## 2. 后端（Python，91 个已安装包）

| 包 | 当前版本 | 告警数 | 修复版本 | 影响面 | 处置 |
|---|---|---|---|---|---|
| `pip` | 25.0.1 | 7（PYSEC-2026-1795/1796/2875/2876/0196/3721） | 25.3 / 26.x | **工具链**，不进运行时 | 升级 pip 即可（`python -m pip install -U pip`；本机网络慢，列为待办） |
| `pytest` | 8.4.2 | 1（PYSEC-2026-1845） | 9.0.3 | **测试依赖**，不进镜像 | 与前端依赖升级同批：pytest 9 需重跑 177 条后端用例后再合并 |

**运行时依赖全部干净**：`fastapi` / `starlette` / `uvicorn` / `pydantic` / `pydantic-settings` /
`sqlalchemy` / `psycopg[binary]` / `alembic` / `python-multipart` 等均无已知漏洞。

> 结论：**生产 API 镜像没有已知 CVE**；两条告警都在开发/CI 工具链上。

## 3. 前端（Node，`pnpm audit` 全量 18 条）

### 3.1 全量（含 devDependencies）

| 严重级 | 条数 | 涉及包 |
|---|---|---|
| critical | 3 | `vitest`（UI server 任意文件读写/执行）、`tinypool` ×2（prototype pollution → RCE） |
| high | 1 | `vite`（launch-editor `fs.existsSync` 绕过） |
| moderate | 13 | `vite` ×9、`react-router` ×2、`@vitest/mocker` ×2、`esbuild`、`minimatch` |
| low | 1 | `vite`（cross-origin script execution） |

### 3.2 仅生产依赖（`pnpm audit --prod`）

只剩 **2 条 moderate，均属 `react-router-dom` 6.26.x**：

| 公告 | 触发条件 | 本项目影响 | 依据 |
|---|---|---|---|
| 反斜杠/协议相对路径导致开放重定向（`<Link>`/`useNavigate`） | 跳转目标由攻击者控制 | **已缓解**：所有 `navigate` 目标为固定站内路径，唯一来自 URL 的 `returnTo` 经 `web/src/pages/login/safeReturnTo.ts` 清洗（拒绝 `//host`、反斜杠、控制字符、指回 `/login`） | 代码评审 + 该模块单测 |
| SSR `deserializeErrors()` 任意构造器注入 | 使用 react-router 的 SSR 数据 API | **不适用**：纯 CSR（Vite SPA，无 SSR/loader hydration） | `web/src/App.tsx` 仅 `RouterProvider` + `createBrowserRouter` |

> 结论：**生产前端没有可利用面**；修复需 `react-router-dom` 6→7（破坏性），与 §4 同批做。

## 4. 处置计划（按性价比排序）

| # | 动作 | 风险 | 验收 |
|---|---|---|---|
| 1 | 升级 `pip`（venv 内） | 无 | `pip-audit` 中 `pip` 告警消失 |
| 2 | 前端依赖升级：`vite` 5→7、`vitest` 2→4（连带 `esbuild`/`tinypool`/`minimatch`）、`react-router-dom` 6→7 | 中（破坏性 major） | `pnpm lint && pnpm tsc --noEmit && pnpm test`（91 用例）与 `pnpm build` 全绿；`pnpm audit` 归零（或只剩需评估项）；E2E UI 5 条复跑 |
| 3 | 后端测试依赖升级：`pytest` 8→9（连带 `pytest-cov`） | 低-中 | `pytest` 177 passed；CI backend job 绿 |
| 4 | 可选：把 `pnpm audit --prod --audit-level=high` 加进 CI frontend job | 低 | 仅生产依赖、阈值 high 以上才红，避免 dev-only 噪声阻塞 |

> 明确不做：为了让告警数变 0 而 `pnpm audit --ignore` 屏蔽公告；或跳过测试直接上 major 升级。

## 5. 追溯

| 文档 | 关系 |
|---|---|
| `docs/02-risk.md` RISK-16 / RISK-17 | 本轮登记的风险与缓解依据 |
| `web/src/pages/login/safeReturnTo.ts` | react-router 开放重定向的代码层缓解 |
| `web/package.json` | 依赖版本来源 |
| `server/pyproject.toml` | 后端运行时/开发依赖来源 |
| `reports/pip-audit.json`、`reports/pnpm-audit*.json` | 原始输出（不入库） |
