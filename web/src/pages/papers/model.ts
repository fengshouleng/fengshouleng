/**
 * 组卷模块的契约镜像、草稿模型与前端校验。
 *
 * 契约依据：contracts/openapi.yaml v1.0.2
 * - `Paper` / `PaperPage` / `PaperQuestion`
 * - `PaperCreate`：请求体**只含** `title` + `questions[]`
 *   （`question_id` / `order_no` / `points` / `partial_rule`），
 *   `total_points` 由服务端计算并随响应返回，请求里不得出现。
 * - 服务端校验（前端在提交前先做同口径拦截）：
 *   `0 < points <= 100` 且为 0.01 的整数倍；`1 <= Σpoints <= 10000`；`1 <= len(questions) <= 100`；
 *   `order_no` 恰为 1..N 连续（本页由数组顺序派生，天然满足）。
 * - 冻结（`status=frozen`）后 PUT/DELETE → 409 `PAPER_FROZEN`（AC-10）；
 *   被考试引用时 DELETE → 409。
 *
 * 说明：字段集中在本目录维护（与 `pages/questions/model.ts` 同风格），
 * 后续可无痛切换到 `pnpm gen:types` 生成的 schema 类型。
 */
import { ApiError } from '../../api/client';
import type { Paginated } from '../../api/types';
import type { Question, QuestionType } from '../questions/model';

export const PAPER_STATUSES = ['draft', 'frozen'] as const;
export type PaperStatus = (typeof PAPER_STATUSES)[number];

export const PAPER_STATUS_LABELS: Record<PaperStatus, string> = {
  draft: '草稿',
  frozen: '已冻结',
};

export const PAPER_STATUS_COLORS: Record<PaperStatus, string> = {
  draft: 'blue',
  frozen: 'red',
};

/** 多选给分规则（仅 type=multiple 生效，ADR-007）。 */
export const PARTIAL_RULES = ['all_or_nothing', 'partial'] as const;
export type PartialRule = (typeof PARTIAL_RULES)[number];

export const PARTIAL_RULE_LABELS: Record<PartialRule, string> = {
  all_or_nothing: '全对得分',
  partial: '按比例给分',
};

export const PARTIAL_RULE_HINTS: Record<PartialRule, string> = {
  all_or_nothing: '全对得满分，否则 0 分',
  partial: '无错选时按命中比例给分，有错选 0 分',
};

/** 契约上限：标题 1–128；单卷 1–100 题；单题 (0,100]；总分 1–10000。 */
export const MAX_PAPER_TITLE_LENGTH = 128;
export const MAX_PAPER_QUESTIONS = 100;
export const MAX_QUESTION_POINTS = 100;
export const MIN_QUESTION_POINTS = 0.01;
export const MAX_TOTAL_POINTS = 10000;
export const MIN_TOTAL_POINTS = 1;
export const DEFAULT_QUESTION_POINTS = 5;

/** 契约错误码（冻结后修改/删除）。 */
export const PAPER_FROZEN_CODE = 'PAPER_FROZEN';

/** 冻结说明文案（列表 / 编辑页共用；AC-10）。 */
export const PAPER_FROZEN_NOTICE = '已用于已发布考试，不可修改';
export const PAPER_FROZEN_DETAIL = '试卷已冻结（frozen），题目、分值与排序均不可调整，仅可查看。';

/** 镜像 components.schemas.PaperQuestion（`partial_rule` 服务端有默认值，可省略）。 */
export interface PaperQuestion {
  question_id: number;
  order_no: number;
  points: number;
  partial_rule?: PartialRule;
}

/** 镜像 components.schemas.Paper。 */
export interface Paper {
  id: number;
  owner_id: number;
  title: string;
  /** 只读计算值 = Σ questions[].points（服务端返回，前端仅展示）。 */
  total_points: number;
  status: PaperStatus;
  /** status=frozen 时服务端保证非空（契约条件不变量）。 */
  frozen_at?: string | null;
  questions: PaperQuestion[];
  created_at: string;
}

/** 镜像 components.schemas.PaperCreate（`total_points` 不属于请求模型）。 */
export interface PaperCreate {
  title: string;
  questions: PaperQuestion[];
}

/** 镜像 components.schemas.PaperPage。 */
export type PaperPage = Paginated<Paper>;

/** GET /papers 查询参数（page_size 上限 100）。 */
export interface PaperListParams {
  page: number;
  page_size: number;
}

