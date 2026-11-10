# web/ — 前端（React 18 + Vite + TS + Ant Design + TanStack Query）

- **负责人**：R4 Frontend（preset: `frontend-impl`）
- **写作用域**：`web/**`
- **任务卡**：`contracts/tasks/TASK-FE-000.yaml`（脚手架，已交付）、`TASK-FE-001.yaml`（登录与角色路由）
- **契约类型**：由 `openapi-typescript` 从 `contracts/openapi.yaml` 生成，禁止手写接口类型
  （见 `src/api/README.md`）

## 目录

```
web/
  src/
    api/          client.ts（Cookie/CSRF/401/错误码）+ types.ts（手写镜像，以 schema.d.ts 为准）
    components/   AppLayout.tsx（侧边栏 + 顶栏 + 用户信息 + 登出）
    pages/        HomePage.tsx / NotFound.tsx / login/LoginPage.tsx
    stores/       auth.ts（内存 + sessionStorage 保存 user 与 csrf_token）
    routes.tsx    路由骨架：/login + 受保护布局 + 404
    App.tsx       ConfigProvider + QueryClientProvider + RouterProvider + 401 → router 跳转
    main.tsx      入口
  tests/          client.test.ts（401 / CSRF / 错误码映射）+ setup.ts
  package.json vite.config.ts tsconfig.json tsconfig.node.json .eslintrc.cjs .env.example
```

## 约定

- **所有请求走 `src/api/client.ts`**：`credentials: 'include'`；非 GET 自动注入 `X-CSRF-Token`
  （token 来自内存 auth store，即登录响应或 `GET /auth/me`）；401 → 清空会话并跳登录；
  错误统一抛 `ApiError { status, code, message, requestId }`。
- **TanStack Query**：GET 请求使用 `apiQueryFn(path, options)` 作为 `queryFn`（自动透传 abort signal）。
- 环境变量：`VITE_API_BASE` 默认 `/api`（dev 由 Vite 反代 `http://localhost:8000`，生产由 Nginx 反代）。
- E2E 依赖的关键元素必须有 `data-testid`（命名：`页面-元素`，如 `login-submit`、`app-logout`）。
- 业务页面（题库/组卷/考试/结果/管理）从 TASK-FE-002 起实现，本目录不做占位死链。

## 命令（CI 执行）

```bash
pnpm install
pnpm gen:types          # 生成 src/api/schema.d.ts（首次开发/CI 需要）
pnpm lint
pnpm tsc --noEmit
pnpm test
pnpm build              # = tsc --noEmit && vite build
```

本地开发：`pnpm dev`（http://localhost:5173，`/api` 反代到 http://localhost:8000）。
