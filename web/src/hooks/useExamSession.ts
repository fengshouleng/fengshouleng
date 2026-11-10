/**
 * 考试会话状态机（TASK-FE-004 关键路径的核心）。
 *
 * 责任：
 * - 进入 `/exams/:examId` 时 POST 创建/恢复会话，并用 `SessionState.answers` 回填（刷新恢复）。
 * - 以 `server_time` 校时，基于 `deadline_at` 倒计时；≤5 分钟高亮；归零自动交卷。
 * - 自动保存：变更后 debounce 30s；切题/交卷前强制保存；`client_seq` 从
 *   `last_client_seq` 起严格 +1；失败进入重试队列（online 恢复立即补发）；
 *   `applied=false` / `saved_seq` 回退时**不回滚本地答案**，仅提示并抬高 seq。
 * - 交卷：提交前强制保存；重复点击由锁与按钮 loading 双重保证；409 EXAM_EXPIRED
 *   视为服务端已自动交卷，刷新会话状态（不解析错误体字段）。
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import { ApiError } from '../api/client';
import {
  AUTO_SAVE_DEBOUNCE_MS,
  COUNTDOWN_URGENT_MS,
  EXAM_EXPIRED_CODE,
  SAVE_RETRY_INTERVAL_MS,
  backfillAnswerMap,
  computeClockOffset,
  describeExamApiError,
  formatClockTime,
  formatCountdown,
  sameSelection,
} from '../pages/exam/model';
import type {
  AnswerSaveResponse,
  SessionQuestion,
  SessionState,
  SubmitResult,
} from '../pages/exam/model';
import {
  getSession,
  saveAnswers,
  startOrResumeSession,
  submitSession,
} from '../pages/exam/api';

export type ExamSaveState = 'idle' | 'saving' | 'saved' | 'error' | 'offline' | 'stale';

export interface SubmitOptions {
  /** 倒计时归零触发的自动交卷：跳过“保存失败即中止”的前置校验。 */
  auto?: boolean;
}

export interface UseExamSessionResult {
  session: SessionState | null;
  loading: boolean;
  loadError: string | null;
  retryLoad: () => void;

  answers: Record<number, string[]>;
  answeredCount: number;
  currentIndex: number;
  currentQuestion: SessionQuestion | null;
  goToQuestion: (index: number) => void;
  toggleOption: (question: SessionQuestion, key: string) => void;

  saveState: ExamSaveState;
  lastSavedAt: string | null;
  saveStatusText: string;
  online: boolean;

  clockOffsetMs: number;
  remainingMs: number;
  remainingText: string;
  urgent: boolean;

  submitting: boolean;
  submitError: string | null;
  expireNotice: string | null;
  /** 是否由倒计时归零触发（用于结果页提示“已自动交卷”）。 */
  autoSubmitted: boolean;
  result: SubmitResult | null;
  submit: (options?: SubmitOptions) => Promise<void>;
  forceSave: () => Promise<boolean>;
}

function isOnline(): boolean {
  return typeof navigator === 'undefined' ? true : navigator.onLine;
}

function describeLoadError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === 'EXAM_NOT_STARTED') return '考试尚未开始，请等待开考时间后再进入';
    if (error.code === 'EXAM_EXPIRED') return '考试已结束，无法进入';
    if (error.code === 'NOT_FOUND') return '考试不存在、未发布或对你不可见';
    if (error.code === 'FORBIDDEN') return '你没有参加该考试的权限';
    if (error.status === 0) return '网络连接失败，请检查网络后重试';
  }
  return describeExamApiError(error, '进入考试失败，请稍后重试');
}

function saveStatusTextOf(
  state: ExamSaveState,
  lastSavedAt: string | null,
  online: boolean,
): string {
  switch (state) {
    case 'saving':
      return '保存中…';
    case 'saved':
      return lastSavedAt ? `答案已保存（${formatClockTime(lastSavedAt)}）` : '答案已保存';
    case 'error':
      return online ? '保存失败，正在自动重试…' : '网络已断开，答案将在恢复后保存';
    case 'offline':
      return '网络已断开，答案将在恢复后自动保存';
    case 'stale':
      return '保存序号已过期，本地答案已保留';
    default:
      return online ? '答案已同步' : '网络已断开，答案将在恢复后自动保存';
  }
}

/**
 * 考试会话 hook。`examId` 为 null 时不发请求（非法路由由页面兜底）。
 */
