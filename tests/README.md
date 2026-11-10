# tests/ — 测试与验证（QA 维护）

- **负责人**：R5 QA（preset: `qa-verify`），**只报不修**
- **写作用域**：`tests/**`、`docs/60-test-plan.md`、`docs/61-gate-report.md`、`reports/**`
- **禁止**：修改 `server/**`、`web/**`、`contracts/**`

## 分层

| 层 | 位置 | 内容 | 何时跑 |
|---|---|---|---|
| 契约测试 | `tests/contract/` | OpenAPI 结构校验、响应 schema、错误码、AC 映射完整性 | 每次 PR（CI） |
| 接口测试 | `server/tests/`（由 BE 编写） | 模块行为与边界 | 每次 PR（CI） |
| 组件测试 | `web/tests/`（由 FE 编写） | 页面交互与路由守卫 | 每次 PR（CI） |
| E2E | `tests/e2e/` | 关键链路（Playwright） | 合并到集成分支后 |
| 安全负例 | `tests/security/` | 越权/IDOR/CSRF/注入/XSS/弱密码 | G4 前全量 |
| 性能冒烟 | `tests/perf/` | 100 并发作答 P95 | G4 前 |

## E2E 关键链路（G4 必过）

```
登录(教师) → 建 3 题 → 组卷(分值合计 100) → 发布考试
  → 登录(学生) → 开始考试 → 作答 → 断网重连 → 交卷
  → 判分 → 学生查成绩/错题本 → 教师查汇总 → 重复交卷(幂等)
```

## 证据约定

- 每个门控报告必须包含：命令、退出码、通过/失败数、覆盖率、缺陷单链接、回滚点 commit
- 报告模板：`docs/61-gate-report.md`
- 当前环境 `pwsh` 不可用（0xC0000142）→ 测试统一在 CI 执行并回传日志

## 已交付：TASK-QA-000（契约层 + E2E 骨架 + 数据工厂）

| 文件 | 作用 | 默认状态 |
|---|---|---|
| `tests/contract/test_openapi_valid.py` | `openapi-spec-validator` 结构校验 + `$ref`/operationId/security/CSRF/分页一致性；AC 支撑断言 | 可在 CI 跑 |
| `tests/contract/test_error_codes.py` | 所有 4xx 引用统一 `Error`、登录失败不泄露信息、CSRF 403、`Error.code` 枚举 | 可在 CI 跑 |
| `tests/contract/test_ac_matrix.py` | 解析 `docs/60-test-plan.md` 的 AC 矩阵，校验 AC-01..22 全覆盖与计划/代码一致 | 可在 CI 跑 |
| `tests/e2e/test_exam_flow.py` | G4 主链路（登录→建题→组卷→发考试→作答→断网→交卷→判分→成绩→幂等） | 默认 skip，`EXAM_E2E=1` 启用 |
| `tests/factories.py` | 独立数据工厂（账号/题库/试卷/考试/导入文件/作答脚本），不依赖生产代码 | — |
| `tests/requirements.txt` | 契约与 E2E 的最小依赖 | — |

```bash
pip install -r tests/requirements.txt
python -m pytest tests/contract -q          # G2/G3/G4 契约门
EXAM_E2E=1 EXAM_BASE_URL=http://127.0.0.1:8080/api python -m pytest tests/e2e -q
```

> 契约缺陷 C-01..C-22 已由 R2 在 `contracts/openapi.yaml` v1.0.1 处置，见 `docs/60-test-plan.md` §8.1；
> `tests/contract` 当前 **0 个 xfail**。新增缺陷时再按“缺陷单号 + xfail”显式标记。
