import type { QueryFunction } from '@tanstack/react-query';

import { clearSession, getCsrfToken } from '../stores/auth';

/**
 * 统一请求层（唯一 HTTP 出口）。
 *
 * 契约依据：
 * - 认证：服务端 Session，HttpOnly Cookie `session_id`（8h），故固定 `credentials: 'include'`
 *   （contracts/openapi.yaml#/components/securitySchemes/sessionCookie）
 * - CSRF：所有非 GET 请求带 `X-CSRF-Token`（token 来自 `GET /auth/me` 或登录响应）
 * - 错误：统一 `{ code, message, request_id }`（#/components/schemas/Error）
 * - 401：清空会话并跳登录（docs/12-permission-matrix.md §3）
 */

const DEFAULT_API_BASE = '/api';

function normalizeBase(raw: string | undefined): string {
  const value = raw?.trim();
  if (!value) return DEFAULT_API_BASE;
  return value.endsWith('/') ? value.slice(0, -1) : value;
}

/** 由 `VITE_API_BASE` 决定，默认 `/api`（dev 由 Vite 反代，生产由 Nginx 反代）。 */
export const API_BASE = normalizeBase(import.meta.env.VITE_API_BASE);

export type HttpMethod = 'GET' | 'HEAD' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

export type QueryParamValue = string | number | boolean | null | undefined;

export interface RequestOptions {
  method?: HttpMethod;
  /** 查询参数：undefined / null 会被忽略，数组会展开为重复 key。 */
  query?: Record<string, QueryParamValue | QueryParamValue[]>;
  /** 对象走 JSON.stringify；FormData（如题目导入）原样发送。 */
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  /** 是否注入 X-CSRF-Token；默认对所有非安全方法（非 GET/HEAD/OPTIONS）注入。 */
  csrf?: boolean;
  /** 401 时是否触发全局跳登录；默认 true（登录接口本身应传 false）。 */
  redirectOn401?: boolean;
}

/** HTTP 状态 → 契约默认错误码（服务端返回了 code 时以服务端为准）。 */
const STATUS_CODE_MAP: Record<number, string> = {
  400: 'BAD_REQUEST',
  401: 'AUTH_REQUIRED',
  403: 'FORBIDDEN',
  404: 'NOT_FOUND',
  409: 'CONFLICT',
  422: 'VALIDATION_ERROR',
  429: 'RATE_LIMITED',
  500: 'INTERNAL_ERROR',
  502: 'BAD_GATEWAY',
  503: 'SERVICE_UNAVAILABLE',
  504: 'GATEWAY_TIMEOUT',
};

const STATUS_MESSAGE_MAP: Record<number, string> = {
  400: '请求格式错误',
  401: '未登录或会话已失效，请重新登录',
  403: '没有权限执行该操作',
  404: '请求的资源不存在',
  409: '当前状态不允许该操作',
  422: '参数校验失败',
  429: '请求过于频繁，请稍后重试',
  500: '服务器内部错误',
  502: '网关错误',
  503: '服务暂不可用',
  504: '网关超时',
};

export function defaultErrorCode(status: number): string {
  return STATUS_CODE_MAP[status] ?? (status === 0 ? 'NETWORK_ERROR' : 'HTTP_ERROR');
}

export interface ApiErrorInit {
  status: number;
  code: string;
  message: string;
  requestId?: string | null;
  /** 解析后的错误体（可能为 null，例如 502 的 HTML 错误页）。 */
  body?: unknown;
}

/** 契约统一错误：始终带 status / code / requestId，便于页面分支与问题追踪。 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;
  readonly body: unknown;

  constructor(init: ApiErrorInit) {
    super(init.message);
    this.name = 'ApiError';
    this.status = init.status;
    this.code = init.code;
    this.requestId = init.requestId ?? null;
    this.body = init.body ?? null;
  }

  get isUnauthorized(): boolean {
    return this.status === 401;
  }

  get isForbidden(): boolean {
    return this.status === 403;
  }

  get isConflict(): boolean {
    return this.status === 409;
  }
}

export interface UnauthorizedDetail {
  error: ApiError;
  /** 触发 401 时的站内路径（含 query），供登录后回跳。 */
  returnTo: string;
}

export type UnauthorizedHandler = (detail: UnauthorizedDetail) => void;

let unauthorizedHandler: UnauthorizedHandler | null = null;

/** 由 App.tsx 注册（接 react-router）；传 null 恢复默认整页跳转。 */
export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  unauthorizedHandler = handler;
}

/** 登录页地址（带 returnTo，登录成功后原路返回）。 */
export function loginRedirectUrl(returnTo: string): string {
  return `/login?returnTo=${encodeURIComponent(returnTo)}`;
}

function currentReturnTo(): string {
  if (typeof window === 'undefined') return '/';
  const { pathname, search } = window.location;
  if (pathname === '/login' || pathname.startsWith('/login/')) return '/';
  return `${pathname}${search}` || '/';
}

/**
 * 401 统一处理：清空本地会话 → 交给已注册的 handler（通常是 router.navigate）
 * → 未注册时兜底整页跳转（保证鉴权失效不会停留在空白页）。
 */
export function handleUnauthorized(error: ApiError): void {
  const returnTo = currentReturnTo();
  clearSession();
  if (unauthorizedHandler) {
    unauthorizedHandler({ error, returnTo });
    return;
  }
  if (typeof window !== 'undefined') {
    window.location.assign(loginRedirectUrl(returnTo));
  }
}

interface ParsedErrorBody {
  code?: string;
  message?: string;
  request_id?: string;
}

function pickString(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() !== '' ? value : undefined;
}

