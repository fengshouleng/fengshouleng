/**
 * 错题本接口封装：所有请求都经 `src/api/client.ts`。
 *
 * 契约：contracts/openapi.yaml v1.0.2
 * - GET /me/wrong-questions  我的错题（仅 student，分页）
 */
import { api } from '../../api/client';

import type { WrongQuestionListParams, WrongQuestionPage } from './model';

export function listMyWrongQuestions(params: WrongQuestionListParams): Promise<WrongQuestionPage> {
  return api.get<WrongQuestionPage>('/me/wrong-questions', {
    query: {
      page: params.page,
      page_size: params.page_size,
    },
  });
}
