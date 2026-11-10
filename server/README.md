# server/ — 后端（FastAPI）

- **负责人**：R3 Backend（preset: `backend-impl`）
- **写作用域**：`server/app/**`、`server/tests/**`、`server/migrations/**`、`server/pyproject.toml`
- **首个任务卡**：`contracts/tasks/TASK-BE-000.yaml`（脚手架已建，业务代码尚未生成）
- **契约**：唯一真相 `contracts/openapi.yaml`；后端不得自行扩接口

## 目录（TASK-BE-000 生成）

```
server/
  app/
    main.py          应用工厂 + request_id + 统一异常处理
    settings.py      pydantic-settings 环境变量
    db.py            SQLAlchemy engine/session
    auth/ users/ questions/ papers/ exams/ sessions/ scoring/ reports/ audit/
  migrations/        Alembic
  tests/             pytest（unit + contract）
  pyproject.toml
```

## 命令（在 CI 或可执行环境运行）

```bash
cd server
pip install -e ".[dev]"
pytest -q
ruff check .
mypy app
uvicorn app.main:app --reload --port 8000
```

> 注意：当前开发机 `pwsh` 不可用（0xC0000142），上述命令由 CI（T4.1）执行并回传证据。
> 本目录当前只有占位 README，代码由 R3 按任务卡生成，**禁止手写业务逻辑绕过契约**。
