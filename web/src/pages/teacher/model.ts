/**
 * 教师考试汇总模块的契约镜像与展示辅助。
 *
 * 契约依据：contracts/openapi.yaml v1.0.2
 * - `ExamResultSummary`：GET /exams/{exam_id}/results（仅考试创建者或 admin）
 *   `total_sessions` 含进行中；`submitted` = submitted + expired；
 *   `average_score` 仅对已判分会话求均值，无已判分会话时为 **0**（CHG-031）。
 * - `ExamPage` / `Exam`：GET /exams（教师自己的考试，用于选择要查看汇总的考试）。
 */
import { ApiError } from '../../api/client';
import type { Paginated } from '../../api/types';

/** 会话状态（与 SessionState.status 同一枚举；expired 计为已交卷）。 */
export type ExamResultStatus = 'in_progress' | 'submitted' | 'expired';

export const EXAM_RESULT_STATUS_LABELS: Record<ExamResultStatus, string> = {
  in_progress: '进行中',
  submitted: '已交卷',
  expired: '已到期',
};

export const EXAM_RESULT_STATUS_COLORS: Record<ExamResultStatus, string> = {
  in_progress: 'processing',
  submitted: 'success',
  expired: 'warning',
};

export type ExamStatus = 'draft' | 'published' | 'closed';

export const EXAM_STATUS_LABELS: Record<ExamStatus, string> = {
  draft: '草稿',
  published: '已发布',
  closed: '已结束',
};

export const EXAM_STATUS_COLORS: Record<ExamStatus, string> = {
  draft: 'blue',
  published: 'green',
  closed: 'default',
};

/** 镜像 ExamResultSummary.items[]（学生明细）。 */
export interface ExamResultStudent {
  student_id: number;
  display_name: string;
  status: ExamResultStatus;
  score?: number;
  submitted_at?: string;
}

/** 镜像 components.schemas.ExamResultSummary。 */
export interface ExamResultSummary {
  exam_id: number;
  total_sessions: number;
  submitted: number;
  average_score: number;
  items: ExamResultStudent[];
}

/** 镜像 components.schemas.Exam（本页只展示选择考试所需的字段）。 */
export interface ExamListItem {
  id: number;
  paper_id: number;
  title: string;
  start_at: string;
  end_at: string;
  duration_min: number;
  status: ExamStatus;
  created_by: number;
  created_at: string;
}

/** 镜像 components.schemas.ExamPage。 */
export type ExamPage = Paginated<ExamListItem>;

/** GET /exams 查询参数。 */
export interface ExamListParams {
  page: number;
  page_size: number;
}

/** 分数显示：整数不带小数，非整数保留两位。 */
export function formatScore(value: number): string {
  const rounded = Math.round(value * 100) / 100;
  return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(2);
}

/** ISO 8601 → 本地「YYYY-MM-DD HH:mm」；非法值原样返回。 */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const pad = (input: number): string => String(input).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(
    date.getHours(),
  )}:${pad(date.getMinutes())}`;
}

/**
 * 考试汇总请求错误 → 中文提示（只按 status / code 分支）。
 * - 403 → 非考试创建者或角色不符（C-17 / CHG-030 口径：教师只看自己考试的汇总）
 * - 404 → 考试不存在或对当前主体不可见
 */
export function describeExamResultsApiError(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (error.status === 403) return '无权查看该考试的成绩汇总';
    if (error.status === 404) return '考试不存在或不可见';
    return error.message || fallback;
  }
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

/** 考试列表错误 → 中文提示。 */
export function describeExamListApiError(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (error.status === 403) return '仅教师或管理员可查看考试汇总';
    return error.message || fallback;
  }
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}
