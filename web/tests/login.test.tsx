import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { RouterProvider, createMemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, api } from '../src/api/client';
import type { MeResponse, User } from '../src/api/types';
import { safeReturnTo } from '../src/pages/login/safeReturnTo';
import { routes } from '../src/routes';
import { clearSession, getCurrentUser, setSession } from '../src/stores/auth';

/**
 * jsdom 缺少 antd 响应式组件（Layout.Sider breakpoint）依赖的浏览器 API，
 * 这里补最小桩；不改变被测逻辑。
 */
function installBrowserStubs(): void {
  if (typeof window.matchMedia !== 'function') {
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      configurable: true,
      value: (query: string) =>
        ({
          matches: false,
          media: query,
          onchange: null,
          addListener: () => undefined,
          removeListener: () => undefined,
          addEventListener: () => undefined,
          removeEventListener: () => undefined,
          dispatchEvent: () => false,
        }) as unknown as MediaQueryList,
    });
  }

  if (typeof window.ResizeObserver === 'undefined') {
    class ResizeObserverStub {
      observe = (): void => undefined;
      unobserve = (): void => undefined;
      disconnect = (): void => undefined;
    }
    window.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;
  }
}

installBrowserStubs();

const student: User = {
  id: 1,
  username: 'stu001',
  display_name: '学生甲',
  role: 'student',
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
};

const teacher: User = {
  id: 2,
  username: 'tea001',
  display_name: '教师乙',
  role: 'teacher',
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
};

function meResponse(user: User): MeResponse {
  return { user, csrf_token: `csrf-${user.role}` };
}

function unauthorized(): ApiError {
  return new ApiError({ status: 401, code: 'AUTH_REQUIRED', message: '未登录或会话已失效' });
}

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason?: unknown) => void;
}

function deferred<T>(): Deferred<T> {
  // 先放 no-op，Promise 执行器会同步覆盖；避免 `let x!` 触发 prefer-const/未赋值告警。
  let resolve: (value: T) => void = () => undefined;
  let reject: (reason?: unknown) => void = () => undefined;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/** 用真实的 routes 建内存路由，测的就是生产路由表（含 AuthGate/RequireAuth/RequireRole）。 */
function renderApp(initialEntries: string[]) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  const router = createMemoryRouter(routes, { initialEntries });
  const result = render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { ...result, router, queryClient };
}

async function fillAndSubmit(username: string, password: string): Promise<void> {
  const user = userEvent.setup({ pointerEventsCheck: 0 });
  await user.type(screen.getByTestId('login-username'), username);
  await user.type(screen.getByTestId('login-password'), password);
  await user.click(screen.getByTestId('login-submit'));
}

