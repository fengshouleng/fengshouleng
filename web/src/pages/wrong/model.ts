/**
 * 错题本模块的契约镜像与展示辅助。
 *
 * 契约依据：contracts/openapi.yaml v1.0.2
 * - `WrongQuestion` / `WrongQuestionPage`：GET /me/wrong-questions（仅 student，分页）
 * - `wrong_count` 为累计错误次数；同一题只出现一次（AC-20）
 */
import { ApiError } from '../../api/client';
import type { Paginated } from '../../api/types';
import type { QuestionType } from '../questions/model';

/** 错题本统一分页大小（契约默认 20，page_size 上限 100）。 */
export const WRONG_QUESTION_PAGE_SIZE = 20;

/** 镜像 components.schemas.WrongQuestion。 */
export interface WrongQuestion {
  question_id: number;
  type: QuestionType;
  stem: string;
  wrong_count: number;
  last_wrong_at: string;
}

/** 镜像 components.schemas.WrongQuestionPage。 */
export type WrongQuestionPage = Paginated<WrongQuestion>;

/** GET /me/wrong-questions 查询参数。 */
export interface WrongQuestionListParams {
  page: number;
  page_size: number;
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

/** 错题本请求错误 → 中文提示（403 说明非学生角色，401 说明会话失效）。 */
export function describeWrongQuestionsApiError(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (error.status === 403) return '仅学生本人可查看错题本';
    if (error.status === 401) return '登录状态已失效，请重新登录';
    return error.message || fallback;
  }
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}
