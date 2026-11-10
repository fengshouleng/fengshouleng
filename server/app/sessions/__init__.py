"""作答会话领域包（TASK-BE-004）。

- `service`：开始/恢复会话、自动保存（client_seq 单调）、幂等交卷、到期自动交卷
- `router`：`POST /exams/{exam_id}/sessions`、`GET /sessions/{id}`、
  `PUT /sessions/{id}/answers`、`POST /sessions/{id}/submit`、`GET /me/exams`
"""
