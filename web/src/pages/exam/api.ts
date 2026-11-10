/**
 * 考试作答接口封装（所有请求统一走 `src/api/client.ts`）。
 *
 * 契约：contracts/openapi.yaml v1.0.2
 * - POST /exams/{exam_id}/sessions   开始或恢复会话（201 新建 / 200 恢复；409 未开始/已结束）
 * - GET  /sessions/{session_id}      会话状态（刷新/到期自动交卷后取现状）
 * - PUT  /sessions/{session_id}/answers  自动保存（client_seq 单调递增，applied 必填）
 * - POST /sessions/{session_id}/submit   幂等交卷（200，replayed 必填，无 409）
 */
import { api } from '../../api/client';

import type {
  AnswerSaveRequest,
  AnswerSaveResponse,
  SessionState,
  SubmitResult,
} from './model';

export function startOrResumeSession(
  examId: number,
  signal?: AbortSignal,
): Promise<SessionState> {
  return api.post<SessionState>(`/exams/${examId}/sessions`, undefined, { signal });
}

export function getSession(sessionId: number): Promise<SessionState> {
  return api.get<SessionState>(`/sessions/${sessionId}`);
}

export function saveAnswers(
  sessionId: number,
  payload: AnswerSaveRequest,
): Promise<AnswerSaveResponse> {
  return api.put<AnswerSaveResponse>(`/sessions/${sessionId}/answers`, payload);
}

export function submitSession(sessionId: number): Promise<SubmitResult> {
  return api.post<SubmitResult>(`/sessions/${sessionId}/submit`);
}
