# scripts/ — 验收与运维快捷脚本

本目录提供「一条命令跑完 V1 验收预检」的入口，供**具备可执行环境**的机器/CI 使用。
脚本已于 2026-11-10 在本机（Windows 11 + Windows PowerShell 5.1）完成首次真实执行：**14 步全绿**。

## verify_all.ps1（Windows）

> ⚠️ **不要双击 `.ps1`**。Windows 可能按「包/关联程序」方式启动并弹出「无法注册包」错误——
> 这不是脚本逻辑错误，而是 OS 启动路径问题。请用下面任一方式：

```bat
REM 方式 1（推荐：自动优先 pwsh 7，找不到则回退 Windows PowerShell 5.1）
scripts\verify_all.cmd

REM 方式 2（在 PowerShell 窗口里执行）
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify_all.ps1

REM 方式 3（机器上只有 Windows PowerShell 5.1）
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify_all.ps1
```

```powershell
# 基础：契约 + 后端 + 前端（最常用）
.\scripts\verify_all.cmd

# 含安全负例（需先起栈 + seed-demo + create-admin）
.\scripts\verify_all.cmd -WithSecurity -ApiUrl http://127.0.0.1:8000/api

# 含 E2E（Playwright，需前端 dev server 或 compose 起的 web）
./scripts/verify_all.ps1 -WithE2E -BaseUrl http://127.0.0.1:5173

# 含性能冒烟（k6 需单独安装）
./scripts/verify_all.ps1 -WithPerf

# 只跑契约
./scripts/verify_all.ps1 -Only contract
```

## verify_all.sh（Linux / macOS / Git Bash / WSL）

```bash
./scripts/verify_all.sh                      # 契约+后端+前端
./scripts/verify_all.sh --with-security --api-url http://127.0.0.1:8000/api
./scripts/verify_all.sh --only contract
```

## Windows 启动失败排查

| 现象 | 原因 | 处理 |
|---|---|---|
| 弹窗「…\verify_all.ps1 无法注册包」 | 双击 `.ps1`，或 `.ps1` 关联到了 MSIX 打包版 PowerShell，系统按「包」启动失败 | 改用 `scripts\verify_all.cmd`，或在终端执行 `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify_all.ps1` |
| 「因为在此系统上禁止运行脚本」 | ExecutionPolicy 限制 | 命令加 `-ExecutionPolicy Bypass`（只影响本次进程，不改系统策略） |
| 中文乱码 | Windows PowerShell 5.1 以 ANSI 读取无 BOM 的 UTF-8 `.ps1` | 本脚本输出已改为英文；仍乱码则用 pwsh 7，或终端先 `chcp 65001` |
| `pwsh` 不是命令 | 未安装 PowerShell 7 | 用 `verify_all.cmd`（自动回退 5.1）或安装 PowerShell 7 |
| 双击后窗口一闪而过 | 脚本结束即关窗 | 用 `.cmd` 包装（内置 pause），或在已有终端里运行 |
| 窗口标题变成「选择 …」、输出像卡住 | cmd 的 QuickEdit 选择模式：选中文本会**暂停**程序 | 按 `Esc` 或右键任意位置退出选择模式 |
| `pip install` 过慢或无输出 | 默认走清华 PyPI 镜像，且每步有 `-StepTimeoutSec`（默认 600s）硬超时会杀掉卡死步骤；旧 pip 缓存锁也会导致挂起 | 脚本已用 `--no-cache-dir` 规避缓存锁；仍失败则 `-PipIndexUrl https://pypi.org/simple` 或调大 `-StepTimeoutSec` |
| 报错「`cmd : 系统无法执行指定的程序`」 | `python` 只是 Windows Store 别名（未安装真正的 Python），启动即失败 | 见下方「本机缺少 Python/Node」 |

## 本机缺少 Python/Node（Store 别名错误）

脚本**优先使用系统安装的 Python / Node**（本机已验证：Python 3.12.10 + Node 20.20.2 + pnpm 10.x）；
Python 依赖装入项目内 `.venv/`（已在 `.gitignore`），不污染系统解释器。
仅当 `where python` 指向 `...\WindowsApps\python.exe`（商店别名，报「系统无法执行指定的程序」）时，
才回退到 DSH 内置运行时（见下表）：

| 工具 | 路径 |
|---|---|
| Python | `C:\Users\<你>\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe` |
| Node | `C:\Users\<你>\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe` |
| pnpm | `C:\Users\<你>\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\pnpm\bin\pnpm.mjs`（用 Node 执行） |

`verify_all.ps1` 会自动检测可用运行时；仅在回退到内置运行时时创建 `pnpm` 包装脚本（放在
`reports/verify-<ts>/_shim/` 并临时加入 `PATH`），无需手工配置。也可显式指定：

```bat
scripts\verify_all.cmd -PythonExe "C:\Users\Admin\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
```

> 说明：使用系统 Python 时依赖装在项目 `.venv/`，回退到内置 Python 时装在其用户目录，
> 两种方式都不修改系统解释器。

## run_stack.ps1 / stop_stack.ps1（本地验收栈）

security / E2E 需要「API + 前端 + 种子数据」同时在跑，用手工开三个窗口容易漏步骤。
这两个脚本把起栈收敛为一条命令（SQLite 开发库）：

```bat
REM 推荐用 .cmd 包装（避免双击 .ps1 的「无法注册包」问题）
REM 迁移 + seed-demo + create-admin(qa-admin) + 启动 API(8000) 与前端(5173)
scripts\run_stack.cmd

REM 重置开发库后重建（仅本地）
scripts\run_stack.cmd -ResetDb

REM 停止
scripts\stop_stack.cmd
```

> 也可以直接在终端执行：
> `powershell -ExecutionPolicy Bypass -File .\scripts\run_stack.ps1`
> **不要双击 `.ps1` 文件**（Windows 会按「包」启动并报错，见上文排查表）。

- 前置：先跑过一次 `verify_all`（生成 `.venv` 与 `web/node_modules`）
- 进程 PID 写入 `.run/`（已 gitignore），日志在 `reports/stack/{api,web}.log`
- 默认账号：`qa-admin` / `demo-teacher` / `demo-student-1` / `demo-student-2`；口令取 `EXAM_QA_PASSWORD`
  （缺省 `dev-demo-password-123`，**仅本地**）
- 起好后在另一终端执行：

```bat
scripts\verify_all.cmd -Only contract -WithSecurity -WithE2E
```

## 产物

- 日志：`reports/verify-<timestamp>/NN-<step>.log`
- 汇总：控制台表格 + 等价 junit 由各步骤自行产出（`--junitxml` 参数已内置在 pytest 步骤）
- 退出码：`0` 全部通过；非 0 = 失败步骤数（CI 可直接用）

## 步骤对照

| 脚本步骤 | 对应 G4 预检（docs/61 §9） |
|---|---|
| contract | 1–2：`openapi-spec-validator` + `pytest tests/contract` |
| backend | 3：`ruff` + `mypy` + `pytest server/tests` |
| frontend | 4：`pnpm lint` + `tsc --noEmit` + `test` + `build` |
| security（可选）| 6：`pytest tests/security`（需运行中的 API） |
| e2e（可选）| 7：`pytest tests/e2e`（需前端 + seed 数据） |
| perf（可选）| 8：`k6 run tests/perf/k6/exam_flow.js`（需独立 PG 环境） |

> 备份恢复演练、staging 上线回滚演练、`docker compose config` 属于环境级动作，见
> `deploy/backup/restore_drill.sh`、`deploy/release/staging_drill.sh` 与 docs/61 §9 第 9–11 步。
