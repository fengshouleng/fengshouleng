/**
 * 成绩页占位（TASK-FE-004 交卷后的落点）。
 *
 * 真正的成绩明细/错题解析由 TASK-FE-005 在 `pages/result/**` 交付；
 * 本占位页只负责承接 `/results/:sessionId` 跳转并展示交卷提示与分数，
 * 保证 409 自动交卷 / replayed=true 等终态不会把学生丢进空白页。
 */
import { Alert, Button, Card, Result, Space, Typography } from 'antd';
import { useLocation, useNavigate, useParams } from 'react-router-dom';

import type { ExamResultNavigationState } from './ExamPage';

export default function ResultPlaceholderPage() {
  const params = useParams<{ sessionId: string }>();
  const location = useLocation();
  const navigate = useNavigate();
  const state = (location.state ?? null) as ExamResultNavigationState | null;

  const score = typeof state?.score === 'number' ? state.score : null;
  const notice = state?.notice ?? null;

  return (
    <div data-testid="result-placeholder">
      <Card>
        <Result
          status={state?.status === 'expired' ? 'warning' : 'success'}
          title={state?.status === 'expired' ? '考试已自动交卷' : '交卷成功'}
          subTitle={
            score === null
              ? '成绩正在生成，可稍后在「我的成绩」中查看。'
              : `本次成绩：${score} 分`
          }
          extra={
            <Space>
              <Button type="primary" onClick={() => navigate('/my-exams')} data-testid="result-back">
                返回我的考试
              </Button>
              <Button onClick={() => navigate('/me/results')}>我的成绩</Button>
            </Space>
          }
        />

        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          {notice ? (
            <div data-testid="result-placeholder-notice">
              <Alert
                type={state?.replayed ? 'info' : state?.status === 'expired' ? 'warning' : 'success'}
                showIcon
                message={notice}
              />
            </div>
          ) : null}
          <Typography.Text type="secondary">
            会话 ID：{params.sessionId ?? '—'}
            {state?.replayed ? ' · 本次为重复交卷，分数与首次一致' : ''}
          </Typography.Text>
          <Typography.Text type="secondary">
            逐题对错、解析与错题本由 TASK-FE-005 交付；当前为占位页。
          </Typography.Text>
        </Space>
      </Card>
    </div>
  );
}
