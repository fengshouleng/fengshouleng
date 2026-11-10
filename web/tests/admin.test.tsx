import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, RouterProvider, createMemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, api } from '../src/api/client';
import type { User } from '../src/api/types';
import AppLayout from '../src/components/AppLayout';
import AuditLogsPage from '../src/pages/admin/AuditLogsPage';
import UsersPage from '../src/pages/admin/UsersPage';
import type { AuditLog, AuditLogPage, UserPage } from '../src/pages/admin/model';
import { routes } from '../src/routes';
import { clearSession, setSession } from '../src/stores/auth';

/**
 * jsdom 缺少 antd 响应式组件（Table/Modal/Layout/Radio）依赖的浏览器 API，
 * 这里补最小桩；不改变被测逻辑（同 questions.test.tsx / exams.test.tsx）。
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

const adminUser: User = {
  id: 9,
  username: 'admin',
  display_name: '管理员',
  role: 'admin',
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
};

const teacherUser: User = {
  id: 2,
  username: 'tea001',
  display_name: '教师乙',
  role: 'teacher',
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
};

const disabledUser: User = {
  id: 3,
  username: 'old-teacher',
  display_name: '离职教师',
  role: 'teacher',
  is_active: false,
  created_at: '2026-01-03T00:00:00Z',
};

const studentUser: User = {
  id: 1,
  username: 'stu001',
  display_name: '学生甲',
  role: 'student',
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
};

function userPage(items: User[], total = items.length): UserPage {
  return { items, total, page: 1, page_size: 20 };
}

const userLog: AuditLog = {
  id: 101,
  actor_id: 2,
  action: 'question.create',
  target_type: 'question',
  target_id: '7',
  ip: '10.0.0.8',
  user_agent: 'vitest',
  detail: { stem: '1+1=?' },
  created_at: '2026-11-01T02:00:00Z',
};

/** 系统记录：actor_id=null、ip="-"（v1.0.3 / CHG-035）。 */
const systemLog: AuditLog = {
  id: 102,
  actor_id: null,
  action: 'backup.cleanup',
  target_type: 'system',
  target_id: '-',
  ip: '-',
  created_at: '2026-11-02T03:00:00Z',
};

function auditPage(items: AuditLog[], total = items.length): AuditLogPage {
  return { items, total, page: 1, page_size: 20 };
}

function newQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
}

function renderUsersPage() {
  const queryClient = newQueryClient();
  const user = userEvent.setup({ pointerEventsCheck: 0 });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <UsersPage />
    </QueryClientProvider>,
  );
  return { ...view, queryClient, user };
}

function renderAuditPage() {
  const queryClient = newQueryClient();
  const user = userEvent.setup({ pointerEventsCheck: 0 });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <AuditLogsPage />
    </QueryClientProvider>,
  );
  return { ...view, queryClient, user };
}

