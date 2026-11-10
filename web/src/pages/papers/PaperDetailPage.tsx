import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Card, Descriptions, Space, Spin, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useNavigate, useParams } from 'react-router-dom';

import { queryKeys } from '../../api/client';
import { QUESTION_TYPE_COLORS, QUESTION_TYPE_LABELS } from '../questions/model';
import { getPaper } from './api';
import { useQuestionMap } from './hooks';
import {
  PAPER_FROZEN_DETAIL,
  PAPER_FROZEN_NOTICE,
  PAPER_STATUS_COLORS,
  PAPER_STATUS_LABELS,
  PARTIAL_RULE_LABELS,
  describePaperApiError,
  formatDateTime,
  formatPoints,
  fromPaperQuestions,
} from './model';
import type { SelectedPaperQuestion } from './model';

/**
 * 试卷详情页（只读）。
 *
 * 展示标题、状态、总分（服务端计算值）、创建/冻结时间与题目明细（顺序 / 分值 / 多选给分规则）。
 * 冻结试卷额外提示「已用于已发布考试，不可修改」；草稿提供「编辑」入口。
 *
 * data-testid：page-paper-detail、papers-detail-row-{question_id}
 */
export default function PaperDetailPage() {
  const { paperId: paperIdParam } = useParams<{ paperId?: string }>();
  const navigate = useNavigate();
  const paperId = Number(paperIdParam);

  const paperQuery = useQuery({
    queryKey: queryKeys.papers({ paper_id: Number.isFinite(paperId) ? paperId : 'new' }),
    queryFn: () => getPaper(paperId),
    enabled: Number.isInteger(paperId) && paperId > 0,
  });

  const remoteQuestions = paperQuery.data?.questions;
  const questionIds = useMemo(
    () => (remoteQuestions ?? []).map((item) => item.question_id),
    [remoteQuestions],
  );
  const { questions: questionById } = useQuestionMap(questionIds);

  const items = useMemo<SelectedPaperQuestion[]>(
    () => (paperQuery.data ? fromPaperQuestions(paperQuery.data, questionById) : []),
    [paperQuery.data, questionById],
  );

  if (paperQuery.isLoading) {
    return (
      <div data-testid="page-paper-detail" style={{ padding: 48, textAlign: 'center' }}>
        <Spin />
      </div>
    );
  }

  if (paperQuery.isError || !paperQuery.data) {
    return (
      <div data-testid="page-paper-detail">
        <Alert
          type="error"
          showIcon
          message={`加载试卷失败：${describePaperApiError(paperQuery.error, '请稍后重试')}`}
          action={
            <Button size="small" onClick={() => navigate('/papers')}>
              返回列表
            </Button>
          }
        />
      </div>
    );
  }

  const paper = paperQuery.data;
  const frozen = paper.status === 'frozen';

  const columns: ColumnsType<SelectedPaperQuestion> = [
    {
      title: '序号',
      key: 'order_no',
      width: 72,
      render: (_value: unknown, _record: SelectedPaperQuestion, index: number) => index + 1,
    },
    {
      title: '题干',
      dataIndex: 'stem',
      render: (stem: string, record: SelectedPaperQuestion) => (
        <div data-testid={`papers-detail-row-${record.question_id}`} style={{ maxWidth: 520 }}>
          <Typography.Text ellipsis={{ tooltip: stem }} style={{ maxWidth: 500 }}>
            {stem}
          </Typography.Text>
        </div>
      ),
    },
    {
      title: '题型',
      dataIndex: 'type',
      width: 100,
      render: (value: SelectedPaperQuestion['type']) => (
        <Tag color={QUESTION_TYPE_COLORS[value]}>{QUESTION_TYPE_LABELS[value]}</Tag>
      ),
    },
    {
      title: '分值',
      dataIndex: 'points',
      width: 100,
      render: (value: number) => `${formatPoints(value)} 分`,
    },
    {
      title: '给分规则',
      dataIndex: 'partial_rule',
      width: 140,
      render: (value: SelectedPaperQuestion['partial_rule'], record: SelectedPaperQuestion) =>
        record.type === 'multiple' ? PARTIAL_RULE_LABELS[value] : '—',
    },
  ];

  return (
    <div data-testid="page-paper-detail">
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Card
          title={
            <Space align="center" wrap>
              <Typography.Title level={4} style={{ margin: 0 }}>
                {paper.title}
              </Typography.Title>
              <Tag color={PAPER_STATUS_COLORS[paper.status]} data-testid="papers-detail-status">
                {PAPER_STATUS_LABELS[paper.status]}
              </Tag>
            </Space>
          }
          extra={
            <Space>
              <Button onClick={() => navigate('/papers')} data-testid="papers-detail-back">
                返回列表
              </Button>
              {frozen ? null : (
                <Button
                  type="primary"
                  onClick={() => navigate(`/papers/${paper.id}/edit`)}
                  data-testid="papers-detail-edit"
                >
                  编辑
                </Button>
              )}
            </Space>
          }
        >
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            {frozen ? (
              <div data-testid="papers-detail-frozen-alert">
                <Alert type="warning" showIcon message={PAPER_FROZEN_NOTICE} description={PAPER_FROZEN_DETAIL} />
              </div>
            ) : null}

            <Descriptions size="small" column={4} bordered>
              <Descriptions.Item label="总分">
                <Typography.Text strong data-testid="papers-detail-total">
                  {formatPoints(paper.total_points)} 分
                </Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="题目数">{paper.questions.length} 题</Descriptions.Item>
              <Descriptions.Item label="创建时间">{formatDateTime(paper.created_at)}</Descriptions.Item>
              <Descriptions.Item label="冻结时间">
                {frozen ? formatDateTime(paper.frozen_at) : '—'}
              </Descriptions.Item>
            </Descriptions>
          </Space>
        </Card>

        <Card title="题目明细">
          <Table<SelectedPaperQuestion>
            rowKey="question_id"
            size="middle"
            dataSource={items}
            columns={columns}
            pagination={false}
            locale={{ emptyText: '该试卷暂无题目' }}
          />
        </Card>
      </Space>
    </div>
  );
}
