/**
 * 成绩接口封装：所有请求都经 `src/api/client.ts`
 * （Cookie / CSRF / 401 / 统一 Error 解析），页面与测试只依赖本模块。
 *
 * 契约：contracts/openapi.yaml v1.0.2
 * - GET /sessions/{session_id}/result  逐题成绩（学生本人或 admin）
 * - GET /me/results                    我的成绩（仅 student）
 */
import { api } from '../../api/client';

import type { MyResultListParams, MyResultPage, ResultDetail } from './model';

export function getSessionResult(sessionId: number): Promise<ResultDetail> {
  return api.get<ResultDetail>(`/sessions/${sessionId}/result`);
}

export function listMyResults(params: MyResultListParams): Promise<MyResultPage> {
  return api.get<MyResultPage>('/me/results', {
    query: {
      page: params.page,
      page_size: params.page_size,
    },
  });
}
