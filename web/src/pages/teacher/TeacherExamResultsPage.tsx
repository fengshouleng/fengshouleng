import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Alert, Card, Space, Statistic, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { Link, useParams } from 'react-router-dom';

import { getExamResults, listTeacherExams } from './api';
import {
  EXAM_RESULT_STATUS_COLORS,
  EXAM_RESULT_STATUS_LABELS,
  EXAM_STATUS_COLORS,
  EXAM_STATUS_LABELS,
  describeExamListApiError,
  describeExamResultsApiError,
  formatDateTime,
  formatScore,
} from './model';
import type { ExamListItem, ExamResultStudent } from './model';

const PAGE_SIZE = 20;

/**
 * 教师考试汇总（TASK-FE-005）：
 * - `/exam-results`：选择自己的考试（GET /exams）
 * - `/exam-results/:examId`：GET /exams/{exam_id}/results
 *
 * 契约口径（v1.0.2）：
 * - `total_sessions` 含进行中；`submitted` = submitted + expired（到期自动交卷计入）；
 * - `average_score` 仅对已判分会话求均值，无已判分会话时为 0；
 * - 仅考试创建者或 admin；越权 403，不存在/不可见 404。
 *
 * data-testid：page-teacher-summary、teacher-summary-table、teacher-total-sessions、
 * teacher-submitted、teacher-average-score、teacher-summary-error、teacher-exams-table
 */
export default function TeacherExamResultsPage() {
  const { examId: rawExamId } = useParams<{ examId: string }>();

  if (rawExamId === undefined) {
    return <TeacherExamList />;
  }

  const examId = Number(rawExamId);
  if (!Number.isInteger(examId) || examId <= 0) {
    return (
      <div data-testid="page-teacher-summary">
        <div data-testid="teacher-summary-invalid">
          <Alert type="warning" showIcon message="考试链接无效" description="请从考试汇总列表选择考试。" />
        </div>
      </div>
    );
  }

  return <TeacherExamSummary examId={examId} />;
}

function TeacherExamSummary({ examId }: { examId: number }) {
  const summaryQuery = useQuery({
    queryKey: ['exam-results', examId],
    queryFn: () => getExamResults(examId),
    retry: false,
  });

  const summary = summaryQuery.data;
  const errorMessage = summaryQuery.isError
    ? describeExamResultsApiError(summaryQuery.error, '加载考试汇总失败，请稍后重试')
    : null;

  const columns: ColumnsType<ExamResultStudent> = [
    {
      title: '学生',
      dataIndex: 'display_name',
      render: (name: string, record: ExamResultStudent) => (
        <Typography.Text data-testid={`teacher-student-${record.student_id}`}>{name}</Typography.Text>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 120,
      render: (status: ExamResultStudent['status']) => (
        <Tag color={EXAM_RESULT_STATUS_COLORS[status]}>{EXAM_RESULT_STATUS_LABELS[status]}</Tag>
      ),
    },
    {
      title: '得分',
      dataIndex: 'score',
      width: 120,
      render: (score: number | undefined) =>
        score === undefined ? (
          <Typography.Text type="secondary">—</Typography.Text>
        ) : (
          <Typography.Text strong>{formatScore(score)} 分</Typography.Text>
        ),
    },
    {
      title: '交卷时间',
      dataIndex: 'submitted_at',
      width: 180,
      render: (value: string | undefined) => (
        <Typography.Text type="secondary">{formatDateTime(value)}</Typography.Text>
      ),
    },
  ];

  return (
    <div data-testid="page-teacher-summary">
      <Card
        title={
          <Space align="center">
            <Typography.Title level={4} style={{ margin: 0 }}>
              考试汇总
            </Typography.Title>
            <Typography.Text type="secondary">考试 #{examId}</Typography.Text>
          </Space>
        }
        extra={<Link to="/exam-results">返回考试列表</Link>}
      >
        <Typography.Paragraph type="secondary">
          平均分只统计已交卷（含到期自动交卷）的会话，无已判分会话时显示 0。
        </Typography.Paragraph>

        {errorMessage ? (
          <div data-testid="teacher-summary-error" style={{ marginBottom: 16 }}>
            <Alert type="error" showIcon message={errorMessage} />
          </div>
        ) : null}

        {errorMessage ? null : (
          <>
            <Space size={40} style={{ marginBottom: 24 }} wrap>
              <div data-testid="teacher-total-sessions">
                <Statistic title="会话总数" value={summary?.total_sessions ?? 0} />
              </div>
              <div data-testid="teacher-submitted">
                <Statistic title="已交卷" value={summary?.submitted ?? 0} />
              </div>
              <div data-testid="teacher-average-score">
                <Statistic
                  title="平均分"
                  value={summary ? formatScore(summary.average_score) : '—'}
                />
              </div>
            </Space>

            <div data-testid="teacher-summary-table">
              <Table<ExamResultStudent>
                rowKey="student_id"
                size="middle"
                loading={summaryQuery.isPending}
                dataSource={summary?.items ?? []}
                columns={columns}
                locale={{ emptyText: '暂无学生会话' }}
                pagination={false}
              />
            </div>
          </>
        )}
      </Card>
    </div>
  );
}

function TeacherExamList() {
  const [page, setPage] = useState(1);

  const examsQuery = useQuery({
    queryKey: ['teacher-exams', page],
    queryFn: () => listTeacherExams({ page, page_size: PAGE_SIZE }),
    retry: false,
  });

  const items = examsQuery.data?.items ?? [];
  const total = examsQuery.data?.total ?? 0;
  const errorMessage = examsQuery.isError
    ? describeExamListApiError(examsQuery.error, '加载考试列表失败，请稍后重试')
    : null;

  const columns: ColumnsType<ExamListItem> = [
    {
      title: '考试',
      dataIndex: 'title',
      render: (title: string, record: ExamListItem) => (
        <div data-testid={`teacher-exam-row-${record.id}`}>
          <Link to={`/exam-results/${record.id}`} data-testid={`teacher-exam-summary-${record.id}`}>
            {title}
          </Link>
        </div>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 110,
      render: (status: ExamListItem['status']) => (
        <Tag color={EXAM_STATUS_COLORS[status]}>{EXAM_STATUS_LABELS[status]}</Tag>
      ),
    },
    {
      title: '开始时间',
      dataIndex: 'start_at',
      width: 180,
      render: (value: string) => (
        <Typography.Text type="secondary">{formatDateTime(value)}</Typography.Text>
      ),
    },
    {
      title: '结束时间',
      dataIndex: 'end_at',
      width: 180,
      render: (value: string) => (
        <Typography.Text type="secondary">{formatDateTime(value)}</Typography.Text>
      ),
    },
  ];

  return (
    <div data-testid="page-teacher-summary">
      <Card
        title={
          <Typography.Title level={4} style={{ margin: 0 }}>
            考试汇总
          </Typography.Title>
        }
        extra={<Typography.Text type="secondary">共 {total} 场考试</Typography.Text>}
      >
        {errorMessage ? (
          <div data-testid="teacher-summary-error" style={{ marginBottom: 16 }}>
            <Alert type="error" showIcon message={errorMessage} />
          </div>
        ) : null}

        <div data-testid="teacher-exams-table">
          <Table<ExamListItem>
            rowKey="id"
            size="middle"
            loading={examsQuery.isPending}
            dataSource={items}
            columns={columns}
            locale={{ emptyText: '暂无考试，创建并发布考试后可查看汇总' }}
            pagination={{
              current: page,
              pageSize: PAGE_SIZE,
              total,
              showSizeChanger: false,
              showTotal: (count) => `共 ${count} 场`,
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
