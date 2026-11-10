import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, api } from '../src/api/client';
import type { User } from '../src/api/types';
import AppLayout from '../src/components/AppLayout';
import type { Paper, PaperPage } from '../src/pages/papers/model';
import ExamManagePage from '../src/pages/teacher/exams/ExamManagePage';
import {
  MAX_DURATION_MINUTES,
  MIN_DURATION_MINUTES,
  buildExamCreate,
  defaultExamWindow,
  emptyExamDraft,
  parseExamValidationError,
  validateExamDraft,
} from '../src/pages/teacher/exams/model';
import type { Exam, ExamCreate, ExamDraft, ExamPage } from '../src/pages/teacher/exams/model';
import { clearSession, setSession } from '../src/stores/auth';

/**
 * jsdom 缺少 antd 响应式组件（Table/Modal/Select/Layout）依赖的浏览器 API，
 * 这里补最小桩；不改变被测逻辑（同 papers.test.tsx / result.test.tsx）。
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

const teacherUser: User = {
  id: 2,
  username: 'tea001',
  display_name: '教师乙',
  role: 'teacher',
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
};

const adminUser: User = {
  id: 9,
  username: 'admin',
  display_name: '管理员',
  role: 'admin',
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
};

const studentUser: User = {
  id: 1,
  username: 'stu001',
  display_name: '学生甲',
  role: 'student',
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
};

function makePaper(overrides: Partial<Paper> = {}): Paper {
  return {
    id: 1,
    owner_id: 2,
    title: '期中试卷',
    total_points: 20,
    status: 'draft',
    questions: [{ question_id: 1, order_no: 1, points: 20, partial_rule: 'all_or_nothing' }],
    created_at: '2026-01-02T08:00:00Z',
    ...overrides,
  };
}

function paperPage(items: Paper[], total = items.length): PaperPage {
  return { items, total, page: 1, page_size: 100 };
}

function makeExam(overrides: Partial<Exam> = {}): Exam {
  return {
    id: 1,
    paper_id: 1,
    title: '第一次月考',
    start_at: '2026-12-01T01:00:00Z',
    end_at: '2026-12-01T03:00:00Z',
    duration_min: 60,
    status: 'draft',
    created_by: 2,
    created_at: '2026-11-01T02:00:00Z',
    ...overrides,
  };
}

function examPage(items: Exam[], total = items.length): ExamPage {
  return { items, total, page: 1, page_size: 20 };
}

/** 页面会并发请求 /exams 与 /papers（试卷标题映射 + 创建表单候选）。 */
function stubApiGet(handler: (path: string) => unknown): void {
  const implementation = ((path: string) =>
    Promise.resolve(handler(path))) as unknown as typeof api.get;
  vi.spyOn(api, 'get').mockImplementation(implementation);
}

function newQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
}

const draftPaper = makePaper({ id: 1, title: '期中试卷', total_points: 20 });
const frozenPaper = makePaper({
  id: 2,
  title: '已发布试卷',
  status: 'frozen',
  total_points: 100,
  frozen_at: '2026-02-01T10:00:00Z',
});
const draftExam = makeExam({ id: 1, title: '第一次月考', paper_id: 1, status: 'draft' });
const publishedExam = makeExam({
  id: 2,
  title: '期中考试',
  paper_id: 2,
  status: 'published',
  duration_min: 90,
  snapshot_version: 1,
});
const closedExam = makeExam({
  id: 3,
  title: '期末补考',
  paper_id: 2,
  status: 'closed',
  duration_min: 45,
  snapshot_version: 1,
});

/** 默认数据源：两份试卷（草稿 + 冻结）、三场考试（草稿/已发布/已结束）。 */
function stubStandardData(): void {
  stubApiGet((path) => {
    if (path === '/papers') return paperPage([draftPaper, frozenPaper]);
    if (path === '/exams') return examPage([draftExam, publishedExam, closedExam], 3);
    throw new Error(`unexpected GET ${path}`);
  });
}

function renderPage() {
  const queryClient = newQueryClient();
  const user = userEvent.setup({ pointerEventsCheck: 0 });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/exams']}>
        <Routes>
          <Route path="/exams" element={<ExamManagePage />} />
          <Route path="/exam-results/:examId" element={<div data-testid="stub-exam-results" />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...view, queryClient, user };
}

