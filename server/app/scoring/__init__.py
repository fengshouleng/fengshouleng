"""判分领域包（TASK-BE-004，ADR-007）。

- `score_question`：纯函数判分（single/judge 全对给分；multiple 支持全对/部分给分）
- `snapshot_questions` / `grade_session`：以试卷冻结快照为唯一依据判分并落库，
  与交卷状态迁移同事务（`app.sessions.service` 调用）
"""
