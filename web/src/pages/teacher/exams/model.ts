/**
 * 教师「考试管理」模块的契约镜像、默认值与前端校验。
 *
 * 契约依据：contracts/openapi.yaml v1.0.2
 * - `GET /exams`：status 筛选 + page/page_size，响应 `ExamPage`；
 * - `POST /exams`：`ExamCreate`，`paper_id` 必须是自己的 **draft** 试卷；
 *   `publish=true` 时同事务冻结试卷并生成快照（`paper.frozen_at`、`exam.snapshot_version`）；
 * - `PATCH /exams/{id}`：`ExamPatch`，状态机 `draft → published → closed` 只进不退。
 *
 * 后端语义（TASK-BE-004）：
 * - 422 字段校验失败；403 非本人试卷/考试；409 非法迁移或已发布考试修改时间窗
 *   （仅 `now < start_at` 允许改时间窗）。
 *
 * 日期时间输入：antd `DatePicker` 的 value 必须是 dayjs 对象，而 dayjs 只是 antd 的
 * 传递依赖（web/package.json 未声明；pnpm 严格 node_modules 下不可直接 import）。
 * 为同时满足「不新增依赖 + strict TS + 组件测试可驱动」，start_at/end_at 使用 AntD
 * `Input type="datetime-local"`（浏览器原生 datetime picker），本地字符串随组件状态
 * 存续，提交时统一转 UTC ISO 8601。
 */
import { ApiError } from '../../../api/client';
import type { Paginated } from '../../../api/types';
import { EXAM_STATUS_COLORS, EXAM_STATUS_LABELS, formatDateTime } from '../model';

export { EXAM_STATUS_COLORS, EXAM_STATUS_LABELS, formatDateTime };

/** 考试状态（契约 `Exam.status`，只进不退：draft→published→closed）。 */
export type ExamStatus = 'draft' | 'published' | 'closed';

export const EXAM_STATUSES: readonly ExamStatus[] = ['draft', 'published', 'closed'];

/** 列表筛选值：`all` 表示不带 status 参数（不过滤）。 */
export type ExamStatusFilter = ExamStatus | 'all';

export const EXAM_STATUS_FILTER_OPTIONS: ReadonlyArray<{
  value: ExamStatusFilter;
  label: string;
}> = [
  { value: 'all', label: '全部' },
  ...EXAM_STATUSES.map((status) => ({ value: status, label: EXAM_STATUS_LABELS[status] })),
];

/** 契约约束（`Exam.duration_min` / `ExamCreate.duration_min`）。 */
export const MIN_DURATION_MINUTES = 10;
export const MAX_DURATION_MINUTES = 300;
/** 契约约束（`ExamCreate.title.maxLength`）。 */
export const MAX_EXAM_TITLE_LENGTH = 128;
/** 列表每页条数（contracts/openapi.yaml v1.0.2 `page_size` 默认 20）。 */
export const EXAM_PAGE_SIZE = 20;

/** 镜像 components.schemas.Exam（`shuffle_*` / `snapshot_version` / `updated_at` 为可选）。 */
export interface Exam {
  id: number;
  paper_id: number;
  title: string;
  start_at: string;
  end_at: string;
  duration_min: number;
  shuffle_questions?: boolean;
  shuffle_options?: boolean;
  status: ExamStatus;
  created_by: number;
  /** 发布时冻结的试卷快照版本；status=draft 时为空（CHG-027 条件不变量）。 */
  snapshot_version?: number;
  created_at: string;
  updated_at?: string;
}

/** 镜像 components.schemas.ExamPage。 */
export type ExamPage = Paginated<Exam>;

/** GET /exams 查询参数（`status` 省略 = 不过滤）。 */
export interface ExamListParams {
  status?: ExamStatus;
  page: number;
  page_size: number;
}

/** 镜像 components.schemas.ExamCreate（`publish` 默认 false）。 */
export interface ExamCreate {
  paper_id: number;
  title: string;
  start_at: string;
  end_at: string;
  duration_min: number;
  shuffle_questions: boolean;
  shuffle_options: boolean;
  publish: boolean;
}

/** 镜像 components.schemas.ExamPatch（本页只用于状态迁移，其余字段保留以对齐契约）。 */
export interface ExamPatch {
  title?: string;
  start_at?: string;
  end_at?: string;
  duration_min?: number;
  status?: ExamStatus;
}

/** 创建表单草稿：日期时间沿用 datetime-local 的本地字符串，提交时再转 ISO。 */
export interface ExamDraft {
  paperId: number | null;
  title: string;
  startAt: string;
  endAt: string;
  durationMin: number;
  shuffleQuestions: boolean;
  shuffleOptions: boolean;
  publish: boolean;
}

/** 前端校验错误 / 422 字段级回显：字段 → 中文提示。 */
export interface ExamDraftErrors {
  paperId?: string;
  title?: string;
  startAt?: string;
  endAt?: string;
  durationMin?: string;
}

function pad(value: number): string {
  return String(value).padStart(2, '0');
}