async function parseErrorBody(response: Response): Promise<ParsedErrorBody | null> {
  let text = '';
  try {
    text = await response.text();
  } catch {
    return null;
  }
  if (!text) return null;

  const contentType = response.headers.get('content-type') ?? '';
  if (contentType.toLowerCase().includes('json')) {
    try {
      const parsed: unknown = JSON.parse(text);
      if (typeof parsed === 'object' && parsed !== null) {
        const record = parsed as Record<string, unknown>;
        return {
          code: pickString(record.code),
          message: pickString(record.message),
          request_id: pickString(record.request_id),
        };
      }
    } catch {
      // 非法 JSON：回退为纯文本消息
    }
  }
  return { message: text.slice(0, 300) };
}

async function toApiError(response: Response): Promise<ApiError> {
  const body = await parseErrorBody(response);
  const statusText = response.statusText || '';
  const fallbackMessage =
    STATUS_MESSAGE_MAP[response.status] ?? (statusText || `请求失败（HTTP ${response.status}）`);
  return new ApiError({
    status: response.status,
    code: body?.code ?? defaultErrorCode(response.status),
    message: body?.message ?? fallbackMessage,
    requestId: body?.request_id ?? null,
    body,
  });
}

async function parseSuccessBody<T>(response: Response): Promise<T> {
  if (response.status === 204 || response.status === 205) return undefined as unknown as T;
  const text = await response.text();
  if (text === '') return undefined as unknown as T;
  const contentType = response.headers.get('content-type') ?? '';
  if (contentType.toLowerCase().includes('json')) {
    try {
      return JSON.parse(text) as T;
    } catch {
      throw new ApiError({
        status: response.status,
        code: 'INVALID_JSON_RESPONSE',
        message: '服务端返回了无法解析的 JSON',
        body: text.slice(0, 300),
      });
    }
  }
  return text as unknown as T;
}

const UNSAFE_METHODS: ReadonlySet<HttpMethod> = new Set<HttpMethod>([
  'POST',
  'PUT',
  'PATCH',
  'DELETE',
]);

function buildUrl(path: string, query?: RequestOptions['query']): string {
  const url = /^https?:\/\//.test(path)
    ? path
    : `${API_BASE}${path.startsWith('/') ? path : `/${path}`}`;
  if (!query) return url;

  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    const values = Array.isArray(value) ? value : [value];
    for (const item of values) {
      if (item === undefined || item === null) continue;
      search.append(key, String(item));
    }
  }
  const qs = search.toString();
  if (!qs) return url;
  return `${url}${url.includes('?') ? '&' : '?'}${qs}`;
}

/** 核心请求函数：Cookie / CSRF / 401 / 错误体解析都在此收敛。 */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? 'GET';
  const headers: Record<string, string> = { Accept: 'application/json', ...options.headers };
  let body: BodyInit | undefined;

  if (options.body !== undefined && options.body !== null) {
    if (typeof FormData !== 'undefined' && options.body instanceof FormData) {
      body = options.body;
    } else {
      headers['Content-Type'] = headers['Content-Type'] ?? 'application/json';
      body = JSON.stringify(options.body);
    }
  }

  if (options.csrf ?? UNSAFE_METHODS.has(method)) {
    const token = getCsrfToken();
    if (token) headers['X-CSRF-Token'] = token;
  }

  let response: Response;
  try {
    response = await fetch(buildUrl(path, options.query), {
      method,
      headers,
      credentials: 'include',
      body,
      signal: options.signal,
    });
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') throw error;
    throw new ApiError({
      status: 0,
      code: 'NETWORK_ERROR',
      message: '网络请求失败，请检查网络连接后重试',
      requestId: null,
    });
  }

  if (!response.ok) {
    const apiError = await toApiError(response);
    if (apiError.status === 401 && (options.redirectOn401 ?? true)) {
      handleUnauthorized(apiError);
    }
    throw apiError;
  }

  return parseSuccessBody<T>(response);
}

type ReadOptions = Omit<RequestOptions, 'method' | 'body'>;

/** 业务代码使用的类型化快捷方法；所有请求最终都走 `request`。 */
export const api = {
  get<T>(path: string, options?: ReadOptions): Promise<T> {
    return request<T>(path, { ...options, method: 'GET' });
  },
  delete<T = void>(path: string, options?: ReadOptions): Promise<T> {
    return request<T>(path, { ...options, method: 'DELETE' });
  },
  post<T>(path: string, body?: unknown, options?: ReadOptions): Promise<T> {
    return request<T>(path, { ...options, method: 'POST', body });
  },
  put<T>(path: string, body?: unknown, options?: ReadOptions): Promise<T> {
    return request<T>(path, { ...options, method: 'PUT', body });
  },
  patch<T>(path: string, body?: unknown, options?: ReadOptions): Promise<T> {
    return request<T>(path, { ...options, method: 'PATCH', body });
  },
};

/**
 * 暴露给 TanStack Query 的 GET queryFn：透传 abort signal，组件卸载时自动取消请求。
 *
 * ```ts
 * useQuery({ queryKey: ['questions', q], queryFn: apiQueryFn<QuestionPage>('/questions', { query: q }) })
 * ```
 */
export function apiQueryFn<T>(path: string, options: ReadOptions = {}): QueryFunction<T> {
  return (context) => request<T>(path, { ...options, method: 'GET', signal: context.signal });
}

/** Query key 工厂：避免各处手写字符串导致缓存失效不一致。 */
export const queryKeys = {
  me: ['auth', 'me'] as const,
  users: (params: Record<string, QueryParamValue> = {}) => ['users', params] as const,
  questions: (params: Record<string, QueryParamValue> = {}) => ['questions', params] as const,
  papers: (params: Record<string, QueryParamValue> = {}) => ['papers', params] as const,
  exams: (params: Record<string, QueryParamValue> = {}) => ['exams', params] as const,
};
