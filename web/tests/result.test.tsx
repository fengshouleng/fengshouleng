import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, api } from '../src/api/client';
import type { User } from '../src/api/types';
import AppLayout from '../src/components/AppLayout';
import MyResultsPage from '../src/pages/result/MyResultsPage';
import ResultPage from '../src/pages/result/ResultPage';
import type { MyResultItem, ResultDetail } from '../src/pages/result/model';
import TeacherExamResultsPage from '../src/pages/teacher/TeacherExamResultsPage';
import type { ExamResultSummary } from '../src/pages/teacher/model';
import WrongQuestionsPage from '../src/pages/wrong/WrongQuestionsPage';
import type { WrongQuestion } from '../src/pages/wrong/model';
import { clearSession, setSession } from '../src/stores/auth';

/**
 * jsdom 缺少 antd 响应式组件（Table/List/Layout）依赖的浏览器 API，
 * 这里补最小桩；不改变被测逻辑（同 papers.test.tsx / questions.test.tsx）。
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

const studentUser: User = {
  id: 1,
  username: 'stu001',
  display_name: '学生甲',
  role: 'student',
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

const resultDetail: ResultDetail = {
  session_id: 8,
  exam_id: 3,
  score: 85,
  total_points: 100,
  submitted_at: '2026-05-01T10:00:00Z',
  items: [
    {
      question_id: 1,
      type: 'single',
      stem: '下列哪个是质数？',
      selected: ['B'],
      correct_answer: ['B'],
      is_correct: true,
      earned_points: 40,
      points: 40,
      explanation: '5 是质数。',
    },
    {
      question_id: 2,
      type: 'multiple',
      stem: '下列哪些是偶数？',
      selected: ['A'],
      correct_answer: ['A', 'B'],
      is_correct: false,
      earned_points: 45,
      points: 60,
      explanation: '少选且无错选，按比例给分。',
    },
  ],
};

const myResult: MyResultItem = {
  session_id: 7,
  exam_id: 2,
  exam_title: '期中考试',
  score: 90,
  total_points: 100,
  submitted_at: '2026-04-01T09:00:00Z',
};

const wrongQuestion: WrongQuestion = {
  question_id: 11,
  type: 'single',
  stem: '以下哪个不是编程语言？',
  wrong_count: 2,
  last_wrong_at: '2026-04-10T12:00:00Z',
};

const secondWrongQuestion: WrongQuestion = {
  question_id: 12,
  type: 'judge',
  stem: '第二页的错题',
  wrong_count: 1,
  last_wrong_at: '2026-04-11T12:00:00Z',
};

const examSummary: ExamResultSummary = {
  exam_id: 5,
  total_sessions: 12,
  submitted: 10,
  average_score: 76.5,
  items: [
    {
      student_id: 1,
      display_name: '学生甲',
      status: 'submitted',
      score: 90,
      submitted_at: '2026-05-01T10:00:00Z',
    },
    { student_id: 2, display_name: '学生乙', status: 'in_progress' },
    {
      student_id: 3,
      display_name: '学生丙',
      status: 'expired',
      score: 63,
      submitted_at: '2026-05-01T11:00:00Z',
    },
  ],
};

type GetOptions = { query?: Record<string, unknown> };

/** 按路径/查询参数返回 GET 响应（页面请求全部经 client.ts，故只 mock api.get）。 */
function stubApiGet(handler: (path: string, options?: GetOptions) => unknown): void {
  const implementation = ((path: string, options?: GetOptions) => {
    try {
      return Promise.resolve(handler(path, options));
    } catch (error) {
      return Promise.reject(error);
    }
  }) as unknown as typeof api.get;
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

function renderRoutes(path: string) {
  const queryClient = newQueryClient();
  const view = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/results/:sessionId" element={<ResultPage />} />
          <Route path="/my-results" element={<MyResultsPage />} />
          <Route path="/wrong-questions" element={<WrongQuestionsPage />} />
          <Route path="/exam-results" element={<TeacherExamResultsPage />} />
          <Route path="/exam-results/:examId" element={<TeacherExamResultsPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...view, queryClient, user: userEvent.setup({ pointerEventsCheck: 0 }) };
}

function renderLayout() {
  return render(
    <QueryClientProvider client={newQueryClient()}>
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route element={<AppLayout />}>
            <Route index element={<div data-testid="stub-home" />} />
            <Route path="my-results" element={<div data-testid="stub-my-results" />} />
            <Route path="wrong-questions" element={<div data-testid="stub-wrong" />} />
            <Route path="exam-results" element={<div data-testid="stub-teacher-summary" />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('result pages', () => {
  beforeEach(() => {
    clearSession();
    window.sessionStorage.clear();
  });

  afterEach(() => {
    clearSession();
    window.sessionStorage.clear();
  });

  it('成绩页：总分/满分、逐题我的答案/正确答案/对错/得分/解析', async () => {
    stubApiGet((path) => {
      if (path === '/sessions/8/result') return resultDetail;
      throw new Error(`unexpected GET ${path}`);
    });

    renderRoutes('/results/8');

    const score = await screen.findByTestId('result-score');
    expect(score).toHaveTextContent('85 / 100 分');
    expect(score).toHaveTextContent('共 2 道题');

    const list = screen.getByTestId('result-list');
    const first = within(list).getByTestId('result-item-1');
    expect(first).toHaveTextContent('下列哪个是质数？');
    expect(first).toHaveTextContent('我的答案：B');
    expect(first).toHaveTextContent('正确答案：B');
    expect(first).toHaveTextContent('回答正确');
    expect(first).toHaveTextContent('40 / 40 分');
    expect(first).toHaveTextContent('5 是质数。');

    const second = within(list).getByTestId('result-item-2');
    expect(second).toHaveTextContent('下列哪些是偶数？');
    expect(second).toHaveTextContent('我的答案：A');
    expect(second).toHaveTextContent('正确答案：A、B');
    expect(second).toHaveTextContent('回答错误');
    expect(second).toHaveTextContent('45 / 60 分');
    expect(second).toHaveTextContent('少选且无错选，按比例给分。');

    expect(api.get).toHaveBeenCalledWith('/sessions/8/result');
  });

  it('成绩页：未交卷 409 NOT_SUBMITTED → 提示「尚未交卷」且不展示成绩', async () => {
    stubApiGet(() => {
      throw new ApiError({
        status: 409,
        code: 'NOT_SUBMITTED',
        message: 'Session has not been submitted',
      });
    });

    renderRoutes('/results/9');

    expect(await screen.findByTestId('result-error')).toHaveTextContent('尚未交卷');
    expect(screen.queryByTestId('result-score')).not.toBeInTheDocument();
    expect(screen.queryByTestId('result-list')).not.toBeInTheDocument();
  });

  it('成绩页：越权访问（403）→ 提示「无权查看该成绩」', async () => {
    stubApiGet(() => {
      throw new ApiError({ status: 403, code: 'FORBIDDEN', message: 'Forbidden' });
    });

    renderRoutes('/results/10');

    expect(await screen.findByTestId('result-error')).toHaveTextContent('无权查看该成绩');
    expect(screen.queryByTestId('result-score')).not.toBeInTheDocument();
  });

  it('我的成绩：列表展示 exam_title/score/total_points/submitted_at 并可进入逐题成绩', async () => {
    stubApiGet((path) => {
      if (path === '/me/results') return { items: [myResult], total: 1, page: 1, page_size: 20 };
      if (path === '/sessions/7/result') return { ...resultDetail, session_id: 7 };
      throw new Error(`unexpected GET ${path}`);
    });

    const { user } = renderRoutes('/my-results');

    const link = await screen.findByTestId('my-results-row-7');
    expect(link).toHaveTextContent('期中考试');
    expect(link).toHaveAttribute('href', '/results/7');
    expect(screen.getByTestId('my-results-table')).toHaveTextContent('90 / 100 分');
    expect(screen.getByTestId('my-results-table')).toHaveTextContent('共 1 条');
    expect(api.get).toHaveBeenCalledWith(
      '/me/results',
      expect.objectContaining({ query: expect.objectContaining({ page: 1, page_size: 20 }) }),
    );

    await user.click(link);
    expect(await screen.findByTestId('result-score')).toHaveTextContent('85 / 100 分');
  });

  it('错题本：展示题干/题型/wrong_count，翻页请求 page=2', async () => {
    stubApiGet((path, options) => {
      if (path !== '/me/wrong-questions') throw new Error(`unexpected GET ${path}`);
      const page = Number(options?.query?.page ?? 1);
      return page === 1
        ? { items: [wrongQuestion], total: 21, page: 1, page_size: 20 }
        : { items: [secondWrongQuestion], total: 21, page: 2, page_size: 20 };
    });

    const { user } = renderRoutes('/wrong-questions');

    const list = await screen.findByTestId('wrong-list');
    const firstWrong = await within(list).findByTestId('wrong-item-11');
    expect(firstWrong).toHaveTextContent('以下哪个不是编程语言？');
    expect(firstWrong).toHaveTextContent('单选题');
    expect(firstWrong).toHaveTextContent('错误 2 次');
    expect(screen.getByTestId('wrong-pagination')).toHaveTextContent('共 21 道错题');

    await user.click(screen.getByTitle('2'));

    expect(await screen.findByTestId('wrong-item-12')).toHaveTextContent('第二页的错题');
    expect(api.get).toHaveBeenCalledWith(
      '/me/wrong-questions',
      expect.objectContaining({ query: expect.objectContaining({ page: 2, page_size: 20 }) }),
    );
  });

  it('教师汇总：total_sessions/submitted/average_score 与学生明细表渲染', async () => {
    stubApiGet((path) => {
      if (path === '/exams/5/results') return examSummary;
      throw new Error(`unexpected GET ${path}`);
    });

    renderRoutes('/exam-results/5');

    const table = await screen.findByTestId('teacher-summary-table');
    await within(table).findByText('学生甲');
    expect(screen.getByTestId('teacher-total-sessions')).toHaveTextContent('12');
    expect(screen.getByTestId('teacher-submitted')).toHaveTextContent('10');
    expect(screen.getByTestId('teacher-average-score')).toHaveTextContent('76.50');

    expect(table).toHaveTextContent('学生甲');
    expect(table).toHaveTextContent('学生乙');
    expect(table).toHaveTextContent('学生丙');
    expect(table).toHaveTextContent('已交卷');
    expect(table).toHaveTextContent('进行中');
    expect(table).toHaveTextContent('已到期');
    expect(table).toHaveTextContent('90 分');
    expect(table).toHaveTextContent('63 分');

    expect(api.get).toHaveBeenCalledWith('/exams/5/results');
  });

  it('教师汇总：越权 403 → 提示「无权查看该考试的成绩汇总」', async () => {
    stubApiGet(() => {
      throw new ApiError({ status: 403, code: 'FORBIDDEN', message: 'Forbidden' });
    });

    renderRoutes('/exam-results/6');

    expect(await screen.findByTestId('teacher-summary-error')).toHaveTextContent(
      '无权查看该考试的成绩汇总',
    );
    expect(screen.queryByTestId('teacher-summary-table')).not.toBeInTheDocument();
  });

  it('菜单：学生可见我的考试/我的成绩/错题本，教师可见考试汇总', async () => {
    setSession(studentUser, 'csrf-student');
    const { unmount } = renderLayout();

    expect(await screen.findByText('我的考试')).toBeInTheDocument();
    expect(screen.getByText('我的成绩')).toBeInTheDocument();
    expect(screen.getByText('错题本')).toBeInTheDocument();
    expect(screen.queryByText('题库管理')).not.toBeInTheDocument();

    unmount();
    clearSession();

    setSession(teacherUser, 'csrf-teacher');
    renderLayout();

    expect(await screen.findByText('题库管理')).toBeInTheDocument();
    expect(screen.getByText('组卷')).toBeInTheDocument();
    expect(screen.getByText('考试汇总')).toBeInTheDocument();
    expect(screen.queryByText('我的成绩')).not.toBeInTheDocument();
  });
});
