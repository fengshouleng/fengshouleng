/**
 * 管理后台（账号管理 + 审计日志）的契约镜像、表单模型与前端校验。
 *
 * 契约依据：`contracts/openapi.yaml` v1.0.3
 * - `GET/POST /users`、`PATCH /users/{user_id}`：仅 **admin**（docs/12-permission-matrix.md §1）；
 *   创建 201、422 字段校验、409 用户名冲突、403 角色不符；PATCH 支持 role / is_active / password；
 * - `GET /audit-logs`：仅 admin，**只读**（无写/删接口），默认最近 180 天，
 *   `from`/`to` 闭区间、按 `created_at` 倒序；`actor_id` 可为 **null**（系统操作，
 *   v1.0.3 / CHG-035），此时 `ip` 固定为 `"-"`；
 * - `UserPage` / `AuditLogPage` 均为 `{ items, total, page, page_size }`。
 *
 * 说明：`src/api/types.ts` 是只读脚手架镜像（本任务写作用域不含 `api/**`），
 * 因此账号/审计专属的 schema 与表单模型在本目录内维护，字段与契约逐项对齐。
 */
import { ApiError } from '../../api/client';
import { ROLE_LABELS, ROLES } from '../../api/types';
import type { Paginated, Role, User } from '../../api/types';

/** `page_size` 契约默认 20、上限 100（contracts/openapi.yaml v1.0.3）。 */
export const ADMIN_USERS_PAGE_SIZE = 20;
export const ADMIN_AUDIT_PAGE_SIZE = 20;

/** 契约 `UserCreate.username`：minLength 3 / maxLength 64。 */
export const USERNAME_MIN_LENGTH = 3;
export const USERNAME_MAX_LENGTH = 64;
/** 契约 `UserCreate.display_name` / `UserPatch.display_name`：maxLength 64。 */
export const DISPLAY_NAME_MAX_LENGTH = 64;
/** 契约 `UserCreate.password` / `UserPatch.password`：minLength 8 / maxLength 128。 */
export const PASSWORD_MIN_LENGTH = 8;
export const PASSWORD_MAX_LENGTH = 128;
/** 契约 `GET /audit-logs.action`：maxLength 64。 */
export const AUDIT_ACTION_MAX_LENGTH = 64;

/** 系统记录（`actor_id=null`，CHG-035）在 UI 中的操作者文案。 */
export const SYSTEM_ACTOR_LABEL = '系统';

export const ROLE_OPTIONS: ReadonlyArray<{ value: Role; label: string }> = ROLES.map((role) => ({
  value: role,
  label: ROLE_LABELS[role],
}));

/** 角色筛选值：`all` 表示不带 role 参数（不过滤）。 */
export type RoleFilter = Role | 'all';

export const ROLE_FILTER_OPTIONS: ReadonlyArray<{ value: RoleFilter; label: string }> = [
  { value: 'all', label: '全部' },
  ...ROLE_OPTIONS,
];

/** 角色标签配色（与 AppLayout 的角色 Tag 保持一致）。 */
export const ROLE_TAG_COLORS: Record<Role, string> = {
  student: 'green',
  teacher: 'blue',
  admin: 'gold',
};

/** 镜像 components.schemas.UserPage。 */
export type UserPage = Paginated<User>;

/** GET /users 查询参数（`role` 省略 = 不过滤）。 */
export interface UserListParams {
  role?: Role;
  page: number;
  page_size: number;
}

/** 镜像 components.schemas.UserCreate。 */
export interface UserCreate {
  username: string;
  display_name: string;
  role: Role;
  password: string;
}

/** 镜像 components.schemas.UserPatch（本页拆成角色/停用/重置密码三种最小补丁）。 */
export interface UserPatch {
  display_name?: string;
  role?: Role;
  is_active?: boolean;
  password?: string;
}

/** 创建账号表单模型（密码不 trim：空格是有效字符）。 */
export interface UserDraft {
  username: string;
  displayName: string;
  role: Role;
  password: string;
}

/** 表单字段级错误 / 422 回显。 */
export interface UserDraftErrors {
  username?: string;
  displayName?: string;
  role?: string;
  password?: string;
}

