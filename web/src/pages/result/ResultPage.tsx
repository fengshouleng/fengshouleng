import { useQuery } from '@tanstack/react-query';
import { Alert, Card, Space, Spin, Tag, Typography } from 'antd';
import { useParams } from 'react-router-dom';

import { QUESTION_TYPE_COLORS, QUESTION_TYPE_LABELS } from '../questions/model';
import { getSessionResult } from './api';
import { describeResultApiError, formatAnswer, formatDateTime, formatScore } from './model';
import type { ResultItem } from './model';

/**
 * 学生成绩页（TASK-FE-005）：`/results/:sessionId`。
 *
 * 契约 GET /sessions/{session_id}/result（v1.0.2）：
 * - `status ∈ {submitted, expired}` 可读；expired（到期自动交卷）与 submitted 展示口径一致；
 * - 未交卷 → 409 `NOT_SUBMITTED` → 提示「尚未交卷」（AC-19）；
 * - 学生访问他人成绩 → 403 `FORBIDDEN` → 提示「无权查看该成绩」（AC-18）；
 * - 逐题展示题干、我的答案、正确答案、对错、得分/满分与解析。
 *
 * data-testid：page-result、result-score、result-list、result-item-{question_id}、
 * result-correct-{question_id}、result-error、result-loading
 */
export default function ResultPage() {
  const { sessionId: rawSessionId } = useParams<{ sessionId: string }>();
  const sessionId = Number(rawSessionId);
  const validSessionId = Number.isInteger(sessionId) && sessionId > 0;

  const resultQuery = useQuery({
    queryKey: ['session-result', sessionId],
    queryFn: () => getSessionResult(sessionId),
    enabled: validSessionId,
    retry: false,
  });

  if (!validSessionId) {
    return (
      <div data-testid="page-result">
        <div data-testid="result-invalid">
          <Alert type="warning" showIcon message="成绩链接无效" description="请从「我的成绩」进入。" />
        </div>
      </div>
    );
  }

  const errorMessage = resultQuery.isError
    ? describeResultApiError(resultQuery.error, '加载成绩失败，请稍后重试')
    : null;
  const detail = resultQuery.data;

  return (
    <div data-testid="page-result">
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Card>
          <Typography.Title level={4} style={{ margin: 0 }}>
            考试成绩
          </Typography.Title>
          <Typography.Text type="secondary">
            {detail ? `交卷时间：${formatDateTime(detail.submitted_at)}` : '正在加载成绩…'}
          </Typography.Text>
        </Card>

        {resultQuery.isPending ? (
          <div data-testid="result-loading" style={{ textAlign: 'center', padding: 48 }}>
            <Spin size="large" />
          </div>
        ) : null}

        {errorMessage ? (
          <div data-testid="result-error">
            <Alert
              type="error"
              showIcon
              message={errorMessage}
              action={
                <Typography.Link
                  onClick={() => {
                    void resultQuery.refetch();
                  }}
                >
                  重试
                </Typography.Link>
              }
            />
          </div>
        ) : null}

        {detail ? (
          <>
            <div data-testid="result-score">
              <Card>
                <Space direction="vertical" size={4}>
                  <Typography.Text type="secondary">本次成绩</Typography.Text>
                  <Typography.Title level={2} style={{ margin: 0 }}>
                    {formatScore(detail.score)}{' '}
                    <Typography.Text type="secondary">
                      / {formatScore(detail.total_points)} 分
                    </Typography.Text>
                  </Typography.Title>
                  <Typography.Text type="secondary">
                    共 {detail.items.length} 道题，逐题结果如下。
                  </Typography.Text>
                </Space>
              </Card>
            </div>

            <div data-testid="result-list">
              <Space direction="vertical" size={12} style={{ width: '100%' }}>
                {detail.items.map((item, index) => (
                  <ResultItemCard key={item.question_id} item={item} index={index} />
                ))}
              </Space>
            </div>
          </>
        ) : null}
      </Space>
    </div>
  );
}

function ResultItemCard({ item, index }: { item: ResultItem; index: number }) {
  return (
    <div data-testid={`result-item-${item.question_id}`}>
      <Card size="small">
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <Space size="small" wrap>
            <Typography.Text strong>第 {index + 1} 题</Typography.Text>
            <Tag color={QUESTION_TYPE_COLORS[item.type]}>{QUESTION_TYPE_LABELS[item.type]}</Tag>
            <span data-testid={`result-correct-${item.question_id}`}>
              <Tag color={item.is_correct ? 'success' : 'error'}>
                {item.is_correct ? '回答正确' : '回答错误'}
              </Tag>
            </span>
          </Space>

          <Typography.Paragraph style={{ marginBottom: 0 }}>
            {item.stem ?? `题目 #${item.question_id}`}
          </Typography.Paragraph>

          <Space size="large" wrap>
            <Typography.Text>
              我的答案：
              <Typography.Text code>{formatAnswer(item.selected)}</Typography.Text>
            </Typography.Text>
            <Typography.Text>
              正确答案：
              <Typography.Text code>{formatAnswer(item.correct_answer)}</Typography.Text>
            </Typography.Text>
            <Typography.Text>
              得分：
              <Typography.Text strong>
                {formatScore(item.earned_points)} / {formatScore(item.points)} 分
              </Typography.Text>
            </Typography.Text>
          </Space>

          <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
            解析：{item.explanation ?? '暂无解析'}
          </Typography.Paragraph>
        </Space>
      </Card>
    </div>
  );
}