describe('exam model', () => {
  it('创建校验：试卷必选、标题必填、时长 10–300 且不超过时间窗', () => {
    const base = emptyExamDraft();
    const emptyErrors = validateExamDraft(base);
    expect(emptyErrors.paperId).toBe('请选择一份草稿试卷');
    expect(emptyErrors.title).toBe('请输入考试标题');

    const valid = { ...base, paperId: 1, title: '第一次月考' };
    expect(validateExamDraft(valid)).toEqual({});

    expect(
      validateExamDraft({ ...valid, durationMin: MIN_DURATION_MINUTES - 1 }).durationMin,
    ).toBeDefined();
    expect(
      validateExamDraft({ ...valid, durationMin: MAX_DURATION_MINUTES + 1 }).durationMin,
    ).toBeDefined();

    const windowed: ExamDraft = {
      ...valid,
      startAt: '2026-12-01T09:00',
      endAt: '2026-12-01T10:00',
      durationMin: 60,
    };
    expect(validateExamDraft(windowed)).toEqual({});
    expect(validateExamDraft({ ...windowed, durationMin: 90 }).durationMin).toContain('时间窗');
    expect(
      validateExamDraft({ ...windowed, startAt: '2026-12-01T10:00', endAt: '2026-12-01T09:00' })
        .endAt,
    ).toBe('结束时间必须晚于开始时间');
  });

  it('buildExamCreate：标题去空格、本地时间转 UTC ISO、开关与 publish 透传', () => {
    const payload = buildExamCreate({
      ...emptyExamDraft(),
      paperId: 7,
      title: '  期中考试  ',
      startAt: '2026-12-01T09:00',
      endAt: '2026-12-01T11:00',
      durationMin: 90,
      shuffleQuestions: true,
      shuffleOptions: true,
      publish: true,
    });

    expect(payload).toMatchObject({
      paper_id: 7,
      title: '期中考试',
      duration_min: 90,
      shuffle_questions: true,
      shuffle_options: true,
      publish: true,
    });
    expect(payload.start_at).toBe(new Date('2026-12-01T09:00').toISOString());
    expect(payload.end_at).toBe(new Date('2026-12-01T11:00').toISOString());
    expect(() => buildExamCreate(emptyExamDraft())).toThrow();
  });

  it('422 message → 字段级错误映射', () => {
    expect(
      parseExamValidationError('body.duration_min: Input should be less than or equal to 300')
        .durationMin,
    ).toContain('10–300');
    expect(
      parseExamValidationError('body.title: String should have at most 128 characters').title,
    ).toContain('128');
    expect(
      parseExamValidationError('body.start_at: Input should be a valid datetime').startAt,
    ).toBeDefined();
    expect(
      parseExamValidationError('body.paper_id: Paper must be a draft paper').paperId,
    ).toBeDefined();
  });

  it('默认时间窗：开始时间在将来，结束时间晚于开始时间', () => {
    const window = defaultExamWindow();
    expect(new Date(window.startAt).getTime()).toBeGreaterThan(Date.now());
    expect(new Date(window.endAt).getTime()).toBeGreaterThan(new Date(window.startAt).getTime());
  });
});

