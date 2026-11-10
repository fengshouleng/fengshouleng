# 17 · 首跑红项预测（静态推演，未执行任何命令）

> 口径：本会话 shell 不可用（`pwsh` 0xC0000142），以下全部是**按代码/配置静态推演**的高概率失败项，
> 不是实际结果。用途：首次 `git push` / 首次 `verify_all` 时按此清单快速定位，避免在多个红项间来回猜。
> 排序：概率 × 影响。「先修 A1/A2/B1，再谈其他」。

## A. CI 首跑（`backend` / `frontend` / `contract` 三个 job）

| # | 概率 | 预测红项 | 根因 | 修复方向 |
|---|---|---|---|---|
| A1 | 高 | `frontend` 安装阶段依赖版本漂移 | `web/pnpm-lock.yaml` 缺失，CI 退化为 `--no-frozen-lockfile`（P-004） | 在可执行环境 `cd web && pnpm install` 后提交锁文件；CI 自动切 frozen |
| A2 | 高 | `backend` 的 `mypy app` 报错 | `pyproject` 设了 `disallow_untyped_defs = true`，只要有函数漏注解即红 | 按报错补返回类型；**不要**全局关闭 strict；实在个别第三方 stub 缺失用 `# type: ignore` 并注明 |
| A3 | 中 | `backend` 的 `ruff check .` 报 `E501` | `select=["E"]` 会启用 E501，中文长串/URL 易超 100 列 | 优先折行；无法折行的加 `# noqa: E501`；若大面积则评估 `ignore=["E501"]`（需记录理由） |
| A4 | 中 | `contract` job `pytest tests/contract` 个别断言红 | QA 断言解析 `docs/12` 文本或契约 description 判据，v1.0.3 文档刚改过 | 看失败用例：若断言基于文档措辞 → QA 修断言；若基于契约结构 → R2 按 CHG 修 |
| A5 | 中 | `contract` job `openapi-spec-validator` 失败 | OpenAPI 3.1 + 内联 `examples` + `$ref` 组合的边界 | 按报错行修契约（v1.0.4 或 CHG），不要绕过校验器 |
| A6 | 低 | `frontend` tsc 报未使用变量/类型 | `noUnusedLocals` + 并行任务合并残留（如 `ResultPlaceholderPage.tsx` 未被引用但会被编译） | 删除孤儿文件或修正 import；`ResultPlaceholderPage` 可安全删除（路由已用真实 ResultPage） |
| A7 | 低 | `frontend` vitest 个别组件测试红 | jsdom 缺 antd 所需 API（每个测试自建 stub，若漏一个即红） | 把 `matchMedia`/`ResizeObserver` stub 抽到 `tests/setup.ts` 复用 |

## B. 本地 `scripts/verify_all`（含起栈与演练）

| # | 概率 | 预测红项 | 根因 | 修复方向 |
|---|---|---|---|---|
| B1 | 高 | E2E 报「browser not found」 | Playwright 浏览器未安装（脚本已内置 `playwright install chromium`，`--skip-install` 时需手动） | 去掉 `--skip-install` 或手动安装 |
| B2 | 高 | `seed-demo` 报表不存在 | 未先 `alembic upgrade head`（本地 SQLite 默认库），或未在 `server/` 目录执行 | 先迁移再 seed；`DATABASE_URL` 指向同一库 |
| B3 | 中 | `tests/security` 大量 401/403 失败 | 未 `create-admin`，或 `EXAM_API_URL` 指向错误前缀/端口 | 按 `tests/security/README.md` 初始化账号 |
| B4 | 中 | k6 命令不存在 | 未安装 k6 | 安装 k6 或跳过 `--with-perf`；性能必须独立 PG 环境 |
| B5 | 中 | 备份/发布演练失败 | 缺 `deploy/.env`、docker 未运行、`SESSION_COOKIE_SECURE` 与 http 冲突 | 按 `docs/70 §3` 准备环境；本地 http 设 `SESSION_COOKIE_SECURE=false` |
| B6 | 低 | E2E 页面 404 | `EXAM_BASE_URL` 应指前端（5173），API 由 Vite 代理；指错会全链路红 | 核对 `EXAM_BASE_URL` 与 `VITE_API_BASE` |

## C. CI 未覆盖的缺口（G4 必须手动执行）

| 缺口 | 影响 | 建议 |
|---|---|---|
| `security` / `e2e` / `perf` 不在 CI job 内 | 仅 `backend/frontend/contract` 随 PR 跑；G4 证据需手动 | 用 `scripts/verify_all --with-security --with-e2e --with-perf`；或后续为 CI 加「起栈 job」 |
| `docker compose config -q` 未入 CI（P-008） | compose 语法错误要到部署才暴露 | 加一步到 CI 或手动执行（docs/61 §9 第 11 步） |
| 备份恢复 / staging 上线回滚演练无自动化 | G4 的「恢复<30min / 回滚<15min」无证据 | 必须在 docker 环境执行 `deploy/backup/restore_drill.sh` 与 `deploy/release/staging_drill.sh` |
| `web/pnpm-lock.yaml` 缺失 | 依赖不可复现（A1） | 见 P-004 |

## D. 静态无法证明、必须靠执行验证的语义

1. **判分边界**：多选 partial 的 `round(…,2)` 与「有错选 0 分」；空答 0 分。
2. **交卷幂等**：并发双提交只产生一条成绩（SQLite 行锁语义与 PG `FOR UPDATE` 不同，测试需覆盖两种）。
3. **到期自动交卷**：`deadline_at` 与 `end_at` 取小、时钟偏移、时区（UTC 存储）。
4. **自动保存乱序**：`client_seq` 回退时 `applied=false` 且不覆盖本地。
5. **前端倒计时/断网恢复**：`server_time` 校时、重试队列、刷新回填。
6. **备份恢复耗时**与**回滚耗时**：纯环境指标，无法静态推断。
7. **会话 8h 真实过期**：安全套件用伪造 Cookie 代理，真实 TTL 待 fake clock。

## E. 10 分钟环境自检（起栈前）

```bash
python --version        # 期望 3.12.x
node --version          # 期望 20.x
pnpm --version          # 期望 9.x
docker compose version  # 需可运行
git --version

cd server && python -m pip install -e ".[dev]" && alembic upgrade head
python -m app.cli check            # DB 连通 + 迁移版本 + 管理员数
python -m app.cli seed-demo        # 开发演示数据（勿用于生产）
curl -s http://127.0.0.1:8000/api/healthz   # {"status":"ok","db":"ok",...}
```

> 结论：**A1/A2/B1/B2 是最可能的四个首红项**。先修这四个，再跑全套；不要一上来就同时修 E2E 与安全套件的红项。
