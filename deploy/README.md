# deploy/ — 部署与运维（R6 Release 维护）

- **负责人**：R6 Release（preset: `release-sre`）
- **写作用域**：`deploy/**`、`.github/workflows/**`、`docs/70-runbook.md`、`docs/71-backup-restore.md`
- **首个任务卡**：T0.6（部署骨架）、T4.1（CI）、T4.2（staging）、T4.3/4.4（备份/监控）、T4.5（上线）

## 拓扑（ADR-008）

```
Internet → nginx (443, TLS, 静态资源, /api 反代)
              ├── api (uvicorn, 内网 8000, /api 前缀)
              ├── postgres (内网 5432, 仅 api 可访问)
              └── backup (cron 每日 pg_dump, 保留 7 天, 挂载卷)
```

## 环境

| 环境 | 用途 | 允许操作 |
|---|---|---|
| staging | 预发验证、备份/回滚演练 | 可重装、可清库 |
| production | 正式服务 | 变更必须人工审批；先 staging 演练；禁止自批自执行 |

## 交付物清单（G4 准入）

- [x] `deploy/docker-compose.yml`、`deploy/nginx.conf`、`deploy/Dockerfile.api`、`deploy/Dockerfile.web`（TASK-REL-001）
- [x] `.github/workflows/ci.yml`（lint/type/test/contract/build/镜像）（TASK-REL-000）
- [x] `docs/70-runbook.md`（上线、回滚、日志排障、健康检查、staging 冒烟、**§15 容量与并发配置**）（TASK-REL-001）
- [x] `docs/75-perf-baseline.md`（性能门槛实测矩阵 + 原生 PG/CI 复现手册；PERF-001）
- [x] api 容量参数：`WEB_CONCURRENCY` / `DB_POOL_SIZE` / `DB_MAX_OVERFLOW`（compose 透传，约束 `workers × (size+overflow) < postgres max_connections=100`）
- [x] `docs/71-backup-restore.md`（备份策略 + 恢复步骤 + 演练记录模板；演练执行待 REL-002）
- [ ] 监控：健康检查、错误率、慢请求、磁盘/备份告警（TASK-REL-003）
- [ ] 回滚演练留档（commit / 镜像 tag / 耗时）（TASK-REL-002）

## 文件地图（TASK-REL-001 交付）

```
deploy/
  docker-compose.yml      # 四服务拓扑（nginx/api/postgres/backup）
  .env.example            # 变量模板（仅占位）
  Dockerfile.api          # python 3.12-slim 多阶段，非 root
  Dockerfile.web          # node 构建 + nginx 运行
  docker-entrypoint.sh    # alembic upgrade head → uvicorn
  nginx.conf              # /api 反代、静态缓存、探针、TLS 占位
  backup/{backup,loop,restore}.sh
  scripts/smoke.sh        # staging 冒烟（自动落证据）
  evidence/               # 冒烟/演练证据（命名规则见其 README）
```

> 变量与部署步骤见 `docs/70-runbook.md`；备份/恢复见 `docs/71-backup-restore.md`。