export function emptyUserDraft(): UserDraft {
  return { username: '', displayName: '', role: 'student', password: '' };
}

export function hasUserDraftErrors(errors: UserDraftErrors): boolean {
  return Object.values(errors).some((message) => Boolean(message));
}

/**
 * 创建账号的前端校验（与服务端 `UserCreate` 口径一致）：
 * 用户名 3–64、显示名 1–64、角色枚举、密码 8–128。
 */
export function validateUserDraft(draft: UserDraft): UserDraftErrors {
  const errors: UserDraftErrors = {};

  const username = draft.username.trim();
  if (!username) {
    errors.username = '请输入用户名';
  } else if (username.length < USERNAME_MIN_LENGTH) {
    errors.username = `用户名至少 ${USERNAME_MIN_LENGTH} 个字符`;
  } else if (username.length > USERNAME_MAX_LENGTH) {
    errors.username = `用户名不能超过 ${USERNAME_MAX_LENGTH} 个字符`;
  }

  const displayName = draft.displayName.trim();
  if (!displayName) {
    errors.displayName = '请输入显示名';
  } else if (displayName.length > DISPLAY_NAME_MAX_LENGTH) {
    errors.displayName = `显示名不能超过 ${DISPLAY_NAME_MAX_LENGTH} 个字符`;
  }

  if (!ROLES.includes(draft.role)) {
    errors.role = '请选择角色';
  }

  if (!draft.password) {
    errors.password = '请输入密码';
  } else if (draft.password.length < PASSWORD_MIN_LENGTH) {
    errors.password = `密码至少 ${PASSWORD_MIN_LENGTH} 位`;
  } else if (draft.password.length > PASSWORD_MAX_LENGTH) {
    errors.password = `密码不能超过 ${PASSWORD_MAX_LENGTH} 位`;
  }

  return errors;
}

/** 表单 → 契约 UserCreate（用户名/显示名去空格，密码原样）。 */
export function buildUserCreate(draft: UserDraft): UserCreate {
  return {
    username: draft.username.trim(),
    display_name: draft.displayName.trim(),
    role: draft.role,
    password: draft.password,
  };
}

/**
 * 422 message（FastAPI 口径，如 `body.username: String should have at least 3 characters`）
 * → 字段级中文提示；未识别的字段只保留表单级错误。
 */
export function parseUserValidationError(message: string): UserDraftErrors {
  const errors: UserDraftErrors = {};
  const text = message ?? '';

  if (/username|用户名/i.test(text)) {
    errors.username = `用户名不合法：${USERNAME_MIN_LENGTH}–${USERNAME_MAX_LENGTH} 个字符`;
  }
  if (/display_name|display name|显示名/i.test(text)) {
    errors.displayName = `显示名不合法：1–${DISPLAY_NAME_MAX_LENGTH} 个字符`;
  }
  if (/role|角色/i.test(text)) {
    errors.role = '角色不合法，请重新选择';
  }
  if (/password|密码/i.test(text)) {
    errors.password = `密码不合法：${PASSWORD_MIN_LENGTH}–${PASSWORD_MAX_LENGTH} 位`;
  }

  return errors;
}

/** 镜像 components.schemas.AuditLog（`actor_id` 可为 null，CHG-035）。 */
export interface AuditLog {
  id: number;
  actor_id: number | null;
  action: string;
  target_type: string;
  target_id: string;
  ip: string;
  user_agent?: string;
  detail?: Record<string, unknown>;
  created_at: string;
}

/** 镜像 components.schemas.AuditLogPage。 */
export type AuditLogPage = Paginated<AuditLog>;

/** GET /audit-logs 查询参数（from/to 为闭区间，ISO 8601 UTC）。 */
export interface AuditLogListParams {
  action?: string;
  actor_id?: number;
  from?: string;
  to?: string;
  page: number;
  page_size: number;
}

/** 审计筛选的输入草稿：时间沿用 `datetime-local` 的本地字符串，查询时转 ISO。 */
export interface AuditFilterDraft {
  action: string;
  actorId: string;
  from: string;
  to: string;
}

export interface AuditFilterErrors {
  actorId?: string;
  from?: string;
  to?: string;
  range?: string;
}

