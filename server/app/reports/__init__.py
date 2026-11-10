"""成绩明细 / 考试汇总 / 错题本（TASK-BE-005）。

模块划分（刻意保持 `__init__` 无导入，避免与 `app.sessions` 形成循环依赖）：

- `schemas`：契约 v1.0.2 的 `ResultDetail` / `MyResultPage` / `WrongQuestionPage` /
  `ExamResultSummary` 响应模型
- `service`：四个读用例（权限 + 分页 + 汇总口径）
- `router`：`GET /sessions/{id}/result`、`GET /me/results`、`GET /me/wrong-questions`、
  `GET /exams/{id}/results`
- `wrong_book`：交卷/到期结算事务内的错题 upsert 钩子（由 `app.sessions.service._finalize` 调用）

写入侧只依赖 SQLAlchemy 模型，不导入 sessions，因此 `sessions.service` 可以安全地反向引用。
"""
