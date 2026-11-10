import { useMemo } from 'react';
import { useQueries } from '@tanstack/react-query';

import { queryKeys } from '../../api/client';
import type { Question } from '../questions/model';
import { getQuestionDetail } from './api';

/**
 * 批量拉取题目详情。
 *
 * `GET /papers/{id}` 只返回 `question_id / order_no / points / partial_rule`（契约 PaperQuestion），
 * 编辑页与详情页需要题干/题型展示，因此按 id 逐个走 `GET /questions/{id}`（走 client 统一缓存）。
 * 题目详情会命中 queryKeys.questions 缓存，重复进入页面不会重复请求。
 */
export function useQuestionMap(questionIds: readonly number[]): {
  questions: Map<number, Question>;
  loading: boolean;
  hasError: boolean;
} {
  const results = useQueries({
    queries: questionIds.map((questionId) => ({
      queryKey: queryKeys.questions({ question_id: questionId }),
      queryFn: () => getQuestionDetail(questionId),
      staleTime: 60_000,
    })),
  });

  const questions = useMemo(() => {
    const map = new Map<number, Question>();
    for (const result of results) {
      if (result.data) map.set(result.data.id, result.data);
    }
    return map;
  }, [results]);

  return {
    questions,
    loading: results.some((result) => result.isPending),
    hasError: results.some((result) => result.isError),
  };
}
