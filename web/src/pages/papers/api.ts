/**
 * 组卷接口封装。
 *
 * 所有请求都经 `src/api/client.ts`（Cookie / CSRF / 401 / 统一 Error 解析），
 * 页面与测试只依赖本模块，便于对 client 做 mock。
 *
 * 契约：contracts/openapi.yaml v1.0.2
 * - GET    /papers                  列表（分页；教师自己的，admin 全部）
 * - POST   /papers                  创建（201，返回 Paper，含服务端计算的 total_points）
 * - GET    /papers/{paper_id}       详情（含 questions[]）
 * - PUT    /papers/{paper_id}       全量修改（200；frozen → 409 PAPER_FROZEN）
 * - DELETE /papers/{paper_id}       删除（204；frozen / 被考试引用 → 409）
 */
import { api } from '../../api/client';
import type { Question } from '../questions/model';

import type { Paper, PaperCreate, PaperListParams, PaperPage } from './model';

/**
 * 题目详情（GET /questions/{question_id}）。
 *
 * 组卷页需要展示已选题目/试卷明细的题干与题型，而 `Paper.questions[]` 只含
 * `question_id / order_no / points / partial_rule`，因此按 id 回查题目详情。
 * 本题库 api.ts 未提供该封装（写作用域不含 `pages/questions/**`），在这里补齐。
 */
export function getQuestionDetail(id: number): Promise<Question> {
  return api.get<Question>(`/questions/${id}`);
}

export function listPapers(params: PaperListParams): Promise<PaperPage> {
  return api.get<PaperPage>('/papers', {
    query: { page: params.page, page_size: params.page_size },
  });
}

export function getPaper(id: number): Promise<Paper> {
  return api.get<Paper>(`/papers/${id}`);
}

export function createPaper(payload: PaperCreate): Promise<Paper> {
  return api.post<Paper>('/papers', payload);
}

export function updatePaper(id: number, payload: PaperCreate): Promise<Paper> {
  return api.put<Paper>(`/papers/${id}`, payload);
}

export function deletePaper(id: number): Promise<void> {
  return api.delete<void>(`/papers/${id}`);
}
