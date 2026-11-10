/**
 * 题库模块的契约镜像与表单模型。
 *
 * 依据 `contracts/openapi.yaml` v1.0.2：
 * - Question / QuestionCreate / Option / QuestionPage / ImportResult / ImportFailure
 * - `Option.key` 只允许 A–F（任务卡要求“选项最多 F”），答案为选项 key 数组
 *
 * 说明：`src/api/types.ts` 属于只读脚手架镜像（本任务写作用域不含 api/**），
 * 因此题库专用 schema 在本目录内维护，字段与契约逐项对齐，后续可无痛切换
 * 到 `pnpm gen:types` 生成的 `components['schemas'][...]`。
 */
import { ApiError } from '../../api/client';
import type { Paginated } from '../../api/types';

export const QUESTION_TYPES = ['single', 'multiple', 'judge'] as const;
export type QuestionType = (typeof QUESTION_TYPES)[number];

export const QUESTION_TYPE_LABELS: Record<QuestionType, string> = {
  single: '单选题',
  multiple: '多选题',
  judge: '判断题',
};

export const QUESTION_TYPE_COLORS: Record<QuestionType, string> = {
  single: 'blue',
  multiple: 'purple',
  judge: 'green',
};

/** 契约 Option.key pattern `^[A-Z]$`；UI 约束为 A–F 连续编号。 */
export const OPTION_KEYS = ['A', 'B', 'C', 'D', 'E', 'F'] as const;
export const MIN_OPTION_COUNT = 2;
export const MAX_OPTION_COUNT = OPTION_KEYS.length;
export const MIN_DIFFICULTY = 1;
export const MAX_DIFFICULTY = 5;
export const MAX_TAG_LENGTH = 32;
export const MIN_MULTIPLE_ANSWER_COUNT = 2;

/** 契约错误码（types.ts 的 ERROR_CODES 为只读脚手架常量，这里补题库专属码）。 */
export const QUESTION_IN_USE_CODE = 'QUESTION_IN_USE';

/** 判断题固定 A=正确 / B=错误，且不可增删。 */
export const JUDGE_OPTION_TEXTS = ['正确', '错误'] as const;

export interface QuestionOption {
  key: string;
  text: string;
}

/** 镜像 components.schemas.Question */
export interface Question {
  id: number;
  owner_id: number;
  type: QuestionType;
  stem: string;
  options: QuestionOption[];
  answer: string[];
  explanation: string;
  difficulty: number;
  tags: string[];
  created_at: string;
  updated_at?: string;
}

/** 镜像 components.schemas.QuestionCreate（PUT 复用同一 schema）。 */
export interface QuestionCreate {
  type: QuestionType;
  stem: string;
  options: QuestionOption[];
  answer: string[];
  explanation: string;
  difficulty: number;
  tags: string[];
}

/** 镜像 components.schemas.QuestionPage（items/total/page/page_size）。 */
export type QuestionPage = Paginated<Question>;

/** GET /questions 查询参数（keyword ≤100、tag ≤32、page_size ≤100）。 */
export interface QuestionListParams {
  keyword?: string;
  type?: QuestionType;
  tag?: string;
  page: number;
  page_size: number;
}

/** 镜像 components.schemas.ImportFailure（row 从 1 开始）。 */
export interface ImportFailure {
  row: number;
  reason: string;
}

/** 镜像 components.schemas.ImportResult（HTTP 恒 200，可能部分成功）。 */
export interface ImportResult {
  imported: number;
  skipped_duplicates: number;
  failed: ImportFailure[];
  total: number;
}

/** 表单内部模型：答案用选项下标（0-based）保存，提交时再映射为 A–F。 */
export interface OptionFormValue {
  text: string;
}

export interface QuestionFormValues {
  type: QuestionType;
  stem: string;
  options: OptionFormValue[];
  answer: number[];
  explanation: string;
  difficulty: number;
  /** 逗号分隔的原始输入，提交时解析为 string[]。 */
  tags: string;
}

