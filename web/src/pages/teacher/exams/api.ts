/**
 * 教师「考试管理」接口封装：所有请求都经 `src/api/client.ts`。
 *
 * 契约：contracts/openapi.yaml v1.0.2
 * - GET   /exams          考试列表（status 筛选 + 分页）
 * - POST  /exams          创建考试（可选立即发布 → 同事务冻结试卷）
 * - PATCH /exams/{id}     状态迁移（draft→published / published→closed）
 *
 * 试卷数据源复用组卷模块的 `GET /papers`（教师只看得到自己的试卷，admin 全部）：
 * - 创建考试只允许选择 `status=draft` 的试卷（发布时后端自动冻结）；
 * - 列表的「试卷」列需要 paper_id → 标题映射，而 `/exams` 只返回 paper_id，
 *   因此这里一次性拉全量试卷（分页循环，page_size 上限 100）。
 */
import { api } from '../../../api/client';
import type { Paper } from '../../papers/model';
import { listPapers } from '../../papers/api';

import type { Exam, ExamCreate, ExamListParams, ExamPage, ExamPatch } from './model';

/** `page_size` 契约上限 100（contracts/openapi.yaml v1.0.2）。 */
const PAPER_PAGE_SIZE = 100;
/** 防御性上限：避免后端 total 异常时无限翻页（100 页 = 10000 份试卷）。 */
const MAX_PAPER_PAGES = 10;

export function listExams(params: ExamListParams): Promise<ExamPage> {
  return api.get<ExamPage>('/exams', {
    query: {
      status: params.status,
      page: params.page,
      page_size: params.page_size,
    },
  });
}

export function createExam(payload: ExamCreate): Promise<Exam> {
  return api.post<Exam>('/exams', payload);
}

export function updateExam(id: number, payload: ExamPatch): Promise<Exam> {
  return api.patch<Exam>(`/exams/${id}`, payload);
}

/** draft → published：后端在同事务冻结试卷并递增 snapshot_version。 */
export function publishExam(id: number): Promise<Exam> {
  return updateExam(id, { status: 'published' });
}

/** published → closed：手动结束考试（进行中会话下次读写按已到期处理）。 */
export function closeExam(id: number): Promise<Exam> {
  return updateExam(id, { status: 'closed' });
}

/** 拉取当前账号可见的全部试卷（列表标题映射 + 创建表单的草稿候选都靠它）。 */
export async function listAllPapers(): Promise<Paper[]> {
  const papers: Paper[] = [];
  let page = 1;

  for (;;) {
    const data = await listPapers({ page, page_size: PAPER_PAGE_SIZE });
    papers.push(...data.items);

    if (data.items.length === 0 || page * PAPER_PAGE_SIZE >= data.total) break;
    page += 1;
    if (page > MAX_PAPER_PAGES) break;
  }

  return papers;
}
