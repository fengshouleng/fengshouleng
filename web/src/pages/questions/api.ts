/**
 * 题库接口封装。
 *
 * 所有请求都经 `src/api/client.ts`（Cookie / CSRF / 401 / 统一 Error 解析），
 * 页面与测试只依赖本模块，便于对 client 做 mock。
 *
 * 契约：contracts/openapi.yaml v1.0.2
 * - GET    /questions                 列表（教师自己的；admin 全部）
 * - POST   /questions                 新建（201）
 * - PUT    /questions/{question_id}   修改（200）
 * - DELETE /questions/{question_id}   软删除（204；被引用 409 QUESTION_IN_USE）
 * - POST   /questions/import          multipart file（200，可能部分失败）
 */
import { api } from '../../api/client';

import type {
  ImportResult,
  Question,
  QuestionCreate,
  QuestionListParams,
  QuestionPage,
} from './model';

export function listQuestions(params: QuestionListParams): Promise<QuestionPage> {
  return api.get<QuestionPage>('/questions', {
    query: {
      keyword: params.keyword,
      type: params.type,
      tag: params.tag,
      page: params.page,
      page_size: params.page_size,
    },
  });
}

export function createQuestion(payload: QuestionCreate): Promise<Question> {
  return api.post<Question>('/questions', payload);
}

export function updateQuestion(id: number, payload: QuestionCreate): Promise<Question> {
  return api.put<Question>(`/questions/${id}`, payload);
}

export function deleteQuestion(id: number): Promise<void> {
  return api.delete<void>(`/questions/${id}`);
}

/** multipart/form-data：字段名固定 `file`（契约 requestBody 要求）。 */
export function importQuestions(file: File): Promise<ImportResult> {
  const body = new FormData();
  body.append('file', file);
  return api.post<ImportResult>('/questions/import', body);
}
