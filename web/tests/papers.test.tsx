import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, api } from '../src/api/client';
import type { User } from '../src/api/types';
import AppLayout from '../src/components/AppLayout';
import PaperEditorPage from '../src/pages/papers/PaperEditorPage';
import PapersPage from '../src/pages/papers/PapersPage';
import {
  buildPaperPayload,
  sumPoints,
  validatePaperDraft,
} from '../src/pages/papers/model';
import type {
  Paper,
  PaperCreate,
  PaperPage,
  SelectedPaperQuestion,
} from '../src/pages/papers/model';
import type { Question, QuestionPage } from '../src/pages/questions/model';
import { clearSession, setSession } from '../src/stores/auth';

/**
 * jsdom 缺少 antd 响应式组件（Table/Modal/Layout/InputNumber）依赖的浏览器 API，
 * 这里补最小桩；不改变被测逻辑（同 login.test.tsx / questions.test.tsx）。
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

const studentUser: User = {
  id: 1,
  username: 'stu001',
  display_name: '学生甲',
  role: 'student',
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
};

const singleQuestion: Question = {
  id: 1,
  owner_id: 2,
  type: 'single',
  stem: '下列哪个是质数？',
  options: [
    { key: 'A', text: '4' },
    { key: 'B', text: '5' },
    { key: 'C', text: '6' },
  ],
  answer: ['B'],
  explanation: '5 只有 1 和自身两个约数。',
  difficulty: 2,
  tags: ['数学'],
  created_at: '2026-01-01T00:00:00Z',
};

const multipleQuestion: Question = {
  id: 2,
  owner_id: 2,
  type: 'multiple',
  stem: '下列哪些是偶数？',
  options: [
    { key: 'A', text: '2' },
    { key: 'B', text: '4' },
    { key: 'C', text: '5' },
  ],
  answer: ['A', 'B'],
  explanation: '2 与 4 都能被 2 整除。',
  difficulty: 2,
  tags: ['数学'],
  created_at: '2026-01-02T00:00:00Z',
};

const thirdQuestion: Question = {
  id: 3,
  owner_id: 2,
  type: 'judge',
  stem: '地球是圆的。',
  options: [
    { key: 'A', text: '正确' },
    { key: 'B', text: '错误' },
  ],
  answer: ['A'],
  explanation: '近似球体。',
  difficulty: 1,
  tags: ['常识'],
  created_at: '2026-01-03T00:00:00Z',
};

function questionPage(items: Question[], total = items.length): QuestionPage {
  return { items, total, page: 1, page_size: 20 };
}

function makePaper(overrides: Partial<Paper> = {}): Paper {
  return {
    id: 1,
    owner_id: 2,
    title: '期中试卷',
    total_points: 10,
    status: 'draft',
    questions: [{ question_id: 1, order_no: 1, points: 10, partial_rule: 'all_or_nothing' }],
    created_at: '2026-01-02T08:00:00Z',
    ...overrides,
  };
}

function makePaperPage(items: Paper[], total = items.length): PaperPage {
  return { items, total, page: 1, page_size: 20 };
}

/** 按路径返回 GET 响应（页面会并发请求 /papers、/questions、/questions/{id}）。 */
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

function renderList() {
  const queryClient = newQueryClient();
  const user = userEvent.setup({ pointerEventsCheck: 0 });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/papers']}>
        <Routes>
          <Route path="/papers" element={<PapersPage />} />
          <Route path="/papers/new" element={<div data-testid="stub-paper-editor" />} />
          <Route path="/papers/:paperId/edit" element={<div data-testid="stub-paper-editor" />} />
          <Route path="/papers/:paperId" element={<div data-testid="stub-paper-detail" />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...view, queryClient, user };
}

function renderEditor(path: string) {
  const queryClient = newQueryClient();
  const user = userEvent.setup({ pointerEventsCheck: 0 });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/papers" element={<div data-testid="stub-papers-list" />} />
          <Route path="/papers/new" element={<PaperEditorPage />} />
          <Route path="/papers/:paperId/edit" element={<PaperEditorPage />} />
          <Route path="/papers/:paperId" element={<div data-testid="stub-paper-detail" />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...view, queryClient, user };
}

function selected(id: number, points: number): SelectedPaperQuestion {
  return {
    question_id: id,
    stem: `题目 ${id}`,
    type: 'single',
    points,
    partial_rule: 'all_or_nothing',
  };
}