function renderLayout() {
  return render(
    <QueryClientProvider client={newQueryClient()}>
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route element={<AppLayout />}>
            <Route index element={<div data-testid="stub-home" />} />
            <Route path="users" element={<div data-testid="stub-users-page" />} />
            <Route path="audit-logs" element={<div data-testid="stub-audit-page" />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function renderRoutes(initialEntries: string[]) {
  return render(
    <QueryClientProvider client={newQueryClient()}>
      <RouterProvider router={createMemoryRouter(routes, { initialEntries })} />
    </QueryClientProvider>,
  );
}

/** 打开创建账号弹窗并填充合法表单。 */
async function fillCreateForm(
  user: ReturnType<typeof userEvent.setup>,
  overrides: { username?: string; displayName?: string; role?: string; password?: string } = {},
): Promise<void> {
  const dialog = await screen.findByRole('dialog');
  await user.type(within(dialog).getByTestId('admin-user-username'), overrides.username ?? 'tea009');
  await user.type(
    within(dialog).getByTestId('admin-user-display-name'),
    overrides.displayName ?? '新教师',
  );
  if (overrides.role) {
    await user.click(within(dialog).getByRole('radio', { name: overrides.role }));
  }
  await user.type(
    within(dialog).getByTestId('admin-user-password'),
    overrides.password ?? 'Passw0rd8',
  );
}

describe('admin users page', () => {
  beforeEach(() => {
    clearSession();
    window.sessionStorage.clear();
    setSession(adminUser, 'csrf-admin');
  });

  afterEach(() => {
    clearSession();
    window.sessionStorage.clear();
  });

  it('列表渲染：用户名/显示名/角色/状态/创建时间，请求带 role/page 参数', async () => {
    const getSpy = vi
      .spyOn(api, 'get')
      .mockResolvedValue(userPage([teacherUser, disabledUser]));

    renderUsersPage();

    expect(await screen.findByTestId('admin-user-row-2')).toHaveTextContent('tea001');
    expect(screen.getByTestId('admin-users-table')).toBeInTheDocument();
    expect(screen.getByTestId('admin-user-row-3')).toHaveTextContent('old-teacher');
    expect(screen.getByTestId('admin-user-role-tag-2')).toHaveTextContent('教师');
    expect(screen.getByTestId('admin-user-status-2')).toHaveTextContent('启用');
    expect(screen.getByTestId('admin-user-status-3')).toHaveTextContent('停用');
    expect(screen.getByText('共 2 个账号')).toBeInTheDocument();
    expect(getSpy).toHaveBeenCalledWith(
      '/users',
      expect.objectContaining({
        query: expect.objectContaining({ page: 1, page_size: 20 }),
      }),
    );
  });

  it('角色筛选与分页：切换角色回到第 1 页，翻页带 page=2', async () => {
    const getSpy = vi
      .spyOn(api, 'get')
      .mockResolvedValue(userPage([teacherUser, disabledUser], 41));

    const { user } = renderUsersPage();
    await screen.findByTestId('admin-user-row-2');

    await user.click(screen.getByTitle('2'));
    await waitFor(() => {
      expect(getSpy).toHaveBeenLastCalledWith(
        '/users',
        expect.objectContaining({ query: expect.objectContaining({ page: 2 }) }),
      );
    });

    await user.click(screen.getByRole('radio', { name: '教师' }));
    await waitFor(() => {
      expect(getSpy).toHaveBeenLastCalledWith(
        '/users',
        expect.objectContaining({
          query: expect.objectContaining({ role: 'teacher', page: 1 }),
        }),
      );
    });
  });

  it('创建校验：空表单展示字段错误且不调用 POST', async () => {
    vi.spyOn(api, 'get').mockResolvedValue(userPage([]));
    const postSpy = vi.spyOn(api, 'post');
    const { user } = renderUsersPage();
    await waitFor(() => expect(api.get).toHaveBeenCalled());

    await user.click(screen.getByTestId('admin-user-create'));
    const dialog = await screen.findByRole('dialog');
    await user.click(screen.getByTestId('admin-user-submit'));

    expect(await within(dialog).findByText('请输入用户名')).toBeInTheDocument();
    expect(within(dialog).getByText('请输入显示名')).toBeInTheDocument();
    expect(within(dialog).getByText('请输入密码')).toBeInTheDocument();
    expect(within(dialog).getByTestId('admin-user-form-error')).toHaveTextContent(
      '请先修正表单中的错误',
    );
    expect(postSpy).not.toHaveBeenCalled();
  });

  it('创建账号：合法表单按 UserCreate 提交，成功后失效列表缓存', async () => {
    const getSpy = vi.spyOn(api, 'get').mockResolvedValue(userPage([]));
    const postSpy = vi
      .spyOn(api, 'post')
      .mockResolvedValue({ ...teacherUser, id: 12, username: 'tea009' });

    const { user, queryClient } = renderUsersPage();
    await waitFor(() => expect(getSpy).toHaveBeenCalledTimes(1));
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');

    await user.click(screen.getByTestId('admin-user-create'));
    await fillCreateForm(user, { role: '教师' });
    await user.click(screen.getByTestId('admin-user-submit'));

    await waitFor(() => expect(postSpy).toHaveBeenCalledTimes(1));
    expect(postSpy).toHaveBeenCalledWith('/users', {
      username: 'tea009',
      display_name: '新教师',
      role: 'teacher',
      password: 'Passw0rd8',
    });
    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['users'] }),
    );
  });

  it('创建 409：用户名冲突回显且保留输入', async () => {
    vi.spyOn(api, 'get').mockResolvedValue(userPage([]));
    vi.spyOn(api, 'post').mockRejectedValue(
      new ApiError({ status: 409, code: 'CONFLICT', message: 'username already exists' }),
    );

    const { user } = renderUsersPage();
    await waitFor(() => expect(api.get).toHaveBeenCalled());

    await user.click(screen.getByTestId('admin-user-create'));
    await fillCreateForm(user);
    const dialog = await screen.findByRole('dialog');
    await user.click(screen.getByTestId('admin-user-submit'));

    expect(await within(dialog).findByTestId('admin-user-form-error')).toHaveTextContent(
      '用户名已存在',
    );
    expect(within(dialog).getByTestId('admin-user-username')).toHaveValue('tea009');
  });

  it('创建 422：字段级错误回显到对应输入', async () => {
    vi.spyOn(api, 'get').mockResolvedValue(userPage([]));
    vi.spyOn(api, 'post').mockRejectedValue(
      new ApiError({
        status: 422,
        code: 'VALIDATION_ERROR',
        message: 'body.password: String should have at least 8 characters',
      }),
    );

    const { user } = renderUsersPage();
    await waitFor(() => expect(api.get).toHaveBeenCalled());

    await user.click(screen.getByTestId('admin-user-create'));
    await fillCreateForm(user, { username: 'tea010', password: 'Passw0rd8' });
    const dialog = await screen.findByRole('dialog');
    await user.click(screen.getByTestId('admin-user-submit'));

    expect(await within(dialog).findByTestId('admin-user-password-error')).toHaveTextContent(
      '密码不合法',
    );
    expect(within(dialog).getByTestId('admin-user-form-error')).toHaveTextContent('参数校验失败');
  });

  it('停用：二次确认后才 PATCH is_active=false，成功后刷新列表', async () => {
    const getSpy = vi.spyOn(api, 'get').mockResolvedValue(userPage([teacherUser]));
    const patchSpy = vi
      .spyOn(api, 'patch')
      .mockResolvedValue({ ...teacherUser, is_active: false });

    const { user } = renderUsersPage();
    const row = (await screen.findByTestId('admin-user-row-2')).closest('tr');
    expect(row).not.toBeNull();

    await user.click(within(row as HTMLElement).getByTestId('admin-user-disable'));
    expect(await screen.findByTestId('admin-user-disable-confirm')).toBeInTheDocument();
    expect(patchSpy).not.toHaveBeenCalled();

    await user.click(screen.getByTestId('admin-user-disable-confirm'));

    await waitFor(() => expect(patchSpy).toHaveBeenCalledWith('/users/2', { is_active: false }));
    await waitFor(() => expect(getSpy).toHaveBeenCalledTimes(2));
  });

  it('停用 403：非管理员操作回显无权限提示', async () => {
    vi.spyOn(api, 'get').mockResolvedValue(userPage([teacherUser]));
    vi.spyOn(api, 'patch').mockRejectedValue(
      new ApiError({ status: 403, code: 'FORBIDDEN', message: 'forbidden' }),
    );

    const { user } = renderUsersPage();
    const row = (await screen.findByTestId('admin-user-row-2')).closest('tr');
    expect(row).not.toBeNull();

    await user.click(within(row as HTMLElement).getByTestId('admin-user-disable'));
    await user.click(await screen.findByTestId('admin-user-disable-confirm'));

    expect(await screen.findByTestId('admin-users-error')).toHaveTextContent(
      '没有权限执行该操作',
    );
  });

  it('启用：停用账号的按钮为「启用」，PATCH is_active=true', async () => {
    vi.spyOn(api, 'get').mockResolvedValue(userPage([disabledUser]));
    const patchSpy = vi
      .spyOn(api, 'patch')
      .mockResolvedValue({ ...disabledUser, is_active: true });

    const { user } = renderUsersPage();
    const row = (await screen.findByTestId('admin-user-row-3')).closest('tr');
    expect(row).not.toBeNull();

    await user.click(within(row as HTMLElement).getByTestId('admin-user-enable'));
    expect(await screen.findByTestId('admin-user-enable-confirm')).toBeInTheDocument();
    await user.click(screen.getByTestId('admin-user-enable-confirm'));

    await waitFor(() => expect(patchSpy).toHaveBeenCalledWith('/users/3', { is_active: true }));
  });

  it('修改角色：弹窗选择角色后 PATCH role', async () => {
    vi.spyOn(api, 'get').mockResolvedValue(userPage([teacherUser]));
    const patchSpy = vi
      .spyOn(api, 'patch')
      .mockResolvedValue({ ...teacherUser, role: 'admin' });

    const { user } = renderUsersPage();
    await screen.findByTestId('admin-user-row-2');

    await user.click(screen.getByTestId('admin-user-role-2'));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('radio', { name: '管理员' }));
    await user.click(screen.getByTestId('admin-user-role-confirm'));

    await waitFor(() => expect(patchSpy).toHaveBeenCalledWith('/users/2', { role: 'admin' }));
  });

  it('重置密码：长度不足拦截，合法新密码 PATCH password', async () => {
    vi.spyOn(api, 'get').mockResolvedValue(userPage([teacherUser]));
    const patchSpy = vi.spyOn(api, 'patch').mockResolvedValue(teacherUser);

    const { user } = renderUsersPage();
    await screen.findByTestId('admin-user-row-2');

    await user.click(screen.getByTestId('admin-user-reset-2'));
    const dialog = await screen.findByRole('dialog');
    const input = within(dialog).getByTestId('admin-user-reset-password');

    await user.type(input, 'short');
    await user.click(screen.getByTestId('admin-user-reset-confirm'));
    expect(await within(dialog).findByTestId('admin-user-reset-error')).toHaveTextContent(
      '密码至少 8 位',
    );
    expect(patchSpy).not.toHaveBeenCalled();

    await user.clear(input);
    await user.type(input, 'newpass88');
    await user.click(screen.getByTestId('admin-user-reset-confirm'));

    await waitFor(() => expect(patchSpy).toHaveBeenCalledWith('/users/2', { password: 'newpass88' }));
  });

  it('防御性 403：非 admin 渲染 403 且不请求管理接口', async () => {
    setSession(teacherUser, 'csrf-teacher');
    const getSpy = vi.spyOn(api, 'get');

    const usersView = renderUsersPage();
    expect(await screen.findByTestId('admin-forbidden')).toBeInTheDocument();
    expect(screen.queryByTestId('admin-users-table')).not.toBeInTheDocument();
    usersView.unmount();

    renderAuditPage();
    expect(await screen.findByTestId('admin-forbidden')).toBeInTheDocument();
    expect(screen.queryByTestId('admin-audit-table')).not.toBeInTheDocument();
    expect(getSpy).not.toHaveBeenCalled();
  });
});