export function useExamSession(examId: number | null): UseExamSessionResult {
  const [session, setSession] = useState<SessionState | null>(null);
  const [loading, setLoading] = useState(examId !== null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [answers, setAnswers] = useState<Record<number, string[]>>({});
  const [currentIndex, setCurrentIndex] = useState(0);
  const [saveState, setSaveState] = useState<ExamSaveState>('idle');
  const [lastSavedAt, setLastSavedAt] = useState<string | null>(null);
  const [online, setOnline] = useState<boolean>(() => isOnline());
  const [clockOffsetMs, setClockOffsetMs] = useState(0);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [expireNotice, setExpireNotice] = useState<string | null>(null);
  const [autoSubmitted, setAutoSubmitted] = useState(false);
  const [result, setResult] = useState<SubmitResult | null>(null);
  const [reloadToken, setReloadToken] = useState(0);

  const mountedRef = useRef(true);
  const sessionRef = useRef<SessionState | null>(null);
  const answersRef = useRef<Record<number, string[]>>({});
  const seqRef = useRef(0);
  const clockOffsetRef = useRef(0);
  const dirtyRef = useRef<Map<number, string[]>>(new Map());
  const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inFlightRef = useRef<Promise<boolean> | null>(null);
  const submitLockRef = useRef(false);
  const autoSubmitRef = useRef(false);
  /** 供 online/offline 事件回调使用的最新动作（避免闭包过期）。 */
  const actionsRef = useRef<{
    forceSave: () => Promise<boolean>;
    autoSubmit: () => void;
  } | null>(null);

  const adoptSession = useCallback((state: SessionState): void => {
    sessionRef.current = state;
    setSession(state);
    seqRef.current = Math.max(seqRef.current, state.last_client_seq);
    const offset = computeClockOffset(state.server_time, Date.now());
    clockOffsetRef.current = offset;
    setClockOffsetMs(offset);
    setNowMs(Date.now());

    // 刷新恢复：服务端 answers 回填。若本地已有未落库的较新答案，保留本地值。
    const backfilled = backfillAnswerMap(state.answers);
    const merged =
      dirtyRef.current.size > 0 ? { ...backfilled, ...answersRef.current } : backfilled;
    answersRef.current = merged;
    setAnswers(merged);
    setCurrentIndex((index) =>
      state.questions.length === 0 ? 0 : Math.min(index, state.questions.length - 1),
    );
    setSaveState('idle');
  }, []);

  function clearDebounce(): void {
    if (debounceTimerRef.current !== null) {
      clearTimeout(debounceTimerRef.current);
      debounceTimerRef.current = null;
    }
  }

  function clearRetry(): void {
    if (retryTimerRef.current !== null) {
      clearTimeout(retryTimerRef.current);
      retryTimerRef.current = null;
    }
  }

  function requeue(entries: Array<[number, string[]]>): void {
    for (const [questionId, selected] of entries) {
      if (dirtyRef.current.has(questionId)) continue;
      if (sameSelection(answersRef.current[questionId] ?? [], selected)) {
        dirtyRef.current.set(questionId, selected);
      }
    }
  }

  function scheduleRetry(): void {
    if (retryTimerRef.current !== null) return;
    retryTimerRef.current = setTimeout(() => {
      retryTimerRef.current = null;
      if (isOnline()) void performSave();
    }, SAVE_RETRY_INTERVAL_MS);
  }

  async function refreshAfterExpired(): Promise<void> {
    const current = sessionRef.current;
    if (!current) return;
    try {
      const state = await getSession(current.id);
      if (!mountedRef.current) return;
      adoptSession(state);
    } catch {
      // 刷新失败时保留本地提示，让用户可手动重试/刷新页面。
    }
  }

  async function executeSave(): Promise<boolean> {
    const current = sessionRef.current;
    if (!current || current.status !== 'in_progress') return true;
    if (dirtyRef.current.size === 0) return true;
    if (!isOnline()) {
      setSaveState('offline');
      return false;
    }

    const entries = Array.from(dirtyRef.current.entries());
    dirtyRef.current.clear();
    clearRetry();

    const clientSeq = Math.max(seqRef.current + 1, 1);
    seqRef.current = clientSeq;
    setSaveState('saving');

    try {
      const response: AnswerSaveResponse = await saveAnswers(current.id, {
        client_seq: clientSeq,
        answers: entries.map(([questionId, selected]) => ({
          question_id: questionId,
          selected,
        })),
      });
      if (!mountedRef.current) return true;

      seqRef.current = Math.max(seqRef.current, response.saved_seq);
      if (response.applied && response.saved_seq >= clientSeq) {
        setLastSavedAt(response.saved_at);
        setSaveState('saved');
        return true;
      }

      // applied=false / saved_seq 回退：服务端已有更高 seq 的答案。
      // 绝不把旧内容写回本地（不覆盖较新的本地答案），仅记录并抬高 seq。
      console.warn('[exam] 自动保存被服务端忽略，本地答案保持较新版本', {
        clientSeq,
        savedSeq: response.saved_seq,
        applied: response.applied,
      });
      setSaveState('stale');
      return true;
    } catch (error) {
      if (error instanceof ApiError && error.code === EXAM_EXPIRED_CODE) {
        setSaveState('error');
        setExpireNotice('考试已到期，系统已自动交卷，成绩已生成');
        await refreshAfterExpired();
        return false;
      }
      requeue(entries);
      setSaveState('error');
      scheduleRetry();
      return false;
    }
  }

  function performSave(): Promise<boolean> {
    if (inFlightRef.current) return inFlightRef.current;
    // finally 回调在微任务里执行（此时 promise 已赋值），故可安全自引用。
    const promise: Promise<boolean> = executeSave().finally(() => {
      if (inFlightRef.current === promise) inFlightRef.current = null;
    });
    inFlightRef.current = promise;
    return promise;
  }

  function scheduleAutoSave(): void {
    clearDebounce();
    debounceTimerRef.current = setTimeout(() => {
      debounceTimerRef.current = null;
      void performSave();
    }, AUTO_SAVE_DEBOUNCE_MS);
  }

  async function forceSave(): Promise<boolean> {
    clearDebounce();
    if (inFlightRef.current) await inFlightRef.current.catch(() => false);
    for (let attempt = 0; attempt < 3; attempt += 1) {
      if (dirtyRef.current.size === 0) return true;
      const saved = await performSave();
      if (!saved) return false;
    }
    return dirtyRef.current.size === 0;
  }

  function applyAnswer(questionId: number, selected: string[]): void {
    const current = sessionRef.current;
    if (!current || current.status !== 'in_progress') return;

    const normalized = [...selected].sort();
    const next = { ...answersRef.current };
    if (normalized.length === 0) {
      delete next[questionId];
    } else {
      next[questionId] = normalized;
    }
    answersRef.current = next;
    setAnswers(next);
    dirtyRef.current.set(questionId, normalized);
    setSaveState('idle');
    scheduleAutoSave();
  }

  function toggleOption(question: SessionQuestion, key: string): void {
    const current = answersRef.current[question.question_id] ?? [];
    if (question.type === 'multiple') {
      applyAnswer(
        question.question_id,
        current.includes(key)
          ? current.filter((item) => item !== key)
          : [...current, key],
      );
      return;
    }
    // 单选/判断：点选即替换，不再重复点击取消（与 radio 语义一致）。
    applyAnswer(question.question_id, [key]);
  }

  function goToQuestion(index: number): void {
    const current = sessionRef.current;
    if (!current) return;
    const maxIndex = Math.max(current.questions.length - 1, 0);
    const bounded = Math.min(Math.max(index, 0), maxIndex);
    // 切题前强制保存（失败进入重试队列，不阻断切题）。
    void forceSave();
    setCurrentIndex(bounded);
    setExpireNotice(null);
  }

  async function submit(options: SubmitOptions = {}): Promise<void> {
    if (submitLockRef.current) return;
    const current = sessionRef.current;
    if (!current || current.status !== 'in_progress') return;

    submitLockRef.current = true;
    setSubmitting(true);
    setSubmitError(null);

    try {
      if (!options.auto) {
        // 交卷前强制保存；未保存成功则中止，避免丢答案。
        const saved = await forceSave();
        if (!saved) {
          setSubmitError('答案尚未保存成功，请检查网络后重试交卷');
          return;
        }
      }

      const response = await submitSession(current.id);
      if (!mountedRef.current) return;
      setResult(response);
      const finished: SessionState = {
        ...current,
        status: response.status,
        score: response.score,
        submitted_at: response.submitted_at,
      };
      sessionRef.current = finished;
      setSession(finished);
      dirtyRef.current.clear();
      clearDebounce();
      clearRetry();
    } catch (error) {
      if (!mountedRef.current) return;
      if (error instanceof ApiError && error.code === EXAM_EXPIRED_CODE) {
        setExpireNotice('考试已到期，系统已自动交卷，成绩已生成');
        setSubmitError(null);
        await refreshAfterExpired();
        return;
      }
      if (error instanceof ApiError && error.status === 409) {
        setSubmitError('当前会话状态不允许交卷，请刷新页面后重试');
        return;
      }
      setSubmitError(describeExamApiError(error, '交卷失败，请稍后重试'));
    } finally {
      submitLockRef.current = false;
      if (mountedRef.current) setSubmitting(false);
    }
  }

  actionsRef.current = {
    forceSave,
    autoSubmit: () => {
      void submit({ auto: true });
    },
  };

  /* ------------------------------- 生命周期 ------------------------------- */

  useEffect(() => {
    if (examId === null) {
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    let cancelled = false;

    autoSubmitRef.current = false;
    submitLockRef.current = false;
    dirtyRef.current.clear();
    seqRef.current = 0;
    setLoading(true);
    setLoadError(null);
    setResult(null);
    setSubmitError(null);
    setExpireNotice(null);
    setAutoSubmitted(false);

    startOrResumeSession(examId, controller.signal)
      .then((state) => {
        if (cancelled) return;
        adoptSession(state);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setLoadError(describeLoadError(error));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [examId, reloadToken, adoptSession]);

  useEffect(() => {
    const timer = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    const handleOnline = (): void => {
      setOnline(true);
      const actions = actionsRef.current;
      if (!actions) return;
      // 恢复后立即补发失败队列；若已过 deadline 则直接触发自动交卷。
      void actions.forceSave().then(() => {
        const current = sessionRef.current;
        if (!current || current.status !== 'in_progress') return;
        const deadlineMs = new Date(current.deadline_at).getTime();
        if (!Number.isNaN(deadlineMs) && Date.now() + clockOffsetRef.current >= deadlineMs) {
          actions.autoSubmit();
        }
      });
    };
    const handleOffline = (): void => {
      setOnline(false);
      if (dirtyRef.current.size > 0) setSaveState('offline');
    };

    window.addEventListener('online', handleOnline);
    window.addEventListener('offline', handleOffline);
    return () => {
      window.removeEventListener('online', handleOnline);
      window.removeEventListener('offline', handleOffline);
    };
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      clearDebounce();
      clearRetry();
    };
    // clear* 只读写 ref，不需要进入依赖数组。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* ------------------------------- 派生状态 ------------------------------- */

  const currentQuestion =
    session && session.questions.length > 0
      ? session.questions[Math.min(currentIndex, session.questions.length - 1)] ?? null
      : null;

  const remainingMs = (() => {
    if (!session) return 0;
    const deadlineMs = new Date(session.deadline_at).getTime();
    if (Number.isNaN(deadlineMs)) return 0;
    return Math.max(0, deadlineMs - (nowMs + clockOffsetMs));
  })();

  const applyingDeadline = Boolean(
    session &&
      session.status === 'in_progress' &&
      !Number.isNaN(new Date(session.deadline_at).getTime()),
  );

  // 倒计时归零 → 自动提交（只触发一次；服务端同样会在到期后自动判分）。
  useEffect(() => {
    if (!applyingDeadline) return;
    if (!session || session.status !== 'in_progress') return;
    if (remainingMs > 0) return;
    if (autoSubmitRef.current) return;
    autoSubmitRef.current = true;
    setAutoSubmitted(true);
    setExpireNotice('考试时间已到，正在自动交卷…');
    void submit({ auto: true });
    // submit 使用 refs 读取最新会话；依赖 remainingMs/session 即可覆盖归零瞬间。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [applyingDeadline, remainingMs, session]);

  const answeredCount = Object.values(answers).filter((selected) => selected.length > 0).length;

  return {
    session,
    loading,
    loadError,
    retryLoad: () => setReloadToken((token) => token + 1),
    answers,
    answeredCount,
    currentIndex,
    currentQuestion,
    goToQuestion,
    toggleOption,
    saveState,
    lastSavedAt,
    saveStatusText: saveStatusTextOf(saveState, lastSavedAt, online),
    online,
    clockOffsetMs,
    remainingMs,
    remainingText: formatCountdown(remainingMs),
    urgent: applyingDeadline && remainingMs <= COUNTDOWN_URGENT_MS,
    submitting,
    submitError,
    expireNotice,
    autoSubmitted,
    result,
    submit,
    forceSave,
  };
}