describe('papers page', () => {
  beforeEach(() => {
    clearSession();
    window.sessionStorage.clear();
  });

  afterEach(() => {
    clearSession();
    window.sessionStorage.clear();
  });

  it('列表：标题/状态/总分/创建时间/操作列，请求带分页参数', async () => {
    const draftPaper = makePaper({ id: 1, title: '期中试卷', total_points: 20 });
    const frozenPaper = makePaper({
      id: 2,
      title: '期末试卷',
      status: 'frozen',
      total_points: 100,
      frozen_at: '2026-02-01T10:00:00Z',
      questions: [{ question_id: 1, order_no: 1, points: 100, partial_rule: 'all_or_nothing' }],
    });
    stubApiGet(() => makePaperPage([draftPaper, frozenPaper]));

    renderList();

    expect(await screen.findByTestId('papers-row-1')).toHaveTextContent('期中试卷');
    expect(screen.getByTestId('papers-row-2')).toHaveTextContent('期末试卷');
    expect(screen.getByTestId('papers-status-1')).toHaveTextContent('草稿');
    expect(screen.getByTestId('papers-status-2')).toHaveTextContent('已冻结');
    expect(screen.getByTestId('papers-total-1')).toHaveTextContent('20 分');
    expect(screen.getByTestId('papers-total-2')).toHaveTextContent('100 分');

    // 创建时间（本地时区格式化，断言日期格式即可）
    expect(screen.getAllByText(/2026-\d{2}-\d{2} \d{2}:\d{2}/).length).toBeGreaterThan(0);

    expect(screen.getByTestId('papers-view-1')).toBeInTheDocument();
    expect(screen.getByTestId('papers-edit-1')).toBeInTheDocument();
    expect(screen.getByTestId('papers-delete-1')).toBeInTheDocument();
    // frozen 仍可打开「编辑」（页面只读 + 冻结提示），删除由后端返回 409
    expect(screen.getByTestId('papers-edit-2')).toBeInTheDocument();
    expect(screen.getByTestId('papers-view-2')).toBeInTheDocument();
    expect(screen.getByTestId('papers-delete-2')).toBeInTheDocument();

    expect(screen.getByText('共 2 份')).toBeInTheDocument();
    expect(api.get).toHaveBeenCalledWith(
      '/papers',
      expect.objectContaining({ query: expect.objectContaining({ page: 1, page_size: 20 }) }),
    );
  });

  it('删除：二次确认后才调用 DELETE，409 PAPER_FROZEN 提示已用于已发布考试', async () => {
    const frozenPaper = makePaper({ id: 2, title: '已发布期末卷', status: 'frozen', total_points: 100 });
    stubApiGet(() => makePaperPage([frozenPaper]));
    const deleteSpy = vi
      .spyOn(api, 'delete')
      .mockRejectedValue(
        new ApiError({ status: 409, code: 'PAPER_FROZEN', message: '试卷已冻结，无法删除' }),
      );

    const { user } = renderList();
    await screen.findByTestId('papers-row-2');

    await user.click(screen.getByTestId('papers-delete-2'));
    expect(await screen.findByTestId('papers-delete-confirm')).toBeInTheDocument();
    expect(deleteSpy).not.toHaveBeenCalled();

    await user.click(screen.getByTestId('papers-delete-confirm'));

    expect(await screen.findByTestId('papers-error')).toHaveTextContent('已用于已发布考试，不可修改');
    expect(deleteSpy).toHaveBeenCalledWith('/papers/2');
    expect(screen.getByTestId('papers-row-2')).toBeInTheDocument();
  });

  it('删除：409 被考试引用提示对应中文文案', async () => {
    stubApiGet(() => makePaperPage([makePaper({ id: 1 })]));
    const deleteSpy = vi
      .spyOn(api, 'delete')
      .mockRejectedValue(
        new ApiError({ status: 409, code: 'CONFLICT', message: 'Paper is referenced by an exam' }),
      );

    const { user } = renderList();
    await screen.findByTestId('papers-row-1');

    await user.click(screen.getByTestId('papers-delete-1'));
    await screen.findByTestId('papers-delete-confirm');
    await user.click(screen.getByTestId('papers-delete-confirm'));

    expect(await screen.findByTestId('papers-error')).toHaveTextContent('试卷已被考试引用，无法删除');
    expect(deleteSpy).toHaveBeenCalledWith('/papers/1');
  });

  it('组卷：勾选加入 / 设分 / 总分实时 / 保存只提交 title+questions 并失效缓存', async () => {
    stubApiGet((path) => {
      if (path === '/questions') return questionPage([singleQuestion, multipleQuestion]);
      throw new Error(`unexpected GET ${path}`);
    });
    const postSpy = vi.spyOn(api, 'post').mockResolvedValue(makePaper({ id: 7 }));

    const { user, queryClient } = renderEditor('/papers/new');
    const picker = await screen.findByTestId('papers-picker');
    await waitFor(() =>
      expect(within(picker).getAllByRole('checkbox').length).toBeGreaterThan(1),
    );

    // 勾选第 1 题 → 「加入已选」
    const firstRow = within(picker).getByText('下列哪个是质数？').closest('tr');
    expect(firstRow).not.toBeNull();
    await user.click(within(firstRow as HTMLElement).getByRole('checkbox'));
    await user.click(screen.getByTestId('papers-add-selected'));
    expect(await screen.findByTestId('papers-selected-1')).toBeInTheDocument();

    // 第 2 题（多选）用单行「加入」
    await user.click(screen.getByTestId('papers-add-2'));
    expect(await screen.findByTestId('papers-selected-2')).toBeInTheDocument();
    expect(screen.getByTestId('papers-points-1')).toBeInTheDocument();
    expect(screen.getByTestId('papers-points-2')).toBeInTheDocument();
    expect(screen.getByTestId('papers-total-points')).toHaveTextContent('10 分');

    // 实时改分：5 + 5 → 30 + 5
    const pointsInput = within(screen.getByTestId('papers-selected-1')).getByRole('spinbutton');
    fireEvent.change(pointsInput, { target: { value: '30' } });
    fireEvent.blur(pointsInput);
    await waitFor(() =>
      expect(screen.getByTestId('papers-total-points')).toHaveTextContent('35 分'),
    );

    // 多选给分规则：切 partial
    await user.click(
      within(screen.getByTestId('papers-selected-2')).getByRole('radio', { name: '按比例给分' }),
    );

    await user.type(screen.getByTestId('papers-title'), '期中测试');
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    await user.click(screen.getByTestId('papers-save'));

    await waitFor(() => expect(postSpy).toHaveBeenCalledTimes(1));
    expect(postSpy).toHaveBeenCalledWith('/papers', {
      title: '期中测试',
      questions: [
        { question_id: 1, order_no: 1, points: 30, partial_rule: 'all_or_nothing' },
        { question_id: 2, order_no: 2, points: 5, partial_rule: 'partial' },
      ],
    });
    const payload = postSpy.mock.calls[0]?.[1] as PaperCreate;
    expect(payload).not.toHaveProperty('total_points');

    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['papers'] }),
    );
    expect(await screen.findByTestId('stub-papers-list')).toBeInTheDocument();
  });

  it('排序：上移改变 order_no 并反映到保存 payload 的顺序', async () => {
    stubApiGet(() => questionPage([singleQuestion, multipleQuestion, thirdQuestion]));
    const postSpy = vi.spyOn(api, 'post').mockResolvedValue(makePaper({ id: 9 }));

    const { user } = renderEditor('/papers/new');
    await screen.findByTestId('papers-add-1');
    await user.click(screen.getByTestId('papers-add-1'));
    await user.click(screen.getByTestId('papers-add-2'));
    await user.click(screen.getByTestId('papers-add-3'));

    await user.click(screen.getByTestId('papers-up-2'));
    expect(screen.getByTestId('papers-order-2')).toHaveTextContent('1');
    expect(screen.getByTestId('papers-order-1')).toHaveTextContent('2');
    expect(screen.getByTestId('papers-order-3')).toHaveTextContent('3');

    // 首行不能再上移，末行不能再下移
    expect(screen.getByTestId('papers-up-2')).toBeDisabled();
    expect(screen.getByTestId('papers-down-3')).toBeDisabled();

    await user.type(screen.getByTestId('papers-title'), '排序测试');
    await user.click(screen.getByTestId('papers-save'));

    await waitFor(() => expect(postSpy).toHaveBeenCalledTimes(1));
    const payload = postSpy.mock.calls[0]?.[1] as PaperCreate;
    expect(payload.questions.map((item) => item.question_id)).toEqual([2, 1, 3]);
    expect(payload.questions.map((item) => item.order_no)).toEqual([1, 2, 3]);
  });

  it('保存前校验：无标题/无题目拦截且不发请求', async () => {
    stubApiGet(() => questionPage([singleQuestion]));
    const postSpy = vi.spyOn(api, 'post');

    const { user } = renderEditor('/papers/new');
    await screen.findByTestId('papers-add-1');

    await user.click(screen.getByTestId('papers-save'));

    expect(await screen.findByTestId('papers-title-error')).toHaveTextContent('请输入试卷标题');
    expect(screen.getByTestId('papers-questions-error')).toHaveTextContent('请至少选择 1 道题目');
    expect(postSpy).not.toHaveBeenCalled();
    expect(screen.getByTestId('papers-form-error')).toHaveTextContent('请先修正表单中的错误');
    // 校验后按钮仍可用，修正后可直接再次保存
    expect(screen.getByTestId('papers-save')).toBeEnabled();
  });

  it('总分/单题分值校验：边界与 10000 上限（模型层，与提交 payload 同口径）', () => {
    expect(validatePaperDraft('', []).title).toBe('请输入试卷标题');
    expect(validatePaperDraft('标题', []).questions).toBe('请至少选择 1 道题目');
    expect(validatePaperDraft('标题', [selected(1, 0)]).pointsByQuestion[1]).toContain('分值必须大于 0');
    expect(validatePaperDraft('标题', [selected(1, 100.5)]).pointsByQuestion[1]).toBeDefined();
    expect(validatePaperDraft('标题', [selected(1, 0.01)]).pointsByQuestion[1]).toBeUndefined();

    const maxItems = Array.from({ length: 100 }, (_value, index) => selected(index + 1, 100));
    expect(sumPoints(maxItems)).toBe(10000);
    expect(validatePaperDraft('满分试卷', maxItems).total).toBeUndefined();

    const overflow = [...maxItems.slice(0, 99), selected(100, 100), selected(101, 0.01)];
    expect(sumPoints(overflow)).toBe(10000.01);
    expect(validatePaperDraft('超分试卷', overflow).total).toContain('10000');

    expect(buildPaperPayload(' 标题 ', [selected(1, 12.346)]).questions[0]?.points).toBe(12.35);
  });

  it('frozen：编辑页只读并提示「已用于已发布考试，不可修改」', async () => {
    const frozenPaper = makePaper({
      id: 3,
      title: '已发布期末卷',
      status: 'frozen',
      total_points: 10,
      frozen_at: '2026-02-01T10:00:00Z',
    });
    stubApiGet((path) => {
      if (path === '/papers/3') return frozenPaper;
      if (path === '/questions/1') return singleQuestion;
      if (path === '/questions') return questionPage([singleQuestion]);
      throw new Error(`unexpected GET ${path}`);
    });
    const putSpy = vi.spyOn(api, 'put');

    renderEditor('/papers/3/edit');

    const alert = await screen.findByTestId('papers-frozen-alert');
    expect(alert).toHaveTextContent('已用于已发布考试，不可修改');
    expect(screen.getByTestId('papers-save')).toBeDisabled();
    expect(screen.getByTestId('papers-title')).toBeDisabled();
    expect(screen.getByTestId('papers-title')).toHaveValue('已发布期末卷');
    // 只读态分值以文本展示
    expect(screen.getByTestId('papers-points-1')).toHaveTextContent('10 分');
    expect(screen.getByTestId('papers-points-1').tagName).not.toBe('INPUT');
    expect(screen.queryByTestId('papers-partial-1')).not.toBeInTheDocument();
    expect(putSpy).not.toHaveBeenCalled();
  });

  it('保存 422：后端字段错误回显到对应分值输入并保留已选题目', async () => {
    stubApiGet(() => questionPage([singleQuestion]));
    vi.spyOn(api, 'post').mockRejectedValue(
      new ApiError({
        status: 422,
        code: 'VALIDATION_ERROR',
        message: 'body.questions.0.points: Input should be less than or equal to 100',
      }),
    );

    const { user } = renderEditor('/papers/new');
    await screen.findByTestId('papers-add-1');
    await user.click(screen.getByTestId('papers-add-1'));
    await user.type(screen.getByTestId('papers-title'), '422 测试');
    await user.click(screen.getByTestId('papers-save'));

    expect(await screen.findByTestId('papers-points-error-1')).toHaveTextContent('分值不合法');
    expect(screen.getByTestId('papers-form-error')).toHaveTextContent('参数校验失败');
    expect(screen.getByTestId('papers-selected-1')).toBeInTheDocument();
  });

  it('导航菜单：教师可见「组卷」并可进入 /papers，学生隐藏', async () => {
    setSession(teacherUser, 'csrf-teacher');
    const queryClient = newQueryClient();
    const { unmount } = render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/']}>
          <Routes>
            <Route element={<AppLayout />}>
              <Route index element={<div data-testid="stub-home" />} />
              <Route path="papers" element={<div data-testid="stub-papers-page" />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const user = userEvent.setup({ pointerEventsCheck: 0 });
    expect(screen.getByText('组卷')).toBeInTheDocument();
    await user.click(screen.getByText('组卷'));
    expect(await screen.findByTestId('stub-papers-page')).toBeInTheDocument();

    unmount();
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
    expect(screen.queryByText('组卷')).not.toBeInTheDocument();
    expect(screen.getByText('工作台')).toBeInTheDocument();
  });
});
