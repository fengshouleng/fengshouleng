import type { QueryFunctionContext } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { API_BASE, ApiError, api, apiQueryFn, setUnauthorizedHandler } from '../src/api/client';
import type { UnauthorizedDetail } from '../src/api/client';
import type { User } from '../src/api/types';
import { AUTH_STORAGE_KEY, clearSession, getAuthState, setSession } from '../src/stores/auth';

const student: User = {
  id: 1,
  username: 'stu001',
  display_name: '学生甲',
  role: 'student',
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
};

const fetchMock = vi.fn();

interface MockResponseInit {
  status: number;
  body?: unknown;
  contentType?: string;
  statusText?: string;
}

/** 轻量 Response 替身：只实现 client.ts 实际读取的字段（jsdom 不提供 fetch/Response）。 */
function mockResponse({
  status,
  body,
  contentType = 'application/json',
  statusText = '',
}: MockResponseInit): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText,
    headers: {
      get: (name: string) => (name.toLowerCase() === 'content-type' ? contentType : null),
    },
    text: async () => {
      if (body === undefined) return '';
      return typeof body === 'string' ? body : JSON.stringify(body);
    },
  } as unknown as Response;
}

interface CapturedRequest {
  url: string;
  init: RequestInit;
}

function headersOf(init: RequestInit): Record<string, string> {
  return (init.headers ?? {}) as unknown as Record<string, string>;
}

function captured(index = -1): CapturedRequest {
  const calls = fetchMock.mock.calls as unknown as Array<[RequestInfo | URL, RequestInit?]>;
  const call = calls.at(index);
  if (!call) throw new Error('fetch 未被调用');
  const [input, init] = call;
  const url =
    typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url;
  return { url, init: init ?? {} };
}

async function expectApiError(promise: Promise<unknown>): Promise<ApiError> {
  const error: unknown = await promise.catch((err: unknown) => err);
  expect(error).toBeInstanceOf(ApiError);
  expect(error).toBeInstanceOf(Error);
  return error as ApiError;
}

