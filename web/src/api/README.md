# src/api —— 契约类型与统一请求层

## schema.d.ts（生成物，勿手改）

```bash
# 在 web/ 下执行
pnpm gen:types      # = openapi-typescript ../contracts/openapi.yaml -o src/api/schema.d.ts
```

- 真相来源：`contracts/openapi.yaml` v1.0（docs/10-arch.md ADR-003）。
- `schema.d.ts` 由脚本生成，**不手工编辑**；改接口 = 改契约 + 重过 G2。
- 本脚手架阶段尚未提交生成文件（此目录以 README 占位）。CI/首次开发请先执行
  `pnpm install && pnpm gen:types`，生成后的 `schema.d.ts` 可提交入库，供 tsc/build 使用。
- `src/api/types.ts` 是手写镜像类型（Role / User / Paginated 等），**以 schema.d.ts 为准**，
  仅用于脚手架阶段编译与中文文案常量；生成 schema 后新代码应使用
  `components['schemas'][...]`。

## client.ts（唯一请求出口）

所有 HTTP 请求必须走 `src/api/client.ts`，禁止在页面里直接 `fetch`：

| 能力 | 说明 |
|---|---|
| Cookie | 固定 `credentials: 'include'`（服务端 Session，HttpOnly `session_id`） |
| CSRF | 非 GET/HEAD 自动注入 `X-CSRF-Token`，值来自内存 auth store（`GET /auth/me` 或登录响应下发） |
| 401 | 清空会话 → 已注册的 `UnauthorizedHandler`（App 中挂到 router）→ 兜底整页跳 `/login` |
| 错误体 | 统一解析 `{ code, message, request_id }` 并抛 `ApiError`；缺失时按 HTTP 状态映射默认 code |
| TanStack Query | `apiQueryFn(path, options)` 直接作为 `queryFn`，透传 `signal` 支持取消 |

示例：

```ts
import { api, apiQueryFn, queryKeys } from '@/api/client'; // 本项目使用相对路径导入

const me = await api.get<MeResponse>('/auth/me');
const questions = useQuery({ queryKey: ['questions', params], queryFn: apiQueryFn<QuestionPage>('/questions', { query: params }) });
```
