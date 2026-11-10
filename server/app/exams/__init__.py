"""考试领域包（TASK-BE-004）。

- `schemas`：`ExamCreate` / `ExamPatch` / `ExamOut` / `ExamPage`（契约 v1.0.2）
- `service`：发布（同事务冻结试卷快照）、状态机、归属校验、审计
- `router`：`GET/POST /exams`、`GET/PATCH /exams/{exam_id}`

作答会话（`/exams/{id}/sessions`、`/sessions/*`、`/me/exams`）在 `app.sessions`。
"""