/** Date → datetime-local 字符串（本地时区，`YYYY-MM-DDTHH:mm`）。 */
export function toDateTimeLocal(date: Date): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(
    date.getHours(),
  )}:${pad(date.getMinutes())}`;
}

/** datetime-local 字符串 → Date（按本地时区解析）；空值/非法值返回 null。 */
export function parseDateTimeLocal(value: string): Date | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** 默认时间窗：明天 09:00 开始，持续 2 小时（避免一打开表单就是过去时间）。 */
export function defaultExamWindow(now: Date = new Date()): { startAt: string; endAt: string } {
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1, 9, 0, 0, 0);
  const end = new Date(start.getTime() + 2 * 60 * 60 * 1000);
  return { startAt: toDateTimeLocal(start), endAt: toDateTimeLocal(end) };
}

/** 新建表单初始值：默认时长 60 分钟、开关全部关闭、不立即发布。 */
export function emptyExamDraft(now: Date = new Date()): ExamDraft {
  const { startAt, endAt } = defaultExamWindow(now);
  return {
    paperId: null,
    title: '',
    startAt,
    endAt,
    durationMin: 60,
    shuffleQuestions: false,
    shuffleOptions: false,
    publish: false,
  };
}

export function hasExamDraftErrors(errors: ExamDraftErrors): boolean {
  return Object.values(errors).some((message) => Boolean(message));
}

/**
 * 提交前校验（与 ExamCreate 的服务端口径一致）：
 * 试卷必选、标题 1–128、开始/结束时间可解析且 end > start、
 * 时长 10–300 的整数且不超过时间窗。
 */
export function validateExamDraft(draft: ExamDraft): ExamDraftErrors {
  const errors: ExamDraftErrors = {};

  if (draft.paperId === null) {
    errors.paperId = '请选择一份草稿试卷';
  }

  const title = draft.title.trim();
  if (!title) {
    errors.title = '请输入考试标题';
  } else if (title.length > MAX_EXAM_TITLE_LENGTH) {
    errors.title = `标题不能超过 ${MAX_EXAM_TITLE_LENGTH} 个字符`;
  }

  const start = parseDateTimeLocal(draft.startAt);
  const end = parseDateTimeLocal(draft.endAt);
  if (!start) errors.startAt = '请选择开始时间';
  if (!end) errors.endAt = '请选择结束时间';
  if (start && end && end.getTime() <= start.getTime()) {
    errors.endAt = '结束时间必须晚于开始时间';
  }

  const durationValid =
    Number.isInteger(draft.durationMin) &&
    draft.durationMin >= MIN_DURATION_MINUTES &&
    draft.durationMin <= MAX_DURATION_MINUTES;
  if (!durationValid) {
    errors.durationMin = `考试时长需为 ${MIN_DURATION_MINUTES}–${MAX_DURATION_MINUTES} 分钟的整数`;
  } else if (start && end && end.getTime() > start.getTime()) {
    const windowMinutes = Math.floor((end.getTime() - start.getTime()) / 60000);
    if (draft.durationMin > windowMinutes) {
      errors.durationMin = `考试时长不能超过时间窗（当前时间窗 ${windowMinutes} 分钟）`;
    }
  }

  return errors;
}

/** 草稿 → 契约 ExamCreate：标题去空格、本地时间转 UTC ISO、开关原样透传。 */
export function buildExamCreate(draft: ExamDraft): ExamCreate {
  const paperId = draft.paperId;
  if (paperId === null) {
    throw new Error('未选择试卷，不能提交创建考试');
  }
  const start = parseDateTimeLocal(draft.startAt);
  const end = parseDateTimeLocal(draft.endAt);
  if (!start || !end) {
    throw new Error('考试时间窗不合法，不能提交创建考试');
  }
  return {
    paper_id: paperId,
    title: draft.title.trim(),
    start_at: start.toISOString(),
    end_at: end.toISOString(),
    duration_min: draft.durationMin,
    shuffle_questions: draft.shuffleQuestions,
    shuffle_options: draft.shuffleOptions,
    publish: draft.publish,
  };
}

/**
 * 考试请求错误 → 中文提示（契约错误体只有 `{code,message,request_id}`）。
 * - 403：非本人试卷/考试（docs/12 §2 第 4 步：可见但归属不符）
 * - 409：非法状态迁移 / 已发布考试过期改时间窗
 * - 422：字段校验失败（结构化回显见 `parseExamValidationError`）
 * - 404：考试不存在或不可见
 */
export function describeExamApiError(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (error.status === 403) return '没有权限执行该操作：只有考试创建者或管理员可以操作';
    if (error.status === 409) {
      return '当前考试状态不允许该操作：状态只能按「草稿 → 已发布 → 已结束」推进，已开始的考试不能修改时间窗';
    }
    if (error.status === 422) {
      return error.message ? `参数校验失败：${error.message}` : '参数校验失败，请检查填写内容';
    }
    if (error.status === 404) return '考试不存在或对当前账号不可见';
    return error.message || fallback;
  }
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

/**
 * 422 的 message 形如 `body.duration_min: Input should be ...`（FastAPI 口径），
 * 按字段名把错误落到对应输入框；未识别的字段只保留表单级错误。
 */
export function parseExamValidationError(message: string): ExamDraftErrors {
  const errors: ExamDraftErrors = {};
  const text = message ?? '';

  if (/paper_id|paper id|试卷/.test(text)) {
    errors.paperId = '所选试卷不可用：只能选择自己创建的草稿试卷';
  }
  if (/title|标题/.test(text)) {
    errors.title = `标题不合法：1–${MAX_EXAM_TITLE_LENGTH} 个字符`;
  }
  if (/start_at|start at|开始时间/.test(text)) {
    errors.startAt = '开始时间不合法，请重新选择';
  }
  if (/end_at|end at|结束时间/.test(text)) {
    errors.endAt = '结束时间不合法，请重新选择';
  }
  if (/duration_min|duration min|时长/.test(text)) {
    errors.durationMin = `考试时长需为 ${MIN_DURATION_MINUTES}–${MAX_DURATION_MINUTES} 分钟的整数`;
  }

  return errors;
}