describe('login page & role routes', () => {
  beforeEach(() => {
    clearSession();
    window.sessionStorage.clear();
  });

  afterEach(() => {
    clearSession();
    window.sessionStorage.clear();
  });

  it('启动先显示 auth-loading；登录成功后按角色跳默认页并写入会话', async () => {
    const me = deferred<MeResponse>();
    const getSpy = vi.spyOn(api, 'get').mockReturnValue(me.promise);
    const postSpy = vi.spyOn(api, 'post').mockResolvedValue(meResponse(teacher));

    const { router } = renderApp(['/login']);
    expect(screen.getByTestId('auth-loading')).toBeInTheDocument();

    me.reject(unauthorized());
    await screen.findByTestId('login-page');
    expect(getSpy).toHaveBeenCalledTimes(1);
    expect(getSpy).toHaveBeenCalledWith('/auth/me', { redirectOn401: false });

    await fillAndSubmit('tea001', 'secret-1');

    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/questions');
    });
    expect(postSpy).toHaveBeenCalledWith(
      '/auth/login',
      { username: 'tea001', password: 'secret-1' },
      { csrf: false, redirectOn401: false },
    );
    expect(getCurrentUser()).toEqual(teacher);
    expect(await screen.findByTestId('page-questions')).toBeInTheDocument();
  });

  it('登录失败：AUTH_INVALID_CREDENTIALS 统一提示「用户名或密码错误」', async () => {
    vi.spyOn(api, 'get').mockRejectedValue(unauthorized());
    const postSpy = vi.spyOn(api, 'post').mockRejectedValue(
      new ApiError({
        status: 401,
        code: 'AUTH_INVALID_CREDENTIALS',
        message: '账号或密码错误（服务端原文不应直接展示）',
      }),
    );

    const { router } = renderApp(['/login']);
    await screen.findByTestId('login-page');

    await fillAndSubmit('not-exist', 'wrong-password');

    expect(await screen.findByTestId('login-error')).toHaveTextContent('用户名或密码错误');
    expect(router.state.location.pathname).toBe('/login');
    expect(getCurrentUser()).toBeNull();
    expect(postSpy).toHaveBeenCalledTimes(1);
  });

  it('safeReturnTo 拦截开放重定向，只放行站内路径', () => {
    expect(safeReturnTo('https://evil.example/steal')).toBe('/');
    expect(safeReturnTo('//evil.example/steal')).toBe('/');
    expect(safeReturnTo('/\\evil.example/steal')).toBe('/');
    expect(safeReturnTo('javascript:alert(1)')).toBe('/');
    expect(safeReturnTo('/login?returnTo=%2Fusers')).toBe('/');
    expect(safeReturnTo(null)).toBe('/');
    expect(safeReturnTo('')).toBe('/');

    expect(safeReturnTo('/me/results?tab=wrong')).toBe('/me/results?tab=wrong');
    expect(safeReturnTo('https://evil.example/steal', '/me/exams')).toBe('/me/exams');
  });

  it('returnTo 为外部地址时登录成功回退到角色默认页', async () => {
    vi.spyOn(api, 'get').mockRejectedValue(unauthorized());
    vi.spyOn(api, 'post').mockResolvedValue(meResponse(student));

    const { router } = renderApp(['/login?returnTo=https%3A%2F%2Fevil.example%2Fsteal']);
    await screen.findByTestId('login-page');

    await fillAndSubmit('stu001', 'secret-1');

    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/me/exams');
    });
    expect(await screen.findByTestId('my-exams-page')).toBeInTheDocument();
  });

  it('returnTo 为站内路径时登录成功原路返回', async () => {
    vi.spyOn(api, 'get').mockRejectedValue(unauthorized());
    vi.spyOn(api, 'post').mockResolvedValue(meResponse(student));

    const { router } = renderApp(['/login?returnTo=%2Fme%2Fresults']);
    await screen.findByTestId('login-page');

    await fillAndSubmit('stu001', 'secret-1');

    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/my-results');
    });
    expect(await screen.findByTestId('page-my-results')).toBeInTheDocument();
  });

  it('角色守卫：学生访问管理员路由渲染 403 且不跳转', async () => {
    vi.spyOn(api, 'get').mockResolvedValue(meResponse(student));

    const { router } = renderApp(['/users']);

    expect(await screen.findByTestId('forbidden-page')).toBeInTheDocument();
    expect(screen.getByText('403')).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/users');
    expect(screen.queryByTestId('page-users')).not.toBeInTheDocument();
    expect(screen.getByTestId('app-layout')).toBeInTheDocument();
  });

  it('/auth/me 401 时只跳一次登录页，不产生引导循环', async () => {
    const getSpy = vi.spyOn(api, 'get').mockRejectedValue(unauthorized());

    const { router } = renderApp(['/me/results']);

    await screen.findByTestId('login-page');
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/login');
    });
    expect(router.state.location.search).toBe('?returnTo=%2Fme%2Fresults');
    expect(getSpy).toHaveBeenCalledTimes(1);

    // 再让事件循环转一圈，确认登录页没有再次触发 /auth/me（无循环）。
    await new Promise((resolve) => {
      setTimeout(resolve, 0);
    });
    expect(getSpy).toHaveBeenCalledTimes(1);
    expect(router.state.location.pathname).toBe('/login');
    expect(getCurrentUser()).toBeNull();
  });

  it('登出：POST /auth/logout 后清空会话与 query 缓存并回登录页', async () => {
    vi.spyOn(api, 'get').mockResolvedValue(meResponse(student));
    const postSpy = vi.spyOn(api, 'post').mockResolvedValue(undefined);

    const { router, queryClient } = renderApp(['/']);
    await screen.findByTestId('app-layout');
    const clearSpy = vi.spyOn(queryClient, 'clear');

    await userEvent.setup({ pointerEventsCheck: 0 }).click(screen.getByTestId('app-logout'));

    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/login');
    });
    expect(postSpy).toHaveBeenCalledWith('/auth/logout', undefined, { redirectOn401: false });
    expect(clearSpy).toHaveBeenCalledTimes(1);
    expect(getCurrentUser()).toBeNull();
  });
});

describe('session hydrate', () => {
  it('已有 sessionStorage 快照时仍以 /auth/me 的返回值为准', async () => {
    setSession(teacher, 'stale-csrf');
    vi.spyOn(api, 'get').mockResolvedValue(meResponse(student));

    const { router } = renderApp(['/me/results']);

    // 等 AuthGate 完成 /auth/me 并渲染出受保护页后，再断言用户已被服务端结果覆盖。
    expect(await screen.findByTestId('page-my-results')).toBeInTheDocument();
    expect(getCurrentUser()).toEqual(student);
    expect(router.state.location.pathname).toBe('/my-results');
  });
});
