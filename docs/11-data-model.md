# 11 · 数据模型 v1.0

- 存储：PostgreSQL 16（生产）/ SQLite（本地与测试，注意 SQLite 无行锁语义，交卷幂等用唯一约束 + 事务模拟）
- 迁移：Alembic，`server/migrations/`
- 命名：snake_case；时间统一 UTC 存储、API 输出 ISO 8601

## 1. 实体关系

```
users 1─N auth_sessions
users 1─N questions (owner)
users 1─N papers (owner)
papers 1─N paper_questions N─1 questions
papers 1─N exams
exams 1─N exam_sessions N─1 users(student)
exam_sessions 1─N answers N─1 questions
users 1─N wrong_book N─1 questions
users 1─N audit_logs (actor)
```

## 2. 表定义

### users
| 列 | 类型 | 约束 |
|---|---|---|
| id | bigserial PK | |
| username | varchar(64) | unique, not null |
| display_name | varchar(64) | not null |
| role | varchar(16) | check in ('student','teacher','admin') |
| password_hash | varchar(255) | not null（argon2id） |
| is_active | boolean | default true |
| created_at / updated_at | timestamptz | |

索引：`ux_users_username`。

### auth_sessions
| 列 | 类型 | 约束 |
|---|---|---|
| id | uuid PK | |
| user_id | bigint FK users | on delete cascade |
| token_hash | varchar(64) | unique（sha256(session_id)，不存明文） |
| csrf_token | varchar(64) | not null |
| expires_at | timestamptz | not null |
| revoked_at | timestamptz | null |
| created_at | timestamptz | |

索引：`ix_auth_sessions_user`、`ix_auth_sessions_expires`（清理任务）。

### questions
| 列 | 类型 | 约束 |
|---|---|---|
| id | bigserial PK | |
| owner_id | bigint FK users | not null |
| type | varchar(16) | check in ('single','multiple','judge') |
| stem | text | not null |
| options | jsonb | 选项数组 `[{key:"A",text:"..."}]`，判断题固定 A=对/B=错 |
| answer | jsonb | 正确答案 key 数组，如 `["A"]` |
| explanation | text | 解析；**V1 API 必填**（契约 QuestionCreate），DB 允许 NULL 以兼容导入中间态 |
| difficulty | smallint | 1–5，默认 3 |
| tags | jsonb | 字符串数组 |
| is_deleted | boolean | default false（软删除） |
| created_at / updated_at | timestamptz | |

索引：`ix_questions_owner`、`ix_questions_type`、GIN(`tags`)。幂等去重键：`(owner_id, type, md5(stem))` 唯一（导入用）。

### papers
| 列 | 类型 | 约束 |
|---|---|---|
| id | bigserial PK | |
| owner_id | bigint FK users | |
| title | varchar(128) | not null |
| total_points | numeric(7,2) | 由明细汇总；上限 10000.00（契约 v1.0.2） |
| status | varchar(16) | draft / frozen |
| frozen_snapshot | jsonb | 冻结时写入：题目+选项+答案+分值+规则 |
| created_at / updated_at | timestamptz | |

### paper_questions
| 列 | 类型 | 约束 |
|---|---|---|
| paper_id | bigint FK papers | PK 组成 |
| question_id | bigint FK questions | PK 组成 |
| order_no | smallint | |
| points | numeric(5,2) | > 0 |
| partial_rule | varchar(16) | all_or_nothing / partial（仅多选生效） |

PK：`(paper_id, question_id)`；约束：同一试卷 `order_no` 唯一。

### exams
| 列 | 类型 | 约束 |
|---|---|---|
| id | bigserial PK | |
| paper_id | bigint FK papers | not null |
| title | varchar(128) | not null |
| start_at / end_at | timestamptz | not null，end_at > start_at |
| duration_min | int | 10–300 |
| shuffle_questions / shuffle_options | boolean | default false |
| status | varchar(16) | draft / published / closed |
| created_by | bigint FK users | |
| created_at | timestamptz | |

索引：`ix_exams_status_start`；发布时校验同一时间段无重叠冲突（可选，V1 软校验）。

### exam_sessions
| 列 | 类型 | 约束 |
|---|---|---|
| id | bigserial PK | |
| exam_id | bigint FK exams | not null |
| student_id | bigint FK users | not null |
| status | varchar(16) | in_progress / submitted / expired |
| started_at | timestamptz | |
| deadline_at | timestamptz | min(start_at + duration, end_at) |
| submitted_at | timestamptz | null |
| score | numeric(7,2) | null，提交后写入；满分上限 10000.00 |
| last_client_seq | int | default 0 |
| UNIQUE(exam_id, student_id) | | 防止重复会话 |

索引：`ux_exam_student`、`ix_sessions_student_status`。

### answers
| 列 | 类型 | 约束 |
|---|---|---|
| session_id | bigint FK exam_sessions | PK 组成 |
| question_id | bigint FK questions | PK 组成 |
| selected | jsonb | 选中 key 数组 |
| is_correct | boolean | null（未判分） |
| earned_points | numeric(5,2) | null |
| updated_at | timestamptz | |

### wrong_book
| 列 | 类型 | 约束 |
|---|---|---|
| student_id | bigint FK users | PK 组成 |
| question_id | bigint FK questions | PK 组成 |
| wrong_count | int | default 1 |
| last_wrong_at | timestamptz | |

### audit_logs
| 列 | 类型 | 约束 |
|---|---|---|
| id | bigserial PK | |
| actor_id | bigint FK users | null（系统操作） |
| action | varchar(64) | 如 `question.create`、`session.submit`、`permission.denied`（越权尝试，docs/12 §4） |
| target_type / target_id | varchar(32) / varchar(64) | |
| ip | varchar(45) | 系统操作记 `"-"`（契约 v1.0.3 CHG-035；inet 类型无法存 `-`） |
| user_agent | varchar(255) | |
| detail | jsonb | 脱敏后详情（禁止存密码/答案明文以外敏感信息） |
| created_at | timestamptz | |

索引：`ix_audit_created`、`ix_audit_actor_action`；保留 180 天（定时任务删除）。

## 3. 状态机

```
exam:    draft ──publish──► published ──close/到 wintime──► closed
session: in_progress ──submit──► submitted
                    └──deadline/到时长──► expired（自动判分，等价 submitted 可读）
paper:   draft ──freeze（首次发布考试时）──► frozen（不可再改）
```

## 4. 关键约束与不变量

1. `(exam_id, student_id)` 唯一 → 一人一次机会；需要补考时新建 exam。
2. 试卷发布后修改 → 409 `PAPER_FROZEN`；考试持有快照，历史成绩不受后续改题影响。
3. 交卷：`in_progress → submitted` 原子迁移；重复提交直接返回既有 score。
4. 自动保存：仅接受 `client_seq > last_client_seq`；服务端按会话行锁串行化。
5. 判分与提交同事务；成绩只读，不提供改分接口（V1）。
6. 软删除题目不物理删除，历史试卷与错题本引用仍可读。
7. 审计写入与业务写操作同一事务边界（失败则整体回滚），保证可追溯。
