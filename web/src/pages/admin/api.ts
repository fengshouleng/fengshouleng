/**
 * 管理后台接口封装：所有请求都经 `src/api/client.ts`（Cookie / CSRF / 401 / 统一 Error）。
 *
 * 契约：contracts/openapi.yaml v1.0.3
 * - GET   /users                 用户列表（role 筛选 + 分页），仅 admin
 * - POST  /users                 创建账号（201；422 校验；409 用户名冲突）
 * - PATCH /users/{user_id}       修改角色 / 停用启用 / 重置密码（均为最小补丁）
 * - GET   /audit-logs            审计日志（只读，无写/删接口）
 *
 * 页面只依赖本模块，组件测试对 `api` 做 mock 即可覆盖全部网络交互。
 */
import { api } from '../../api/client';
import type { Role, User } from '../../api/types';

import type {
  AuditLogListParams,
  AuditLogPage,
  UserCreate,
  UserListParams,
  UserPage,
  UserPatch,
} from './model';

export function listUsers(params: UserListParams): Promise<UserPage> {
  return api.get<UserPage>('/users', {
    query: {
      role: params.role,
      page: params.page,
      page_size: params.page_size,
    },
  });
}

export function createUser(payload: UserCreate): Promise<User> {
  return api.post<User>('/users', payload);
}

/** PATCH /users/{id}：契约 `UserPatch`，只提交需要变更的字段。 */
export function patchUser(id: number, payload: UserPatch): Promise<User> {
  return api.patch<User>(`/users/${id}`, payload);
}

/** 停用/启用（契约 `is_active`）；停用后该账号现有会话立即失效（AC-04）。 */
export function setUserActive(id: number, isActive: boolean): Promise<User> {
  return patchUser(id, { is_active: isActive });
}

export function changeUserRole(id: number, role: Role): Promise<User> {
  return patchUser(id, { role });
}

/** 重置密码：契约 minLength 8 / maxLength 128，由表单先校验。 */
export function resetUserPassword(id: number, password: string): Promise<User> {
  return patchUser(id, { password });
}

/** 审计日志列表：action 关键字 / actor_id / from-to 闭区间 / 分页。 */
export function listAuditLogs(params: AuditLogListParams): Promise<AuditLogPage> {
  return api.get<AuditLogPage>('/audit-logs', {
    query: {
      action: params.action,
      actor_id: params.actor_id,
      from: params.from,
      to: params.to,
      page: params.page,
      page_size: params.page_size,
    },
  });
}