describe('teacher exam management page', () => {
  beforeEach(() => {
    clearSession();
    window.sessionStorage.clear();
  });

  afterEach(() => {
    clearSession();
    window.sessionStorage.clear();
  });

  it('列表：标题/试卷/时间窗/时长/状态/创建时间，操作按状态互斥', async () => {
    stubStandardData();

    renderPage();

    expect(await screen.findByTestId('exams-row-1')).toHaveTextContent('第一次月考');
    expect(screen.getByTestId('exams-row-2')).toHaveTextContent('期中考试');
    expect(screen.getByTestId('exams-row-3')).toHaveTextContent('期末补考');

    // 「试卷」列标题来自 GET /papers 的 paper_id → title 映射（独立请求，需等待回填）
    await waitFor(() =>
      expect(screen.getByTestId('exams-paper-title-1')).toHaveTextContent('期中试卷'),
    );
    expect(screen.getByTestId('exams-paper-title-2')).toHaveTextContent('已发布试卷');

    expect(screen.getByTestId('exams-status-1')).toHaveTextContent('草稿');
    expect(screen.getByTestId('exams-status-2')).toHaveTextContent('已发布');
    expect(screen.getByTestId('exams-status-3')).toHaveTextContent('已结束');

    expect(screen.getByTestId('exams-duration-1')).toHaveTextContent('60 分钟');
    expect(screen.getByTestId('exams-duration-2')).toHaveTextContent('90 分钟');
    // 时间窗与创建时间（本地时区，断言格式即可）
    expect(screen.getAllByText(/2026-\d{2}-\d{2} \d{2}:\d{2}/).length).toBeGreaterThanOrEqual(9);

    // draft 可发布、published 可关闭、closed 只能查看
    expect(screen.getByTestId('exams-publish-1')).toBeInTheDocument();
    expect(screen.queryByTestId('exams-close-1')).not.toBeInTheDocument();
    expect(screen.getByTestId('exams-close-2')).toBeInTheDocument();
    expect(screen.queryByTestId('exams-publish-2')).not.toBeInTheDocument();
    expect(screen.queryByTestId('exams-publish-3')).not.toBeInTheDocument();
    expect(screen.queryByTestId('exams-close-3')).not.toBeInTheDocument();
    expect(screen.getByTestId('exams-view-3')).toBeInTheDocument();

    expect(screen.getByText('共 3 场')).toBeInTheDocument();
    expect(api.get).toHaveBeenCalledWith(
      '/exams',
      expect.objectContaining({
        query: expect.objectContaining({ page: 1, page_size: 20 }),
      }),
    );
  });

  it('状态筛选：切换「已发布」重新请求并传 status，回到第 1 页', async () => {
    stubStandardData();

    const { user } = renderPage();
    await screen.findByTestId('exams-row-1');

    await user.click(screen.getByRole('radio', { name: '已发布' }));

    await waitFor(() =>
      expect(api.get).toHaveBeenLastCalledWith(
        '/exams',
        expect.objectContaining({
          query: expect.objectContaining({ status: 'published', page: 1 }),
        }),
      ),
    );
  });

  it('创建校验：未选试卷 / 标题为空时拦截，不调用 POST', async () => {
    stubStandardData();
    const postSpy = vi.spyOn(api, 'post');

    const { user } = renderPage();
    await screen.findByTestId('exams-row-1');

    await user.click(screen.getByTestId('exams-create'));
    expect(await screen.findByTestId('exams-submit')).toBeInTheDocument();
    await user.click(screen.getByTestId('exams-submit'));

    expect(await screen.findByTestId('exams-paper-error')).toHaveTextContent('请选择一份草稿试卷');
    expect(screen.getByTestId('exams-title-error')).toHaveTextContent('请输入考试标题');
    expect(screen.getByTestId('exams-form-error')).toHaveTextContent('请先修正表单中的错误');
    expect(postSpy).not.toHaveBeenCalled();
  });

  it('创建：只列草稿试卷，提交 ExamCreate 后刷新列表', async () => {
    stubStandardData();
    const postSpy = vi.spyOn(api, 'post').mockResolvedValue(makeExam({ id: 9 }));

    const { user, queryClient } = renderPage();
    await screen.findByTestId('exams-row-1');
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');

    await user.click(screen.getByTestId('exams-create'));
    await screen.findByTestId('exams-submit');

    await user.click(screen.getByRole('combobox'));
    expect(await screen.findByText('期中试卷（20 分）')).toBeInTheDocument();
    // 已冻结试卷不可用于新考试
    expect(screen.queryByText('已发布试卷（100 分）')).not.toBeInTheDocument();
    await user.click(screen.getByText('期中试卷（20 分）'));

    await user.type(screen.getByTestId('exams-title'), '第一次月考');
    await user.click(screen.getByTestId('exams-publish-now'));
    await user.click(screen.getByTestId('exams-submit'));

    await waitFor(() => expect(postSpy).toHaveBeenCalledTimes(1));
    expect(postSpy).toHaveBeenCalledWith(
      '/exams',
      expect.objectContaining({
        paper_id: 1,
        title: '第一次月考',
        duration_min: 60,
        publish: true,
        shuffle_questions: false,
        shuffle_options: false,
      }),
    );

    const payload = postSpy.mock.calls[0]?.[1] as ExamCreate;
    expect(payload.start_at).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/);
    expect(new Date(payload.end_at).getTime()).toBeGreaterThan(
      new Date(payload.start_at).getTime(),
    );

    await waitFor(() => expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['exams'] }));
  });

  it('发布：二次确认后才 PATCH status=published，并失效考试/试卷缓存', async () => {
    stubStandardData();
    const patchSpy = vi
      .spyOn(api, 'patch')
      .mockResolvedValue(makeExam({ id: 1, status: 'published', snapshot_version: 1 }));

    const { user, queryClient } = renderPage();
    await screen.findByTestId('exams-row-1');
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');

    await user.click(screen.getByTestId('exams-publish-1'));
    expect(await screen.findByTestId('exams-publish-confirm')).toBeInTheDocument();
    expect(patchSpy).not.toHaveBeenCalled();

    await user.click(screen.getByTestId('exams-publish-confirm'));

    await waitFor(() => expect(patchSpy).toHaveBeenCalledWith('/exams/1', { status: 'published' }));
    await waitFor(() => expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['exams'] }));
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['papers'] });
  });

  it('关闭：二次确认后才 PATCH status=closed', async () => {
    stubStandardData();
    const patchSpy = vi
      .spyOn(api, 'patch')
      .mockResolvedValue(makeExam({ id: 2, status: 'closed', snapshot_version: 1 }));

    const { user } = renderPage();
    await screen.findByTestId('exams-row-2');

    await user.click(screen.getByTestId('exams-close-2'));
    expect(await screen.findByTestId('exams-close-confirm')).toBeInTheDocument();
    expect(patchSpy).not.toHaveBeenCalled();

    await user.click(screen.getByTestId('exams-close-confirm'));

    await waitFor(() => expect(patchSpy).toHaveBeenCalledWith('/exams/2', { status: 'closed' }));
  });

  it('发布 409：非法状态迁移 → 中文提示并刷新列表', async () => {
    stubStandardData();
    const patchSpy = vi.spyOn(api, 'patch').mockRejectedValue(
      new ApiError({
        status: 409,
        code: 'CONFLICT',
        message: 'Exam status transition is not allowed',
      }),
    );

    const { user, queryClient } = renderPage();
    await screen.findByTestId('exams-row-1');
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');

    await user.click(screen.getByTestId('exams-publish-1'));
    await user.click(await screen.findByTestId('exams-publish-confirm'));

    expect(await screen.findByTestId('exams-error')).toHaveTextContent('状态只能按');
    expect(patchSpy).toHaveBeenCalledWith('/exams/1', { status: 'published' });
    await waitFor(() => expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['exams'] }));
  });

  it('发布 403：非本人考试提示没有权限', async () => {
    stubStandardData();
    vi.spyOn(api, 'patch').mockRejectedValue(
      new ApiError({ status: 403, code: 'FORBIDDEN', message: 'Forbidden' }),
    );

    const { user } = renderPage();
    await screen.findByTestId('exams-row-1');

    await user.click(screen.getByTestId('exams-publish-1'));
    await user.click(await screen.findByTestId('exams-publish-confirm'));

    expect(await screen.findByTestId('exams-error')).toHaveTextContent('没有权限执行该操作');
  });

  it('创建 422：字段级错误回显到时长输入并保留表单内容', async () => {
    stubStandardData();
    vi.spyOn(api, 'post').mockRejectedValue(
      new ApiError({
        status: 422,
        code: 'VALIDATION_ERROR',
        message: 'body.duration_min: Input should be less than or equal to 300',
      }),
    );

    const { user } = renderPage();
    await screen.findByTestId('exams-row-1');

    await user.click(screen.getByTestId('exams-create'));
    await screen.findByTestId('exams-submit');
    await user.click(screen.getByRole('combobox'));
    await user.click(await screen.findByText('期中试卷（20 分）'));
    await user.type(screen.getByTestId('exams-title'), '422 考试');
    await user.click(screen.getByTestId('exams-submit'));

    expect(await screen.findByTestId('exams-duration-error')).toHaveTextContent('10–300');
    expect(screen.getByTestId('exams-form-error')).toHaveTextContent('参数校验失败');
    expect(screen.getByTestId('exams-title')).toHaveValue('422 考试');
    expect(screen.getByTestId('exams-submit')).toBeEnabled();
  });

  it('菜单：教师/管理员可见「考试管理」并进入 /exams，学生隐藏', async () => {
    setSession(teacherUser, 'csrf-teacher');
    const teacherView = render(
      <QueryClientProvider client={newQueryClient()}>
        <MemoryRouter initialEntries={['/']}>
          <Routes>
            <Route element={<AppLayout />}>
              <Route index element={<div data-testid="stub-home" />} />
              <Route path="exams" element={<div data-testid="stub-exams-page" />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const user = userEvent.setup({ pointerEventsCheck: 0 });
    expect(screen.getByText('考试管理')).toBeInTheDocument();
    await user.click(screen.getByText('考试管理'));
    expect(await screen.findByTestId('stub-exams-page')).toBeInTheDocument();

    teacherView.unmount();
    clearSession();

    setSession(adminUser, 'csrf-admin');
    const adminView = render(
      <QueryClientProvider client={newQueryClient()}>
        <MemoryRouter initialEntries={['/']}>
          <Routes>
            <Route element={<AppLayout />}>
              <Route index element={<div data-testid="stub-home" />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(screen.getByText('考试管理')).toBeInTheDocument();
    adminView.unmount();
    clearSession();

    setSession(studentUser, 'csrf-student');
    render(
      <QueryClientProvider client={newQueryClient()}>
        <MemoryRouter initialEntries={['/']}>
          <Routes>
            <Route element={<AppLayout />}>
              <Route index element={<div data-testid="stub-home" />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(screen.queryByText('考试管理')).not.toBeInTheDocument();
    expect(screen.getByText('我的考试')).toBeInTheDocument();
  });
});
