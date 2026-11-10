import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation, useParams } from 'react-router-dom';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, api } from '../src/api/client';
import type { User } from '../src/api/types';
import AppLayout from '../src/components/AppLayout';
import ExamPage from '../src/pages/exam/ExamPage';
import type {
  AnswerSaveRequest,
  AnswerSaveResponse,
  MyExamPage,
  SessionQuestion,
  SessionState,
  SubmitResult,
} from '../src/pages/exam/model';
import MyExamsPage from '../src/pages/student/MyExamsPage';
import { clearSession, setSession } from '../src/stores/auth';

/**
 * jsdom 缺少 antd（Table/Modal/Layout）依赖的浏览器 API，补最小桩；
 * 不改变被测逻辑（同 questions.test.tsx / papers.test.tsx）。
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

/** navigator.onLine 在测试中可切换（断网恢复用例）。 */
let onlineValue = true;
beforeAll(() => {
  Object.defineProperty(window.navigator, 'onLine', {
    configurable: true,
    get: () => onlineValue,
  });
});

beforeEach(() => {
  onlineValue = true;
  clearSession();
  window.sessionStorage.clear();
});

afterEach(() => {
  clearSession();
  window.sessionStorage.clear();
  onlineValue = true;
});

const questions: SessionQuestion[] = [
  {
    question_id: 1,
    type: 'single',
    stem: '下列哪个是质数？',
    options: [
      { key: 'A', text: '4' },
      { key: 'B', text: '5' },
      { key: 'C', text: '6' },
    ],
    order_no: 1,
    points: 10,
  },
  {
    question_id: 2,
    type: 'multiple',
    stem: '下列哪些是偶数？',
    options: [
      { key: 'A', text: '2' },
      { key: 'B', text: '4' },
      { key: 'C', text: '5' },
    ],
    order_no: 2,
    points: 10,
  },
  {
    question_id: 3,
    type: 'judge',
    stem: '地球是圆的。',
    options: [
      { key: 'A', text: '正确' },
      { key: 'B', text: '错误' },
    ],
    order_no: 3,
    points: 10,
  },
];

function makeSession(overrides: Partial<SessionState> = {}): SessionState {
  return {
    id: 101,
    exam_id: 1,
    status: 'in_progress',
    started_at: '2026-06-01T09:00:00Z',
    deadline_at: '2026-06-01T10:00:00Z',
    server_time: '2026-06-01T09:00:00Z',
    last_client_seq: 0,
    submitted_at: null,
    score: null,
    answers: [],
    questions,
    ...overrides,
  };
}

function makeSaveResponse(
  savedSeq: number,
  overrides: Partial<AnswerSaveResponse> = {},
): AnswerSaveResponse {
  return {
    saved_seq: savedSeq,
    saved_at: '2026-06-01T09:05:00Z',
    applied: true,
    ...overrides,
  };
}

function makeSubmitResult(overrides: Partial<SubmitResult> = {}): SubmitResult {
  return {
    session_id: 101,
    status: 'submitted',
    score: 80,
    submitted_at: '2026-06-01T09:30:00Z',
    replayed: false,
    ...overrides,
  };
}

function stubPost(handler: (path: string, body?: unknown) => unknown) {
  const implementation = ((path: string, body?: unknown) =>
    Promise.resolve().then(() => handler(path, body))) as unknown as typeof api.post;
  return vi.spyOn(api, 'post').mockImplementation(implementation);
}

function stubPut(handler: (path: string, body?: unknown) => unknown) {
  const implementation = ((path: string, body?: unknown) =>
    Promise.resolve().then(() => handler(path, body))) as unknown as typeof api.put;
  return vi.spyOn(api, 'put').mockImplementation(implementation);
}

function stubGet(handler: (path: string) => unknown) {
  const implementation = ((path: string) =>
    Promise.resolve().then(() => handler(path))) as unknown as typeof api.get;
  return vi.spyOn(api, 'get').mockImplementation(implementation);
}

function newQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
}