/**
 * 组卷草稿中的单题：`order_no` 由数组下标派生（1..N），分值/规则可编辑。
 * 冗余保存题干与题型，避免右侧列表为了展示而重复请求题目详情。
 */
export interface SelectedPaperQuestion {
  question_id: number;
  stem: string;
  type: QuestionType;
  points: number;
  partial_rule: PartialRule;
}

/** 前端校验错误：字段 → 中文提示（422 回显复用同一结构）。 */
export interface PaperDraftErrors {
  title?: string;
  questions?: string;
  total?: string;
  /** key = question_id */
  pointsByQuestion: Record<number, string>;
}

export function emptyDraftErrors(): PaperDraftErrors {
  return { pointsByQuestion: {} };
}

export function hasDraftErrors(errors: PaperDraftErrors): boolean {
  return (
    Boolean(errors.title) ||
    Boolean(errors.questions) ||
    Boolean(errors.total) ||
    Object.keys(errors.pointsByQuestion).length > 0
  );
}

/** 清除某题的分值错误（同时清掉随分值变化的总分错误），返回新对象。 */
export function clearQuestionError(
  errors: PaperDraftErrors,
  questionId: number,
): PaperDraftErrors {
  const hasPointError = Object.prototype.hasOwnProperty.call(
    errors.pointsByQuestion,
    String(questionId),
  );
  if (!hasPointError && !errors.total) return errors;

  const pointsByQuestion: Record<number, string> = Object.fromEntries(
    Object.entries(errors.pointsByQuestion).filter(([key]) => Number(key) !== questionId),
  );
  return { ...errors, total: undefined, pointsByQuestion };
}

/** 四舍五入到 2 位小数（避免 0.1+0.2 的浮点尾巴影响 multipleOf 0.01 校验）。 */
export function roundPoints(value: number): number {
  return Math.round(value * 100) / 100;
}

/** 总分 = Σ points（两位小数，与服务端口径一致）。 */
export function sumPoints(items: readonly SelectedPaperQuestion[]): number {
  return roundPoints(items.reduce((total, item) => total + item.points, 0));
}

/** 单题分值是否合法：`0 < points <= 100` 且最多 2 位小数。 */
export function isPointsValid(value: number): boolean {
  if (!Number.isFinite(value)) return false;
  if (value <= 0 || value > MAX_QUESTION_POINTS) return false;
  return Math.abs(value * 100 - Math.round(value * 100)) < 1e-6;
}

export function isMultipleType(type: QuestionType): boolean {
  return type === 'multiple';
}

/**
 * 提交前校验（与 PaperCreate 的服务端口径一致）：
 * 标题非空且 ≤128、至少 1 题且 ≤100、单题分值合法、总分在 1–10000 之间。
 */
export function validatePaperDraft(
  title: string,
  items: readonly SelectedPaperQuestion[],
): PaperDraftErrors {
  const errors = emptyDraftErrors();
  const trimmed = title.trim();

  if (!trimmed) {
    errors.title = '请输入试卷标题';
  } else if (trimmed.length > MAX_PAPER_TITLE_LENGTH) {
    errors.title = `标题不能超过 ${MAX_PAPER_TITLE_LENGTH} 个字符`;
  }

  if (items.length === 0) {
    errors.questions = '请至少选择 1 道题目';
  } else if (items.length > MAX_PAPER_QUESTIONS) {
    errors.questions = `单张试卷最多 ${MAX_PAPER_QUESTIONS} 道题目`;
  }

  for (const item of items) {
    if (!isPointsValid(item.points)) {
      errors.pointsByQuestion[item.question_id] = `分值必须大于 0 且不超过 ${MAX_QUESTION_POINTS}，最多 2 位小数`;
    }
  }

  const total = sumPoints(items);
  if (items.length > 0 && total > MAX_TOTAL_POINTS) {
    errors.total = `试卷总分不能超过 ${MAX_TOTAL_POINTS} 分（当前 ${formatPoints(total)} 分）`;
  } else if (items.length > 0 && total < MIN_TOTAL_POINTS) {
    errors.total = '试卷总分必须大于 0 分';
  }

  return errors;
}

/**
 * 草稿 → 契约 PaperCreate。
 * - `order_no` 按当前顺序生成 1..N（与后端「必须连续」的校验天然一致）
 * - `points` 归一到两位小数
 * - `partial_rule` 只对多选题有意义；其他题型统一提交 `all_or_nothing`
 */
