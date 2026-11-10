import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Alert, Card, Table, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { Link } from 'react-router-dom';

import { listMyResults } from './api';
import {
  RESULT_PAGE_SIZE,
  describeMyResultsApiError,
  formatDateTime,
  formatScore,
} from './model';
import type { MyResultItem } from './model';

/**
 * 我的成绩列表（TASK-FE-005）：`/my-results`。
 *
 * 契约 GET /me/results（仅 student，分页）：
 * `MyResultItem` 含 session_id，点击考试标题进入 `/results/{session_id}` 逐题成绩。
 *
 * data-testid：page-my-results、my-results-table、my-results-row-{session_id}、
 * my-results-error
 */
export default function MyResultsPage() {
  const [page, setPage] = useState(1);

  const resultsQuery = useQuery({
    queryKey: ['my-results', page],
    queryFn: () => listMyResults({ page, page_size: RESULT_PAGE_SIZE }),
    retry: false,
  });

  const items = resultsQuery.data?.items ?? [];
  const total = resultsQuery.data?.total ?? 0;
  const errorMessage = resultsQuery.isError
    ? describeMyResultsApiError(resultsQuery.error, '加载我的成绩失败，请稍后重试')
    : null;

  const columns: ColumnsType<MyResultItem> = [
    {
      title: '考试',
      dataIndex: 'exam_title',
      render: (title: string, record: MyResultItem) => (
        <Link to={`/results/${record.session_id}`} data-testid={`my-results-row-${record.session_id}`}>
          {title}
        </Link>
      ),
    },
    {
      title: '成绩',
      key: 'score',
      width: 180,
      render: (_value: unknown, record: MyResultItem) => (
        <Typography.Text strong>
          {formatScore(record.score)} / {formatScore(record.total_points)} 分
        </Typography.Text>
      ),
    },
    {
      title: '交卷时间',
      dataIndex: 'submitted_at',
      width: 180,
      render: (value: string) => (
        <Typography.Text type="secondary">{formatDateTime(value)}</Typography.Text>
      ),
    },
  ];

  return (
    <div data-testid="page-my-results">
      <Card
        title={
          <Typography.Title level={4} style={{ margin: 0 }}>
            我的成绩
          </Typography.Title>
        }
        extra={<Typography.Text type="secondary">共 {total} 次</Typography.Text>}
      >
        {errorMessage ? (
          <div data-testid="my-results-error" style={{ marginBottom: 16 }}>
            <Alert type="error" showIcon message={errorMessage} />
          </div>
        ) : null}

        <div data-testid="my-results-table">
          <Table<MyResultItem>
            rowKey="session_id"
            size="middle"
            loading={resultsQuery.isPending}
            dataSource={items}
            columns={columns}
            locale={{ emptyText: '暂无成绩，交卷后即可在这里查看' }}
            pagination={{
              current: page,
              pageSize: RESULT_PAGE_SIZE,
              total,
              showSizeChanger: false,
              showTotal: (count) => `共 ${count} 条`,
              onChange: (nextPage) => {
                setPage(nextPage);
              },
            }}
          />
        </div>
      </Card>
    </div>
  );
}
