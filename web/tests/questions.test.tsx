import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, api } from '../src/api/client';
import type { User } from '../src/api/types';
import AppLayout from '../src/components/AppLayout';
import QuestionsPage from '../src/pages/questions/QuestionsPage';
import type { ImportResult, Question, QuestionPage } from '../src/pages/questions/model';
import { clearSession, setSession } from '../src/stores/auth';

/**
 * jsdom 缺少 antd 响应式组件（Table/Modal/Layout）依赖的浏览器 API，
 * 这里补最小桩；不改变被测逻辑（同 login.test.tsx）。
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
    { key: 'D', text: '8' },
  ],
  answer: ['B'],
  explanation: '5 只有 1 和自身两个约数。',
  difficulty: 2,
  tags: ['数学', '质数'],
  created_at: '2026-01-01T00:00:00Z',
};

const judgeQuestion: Question = {
  id: 2,
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
  created_at: '2026-01-02T00:00:00Z',
};

function questionPage(items: Question[], total = items.length): QuestionPage {
  return { items, total, page: 1, page_size: 20 };
}

function renderQuestions() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  const user = userEvent.setup({ pointerEventsCheck: 0 });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <QuestionsPage />
    </QueryClientProvider>,
  );
  return { ...view, queryClient, user };
}

function renderLayout() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route element={<AppLayout />}>
            <Route index element={<div data-testid="stub-home" />} />
            <Route path="questions" element={<div data-testid="stub-questions-page" />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('questions page', () => {
  beforeEach(() => {
    clearSession();
    window.sessionStorage.clear();
  });

  afterEach(() => {
    clearSession();
    window.sessionStorage.clear();
  });

  it('列表渲染：题干/题型/难度/标签/操作列，请求带分页参数', async () => {
    const getSpy = vi
      .spyOn(api, 'get')
      .mockResolvedValue(questionPage([singleQuestion, judgeQuestion]));

    renderQuestions();

    expect(await screen.findByTestId('questions-row-1')).toHaveTextContent('下列哪个是质数？');
    expect(screen.getByTestId('questions-row-2')).toHaveTextContent('地球是圆的。');

    const row1 = screen.getByTestId('questions-row-1').closest('tr');
    expect(row1).not.toBeNull();
    expect(within(row1 as HTMLElement).getByText('单选题')).toBeInTheDocument();
    expect(within(row1 as HTMLElement).getByText('质数')).toBeInTheDocument();

    expect(screen.getByTestId('questions-edit-1')).toBeInTheDocument();
    expect(screen.getByTestId('questions-delete-2')).toBeInTheDocument();
    expect(screen.getByText('共 2 题')).toBeInTheDocument();
    expect(getSpy).toHaveBeenCalledWith(
      '/questions',
      expect.objectContaining({
        query: expect.objectContaining({ page: 1, page_size: 20 }),
      }),
    );
  });

  it('筛选：关键词 / 题型 / 标签参数生效并重置到第 1 页', async () => {
    const getSpy = vi.spyOn(api, 'get').mockResolvedValue(questionPage([singleQuestion]));
    const { user } = renderQuestions();
    await screen.findByTestId('questions-row-1');

    await user.type(screen.getByTestId('questions-keyword'), '质数');
    await user.click(screen.getByTestId('questions-search'));
    await waitFor(() => {
      expect(getSpy).toHaveBeenLastCalledWith(
        '/questions',
        expect.objectContaining({ query: expect.objectContaining({ keyword: '质数', page: 1 }) }),
      );
    });

    await user.click(screen.getByRole('radio', { name: '多选题' }));
    await waitFor(() => {
      expect(getSpy).toHaveBeenLastCalledWith(
        '/questions',
        expect.objectContaining({
          query: expect.objectContaining({ keyword: '质数', type: 'multiple' }),
        }),
      );
    });

    await user.type(screen.getByTestId('questions-tag-filter'), '代数');
    await user.click(screen.getByTestId('questions-search'));
    await waitFor(() => {
      expect(getSpy).toHaveBeenLastCalledWith(
        '/questions',
        expect.objectContaining({ query: expect.objectContaining({ tag: '代数' }) }),
      );
    });
  });

  it('表单校验：空表单提交展示题干/选项/答案/解析错误且不发请求', async () => {
    vi.spyOn(api, 'get').mockResolvedValue(questionPage([]));
    const postSpy = vi.spyOn(api, 'post');
    const { user } = renderQuestions();
    await waitFor(() => expect(api.get).toHaveBeenCalled());

    await user.click(screen.getByTestId('questions-create'));
    expect(await screen.findByTestId('questions-stem')).toBeInTheDocument();

    await user.click(screen.getByTestId('questions-submit'));

    expect(await screen.findByText('请输入题干')).toBeInTheDocument();
    expect(await screen.findByText('请输入解析')).toBeInTheDocument();
    expect(await screen.findByText('请选择 1 个正确答案')).toBeInTheDocument();
    expect((await screen.findAllByText('请输入选项内容')).length).toBeGreaterThan(0);
    expect(postSpy).not.toHaveBeenCalled();
  });

  it('题型切换：判断题固定 A/B 且不可增删，多选要求至少 2 个答案', async () => {
    vi.spyOn(api, 'get').mockResolvedValue(questionPage([]));
    const { user } = renderQuestions();
    await waitFor(() => expect(api.get).toHaveBeenCalled());

    await user.click(screen.getByTestId('questions-create'));
    const dialog = await screen.findByRole('dialog');

    await user.click(within(dialog).getByRole('radio', { name: '判断题' }));
    expect(screen.getByTestId('questions-option-text-0')).toHaveValue('正确');
    expect(screen.getByTestId('questions-option-text-1')).toHaveValue('错误');
    expect(screen.queryByTestId('questions-option-add')).not.toBeInTheDocument();
    expect(screen.queryByTestId('questions-option-remove-0')).not.toBeInTheDocument();

    await user.click(within(dialog).getByRole('radio', { name: '多选题' }));
    expect(await screen.findByTestId('questions-option-add')).toBeInTheDocument();

    await user.click(screen.getByTestId('questions-submit'));
    expect(await screen.findByText('多选题请至少选择 2 个正确答案')).toBeInTheDocument();
  });

  it('新建：合法表单按契约 payload 提交，成功后失效列表缓存重新拉取', async () => {
    const getSpy = vi.spyOn(api, 'get').mockResolvedValue(questionPage([]));
    const postSpy = vi.spyOn(api, 'post').mockResolvedValue({ ...singleQuestion, id: 9 });
    const { user } = renderQuestions();
    await waitFor(() => expect(getSpy).toHaveBeenCalledTimes(1));

    await user.click(screen.getByTestId('questions-create'));
    await screen.findByTestId('questions-stem');

    await user.type(screen.getByTestId('questions-stem'), '1+1=?');
    await user.type(screen.getByTestId('questions-option-text-0'), '1');
    await user.type(screen.getByTestId('questions-option-text-1'), '2');
    await user.type(screen.getByTestId('questions-option-text-2'), '3');
    await user.type(screen.getByTestId('questions-option-text-3'), '4');
    await user.click(screen.getByRole('radio', { name: 'B. 2' }));
    await user.type(screen.getByTestId('questions-explanation'), '基础算术');
    await user.type(screen.getByTestId('questions-tags'), '算术, 小学，算术');

    await user.click(screen.getByTestId('questions-submit'));

    await waitFor(() => expect(postSpy).toHaveBeenCalledTimes(1));
    expect(postSpy).toHaveBeenCalledWith('/questions', {
      type: 'single',
      stem: '1+1=?',
      options: [
        { key: 'A', text: '1' },
        { key: 'B', text: '2' },
        { key: 'C', text: '3' },
        { key: 'D', text: '4' },
      ],
      answer: ['B'],
      explanation: '基础算术',
      difficulty: 3,
      tags: ['算术', '小学'],
    });

    await waitFor(() => expect(getSpy).toHaveBeenCalledTimes(2));
  });

  it('编辑保存失败：后端 422 错误在弹窗内回显且保留输入', async () => {
    vi.spyOn(api, 'get').mockResolvedValue(questionPage([singleQuestion]));
    const putSpy = vi
      .spyOn(api, 'put')
      .mockRejectedValue(
        new ApiError({ status: 422, code: 'VALIDATION_ERROR', message: 'stem 长度超出限制' }),
      );
    const { user } = renderQuestions();
    await screen.findByTestId('questions-row-1');

    await user.click(screen.getByTestId('questions-edit-1'));
    const stemInput = await screen.findByTestId('questions-stem');
    await waitFor(() => {
      expect(stemInput).toHaveValue('下列哪个是质数？');
    });

    await user.click(screen.getByTestId('questions-submit'));

    expect(await screen.findByTestId('questions-form-error')).toHaveTextContent('参数校验失败');
    expect(screen.getByTestId('questions-stem')).toHaveValue('下列哪个是质数？');
    expect(putSpy).toHaveBeenCalledTimes(1);
    expect(putSpy.mock.calls[0]?.[0]).toBe('/questions/1');
    expect(putSpy.mock.calls[0]?.[1]).toMatchObject({
      type: 'single',
      stem: '下列哪个是质数？',
      answer: ['B'],
      tags: ['数学', '质数'],
    });
  });

  it('批量导入：multipart 上传后渲染 imported/failed/total/skipped 结果表', async () => {
    vi.spyOn(api, 'get').mockResolvedValue(questionPage([]));
    const importResult: ImportResult = {
      imported: 2,
      skipped_duplicates: 1,
      failed: [{ row: 3, reason: '缺少解析' }],
      total: 4,
    };
    const postSpy = vi.spyOn(api, 'post').mockResolvedValue(importResult);
    const { user } = renderQuestions();
    await waitFor(() => expect(api.get).toHaveBeenCalled());

    await user.click(screen.getByTestId('questions-import'));
    await screen.findByTestId('questions-import-file');

    const file = new File(['type,stem\nsingle,1+1'], 'questions.csv', { type: 'text/csv' });
    fireEvent.change(screen.getByTestId('questions-import-file'), {
      target: { files: [file] },
    });
    await user.click(screen.getByTestId('questions-import-submit'));

    expect(await screen.findByTestId('questions-import-result')).toBeInTheDocument();
    expect(screen.getByTestId('questions-import-total')).toHaveTextContent('4');
    expect(screen.getByTestId('questions-import-imported')).toHaveTextContent('2');
    expect(screen.getByTestId('questions-import-skipped')).toHaveTextContent('1');
    expect(screen.getByTestId('questions-import-failed')).toHaveTextContent('1');

    const resultArea = screen.getByTestId('questions-import-result');
    expect(within(resultArea).getByText('缺少解析')).toBeInTheDocument();
    expect(within(resultArea).getByText('3')).toBeInTheDocument();

    const call = postSpy.mock.calls[0];
    expect(call?.[0]).toBe('/questions/import');
    const body = call?.[1];
    expect(body).toBeInstanceOf(FormData);
    expect((body as FormData).get('file')).toBe(file);
  });

  it('删除：二次确认后才调用 DELETE，409 提示「题目已被试卷引用」且题目保留', async () => {
    vi.spyOn(api, 'get').mockResolvedValue(questionPage([singleQuestion]));
    const deleteSpy = vi
      .spyOn(api, 'delete')
      .mockRejectedValue(
        new ApiError({ status: 409, code: 'QUESTION_IN_USE', message: 'Question is used by a paper' }),
      );
    const { user } = renderQuestions();
    await screen.findByTestId('questions-row-1');

    await user.click(screen.getByTestId('questions-delete-1'));
    expect(await screen.findByTestId('questions-delete-confirm')).toBeInTheDocument();
    expect(deleteSpy).not.toHaveBeenCalled();

    await user.click(screen.getByTestId('questions-delete-confirm'));

    expect(await screen.findByTestId('questions-error')).toHaveTextContent('题目已被试卷引用');
    expect(deleteSpy).toHaveBeenCalledWith('/questions/1');
    expect(screen.getByTestId('questions-row-1')).toBeInTheDocument();
  });

  it('删除：403 FORBIDDEN 提示无权限', async () => {
    vi.spyOn(api, 'get').mockResolvedValue(questionPage([singleQuestion]));
    const deleteSpy = vi
      .spyOn(api, 'delete')
      .mockRejectedValue(new ApiError({ status: 403, code: 'FORBIDDEN', message: 'forbidden' }));
    const { user } = renderQuestions();
    await screen.findByTestId('questions-row-1');

    await user.click(screen.getByTestId('questions-delete-1'));
    await screen.findByTestId('questions-delete-confirm');
    await user.click(screen.getByTestId('questions-delete-confirm'));

    await waitFor(() => {
      expect(screen.getByTestId('questions-error')).toHaveTextContent('没有权限执行该操作');
    });
    expect(deleteSpy).toHaveBeenCalledWith('/questions/1');
  });

  it('导航菜单：教师可见「题库管理」并可进入 /questions，学生隐藏', async () => {
    setSession(teacherUser, 'csrf-teacher');
    const { unmount } = renderLayout();
    const user = userEvent.setup({ pointerEventsCheck: 0 });

    await user.click(screen.getByText('题库管理'));
    expect(await screen.findByTestId('stub-questions-page')).toBeInTheDocument();

    unmount();
    clearSession();

    setSession(studentUser, 'csrf-student');
    renderLayout();
    expect(screen.queryByText('题库管理')).not.toBeInTheDocument();
    expect(screen.getByText('工作台')).toBeInTheDocument();
  });
});
