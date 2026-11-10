/**
 * 考试作答模块的契约镜像与纯函数工具。
 *
 * 契约依据：contracts/openapi.yaml v1.0.2
 * - `SessionState`：进入/恢复会话、刷新回填的唯一数据源；`answers` / `submitted_at`
 *   为恢复必读字段（C-01），`last_client_seq` 为自动保存序号基线（ADR-006）。
 * - `AnswerSaveRequest` / `AnswerSaveResponse`：`client_seq` 单调递增；
 *   `applied` 必填（false = 旧 seq 被忽略，HTTP 仍 200，C-16）。
 * - `SubmitResult`：提交/重复提交都是 200；`replayed` 必填（C-15）。
 * - `MyExamPage`：根级 `server_time` 必填，前端必须用它校时（CHG-024），
 *   不得用浏览器本地时钟判断“未开始/可进入/已截止”。
 *
 * 说明：与 `pages/questions/model.ts`、`pages/papers/model.ts` 同风格，
 * 契约字段在本目录集中维护，后续可无痛切换到 `pnpm gen:types` 生成的 schema。
 */
import { ApiError } from '../../api/client';
import type { QuestionOption, QuestionType } from '../questions/model';

/* -------------------------------------------------------------------------- */
/* 状态枚举与文案                                                              */
/* -------------------------------------------------------------------------- */

export const SESSION_STATUSES = ['in_progress', 'submitted', 'expired'] as const;
export type SessionStatus = (typeof SESSION_STATUSES)[number];

/** `/me/exams` 口径：`not_started` = 无会话（C-02）。 */
export const MY_EXAM_STATUSES = ['not_started', ...SESSION_STATUSES] as const;
export type MyExamStatus = (typeof MY_EXAM_STATUSES)[number];

export const SESSION_STATUS_LABELS: Record<MyExamStatus, string> = {
  not_started: '未开始',
  in_progress: '进行中',
  submitted: '已交卷',
  expired: '已过期',
};

export const SESSION_STATUS_COLORS: Record<MyExamStatus, string> = {
  not_started: 'default',
  in_progress: 'processing',
  submitted: 'success',
  expired: 'warning',
};

/** 契约错误码（AC-11 / AC-14）。 */
export const EXAM_NOT_STARTED_CODE = 'EXAM_NOT_STARTED';
export const EXAM_EXPIRED_CODE = 'EXAM_EXPIRED';

/** 自动保存 debounce：任务卡要求 ≥30s。 */
export const AUTO_SAVE_DEBOUNCE_MS = 30_000;
/** 保存失败后的重试间隔（在线时）。 */
export const SAVE_RETRY_INTERVAL_MS = 5_000;
/** 倒计时高亮阈值：≤5 分钟。 */
export const COUNTDOWN_URGENT_MS = 5 * 60 * 1000;

/* -------------------------------------------------------------------------- */
/* 契约类型镜像                                                                */
/* -------------------------------------------------------------------------- */

export interface SessionQuestion {
  question_id: number;
  type: QuestionType;
  stem: string;
  options: QuestionOption[];
  order_no: number;
  points: number;
}

/** 契约 AnswerItem：增量保存时只需携带本次变更的题目。 */
export interface AnswerItem {
  question_id: number;
  selected: string[];
}

export interface SessionState {
  id: number;
  exam_id: number;
  status: SessionStatus;
  started_at: string;
  deadline_at: string;
  server_time: string;
  last_client_seq: number;
  /** 已交卷/已到期时非空（契约条件不变量）。 */
  submitted_at?: string | null;
  /** `status ∈ {submitted, expired}` 时必有值。 */
  score?: number | null;
  answers: AnswerItem[];
  questions: SessionQuestion[];
}

export interface AnswerSaveRequest {
  client_seq: number;
  answers: AnswerItem[];
}

export interface AnswerSaveResponse {
  saved_seq: number;
  saved_at: string;
  /** 必填：true=已落库；false=旧 seq 被忽略（C-16）。 */
  applied: boolean;
}

export interface SubmitResult {
  session_id: number;
  status: 'submitted' | 'expired';
  score: number;
  submitted_at: string;
  /** 必填：true=重复提交/已自动交卷（C-15）。 */
  replayed: boolean;
}

export interface MyExamItem {
  exam_id: number;
  exam_title: string;
  start_at: string;
  end_at: string;
  duration_min: number;
  session_status: MyExamStatus;
  session_id?: number | null;
  score?: number | null;
  total_points?: number | null;
  submitted_at?: string | null;
}

export interface MyExamPage {
  items: MyExamItem[];
  total: number;
  page: number;
  page_size: number;
  /** 服务端当前时间（UTC），校时唯一依据（CHG-024）。 */
  server_time: string;
}