describe('admin audit logs page', () => {
  beforeEach(() => {
    clearSession();
    window.sessionStorage.clear();
    setSession(adminUser, 'csrf-admin');
  });

  afterEach(() => {
    clearSession();
    window.sessionStorage.clear();
  });

  it('只读列表：时间/操作者/动作/目标/IP，系统记录显示「系统」，无删除入口', async () => {
    const getSpy = vi.spyOn(api, 'get').mockResolvedValue(auditPage([userLog, systemLog]));
    const deleteSpy = vi.spyOn(api, 'delete');

    renderAuditPage();

    expect(await screen.findByTestId('admin-audit-row-101')).toBeInTheDocument();
    expect(screen.getByTestId('admin-audit-table')).toBeInTheDocument();
    expect(screen.getByTestId('admin-audit-actor-101')).toHaveTextContent('#2');
    expect(screen.getByTestId('admin-audit-actor-102')).toHaveTextContent('系统');
    expect(screen.getByText('question.create')).toBeInTheDocument();
    expect(screen.getByText('question #7')).toBeInTheDocument();
    expect(screen.getByText('10.0.0.8')).toBeInTheDocument();
    expect(screen.getByText('backup.cleanup')).toBeInTheDocument();
    expect(screen.getByText('共 2 条')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /删除/ })).not.toBeInTheDocument();
    expect(deleteSpy).not.toHaveBeenCalled();
    expect(getSpy).toHaveBeenCalledWith(
      '/audit-logs',
      expect.objectContaining({
        query: expect.objectContaining({ page: 1, page_size: 20 }),
      }),
    );
  });

  it('筛选：action 关键字 / actor_id / 时间范围进入查询参数并回到第 1 页', async () => {
    const getSpy = vi.spyOn(api, 'get').mockResolvedValue(auditPage([userLog], 45));

    const { user } = renderAuditPage();
    await screen.findByTestId('admin-audit-row-101');

    await user.type(screen.getByTestId('admin-audit-action'), 'question.create');
    await user.type(screen.getByTestId('admin-audit-actor'), '2');
    fireEvent.change(screen.getByTestId('admin-audit-from'), {
      target: { value: '2026-11-01T00:00' },
    });
    fireEvent.change(screen.getByTestId('admin-audit-to'), {
      target: { value: '2026-11-30T23:59' },
    });
    await user.click(screen.getByTestId('admin-audit-search'));

    await waitFor(() => {
      expect(getSpy).toHaveBeenLastCalledWith(
        '/audit-logs',
        expect.objectContaining({
          query: expect.objectContaining({
            action: 'question.create',
            actor_id: 2,
            from: new Date('2026-11-01T00:00').toISOString(),
            to: new Date('2026-11-30T23:59').toISOString(),
            page: 1,
            page_size: 20,
          }),
        }),
      );
    });
  });

  it('筛选校验：非法操作者与倒序时间被拦截，不发请求', async () => {
    const getSpy = vi.spyOn(api, 'get').mockResolvedValue(auditPage([userLog]));

    const { user } = renderAuditPage();
    await screen.findByTestId('admin-audit-row-101');
    const callsBefore = getSpy.mock.calls.length;

    await user.type(screen.getByTestId('admin-audit-actor'), 'abc');
    fireEvent.change(screen.getByTestId('admin-audit-from'), {
      target: { value: '2026-12-05T00:00' },
    });
    fireEvent.change(screen.getByTestId('admin-audit-to'), {
      target: { value: '2026-12-01T00:00' },
    });
    await user.click(screen.getByTestId('admin-audit-search'));

    expect(await screen.findByTestId('admin-audit-actor-error')).toHaveTextContent(
      '操作者 ID 需为正整数',
    );
    expect(screen.getByTestId('admin-audit-filter-error')).toHaveTextContent(
      '开始时间不能晚于结束时间',
    );
    expect(getSpy.mock.calls.length).toBe(callsBefore);
  });

  it('分页：翻到第 2 页请求 page=2，页大小保持 20', async () => {
    const getSpy = vi
      .spyOn(api, 'get')
      .mockResolvedValue(auditPage([userLog, systemLog], 45));

    const { user } = renderAuditPage();
    await screen.findByTestId('admin-audit-row-101');

    await user.click(screen.getByTitle('2'));

    await waitFor(() => {
      expect(getSpy).toHaveBeenLastCalledWith(
        '/audit-logs',
        expect.objectContaining({
          query: expect.objectContaining({ page: 2, page_size: 20 }),
        }),
      );
    });
  });
});