interface ResultStubState {
  score?: number | null;
  status?: string;
  replayed?: boolean;
  notice?: string;
}

function ResultStub() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const location = useLocation();
  const state = (location.state ?? null) as ResultStubState | null;
  return (
    <div data-testid="stub-result-page">
      <span data-testid="stub-result-session">{sessionId}</span>
      <span data-testid="stub-result-score">{state?.score ?? ''}</span>
      <span data-testid="stub-result-replayed">{state?.replayed ? 'true' : 'false'}</span>
      <span data-testid="stub-result-notice">{state?.notice ?? ''}</span>
    </div>
  );
}

function renderExam() {
  const user = userEvent.setup({ pointerEventsCheck: 0 });
  const view = render(
    <QueryClientProvider client={newQueryClient()}>
      <MemoryRouter initialEntries={['/exams/1']}>
        <Routes>
          <Route path="/exams/:examId" element={<ExamPage />} />
          <Route path="/results/:sessionId" element={<ResultStub />} />
          <Route path="/my-exams" element={<div data-testid="stub-my-exams" />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...view, user };
}

describe('exam session hook', () => {
  it('client_seq 从 last_client_seq 起严格递增，切题触发保存', async () => {
    const putSpy = stubPut((_path, body) => {
      const payload = body as AnswerSaveRequest;
      return makeSaveResponse(payload.client_seq);
    });
    stubPost((path) => {
      if (path === '/exams/1/sessions') return makeSession({ last_client_seq: 4 });
      throw new Error(`unexpected POST ${path}`);
    });

    const { user } = renderExam();
    await screen.findByTestId('answer-1-B');

    await user.click(screen.getByTestId('answer-1-B'));
    await user.click(screen.getByTestId('exam-next'));

    await waitFor(() => expect(putSpy).toHaveBeenCalledTimes(1));
    expect(putSpy.mock.calls[0]?.[0]).toBe('/sessions/101/answers');
    expect(putSpy.mock.calls[0]?.[1]).toEqual({
      client_seq: 5,
      answers: [{ question_id: 1, selected: ['B'] }],
    });

    await screen.findByTestId('answer-2-A');
    await user.click(screen.getByTestId('answer-2-A'));
    await user.click(screen.getByTestId('answer-2-C'));
    await user.click(screen.getByTestId('exam-next'));
    await waitFor(() => expect(putSpy).toHaveBeenCalledTimes(2));
    expect(putSpy.mock.calls[1]?.[1]).toEqual({
      client_seq: 6,
      answers: [{ question_id: 2, selected: ['A', 'C'] }],
    });
  });

  it('刷新恢复：SessionState.answers 回填到选项', async () => {
    stubPost(() =>
      makeSession({
        answers: [
          { question_id: 1, selected: ['B'] },
          { question_id: 2, selected: ['A', 'C'] },
        ],
      }),
    );

    const { user } = renderExam();

    expect(await screen.findByTestId('answer-1-B')).toBeChecked();
    expect(screen.getByText(/已答 2 \/ 3 题/)).toBeInTheDocument();

    // 切到第 2 题，多选答案同样来自服务端回填（切题不产生保存，因为没有本地变更）。
    await user.click(screen.getByTestId('exam-next'));
    expect(await screen.findByTestId('answer-2-A')).toBeChecked();
    expect(screen.getByTestId('answer-2-B')).not.toBeChecked();
    expect(screen.getByTestId('answer-2-C')).toBeChecked();
  });

  it('倒计时归零：自动提交一次并跳转成绩页', async () => {
    const postSpy = stubPost((path) => {
      if (path === '/exams/1/sessions') {
        return makeSession({
          deadline_at: '2026-06-01T08:50:00Z',
          server_time: '2026-06-01T10:00:00Z',
        });
      }
      if (path === '/sessions/101/submit') return makeSubmitResult();
      throw new Error(`unexpected POST ${path}`);
    });

    renderExam();

    await waitFor(() => {
      const submits = postSpy.mock.calls.filter(([path]) => path === '/sessions/101/submit');
      expect(submits).toHaveLength(1);
    });
    expect(await screen.findByTestId('stub-result-page')).toBeInTheDocument();
    expect(screen.getByTestId('stub-result-score')).toHaveTextContent('80');
  });

  it('倒计时 ≤5 分钟高亮（data-urgent）', async () => {
    stubPost(() =>
      makeSession({
        deadline_at: '2026-06-01T09:04:00Z',
        server_time: '2026-06-01T09:00:00Z',
      }),
    );

    renderExam();

    const countdown = await screen.findByTestId('exam-countdown');
    expect(countdown).toHaveAttribute('data-urgent', 'true');
    expect(countdown).toHaveTextContent(/^00:0[0-4]:\d{2}$/);
  });

  it('断网：保存进入队列，online 恢复后立即补发', async () => {
    onlineValue = false;
    const putSpy = stubPut((_path, body) => {
      const payload = body as AnswerSaveRequest;
      return makeSaveResponse(payload.client_seq);
    });
    stubPost(() => makeSession({ last_client_seq: 4 }));

    const { user } = renderExam();
    await screen.findByTestId('answer-1-B');

    await user.click(screen.getByTestId('answer-1-B'));
    await user.click(screen.getByTestId('exam-next'));

    expect(putSpy).not.toHaveBeenCalled();
    expect(screen.getByTestId('exam-save-status')).toHaveTextContent('网络已断开');

    onlineValue = true;
    fireEvent(window, new Event('online'));

    await waitFor(() => expect(putSpy).toHaveBeenCalledTimes(1));
    expect(putSpy.mock.calls[0]?.[1]).toEqual({
      client_seq: 5,
      answers: [{ question_id: 1, selected: ['B'] }],
    });
    await waitFor(() =>
      expect(screen.getByTestId('exam-save-status')).toHaveTextContent('答案已保存'),
    );
  });

  it('applied=false：静默提示且不回滚本地答案，下一次保存抬高 seq', async () => {
    const putSpy = stubPut((_path, body) => {
      const payload = body as AnswerSaveRequest;
      if (payload.client_seq === 4) {
        return makeSaveResponse(9, { applied: false });
      }
      return makeSaveResponse(payload.client_seq);
    });
    stubPost(() => makeSession({ last_client_seq: 3 }));

    const { user } = renderExam();
    await screen.findByTestId('answer-1-B');

    await user.click(screen.getByTestId('answer-1-B'));
    await user.click(screen.getByTestId('exam-next'));

    await waitFor(() => expect(putSpy).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(screen.getByTestId('exam-save-status')).toHaveTextContent('保存序号已过期'),
    );
    expect(screen.queryByTestId('exam-submit-error')).not.toBeInTheDocument();

    // 回到第 1 题确认本地答案仍在（不因 applied=false 被回滚）。
    await user.click(screen.getByTestId('exam-prev'));
    expect(await screen.findByTestId('answer-1-B')).toBeChecked();
    await user.click(screen.getByTestId('exam-next'));

    // 本地答案保留；下一次保存基于服务端 saved_seq=9，发 10。
    await screen.findByTestId('answer-2-A');
    await user.click(screen.getByTestId('answer-2-A'));
    await user.click(screen.getByTestId('exam-next'));
    await waitFor(() => expect(putSpy).toHaveBeenCalledTimes(2));
    expect((putSpy.mock.calls[1]?.[1] as AnswerSaveRequest).client_seq).toBe(10);
  });
});

describe('exam page submit', () => {
  it('交卷前强制保存；重复点击只提交一次并跳转', async () => {
    let resolveSubmit: ((value: SubmitResult) => void) | null = null;
    const pendingSubmit = new Promise<SubmitResult>((resolve) => {
      resolveSubmit = resolve;
    });
    const postSpy = stubPost((path) => {
      if (path === '/exams/1/sessions') return makeSession();
      if (path === '/sessions/101/submit') return pendingSubmit;
      throw new Error(`unexpected POST ${path}`);
    });
    const putSpy = stubPut((_path, body) =>
      makeSaveResponse((body as AnswerSaveRequest).client_seq),
    );

    const { user } = renderExam();
    await screen.findByTestId('answer-1-B');

    await user.click(screen.getByTestId('answer-1-B'));
    await user.click(screen.getByTestId('exam-submit'));
    expect(await screen.findByTestId('exam-confirm-submit')).toBeInTheDocument();
    expect(postSpy.mock.calls.some(([path]) => path === '/sessions/101/submit')).toBe(false);

    await user.click(screen.getByTestId('exam-confirm-submit'));

    await waitFor(() => expect(putSpy).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(
        postSpy.mock.calls.filter(([path]) => path === '/sessions/101/submit'),
      ).toHaveLength(1),
    );

    // 第二次点击被 loading/disabled 拦截，不产生第二次提交。
    fireEvent.click(screen.getByTestId('exam-confirm-submit'));
    expect(
      postSpy.mock.calls.filter(([path]) => path === '/sessions/101/submit'),
    ).toHaveLength(1);

    // TS 无法感知 Promise 构造函数里的赋值，这里显式断言回可空函数类型
    (resolveSubmit as ((value: SubmitResult) => void) | null)?.(makeSubmitResult());
    expect(await screen.findByTestId('stub-result-page')).toBeInTheDocument();
    expect(screen.getByTestId('stub-result-score')).toHaveTextContent('80');
  });

  it('replayed=true：提示「已交卷，显示原成绩」', async () => {
    stubPost((path) => {
      if (path === '/exams/1/sessions') return makeSession();
      if (path === '/sessions/101/submit') return makeSubmitResult({ replayed: true });
      throw new Error(`unexpected POST ${path}`);
    });
    stubPut((_path, body) => makeSaveResponse((body as AnswerSaveRequest).client_seq));

    const { user } = renderExam();
    await screen.findByTestId('answer-1-B');
    await user.click(screen.getByTestId('exam-submit'));
    await user.click(await screen.findByTestId('exam-confirm-submit'));

    expect((await screen.findAllByText('已交卷，显示原成绩')).length).toBeGreaterThan(0);
    expect(await screen.findByTestId('stub-result-page')).toBeInTheDocument();
    expect(screen.getByTestId('stub-result-replayed')).toHaveTextContent('true');
  });

  it('提交 409 EXAM_EXPIRED：刷新会话状态并跳成绩页', async () => {
    const getSpy = stubGet((path) => {
      if (path === '/sessions/101') {
        return makeSession({
          status: 'expired',
          score: 60,
          submitted_at: '2026-06-01T09:50:00Z',
        });
      }
      throw new Error(`unexpected GET ${path}`);
    });
    stubPost((path) => {
      if (path === '/exams/1/sessions') return makeSession();
      if (path === '/sessions/101/submit') {
        throw new ApiError({ status: 409, code: 'EXAM_EXPIRED', message: 'exam expired' });
      }
      throw new Error(`unexpected POST ${path}`);
    });
    stubPut((_path, body) => makeSaveResponse((body as AnswerSaveRequest).client_seq));

    const { user } = renderExam();
    await screen.findByTestId('answer-1-B');
    await user.click(screen.getByTestId('exam-submit'));
    await user.click(await screen.findByTestId('exam-confirm-submit'));

    expect(await screen.findByTestId('stub-result-page')).toBeInTheDocument();
    expect(getSpy).toHaveBeenCalledWith('/sessions/101');
    expect(screen.getByTestId('stub-result-session')).toHaveTextContent('101');
    expect(screen.getByTestId('stub-result-notice')).toHaveTextContent('自动交卷');
    expect((await screen.findAllByText(/考试已到期/)).length).toBeGreaterThan(0);
  });
});

describe('my exams page', () => {
  const studentUser: User = {
    id: 1,
    username: 'stu001',
    display_name: '学生甲',
    role: 'student',
    is_active: true,
    created_at: '2026-01-01T00:00:00Z',
  };

  function makeMyExamPage(): MyExamPage {
    return {
      items: [
        {
          exam_id: 1,
          exam_title: '期中考试',
          start_at: '2026-06-01T08:00:00Z',
          end_at: '2026-06-01T10:00:00Z',
          duration_min: 60,
          session_status: 'not_started',
          session_id: null,
          score: null,
          total_points: 30,
          submitted_at: null,
        },
        {
          exam_id: 2,
          exam_title: '期末考试',
          start_at: '2026-06-01T12:00:00Z',
          end_at: '2026-06-01T13:00:00Z',
          duration_min: 60,
          session_status: 'not_started',
          session_id: null,
          score: null,
          total_points: 30,
          submitted_at: null,
        },
        {
          exam_id: 3,
          exam_title: '月考',
          start_at: '2026-05-01T09:00:00Z',
          end_at: '2026-05-01T10:00:00Z',
          duration_min: 60,
          session_status: 'submitted',
          session_id: 303,
          score: 88,
          total_points: 100,
          submitted_at: '2026-05-01T09:40:00Z',
        },
      ],
      total: 3,
      page: 1,
      page_size: 20,
      server_time: '2026-06-01T09:00:00Z',
    };
  }

  function renderMyExams() {
    const user = userEvent.setup({ pointerEventsCheck: 0 });
    const view = render(
      <QueryClientProvider client={newQueryClient()}>
        <MemoryRouter initialEntries={['/me/exams']}>
          <Routes>
            <Route path="/me/exams" element={<MyExamsPage />} />
            <Route path="/my-exams" element={<MyExamsPage />} />
            <Route path="/exams/:examId" element={<div data-testid="stub-exam-page" />} />
            <Route path="/results/:sessionId" element={<div data-testid="stub-result-page" />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    return { ...view, user };
  }

  it('列表：server_time 校时、状态标签、成绩与进入按钮', async () => {
    stubGet((path) => {
      if (path === '/me/exams') return makeMyExamPage();
      throw new Error(`unexpected GET ${path}`);
    });

    const { user } = renderMyExams();

    expect(await screen.findByTestId('my-exams-page')).toBeInTheDocument();
    // 数据是异步拉取的：先等依赖 server_time 的元素出现，再断言其余字段。
    expect(await screen.findByTestId('my-exams-server-time')).toHaveTextContent('服务器时间');
    expect(screen.getByTestId('my-exams-status-1')).toHaveTextContent('未开始');
    expect(screen.getByTestId('my-exams-status-3')).toHaveTextContent('已交卷');
    expect(screen.getByTestId('my-exams-score-3')).toHaveTextContent('88');
    expect(screen.queryByTestId('my-exams-score-1')).not.toBeInTheDocument();

    // 未开考（服务器时间 09:00 < 12:00）入口禁用
    expect(within(screen.getByTestId('exam-start-2')).getByRole('button')).toBeDisabled();
    // 可进入的考试：进入 /exams/1
    await user.click(within(screen.getByTestId('exam-start-1')).getByRole('button'));
    expect(await screen.findByTestId('stub-exam-page')).toBeInTheDocument();
  });

  it('已交卷条目跳转逐题成绩页', async () => {
    stubGet(() => makeMyExamPage());

    const { user } = renderMyExams();
    await screen.findByTestId('my-exams-row-3');
    await user.click(screen.getByTestId('my-exams-result-3'));
    expect(await screen.findByTestId('stub-result-page')).toBeInTheDocument();
  });

  it('学生菜单显示「我的考试」', async () => {
    setSession(studentUser, 'csrf-student');
    render(
      <QueryClientProvider client={newQueryClient()}>
        <MemoryRouter initialEntries={['/me/exams']}>
          <Routes>
            <Route element={<AppLayout />}>
              <Route path="/me/exams" element={<div data-testid="stub-my-exams" />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(screen.getByText('我的考试')).toBeInTheDocument();
  });
});