/** 我的考试列表中的派生可操作性（由 server_time 校时后判断）。 */
export const EXAM_AVAILABILITIES = ['enter', 'not_started', 'closed', 'finished'] as const;
export type ExamAvailability = (typeof EXAM_AVAILABILITIES)[number];

/* -------------------------------------------------------------------------- */
/* 纯函数                                                                      */
/* -------------------------------------------------------------------------- */

export function isFinishedStatus(status: SessionStatus): boolean {
  return status === 'submitted' || status === 'expired';
}

/** 到期/已交卷判定：状态非作答中，或已回填 submitted_at / score。 */
export function isSessionFinished(session: SessionState | null): boolean {
  if (!session) return false;
  return (
    session.status !== 'in_progress' ||
    (session.submitted_at !== null && session.submitted_at !== undefined) ||
    (session.score !== null && session.score !== undefined)
  );
}

export function parseTime(value: string | null | undefined): number {
  if (!value) return Number.NaN;
  return new Date(value).getTime();
}

/** 服务端时间 − 本地接收时间（毫秒）。后续本地时间先加该偏移再与 deadline 比较。 */
export function computeClockOffset(serverTime: string, receivedAtMs: number): number {
  const serverMs = parseTime(serverTime);
  if (Number.isNaN(serverMs)) return 0;
  return serverMs - receivedAtMs;
}

/** 剩余时间格式化 HH:MM:SS（向上取整，归零显示 00:00:00）。 */
export function formatCountdown(remainingMs: number): string {
  const totalSeconds = Math.max(0, Math.ceil(remainingMs / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return [hours, minutes, seconds].map((part) => String(part).padStart(2, '0')).join(':');
}

function pad2(value: number): string {
  return String(value).padStart(2, '0');
}

/** 本地时区展示（yyyy-MM-dd HH:mm）；非法/空值 → “—”。 */
export function formatDateTime(value: string | null | undefined): string {
  const time = parseTime(value);
  if (Number.isNaN(time)) return '—';
  const date = new Date(time);
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ${pad2(
    date.getHours(),
  )}:${pad2(date.getMinutes())}`;
}

export function formatClockTime(value: string | null | undefined): string {
  const time = parseTime(value);
  if (Number.isNaN(time)) return '—';
  const date = new Date(time);
  return `${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}`;
}

/** `server_time` 与本地时钟差的中文描述（正数 = 本地慢）。 */
export function describeClockOffset(offsetMs: number): string {
  const seconds = Math.round(offsetMs / 1000);
  if (Math.abs(seconds) < 1) return '本机时钟与服务器一致';
  return seconds > 0
    ? `本机时钟比服务器慢 ${seconds} 秒（已按服务器时间校时）`
    : `本机时钟比服务器快 ${Math.abs(seconds)} 秒（已按服务器时间校时）`;
}

/** 把 SessionState.answers 回填为 `question_id → selected[]`。 */
export function backfillAnswerMap(answers: readonly AnswerItem[]): Record<number, string[]> {
  const map: Record<number, string[]> = {};
  for (const item of answers) {
    if (item.selected.length > 0) map[item.question_id] = [...item.selected];
  }
  return map;
}

export function sameSelection(a: readonly string[], b: readonly string[]): boolean {
  if (a.length !== b.length) return false;
  const left = [...a].sort();
  const right = [...b].sort();
  return left.every((value, index) => value === right[index]);
}

/**
 * 列表页派生可操作性：
 * - 已有会话 && 已交卷/到期 → finished（看成绩）
 * - 已有会话 && 作答中 → enter（继续考试）
 * - 无会话：用 server_time 校时后比较 start_at / end_at
 */
export function resolveAvailability(
  item: MyExamItem,
  nowMsWithOffset: number,
): ExamAvailability {
  if (item.session_status === 'in_progress') return 'enter';
  if (item.session_status === 'submitted' || item.session_status === 'expired') return 'finished';

  const startMs = parseTime(item.start_at);
  const endMs = parseTime(item.end_at);
  if (!Number.isNaN(startMs) && nowMsWithOffset < startMs) return 'not_started';
  if (!Number.isNaN(endMs) && nowMsWithOffset > endMs) return 'closed';
  return 'enter';
}

/** 统一下载/进入错误文案；网络错误与业务错误分开提示。 */
export function describeExamApiError(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (error.status === 0 || error.code === 'NETWORK_ERROR') {
      return '网络连接失败，请检查网络后重试';
    }
    if (error.code === EXAM_EXPIRED_CODE) return '考试已到期，系统已自动交卷';
    if (error.code === EXAM_NOT_STARTED_CODE) return '考试尚未开始，请等待开考时间';
    if (error.code === 'NOT_FOUND') return '考试不存在或未发布';
    if (error.code === 'FORBIDDEN') return '你没有参加该考试的权限';
    return error.message || fallback;
  }
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}