describe('api/client', () => {
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
    setUnauthorizedHandler(null);
    clearSession();
    window.sessionStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setUnauthorizedHandler(null);
    clearSession();
  });

  it('默认 API_BASE 为 /api；setSession 持久化 user 与 csrf_token', () => {
    expect(API_BASE).toBe('/api');

    setSession(student, 'csrf-1');
    expect(getAuthState()).toEqual({ user: student, csrfToken: 'csrf-1' });
    expect(window.sessionStorage.getItem(AUTH_STORAGE_KEY)).toContain('"csrfToken":"csrf-1"');

    clearSession();
    expect(getAuthState().user).toBeNull();
    expect(getAuthState().csrfToken).toBeNull();
    expect(window.sessionStorage.getItem(AUTH_STORAGE_KEY)).toBeNull();
  });

  it('GET：带 credentials=include、不注入 X-CSRF-Token、query 正常序列化', async () => {
    setSession(student, 'csrf-1');
    fetchMock.mockResolvedValueOnce(mockResponse({ status: 200, body: { items: [], total: 0 } }));

    const data = await api.get<{ total: number }>('/questions', {
      query: { page: 2, type: 'single', keyword: undefined },
    });

    expect(data).toEqual({ items: [], total: 0 });
    const { url, init } = captured();
    expect(url).toBe('/api/questions?page=2&type=single');
    expect(init.method).toBe('GET');
    expect(init.credentials).toBe('include');
    expect(headersOf(init)['Accept']).toBe('application/json');
    expect(headersOf(init)['X-CSRF-Token']).toBeUndefined();
  });

  it('非 GET：自动注入 X-CSRF-Token 并序列化 JSON body', async () => {
    setSession(student, 'csrf-abc');
    fetchMock.mockResolvedValueOnce(mockResponse({ status: 201, body: { id: 9 } }));

    await api.post('/answers', { answer: ['A'] });

    const { init } = captured();
    const headers = headersOf(init);
    expect(init.method).toBe('POST');
    expect(headers['X-CSRF-Token']).toBe('csrf-abc');
    expect(headers['Content-Type']).toBe('application/json');
    expect(init.body).toBe(JSON.stringify({ answer: ['A'] }));
  });

  it('csrf:false 可显式跳过注入（登录接口免 CSRF）', async () => {
    setSession(student, 'csrf-abc');
    fetchMock.mockResolvedValueOnce(
      mockResponse({ status: 200, body: { user: student, csrf_token: 'new' } }),
    );

    await api.post(
      '/auth/login',
      { username: 'stu001', password: 'secret' },
      { csrf: false, redirectOn401: false },
    );

    expect(headersOf(captured().init)['X-CSRF-Token']).toBeUndefined();
  });

  it('401：抛 ApiError、清空会话并触发跳登录 handler', async () => {
    setSession(student, 'csrf-abc');
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    fetchMock.mockResolvedValueOnce(
      mockResponse({
        status: 401,
        body: { code: 'AUTH_REQUIRED', message: '会话已失效', request_id: 'req-401' },
      }),
    );

    const error = await expectApiError(api.get('/auth/me'));

    expect(error.status).toBe(401);
    expect(error.code).toBe('AUTH_REQUIRED');
    expect(error.message).toBe('会话已失效');
    expect(error.requestId).toBe('req-401');
    expect(error.isUnauthorized).toBe(true);

    expect(getAuthState().user).toBeNull();
    expect(getAuthState().csrfToken).toBeNull();
    expect(onUnauthorized).toHaveBeenCalledTimes(1);

    const detail = onUnauthorized.mock.calls[0]?.[0] as UnauthorizedDetail;
    expect(detail.error).toBe(error);
    expect(detail.returnTo).toBe('/');
  });

  it('错误码映射：优先服务端 code，缺失时按 HTTP 状态兜底', async () => {
    fetchMock
      .mockResolvedValueOnce(
        mockResponse({
          status: 403,
          body: { code: 'FORBIDDEN', message: '无权限', request_id: 'req-403' },
        }),
      )
      .mockResolvedValueOnce(
        mockResponse({
          status: 409,
          body: { code: 'EXAM_NOT_STARTED', message: '考试未开始', request_id: 'req-409' },
        }),
      )
      .mockResolvedValueOnce(mockResponse({ status: 404, body: undefined }))
      .mockResolvedValueOnce(mockResponse({ status: 500, body: 'boom', contentType: 'text/plain' }));

    const forbidden = await expectApiError(api.get('/users'));
    expect(forbidden.code).toBe('FORBIDDEN');
    expect(forbidden.message).toBe('无权限');
    expect(forbidden.requestId).toBe('req-403');
    expect(forbidden.isForbidden).toBe(true);

    const conflict = await expectApiError(api.get('/exams/1/sessions'));
    expect(conflict.code).toBe('EXAM_NOT_STARTED');
    expect(conflict.isConflict).toBe(true);

    const notFound = await expectApiError(api.get('/questions/1'));
    expect(notFound.code).toBe('NOT_FOUND');
    expect(notFound.message).toBe('请求的资源不存在');
    expect(notFound.requestId).toBeNull();

    const serverError = await expectApiError(api.get('/healthz'));
    expect(serverError.status).toBe(500);
    expect(serverError.code).toBe('INTERNAL_ERROR');
    expect(serverError.message).toBe('boom');
  });

  it('204 No Content 返回 undefined（登出等无响应体接口）', async () => {
    fetchMock.mockResolvedValueOnce(mockResponse({ status: 204, body: undefined }));

    await expect(api.delete('/questions/1')).resolves.toBeUndefined();
  });

  it('网络异常映射为 NETWORK_ERROR（status 0）', async () => {
    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'));

    const error = await expectApiError(api.get('/healthz'));
    expect(error.status).toBe(0);
    expect(error.code).toBe('NETWORK_ERROR');
  });

  it('apiQueryFn 透传 query 与 abort signal，供 TanStack Query 直接使用', async () => {
    fetchMock.mockResolvedValueOnce(mockResponse({ status: 200, body: { items: [] } }));
    const controller = new AbortController();
    const queryFn = apiQueryFn<{ items: unknown[] }>('/questions', {
      query: { page: 1, tag: '代数' },
    });

    await queryFn({
      queryKey: ['questions', { page: 1 }],
      signal: controller.signal,
      meta: undefined,
    } as unknown as QueryFunctionContext);

    const { url, init } = captured();
    expect(url).toBe('/api/questions?page=1&tag=%E4%BB%A3%E6%95%B0');
    expect(init.signal).toBe(controller.signal);
  });
});
