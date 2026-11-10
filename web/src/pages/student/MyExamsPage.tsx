/**
 * 我的考试列表（学生入口）。
 *
 * 契约：GET /me/exams（contracts/openapi.yaml v1.0.2，CHG-024）
 * - 根级 `server_time` 必填：所有“未开始 / 可进入 / 已截止”判断都用它校时，
 *   不依赖浏览器本地时钟；
 * - `session_status`：not_started / in_progress / submitted / expired；
 *   submitted/expired 展示成绩并跳 `/results/{session_id}`；
 * - 进入考试跳 `/exams/{exam_id}`，由作答页 POST 创建/恢复会话。
 */
import { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Card, Space, Table, Tag, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import { useNavigate } from 'react-router-dom';

import { api } from '../../api/client';
import {
  SESSION_STATUS_COLORS,
  SESSION_STATUS_LABELS,
  computeClockOffset,
  describeClockOffset,
  describeExamApiError,
  formatDateTime,
  resolveAvailability,
} from '../exam/model';
import type { ExamAvailability, MyExamItem, MyExamPage } from '../exam/model';

const PAGE_SIZE = 20;

interface MyExamRow extends MyExamItem {
  availability: ExamAvailability;
}

function formatScore(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

function actionLabel(row: MyExamRow): string {
  if (row.session_status === 'in_progress') return '继续考试';
  if (row.availability === 'not_started') return '未开始';
  if (row.availability === 'closed') return '已截止';
  return '进入考试';
}

export default function MyExamsPage() {
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const [nowMs, setNowMs] = useState(() => Date.now());

  const examsQuery = useQuery({
    queryKey: ['me-exams', page, PAGE_SIZE],
    queryFn: () =>
      api.get<MyExamPage>('/me/exams', {
        query: { page, page_size: PAGE_SIZE },
      }),
  });

  useEffect(() => {
    const timer = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  const serverTime = examsQuery.data?.server_time ?? null;

  /**
   * 校时：收到响应时的本地时间与 `server_time` 的差；列表的“未开始/已截止”
   * 全部叠加该偏移后再比较，避免本机时钟不准导致入口判断错误。
   */
  const clockOffsetMs = useMemo(() => {
    if (!serverTime) return 0;
    return computeClockOffset(serverTime, examsQuery.dataUpdatedAt || Date.now());
  }, [serverTime, examsQuery.dataUpdatedAt]);

  const rows = useMemo<MyExamRow[]>(() => {
    const items = examsQuery.data?.items ?? [];
    const nowWithOffset = nowMs + clockOffsetMs;
    return items.map((item) => ({
      ...item,
      availability: resolveAvailability(item, nowWithOffset),
    }));
  }, [examsQuery.data, nowMs, clockOffsetMs]);

  const columns: ColumnsType<MyExamRow> = [
    {
      title: '考试',
      dataIndex: 'exam_title',
      render: (title: string, row) => (
        <div data-testid={`my-exams-row-${row.exam_id}`}>
          <Typography.Text strong>{title}</Typography.Text>
          <div>
            <Typography.Text type="secondary">考试 ID：{row.exam_id}</Typography.Text>
          </div>
        </div>
      ),
    },
    {
      title: '考试时间窗',
      key: 'window',
      render: (_value, row) => (
        <Typography.Text>
          {formatDateTime(row.start_at)} ~ {formatDateTime(row.end_at)}
        </Typography.Text>
      ),
    },
    {
      title: '时长',
      dataIndex: 'duration_min',
      width: 100,
      render: (minutes: number) => `${minutes} 分钟`,
    },
    {
      title: '状态',
      key: 'status',
      width: 190,
      render: (_value, row) => (
        <Space direction="vertical" size={2}>
          <span data-testid={`my-exams-status-${row.exam_id}`}>
            <Tag color={SESSION_STATUS_COLORS[row.session_status]}>
              {SESSION_STATUS_LABELS[row.session_status]}
            </Tag>
            {row.availability === 'not_started' ? <Tag color="blue">未开考</Tag> : null}
            {row.availability === 'closed' ? <Tag>已截止</Tag> : null}
          </span>
          {row.score !== null && row.score !== undefined ? (
            <span data-testid={`my-exams-score-${row.exam_id}`}>
              <Typography.Text strong>{formatScore(row.score)}</Typography.Text>
              <Typography.Text type="secondary">
                {row.total_points !== null && row.total_points !== undefined
                  ? ` / ${formatScore(row.total_points)} 分`
                  : ' 分'}
              </Typography.Text>
            </span>
          ) : null}
        </Space>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 150,
      render: (_value, row) => {
        if (row.availability === 'finished') {
          return row.session_id !== null && row.session_id !== undefined ? (
            <Button
              type="link"
              onClick={() => navigate(`/results/${row.session_id}`)}
              data-testid={`my-exams-result-${row.exam_id}`}
            >
              查看成绩
            </Button>
          ) : (
            <Button type="link" disabled>
              已交卷
            </Button>
          );
        }

        const disabled = row.availability !== 'enter';
        return (
          <span data-testid={`exam-start-${row.exam_id}`}>
            <Button
              type={disabled ? 'default' : 'primary'}
              disabled={disabled}
              onClick={() => navigate(`/exams/${row.exam_id}`)}
              data-testid="exam-start"
              data-exam-id={row.exam_id}
            >
              {actionLabel(row)}
            </Button>
          </span>
        );
      },
    },
  ];

  const listError = examsQuery.isError
    ? describeExamApiError(examsQuery.error, '加载我的考试失败，请稍后重试')
    : null;

  return (
    <div data-testid="my-exams-page">
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Card
          title={
            <Space align="center" wrap>
              <Typography.Title level={4} style={{ margin: 0 }}>
                我的考试
              </Typography.Title>
              <Typography.Text type="secondary">
                共 {examsQuery.data?.total ?? 0} 场
              </Typography.Text>
            </Space>
          }
          extra={
            <Space>
              {serverTime ? (
                <span data-testid="my-exams-server-time">
                  <Typography.Text type="secondary">
                    服务器时间 {formatDateTime(serverTime)}
                  </Typography.Text>
                </span>
              ) : null}
              <Button
                onClick={() => {
                  void examsQuery.refetch();
                }}
                data-testid="my-exams-refresh"
              >
                刷新
              </Button>
            </Space>
          }
        >
          {serverTime ? (
            <span data-testid="my-exams-clock-offset">
              <Typography.Text type="secondary">
                {describeClockOffset(clockOffsetMs)}
              </Typography.Text>
            </span>
          ) : null}
        </Card>

        {listError ? (
          <div data-testid="my-exams-error">
            <Alert
              type="error"
              showIcon
              message={listError}
              action={
                <Button
                  size="small"
                  onClick={() => {
                    void examsQuery.refetch();
                  }}
                >
                  重试
                </Button>
              }
            />
          </div>
        ) : null}

        <Card>
          <Table<MyExamRow>
            rowKey="exam_id"
            size="middle"
            loading={examsQuery.isLoading}
            dataSource={rows}
            columns={columns}
            locale={{ emptyText: '暂无可参加的考试' }}
            pagination={{
              current: page,
              pageSize: PAGE_SIZE,
              total: examsQuery.data?.total ?? 0,
              showSizeChanger: false,
              showTotal: (count) => `共 ${count} 场考试`,
              onChange: (nextPage) => setPage(nextPage),
            }}
          />
        </Card>
      </Space>
    </div>
  );
}
