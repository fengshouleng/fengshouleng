/**
 * 成绩模块的契约镜像与展示辅助。
 *
 * 契约依据：contracts/openapi.yaml v1.0.2
 * - `ResultItem` / `ResultDetail`：GET /sessions/{session_id}/result
 *   （仅学生本人或 admin；未交卷 409 `NOT_SUBMITTED`，他人 403 `FORBIDDEN`）
 * - `MyResultItem` / `MyResultPage`：GET /me/results
 * - `MyExamItem` 的 `score/total_points` 与本模块展示口径一致
 *
 * 说明：字段集中在本目录维护（与 `pages/questions/model.ts`、`pages/papers/model.ts` 同风格），
 * 后续可无痛切换到 `pnpm gen:types` 生成的 schema 类型。
 */
import { ApiError } from '../../api/client';
import type { Paginated } from '../../api/types';
import type { QuestionType } from '../questions/model';

/** 契约错误码：未交卷请求逐题结果（AC-19）。 */
export const NOT_SUBMITTED_CODE = 'NOT_SUBMITTED';

/** 成绩列表 / 错题本统一分页大小（契约默认 20，page_size 上限 100）。 */
export const RESULT_PAGE_SIZE = 20;

/** 镜像 components.schemas.ResultItem（additionalProperties: false）。 */
export interface ResultItem {
  question_id: number;
  type: QuestionType;
  stem?: string;
  selected: string[];
  correct_answer: string[];
  is_correct: boolean;
  earned_points: number;
  points: number;
  explanation?: string;
}

/** 镜像 components.schemas.ResultDetail（submitted 与 expired 均可读）。 */
export interface ResultDetail {
  session_id: number;
  exam_id: number;
  score: number;
  total_points: number;
  submitted_at: string;
  items: ResultItem[];
}

/** 镜像 components.schemas.MyResultItem（带 session_id 以便跳转逐题结果）。 */
export interface MyResultItem {
  session_id: number;
  exam_id: number;
  exam_title: string;
  score: number;
  total_points: number;
  submitted_at: string;
}

/** 镜像 components.schemas.MyResultPage。 */
export type MyResultPage = Paginated<MyResultItem>;

/** GET /me/results 查询参数。 */
export interface MyResultListParams {
  page: number;
  page_size: number;
}

/** 分数显示：整数不带小数，非整数保留两位（100 / 12.50），与服务端 multipleOf 0.01 对齐。 */
export function formatScore(value: number): string {
  const rounded = Math.round(value * 100) / 100;
  return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(2);
}

/** 答案 key 数组 → 中文展示（空数组表示未作答）。 */
export function formatAnswer(keys: readonly string[]): string {
  return keys.length > 0 ? keys.join('、') : '未作答';
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
 * 成绩请求错误 → 中文提示（只按 status / code 分支，禁止解析 message）。
 *
 * - 409 `NOT_SUBMITTED` → 尚未交卷（AC-19）
 * - 403 → 学生访问他人成绩（AC-18，服务端 IDOR 返回 403）
 * - 404 → 成绩不存在或会话不可见
 */
export function describeResultApiError(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (error.code === NOT_SUBMITTED_CODE) return '尚未交卷，交卷后才能查看成绩与解析';
    if (error.status === 409) return '尚未交卷，交卷后才能查看成绩与解析';
    if (error.status === 403) return '无权查看该成绩';
    if (error.status === 404) return '成绩不存在或已被删除';
    return error.message || fallback;
  }
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

/** 我的成绩列表错误 → 中文提示（学生本人可见，403 说明角色不符）。 */
export function describeMyResultsApiError(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (error.status === 403) return '仅学生本人可查看我的成绩';
    if (error.status === 401) return '登录状态已失效，请重新登录';
    return error.message || fallback;
  }
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}