describe('admin routing and menu', () => {
  beforeEach(() => {
    clearSession();
    window.sessionStorage.clear();
  });

  afterEach(() => {
    clearSession();
    window.sessionStorage.clear();
  });

  it('路由守卫：teacher 访问 /users 显示 403，admin 进入渲染用户管理页', async () => {
    setSession(teacherUser, 'csrf-teacher');
    const getSpy = vi.spyOn(api, 'get');
    getSpy.mockImplementation(((path: string) => {
      if (path === '/auth/me') {
        return Promise.resolve({ user: teacherUser, csrf_token: 'csrf-teacher' });
      }
      throw new Error(`unexpected GET ${path}`);
    }) as unknown as typeof api.get);

    const teacherView = renderRoutes(['/users']);
    expect(await screen.findByTestId('forbidden-page')).toBeInTheDocument();
    expect(screen.queryByTestId('admin-users-table')).not.toBeInTheDocument();
    teacherView.unmount();

    clearSession();
    setSession(adminUser, 'csrf-admin');
    getSpy.mockImplementation(((path: string) => {
      if (path === '/auth/me') {
        return Promise.resolve({ user: adminUser, csrf_token: 'csrf-admin' });
      }
      if (path === '/users') return Promise.resolve(userPage([teacherUser]));
      throw new Error(`unexpected GET ${path}`);
    }) as unknown as typeof api.get);

    renderRoutes(['/users']);
    expect(await screen.findByTestId('admin-user-row-2')).toHaveTextContent('tea001');
    expect(screen.getByTestId('admin-users-table')).toBeInTheDocument();
  });

  it('路由接入：admin 访问 /audit-logs 渲染审计日志页', async () => {
    setSession(adminUser, 'csrf-admin');
    vi.spyOn(api, 'get').mockImplementation(((path: string) => {
      if (path === '/auth/me') {
        return Promise.resolve({ user: adminUser, csrf_token: 'csrf-admin' });
      }
      if (path === '/audit-logs') return Promise.resolve(auditPage([systemLog]));
      throw new Error(`unexpected GET ${path}`);
    }) as unknown as typeof api.get);

    renderRoutes(['/audit-logs']);
    expect(await screen.findByTestId('admin-audit-row-102')).toBeInTheDocument();
    expect(screen.getByTestId('admin-audit-table')).toBeInTheDocument();
    expect(screen.getByTestId('admin-audit-actor-102')).toHaveTextContent('系统');
  });

  it('菜单：admin 可见「用户管理」「审计日志」并可进入，教师/学生隐藏', async () => {
    setSession(adminUser, 'csrf-admin');
    const adminView = renderLayout();
    const user = userEvent.setup({ pointerEventsCheck: 0 });

    expect(screen.getByText('用户管理')).toBeInTheDocument();
    expect(screen.getByText('审计日志')).toBeInTheDocument();

    await user.click(screen.getByText('用户管理'));
    expect(await screen.findByTestId('stub-users-page')).toBeInTheDocument();

    await user.click(screen.getByText('审计日志'));
    expect(await screen.findByTestId('stub-audit-page')).toBeInTheDocument();
    adminView.unmount();

    clearSession();
    setSession(teacherUser, 'csrf-teacher');
    const teacherView = renderLayout();
    expect(screen.queryByText('用户管理')).not.toBeInTheDocument();
    expect(screen.queryByText('审计日志')).not.toBeInTheDocument();
    teacherView.unmount();

    clearSession();
    setSession(studentUser, 'csrf-student');
    renderLayout();
    expect(screen.queryByText('用户管理')).not.toBeInTheDocument();
    expect(screen.queryByText('审计日志')).not.toBeInTheDocument();
    expect(screen.getByText('我的考试')).toBeInTheDocument();
  });
});
