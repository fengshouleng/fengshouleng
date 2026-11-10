/**
 * 教师考试汇总接口封装：所有请求都经 `src/api/client.ts`。
 *
 * 契约：contracts/openapi.yaml v1.0.2
 * - GET /exams/{exam_id}/results  考试汇总（仅创建者或 admin）
 * - GET /exams                    教师自己的考试（用于选择考试）
 */
import { api } from '../../api/client';

import type { ExamListParams, ExamPage, ExamResultSummary } from './model';

export function getExamResults(examId: number): Promise<ExamResultSummary> {
  return api.get<ExamResultSummary>(`/exams/${examId}/results`);
}

export function listTeacherExams(params: ExamListParams): Promise<ExamPage> {
  return api.get<ExamPage>('/exams', {
    query: {
      page: params.page,
      page_size: params.page_size,
    },
  });
}
