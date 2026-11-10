import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Alert, Card, List, Pagination, Space, Tag, Typography } from 'antd';

import { QUESTION_TYPE_COLORS, QUESTION_TYPE_LABELS } from '../questions/model';
import { listMyWrongQuestions } from './api';
import {
  WRONG_QUESTION_PAGE_SIZE,
  describeWrongQuestionsApiError,
  formatDateTime,
} from './model';
import type { WrongQuestion } from './model';

/**
 * 我的错题本（TASK-FE-005）：`/wrong-questions`。
 *
 * 契约 GET /me/wrong-questions（仅 student，分页）：
 * 展示题干、题型、累计错误次数 `wrong_count` 与最后错误时间 `last_wrong_at`；
 * 同一题只出现一次（AC-20）。
 *
 * data-testid：page-wrong-questions、wrong-list、wrong-item-{question_id}、
 * wrong-pagination、wrong-error
 */
export default function WrongQuestionsPage() {
  const [page, setPage] = useState(1);

  const wrongQuery = useQuery({
    queryKey: ['my-wrong-questions', page],
    queryFn: () => listMyWrongQuestions({ page, page_size: WRONG_QUESTION_PAGE_SIZE }),
    retry: false,
  });

  const items = wrongQuery.data?.items ?? [];
  const total = wrongQuery.data?.total ?? 0;
  const errorMessage = wrongQuery.isError
    ? describeWrongQuestionsApiError(wrongQuery.error, '加载错题本失败，请稍后重试')
    : null;

  return (
    <div data-testid="page-wrong-questions">
      <Card
        title={
          <Typography.Title level={4} style={{ margin: 0 }}>
            错题本
          </Typography.Title>
        }
        extra={<Typography.Text type="secondary">共 {total} 道错题</Typography.Text>}
      >
        <Typography.Paragraph type="secondary">
          同一道题多次答错只记录一次，答错次数累计展示，可按时间回顾薄弱知识点。
        </Typography.Paragraph>

        {errorMessage ? (
          <div data-testid="wrong-error" style={{ marginBottom: 16 }}>
            <Alert type="error" showIcon message={errorMessage} />
          </div>
        ) : null}

        <div data-testid="wrong-list">
          <List<WrongQuestion>
            loading={wrongQuery.isPending}
            dataSource={items}
            locale={{ emptyText: '暂无错题，继续保持' }}
            renderItem={(item) => (
              <List.Item key={item.question_id}>
                <div data-testid={`wrong-item-${item.question_id}`} style={{ width: '100%' }}>
                  <Space direction="vertical" size={8} style={{ width: '100%' }}>
                    <Space size="small" wrap>
                      <Tag color={QUESTION_TYPE_COLORS[item.type]}>
                        {QUESTION_TYPE_LABELS[item.type]}
                      </Tag>
                      <Typography.Text strong>{item.stem}</Typography.Text>
                    </Space>
                    <Space size="large" wrap>
                      <Typography.Text type="danger">
                        错误 {item.wrong_count} 次
                      </Typography.Text>
                      <Typography.Text type="secondary">
                        最后错误时间：{formatDateTime(item.last_wrong_at)}
                      </Typography.Text>
                    </Space>
                  </Space>
                </div>
              </List.Item>
            )}
          />
        </div>

        {total > 0 ? (
          <div data-testid="wrong-pagination" style={{ marginTop: 16, textAlign: 'right' }}>
            <Pagination
              current={page}
              pageSize={WRONG_QUESTION_PAGE_SIZE}
              total={total}
              showSizeChanger={false}
              showTotal={(count) => `共 ${count} 道错题`}
              onChange={(nextPage) => {
                setPage(nextPage);
              }}
            />
          </div>
        ) : null}
      </Card>
    </div>
  );
}