export function buildPaperPayload(
  title: string,
  items: readonly SelectedPaperQuestion[],
): PaperCreate {
  return {
    title: title.trim(),
    questions: items.map((item, index) => ({
      question_id: item.question_id,
      order_no: index + 1,
      points: roundPoints(item.points),
      partial_rule: isMultipleType(item.type) ? item.partial_rule : 'all_or_nothing',
    })),
  };
}

/** 题库题目 → 草稿条目（默认分值 5 分、多选默认全对得分）。 */
export function toSelectedQuestion(
  question: Question,
  points: number = DEFAULT_QUESTION_POINTS,
): SelectedPaperQuestion {
  return {
    question_id: question.id,
    stem: question.stem,
    type: question.type,
    points: roundPoints(points),
    partial_rule: 'all_or_nothing',
  };
}

/** 试卷（服务端详情）→ 草稿条目；题目详情由 `questionById` 补齐（缺失时退化为占位文案）。 */
export function fromPaperQuestions(
  paper: Paper,
  questionById: ReadonlyMap<number, Question>,
): SelectedPaperQuestion[] {
  return [...paper.questions]
    .sort((a, b) => a.order_no - b.order_no)
    .map((item) => {
      const question = questionById.get(item.question_id);
      return {
        question_id: item.question_id,
        stem: question?.stem ?? `题目 #${item.question_id}`,
        type: question?.type ?? 'single',
        points: roundPoints(item.points),
        partial_rule: item.partial_rule ?? 'all_or_nothing',
      };
    });
}

/** 总分显示：整数不带小数，非整数保留两位（100 / 12.50）。 */
export function formatPoints(value: number): string {
  const rounded = roundPoints(value);
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
 * 试卷请求错误 → 中文提示。
 *
 * 契约把错误收敛为 `{ code, message, request_id }`，因此只按 `status` / `code` 分支：
 * - 403 → 无权限；404 → 不存在
 * - 409 `PAPER_FROZEN` → 已用于已发布考试，不可修改（AC-10）
 * - 409（其他）→ 被考试引用，无法删除
 * - 422 → 参数校验失败（字段级回显见 `parseValidationError`）
 */
export function describePaperApiError(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (error.status === 403) return '没有权限执行该操作';
    if (error.status === 404) return '试卷不存在或已被删除';
    if (error.code === PAPER_FROZEN_CODE) return `${PAPER_FROZEN_NOTICE}（试卷已冻结）`;
    if (error.status === 409) return '试卷已被考试引用，无法删除';
    if (error.status === 422) {
      return error.message ? `参数校验失败：${error.message}` : '参数校验失败，请检查填写内容';
    }
    return error.message || fallback;
  }
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

/**
 * 422 错误体只有 `{ code, message }`（无结构化字段），但 FastAPI 的 message
 * 形如 `body.questions.0.points: ...`，因此按字段位置前缀把错误落到对应输入框：
 * - `body.title` / 「标题」→ 标题
 * - `body.questions.{index}.points` → 第 index 题的分值（按提交顺序对应草稿下标）
 * - `body.questions.{index}.order_no` / `partial_rule` / 「顺序」→ 题目区
 * - 「总分」/ `total_points` → 总分
 */
export function parseValidationError(
  message: string,
  items: readonly SelectedPaperQuestion[],
): PaperDraftErrors {
  const errors = emptyDraftErrors();
  const text = message ?? '';

  const pointsMatch = /questions(?:\.|\[)(\d+)\]?\.?points/.exec(text);
  if (pointsMatch) {
    const index = Number(pointsMatch[1]);
    const item = Number.isInteger(index) ? items[index] : undefined;
    if (item) {
      errors.pointsByQuestion[item.question_id] = `分值不合法：需大于 0、不超过 ${MAX_QUESTION_POINTS}，且最多 2 位小数`;
    } else {
      errors.questions = '题目分值不合法，请检查后重试';
    }
  }

  if (/title|标题/.test(text)) {
    errors.title = `标题不合法：1–${MAX_PAPER_TITLE_LENGTH} 个字符`;
  }
  if (/order_no|顺序/.test(text)) {
    errors.questions = '题目顺序不合法，请调整排序后重试';
  }
  if (/partial_rule|给分规则/.test(text)) {
    errors.questions = '多选给分规则不合法，请重新选择';
  }
  if (/总分|total_points/.test(text)) {
    errors.total = `试卷总分必须在 ${MIN_TOTAL_POINTS}–${MAX_TOTAL_POINTS} 之间`;
  }

  return errors;
}