export function optionKeyAt(index: number): string {
  return OPTION_KEYS[index] ?? String.fromCharCode(65 + index);
}

/** 逗号（中/英文）、顿号、分号均可分隔；去空、去重、保持顺序。 */
export function parseTags(raw: string | null | undefined): string[] {
  if (!raw) return [];
  const seen = new Set<string>();
  const tags: string[] = [];
  for (const piece of raw.split(/[,，、;；]/)) {
    const tag = piece.trim();
    if (!tag || seen.has(tag)) continue;
    seen.add(tag);
    tags.push(tag);
  }
  return tags;
}

export function judgeFormOptions(): OptionFormValue[] {
  return JUDGE_OPTION_TEXTS.map((text) => ({ text }));
}

/** 新建题目时的初始值（4 个空选项，满足契约 minItems=2）。 */
export function emptyFormValues(): QuestionFormValues {
  return {
    type: 'single',
    stem: '',
    options: [{ text: '' }, { text: '' }, { text: '' }, { text: '' }],
    answer: [],
    explanation: '',
    difficulty: 3,
    tags: '',
  };
}

/** 编辑：契约 Question → 表单模型（答案 key → 选项下标）。 */
export function toFormValues(question: Question): QuestionFormValues {
  const isJudge = question.type === 'judge';
  const options = isJudge
    ? judgeFormOptions()
    : question.options.map((option) => ({ text: option.text }));
  while (options.length < MIN_OPTION_COUNT) options.push({ text: '' });

  const indexByKey = new Map<string, number>();
  if (isJudge) {
    indexByKey.set('A', 0);
    indexByKey.set('B', 1);
  }
  question.options.forEach((option, index) => {
    if (!indexByKey.has(option.key)) indexByKey.set(option.key, index);
  });

  const answer = Array.from(
    new Set(
      question.answer
        .map((key) => indexByKey.get(key))
        .filter((index): index is number => index !== undefined && index < options.length),
    ),
  ).sort((a, b) => a - b);

  return {
    type: question.type,
    stem: question.stem,
    options,
    answer,
    explanation: question.explanation,
    difficulty: question.difficulty,
    tags: question.tags.join(', '),
  };
}

/** 表单模型 → 契约 QuestionCreate（trim 题干/选项/解析，答案按下标排序映射 key）。 */
export function buildQuestionPayload(values: QuestionFormValues): QuestionCreate {
  const options = values.options.slice(0, MAX_OPTION_COUNT).map((option, index) => ({
    key: optionKeyAt(index),
    text: option.text.trim(),
  }));
  const answer = Array.from(new Set(values.answer))
    .filter((index) => index >= 0 && index < options.length)
    .sort((a, b) => a - b)
    .map((index) => optionKeyAt(index));

  return {
    type: values.type,
    stem: values.stem.trim(),
    options,
    answer,
    explanation: values.explanation.trim(),
    difficulty: values.difficulty,
    tags: parseTags(values.tags),
  };
}

/**
 * 题库请求错误 → 中文提示。
 *
 * 契约把错误收敛为 `{ code, message, request_id }`（禁止解析 message 分支），
 * 因此这里只按 `status` / `code` 分支：
 * - 403 → 无权限（docs/12-permission-matrix.md §2 第 3/4 步）
 * - 409 `QUESTION_IN_USE` → 题目已被试卷引用（AC-07）
 * - 422 → 参数校验失败（表单/导入的错误回显）
 */
export function describeQuestionApiError(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (error.status === 403) return '没有权限执行该操作';
    if (error.status === 404) return '题目不存在或已被删除';
    if (error.code === QUESTION_IN_USE_CODE) return '题目已被试卷引用';
    if (error.status === 409) return error.message || '当前状态不允许该操作';
    if (error.status === 422) {
      return error.message ? `参数校验失败：${error.message}` : '参数校验失败，请检查填写内容';
    }
    return error.message || fallback;
  }
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}
