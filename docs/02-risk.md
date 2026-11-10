# 02 · 风险登记册

> 规则：R0 唯一维护；任何 Worker 的 `blocked`/`contract_issue` 都先落这里再处理；关闭需写证据。

| ID | 风险 | 概率 | 影响 | 缓解措施 | 触发信号 | 负责人 |
|---|---|---|---|---|---|---|
| RISK-01 | 2 周内范围超出产能，主链路延期 | 高 | 高 | PRD §3.3 砍单顺序；D6 做 G3 中期检查；P2 默认不做 | D6 时 P0 完成度 < 60% | R0 |
| RISK-02 | 契约冻结不彻底，前后端返工 | 中 | 高 | G2 强制 lint + AC→接口覆盖检查；契约只由 R2 改；任务卡绑定契约片段 | Worker 返回 contract_issue ≥ 2 次 | R2 |
| RISK-03 | 交卷/自动保存并发导致成绩重复或答案覆盖 | 中 | 高 | ADR-005/006：状态机 + 唯一约束 + client_seq 单调；专项用例 AC-13/15 | 幂等用例失败 | R3 |
| RISK-04 | 人类验收带宽不足，并行产出积压 | 高 | 中 | 并发上限 3；合并前自动检查；人类只过 3 道门 | 待审 PR > 3 | R0 |
| RISK-05 | 越权/IDOR 漏洞（学生看他人成绩） | 中 | 高 | RBAC 矩阵 + 负例用例 AC-03/18；QA 独立验证 | 安全用例失败 | R3/R5 |
| RISK-06 | 审计缺失导致生产问题不可追溯 | 低 | 中 | 所有写操作统一中间件落审计；AC-22 | 抽查缺记录 | R3 |
| RISK-07 | 判分规则理解不一致（多选部分给分） | 中 | 中 | AC-17 固化公式；R2 在 ADR 中写死小数位与边界 | 成绩与预期不符 | R2/R5 |
| RISK-08 | 执行环境受限（本机 pwsh 不可用 0xC0000142），测试无法本地跑 | 高 | 中 | 验证统一放 CI（GitHub Actions）；QA 在 CI 跑证据；本地仅文档/契约 | 本地 pytest/pnpm 失败 | R6 |
| RISK-09 | 依赖库版本漂移导致构建不可复现 | 低 | 中 | 锁文件 + 固定基础镜像 + CI 缓存；禁止运行中升级 | CI 两次结果不一致 | R6 |
| RISK-10 | 数据备份未演练，故障时不可恢复 | 中 | 高 | T4.3 备份/恢复演练列入 G4 准入 | 演练未做 | R6 |
| RISK-11 | 逐题解析 `explanation` 未进冻结快照，题库后续编辑会改变历史成绩页解析 | 中 | 低 | V1 接受为已知限制；判分与分数不受影响；彻底修复需扩展 `freeze_paper` 快照（CHG 级） | 教师反馈「旧成绩解析变了」 | R2/R3 |
| RISK-12 | FE 测试依赖 antd 组件内部 `data-*` 透传（如 InputNumber），CI 首跑可能红 | 中 | 低 | 断言改到组件外层容器；FE-003 已标注 | frontend job 测试失败 | R4 |
| RISK-13 | 本机 Docker Desktop / WSL 曾损坏（`REGDB_E_CLASSNOTREG`、引擎无法启动），阻塞 staging 演练与容器内性能复测 | 低（已修复） | 中 | **2026-11-10 12:30 已修复**：`wsl -l -v` 可见 `docker-desktop` distro，Docker engine 29.9.3 可用，`docker compose config` exit 0；容器内性能复测随后执行 | `docker info` 失败 / `wsl --shutdown` 报注册表错误 | R6/R0 |
| RISK-14 | 性能尾延迟受存储/OS 影响：本机 NTFS 上 100 并发开考的 WAL `fsync` 串行，`answer_save` p95 300–590ms；Linux/SSD 未实测 | 中 | 高 | 原生复测已完成（`docs/61 §10.2`、`docs/75`）；部署配置已加 `WEB_CONCURRENCY` + 池摊分；**最终销项需在 fsync=on 的 Linux/SSD 上复跑 k6** | Linux 复测 save p95 ≥300ms | R0/R6 |
| RISK-15 | `app/middleware/access_log.py` 用模块级锁 + 每行 `flush()` 写 stdout：日志消费者慢（PowerShell 管道、前台 `compose up`、慢 journald）时会把全部请求串行化，实测 p95 700ms→20–31s 并诱发 `QueuePool` 500 | 中 | 高 | 压测/生产用 detached + json-file 限额，不把容器日志接到慢消费者；长期改为队列异步写（RISK 记录，不改本批代码） | 日志管道下 P95 突增到秒级、`QueuePool timeout` 500 | R0 |

## 升级规则

- P0/P1 风险触发且 30 分钟内无法定位 → 升级人工，R0 记录「阻塞点 + 已排除项 + 建议方案」；
- 同一风险连续两轮重试未解决 → 换人/换会话（见流程 §4.2），不做无限重试。