/** 通过校验后真正用于请求的筛选值。 */
export interface AuditFilterValues {
  action?: string;
  actor_id?: number;
  from?: string;
  to?: string;
}

export function emptyAuditFilterDraft(): AuditFilterDraft {
  return { action: '', actorId: '', from: '', to: '' };
}

export function hasAuditFilterErrors(errors: AuditFilterErrors): boolean {
  return Object.values(errors).some((message) => Boolean(message));
}

/** `datetime-local` 字符串 → Date（本地时区）；空值/非法值返回 null。 */
export function parseDateTimeLocal(value: string): Date | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/**
 * 审计筛选校验：actor_id 必须为正整数；from/to 可解析且 from 不得晚于 to
 * （契约 `from`/`to` 为闭区间）。
 */
export function validateAuditFilter(draft: AuditFilterDraft): AuditFilterErrors {
  const errors: AuditFilterErrors = {};

  const actor = draft.actorId.trim();
  if (actor) {
    const parsed = Number(actor);
    if (!/^\d+$/.test(actor) || !Number.isSafeInteger(parsed) || parsed <= 0) {
      errors.actorId = '操作者 ID 需为正整数';
    }
  }

  const from = parseDateTimeLocal(draft.from);
  if (draft.from && !from) errors.from = '开始时间格式不正确';
  const to = parseDateTimeLocal(draft.to);
  if (draft.to && !to) errors.to = '结束时间格式不正确';
  if (from && to && from.getTime() > to.getTime()) {
    errors.range = '开始时间不能晚于结束时间';
  }

  return errors;
}

/**
 * 草稿 → 请求参数：空文本不传（服务端按默认最近 180 天处理），
 * `from`/`to` 由本地 `datetime-local` 转 ISO 8601 UTC。
 */
export function buildAuditFilter(draft: AuditFilterDraft): AuditFilterValues {
  const action = draft.action.trim();
  const actor = draft.actorId.trim();
  const from = parseDateTimeLocal(draft.from);
  const to = parseDateTimeLocal(draft.to);

  return {
    action: action || undefined,
    actor_id: actor ? Number(actor) : undefined,
    from: from ? from.toISOString() : undefined,
    to: to ? to.toISOString() : undefined,
  };
}

/** ISO 8601 → 本地「YYYY-MM-DD HH:mm」；非法值原样返回。 */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const pad = (input: number): string => String(input).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(
    date.getHours(),
  )}:${pad(date.getMinutes())}`;
}

/** 审计操作者：系统记录（null / 缺省）显示「系统」，用户操作显示 `#id`。 */
export function formatActor(actorId: number | null | undefined): string {
  if (actorId === null || actorId === undefined) return SYSTEM_ACTOR_LABEL;
  return `#${actorId}`;
}

/** 审计目标：`target_type #target_id`，例如 `question #7`。 */
export function formatTarget(targetType: string, targetId: string): string {
  const type = targetType.trim();
  const id = targetId.trim();
  if (type && id) return `${type} #${id}`;
  return type || id || '—';
}

/**
 * 账号接口错误 → 中文提示（契约错误体只有 `{code,message,request_id}`，按 status/code 分支）：
 * - 403 → 仅 admin 可管理账号（docs/12 §2 第 3 步）
 * - 404 → 账号不存在
 * - 409 → 创建时用户名已被占用
 * - 422 → 字段校验失败（结构化回显见 `parseUserValidationError`）
 */
export function describeUserApiError(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return '登录状态已失效，请重新登录';
    if (error.status === 403) return '没有权限执行该操作：仅管理员可管理账号';
    if (error.status === 404) return '账号不存在或已被删除';
    if (error.status === 409) return '用户名已存在，请更换后重试';
    if (error.status === 422) {
      return error.message ? `参数校验失败：${error.message}` : '参数校验失败，请检查填写内容';
    }
    return error.message || fallback;
  }
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

/** 审计日志接口错误 → 中文提示（403：非 admin）。 */
export function describeAuditApiError(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return '登录状态已失效，请重新登录';
    if (error.status === 403) return '没有权限查看审计日志：仅管理员可访问';
    return error.message || fallback;
  }
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}
