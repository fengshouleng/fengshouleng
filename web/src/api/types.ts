/**
 * 手写镜像类型（hand-written mirror）。
 *
 * ⚠️ 以 `src/api/schema.d.ts` 为准：该文件由 `pnpm gen:types`
 * （openapi-typescript contracts/openapi.yaml）生成，是契约的唯一真相。
 * 本文件只是为了让脚手架阶段（尚未生成 schema）也能通过 tsc 而存在的
 * 最小镜像，字段变动必须回归契约 `contracts/openapi.yaml`（v1.0）。
 *
 * 生成 schema 后，页面应优先使用：
 *   import type { components } from './schema';
 *   type User = components['schemas']['User'];
 * 本文件届时仅用于语义化别名（Role / Paginated 等）与中文文案常量。
 */

export const ROLES = ['student', 'teacher', 'admin'] as const;
export type Role = (typeof ROLES)[number];

export const ROLE_LABELS: Record<Role, string> = {
  student: '学生',
  teacher: '教师',
  admin: '管理员',
};

/** 镜像 `components.schemas.User` */
export interface User {
  id: number;
  username: string;
  display_name: string;
  role: Role;
  is_active: boolean;
  /** ISO 8601 UTC */
  created_at: string;
}

/** 镜像 `components.schemas.LoginRequest` */
export interface LoginRequest {
  username: string;
  password: string;
}

/** 镜像 `components.schemas.LoginResponse` / `MeResponse`（两者结构一致：user + csrf_token） */
export interface AuthPayload {
  user: User;
  csrf_token: string;
}

export type LoginResponse = AuthPayload;
export type MeResponse = AuthPayload;

/** 镜像 `components.schemas.Error`：统一错误体 `{ code, message, request_id }` */
export interface ApiErrorBody {
  code: string;
  message: string;
  /** uuid，便于对日志排查 */
  request_id: string;
}

/**
 * 分页包装：契约中各 `*Page` 结构一致（items/total/page/page_size）。
 * page 从 1 开始，page_size 默认 20、最大 100。
 */
export interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

/** 分页查询参数（page_size 上限 100 由服务端与前端共同约束） */
export interface PageQuery {
  page?: number;
  page_size?: number;
}

/** 契约与 docs/10-arch.md「错误码约定」中出现的错误码，便于页面做分支提示。 */
export const ERROR_CODES = {
  AUTH_REQUIRED: 'AUTH_REQUIRED',
  AUTH_INVALID_CREDENTIALS: 'AUTH_INVALID_CREDENTIALS',
  CSRF_INVALID: 'CSRF_INVALID',
  FORBIDDEN: 'FORBIDDEN',
  NOT_FOUND: 'NOT_FOUND',
  VALIDATION_ERROR: 'VALIDATION_ERROR',
  EXAM_NOT_STARTED: 'EXAM_NOT_STARTED',
  EXAM_EXPIRED: 'EXAM_EXPIRED',
  ALREADY_SUBMITTED: 'ALREADY_SUBMITTED',
} as const;

export type ErrorCode = (typeof ERROR_CODES)[keyof typeof ERROR_CODES];
