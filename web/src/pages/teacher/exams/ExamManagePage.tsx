import { useMemo, useState } from 'react';
import type { ReactElement, ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  Input,
  InputNumber,
  Modal,
  Radio,
  Row,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useNavigate } from 'react-router-dom';

import { ApiError, queryKeys } from '../../../api/client';
import { formatPoints } from '../../papers/model';
import type { Paper } from '../../papers/model';
import { closeExam, createExam, listAllPapers, listExams, publishExam } from './api';
import {
  EXAM_PAGE_SIZE,
  EXAM_STATUS_COLORS,
  EXAM_STATUS_FILTER_OPTIONS,
  EXAM_STATUS_LABELS,
  MAX_DURATION_MINUTES,
  MAX_EXAM_TITLE_LENGTH,
  MIN_DURATION_MINUTES,
  buildExamCreate,
  describeExamApiError,
  emptyExamDraft,
  formatDateTime,
  hasExamDraftErrors,
  parseExamValidationError,
  validateExamDraft,
} from './model';
import type {
  Exam,
  ExamCreate,
  ExamDraft,
  ExamDraftErrors,
  ExamStatusFilter,
} from './model';

/** 行内状态操作（发布 / 关闭），两者都需要二次确认。 */
type ExamAction = 'publish' | 'close';

interface ConfirmState {
  exam: Exam;
  action: ExamAction;
}

/**
 * 教师「考试管理」页（TASK-FE-007）。
 *
 * - 列表：GET /exams（status 筛选 + 分页），列 标题/试卷/时间窗/时长/状态/创建时间/操作；
 * - 创建：POST /exams，只允许选择自己的 **draft** 试卷（发布时后端自动冻结），
 *   标题、start_at/end_at、duration_min、乱序开关、publish 勾选；
 * - 发布/关闭：PATCH /exams/{id}（status），二次确认；非法迁移 409 → 中文提示并刷新列表；
 * - 403（非本人试卷/考试）与 422（字段校验）错误回显；
 * - 写操作成功后失效 `['exams']` 与 `['papers']`（发布会让试卷冻结，影响组卷/创建候选）。
 *
 * data-testid：page-exams、exams-create、exams-submit、exams-status-filter、
 * exams-row-{id}、exams-status-{id}、exams-paper-title-{id}、exams-duration-{id}、
 * exams-publish-{id}、exams-close-{id}、exams-view-{id}、
 * exams-publish-confirm、exams-close-confirm、exams-error
 */
export default function ExamManagePage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [statusFilter, setStatusFilter] = useState<ExamStatusFilter>('all');
  const [page, setPage] = useState(1);
  const [pageError, setPageError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<ConfirmState | null>(null);

  const [createOpen, setCreateOpen] = useState(false);
  const [draft, setDraft] = useState<ExamDraft>(() => emptyExamDraft());
  const [draftErrors, setDraftErrors] = useState<ExamDraftErrors>({});
  const [formError, setFormError] = useState<string | null>(null);

  const statusParam = statusFilter === 'all' ? undefined : statusFilter;

  const examsQuery = useQuery({
    queryKey: queryKeys.exams({
      status: statusParam,
      page,
      page_size: EXAM_PAGE_SIZE,
    }),
    queryFn: () => listExams({ status: statusParam, page, page_size: EXAM_PAGE_SIZE }),
  });

  /**
   * 试卷有两个用途：列表的 paper_id → 标题映射，创建表单的草稿候选。
   * `/exams` 不返回试卷标题，因此这里额外拉一次 `GET /papers`（教师仅自己的，admin 全部）。
   */
  const papersQuery = useQuery({
    queryKey: ['papers', 'all'],
    queryFn: listAllPapers,
    staleTime: 60_000,
  });

  const paperById = useMemo(
    () =>
      new Map<number, Paper>(
        (papersQuery.data ?? []).map((paper): [number, Paper] => [paper.id, paper]),
      ),
    [papersQuery.data],
  );

  /** 只有草稿试卷可以创建考试；已冻结试卷用于既往考试，不能再选。 */
  const draftPapers = useMemo(
    () => (papersQuery.data ?? []).filter((paper) => paper.status === 'draft'),
    [papersQuery.data],
  );

  /** `['exams']` / `['papers']` 前缀一次失效所有筛选组合与分页缓存。 */
  const refreshAfterWrite = (): void => {
    void queryClient.invalidateQueries({ queryKey: ['exams'] });
    void queryClient.invalidateQueries({ queryKey: ['papers'] });
  };

  const transitionMutation = useMutation({
    mutationFn: (input: ConfirmState) =>
      input.action === 'publish' ? publishExam(input.exam.id) : closeExam(input.exam.id),
    onSuccess: (_exam, input) => {
      setConfirming(null);
      setPageError(null);
      refreshAfterWrite();
      message.success(input.action === 'publish' ? '考试已发布，试卷已冻结' : '考试已结束');
    },
    onError: (error) => {
      // 409 非法迁移说明列表里的状态已过期：提示 + 刷新，避免用户反复点同一个按钮。
      setConfirming(null);
      setPageError(describeExamApiError(error, '操作失败，请稍后重试'));
      refreshAfterWrite();
    },
  });

  const createMutation = useMutation({
    mutationFn: (payload: ExamCreate) => createExam(payload),
    onSuccess: () => {
      setCreateOpen(false);
      setFormError(null);
      setDraftErrors({});
      refreshAfterWrite();
      message.success('考试已创建');
    },
    onError: (error) => {
      setFormError(describeExamApiError(error, '创建考试失败，请稍后重试'));
      if (error instanceof ApiError && error.status === 422) {
        setDraftErrors(parseExamValidationError(error.message));
      }
    },
  });

  const updateDraft = (patch: Partial<ExamDraft>): void => {
    setDraft((previous) => ({ ...previous, ...patch }));
    setDraftErrors((previous) => {
      const next: ExamDraftErrors = { ...previous };
      if (patch.paperId !== undefined) delete next.paperId;
      if (patch.title !== undefined) delete next.title;
      if (patch.startAt !== undefined) delete next.startAt;
      if (patch.endAt !== undefined) delete next.endAt;
      if (patch.durationMin !== undefined) delete next.durationMin;
      return next;
    });
  };

  const openCreate = (): void => {
    setDraft(emptyExamDraft());
    setDraftErrors({});
    setFormError(null);
    setCreateOpen(true);
  };

  const submitCreate = (): void => {
    if (createMutation.isPending) return;
    const errors = validateExamDraft(draft);
    setDraftErrors(errors);
    if (hasExamDraftErrors(errors)) {
      setFormError('请先修正表单中的错误');
      return;
    }
    setFormError(null);
    createMutation.mutate(buildExamCreate(draft));
  };

  const confirmTransition = (): void => {
    if (!confirming || transitionMutation.isPending) return;
    transitionMutation.mutate(confirming);
  };

  const items = examsQuery.data?.items ?? [];
  const total = examsQuery.data?.total ?? 0;
  const listError = examsQuery.isError
    ? examsQuery.error instanceof ApiError && examsQuery.error.status === 403
      ? '仅教师或管理员可查看考试管理'
      : describeExamApiError(examsQuery.error, '加载考试列表失败，请稍后重试')
    : null;

  const columns: ColumnsType<Exam> = [
    {
      title: '标题',
      dataIndex: 'title',
      render: (title: string, record: Exam) => (
        <div data-testid={`exams-row-${record.id}`} style={{ maxWidth: 320 }}>
          <Typography.Text ellipsis={{ tooltip: title }} style={{ maxWidth: 300 }}>
            {title}
          </Typography.Text>
        </div>
      ),
    },
    {
      title: '试卷',
      dataIndex: 'paper_id',
      width: 220,
      render: (_value: unknown, record: Exam) => {
        const paper = paperById.get(record.paper_id);
        return (
          <Typography.Text
            type={paper ? undefined : 'secondary'}
            data-testid={`exams-paper-title-${record.id}`}
          >
            {paper ? paper.title : `试卷 #${record.paper_id}`}
          </Typography.Text>
        );
      },
    },
    {
      title: '开始时间',
      dataIndex: 'start_at',
      width: 160,
      render: (value: string) => (
        <Typography.Text type="secondary">{formatDateTime(value)}</Typography.Text>
      ),
    },
    {
      title: '结束时间',
      dataIndex: 'end_at',
      width: 160,
      render: (value: string) => (
        <Typography.Text type="secondary">{formatDateTime(value)}</Typography.Text>
      ),
    },
    {
      title: '时长',
      dataIndex: 'duration_min',
      width: 100,
      render: (value: number, record: Exam) => (
        <Typography.Text data-testid={`exams-duration-${record.id}`}>{value} 分钟</Typography.Text>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (_value: unknown, record: Exam) => (
        <Tag
          color={EXAM_STATUS_COLORS[record.status]}
          data-testid={`exams-status-${record.id}`}
        >
          {EXAM_STATUS_LABELS[record.status]}
        </Tag>
      ),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 160,
      render: (value: string) => (
        <Typography.Text type="secondary">{formatDateTime(value)}</Typography.Text>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 170,
      render: (_value: unknown, record: Exam) => (
        <Space size="small">
          {record.status === 'draft' ? (
            <Button
              type="link"
              size="small"
              onClick={() => {
                setPageError(null);
                setConfirming({ exam: record, action: 'publish' });
              }}
              data-testid={`exams-publish-${record.id}`}
            >
              发布
            </Button>
          ) : null}
          {record.status === 'published' ? (
            <Button
              type="link"
              size="small"
              danger
              onClick={() => {
                setPageError(null);
                setConfirming({ exam: record, action: 'close' });
              }}
              data-testid={`exams-close-${record.id}`}
            >
              关闭
            </Button>
          ) : null}
          <Button
            type="link"
            size="small"
            onClick={() => {
              navigate(`/exam-results/${record.id}`);
            }}
            data-testid={`exams-view-${record.id}`}
          >
            查看
          </Button>
        </Space>
      ),
    },
  ];

  const modalTitle = confirming?.action === 'publish' ? '发布考试' : '结束考试';
  const confirmTestId =
    confirming?.action === 'publish' ? 'exams-publish-confirm' : 'exams-close-confirm';

  return (
    <div data-testid="page-exams">
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Card
          title={
            <Space align="center">
              <Typography.Title level={4} style={{ margin: 0 }}>
                考试管理
              </Typography.Title>
              <Typography.Text type="secondary">共 {total} 场考试</Typography.Text>
            </Space>
          }
          extra={
            <Button type="primary" onClick={openCreate} data-testid="exams-create">
              创建考试
            </Button>
          }
        >
          <Space wrap align="center">
            <Typography.Text type="secondary">状态筛选</Typography.Text>
            <div data-testid="exams-status-filter">
              <Radio.Group
                value={statusFilter}
                optionType="button"
                buttonStyle="solid"
                options={EXAM_STATUS_FILTER_OPTIONS.map((option) => ({
                  label: option.label,
                  value: option.value,
                }))}
                onChange={(event) => {
                  setStatusFilter(event.target.value as ExamStatusFilter);
                  setPage(1);
                }}
              />
            </div>
            <Typography.Text type="secondary">
              仅展示自己创建的考试；发布后试卷自动冻结，状态只能按草稿 → 已发布 → 已结束推进。
            </Typography.Text>
          </Space>
        </Card>

        {pageError ? (
          <div data-testid="exams-error">
            <Alert
              type="error"
              showIcon
              closable
              message={pageError}
              onClose={() => {
                setPageError(null);
              }}
            />
          </div>
        ) : null}

        {listError ? (
          <div data-testid="exams-list-error">
            <Alert
              type="error"
              showIcon
              message={`加载考试失败：${listError}`}
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
          <div data-testid="exams-table">
            <Table<Exam>
              rowKey="id"
              size="middle"
              loading={examsQuery.isLoading}
              dataSource={items}
              columns={columns}
              locale={{ emptyText: '暂无考试，点击「创建考试」创建第一场考试' }}
              pagination={{
                current: page,
                pageSize: EXAM_PAGE_SIZE,
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
      </Space>

      <Modal
        open={createOpen}
        title="创建考试"
        width={720}
        onCancel={() => {
          if (!createMutation.isPending) setCreateOpen(false);
        }}
        maskClosable={!createMutation.isPending}
        keyboard={!createMutation.isPending}
        styles={{ body: { maxHeight: '62vh', overflowY: 'auto', paddingTop: 8 } }}
        footer={
          <Space>
            <Button
              disabled={createMutation.isPending}
              onClick={() => {
                setCreateOpen(false);
              }}
            >
              取消
            </Button>
            <Button
              type="primary"
              loading={createMutation.isPending}
              onClick={submitCreate}
              data-testid="exams-submit"
            >
              创建考试
            </Button>
          </Space>
        }
      >
        {formError ? (
          <div data-testid="exams-form-error" style={{ marginBottom: 16 }}>
            <Alert type="error" showIcon message={formError} />
          </div>
        ) : null}

        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <div data-testid="exams-paper">
            <FieldLabel>试卷（只列出自己创建的草稿试卷；发布后自动冻结）</FieldLabel>
            <Select
              showSearch
              allowClear
              optionFilterProp="label"
              placeholder="请选择草稿试卷"
              style={{ width: '100%' }}
              loading={papersQuery.isLoading}
              value={draft.paperId ?? undefined}
              options={draftPapers.map((paper) => ({
                value: paper.id,
                label: `${paper.title}（${formatPoints(paper.total_points)} 分）`,
              }))}
              notFoundContent={
                papersQuery.isLoading
                  ? '正在加载试卷…'
                  : draftPapers.length === 0
                    ? '暂无草稿试卷，请先到「组卷」创建'
                    : '未找到匹配的试卷'
              }
              onChange={(value: number | undefined) => {
                updateDraft({ paperId: value ?? null });
              }}
            />
            {papersQuery.isError ? (
              <Typography.Text type="danger" style={{ display: 'block', fontSize: 12, marginTop: 4 }}>
                试卷加载失败，请刷新页面后重试
              </Typography.Text>
            ) : null}
            {!papersQuery.isLoading && draftPapers.length === 0 ? (
              <Typography.Text type="secondary" style={{ display: 'block', fontSize: 12, marginTop: 4 }}>
                还没有可用的草稿试卷：已冻结的试卷不能再次用于新考试。
              </Typography.Text>
            ) : null}
            <FieldError text={draftErrors.paperId} testId="exams-paper-error" />
          </div>

          <div data-testid="exams-title-field">
            <FieldLabel>标题</FieldLabel>
            <Input
              value={draft.title}
              maxLength={MAX_EXAM_TITLE_LENGTH}
              showCount
              placeholder="例如：2026 春季期中考试"
              onChange={(event) => {
                updateDraft({ title: event.target.value });
              }}
              data-testid="exams-title"
            />
            <FieldError text={draftErrors.title} testId="exams-title-error" />
          </div>

          <Row gutter={16}>
            <Col span={12}>
              <FieldLabel>开始时间</FieldLabel>
              <Input
                type="datetime-local"
                step={60}
                value={draft.startAt}
                onChange={(event) => {
                  updateDraft({ startAt: event.target.value });
                }}
                data-testid="exams-start-at"
              />
              <FieldError text={draftErrors.startAt} testId="exams-start-at-error" />
            </Col>
            <Col span={12}>
              <FieldLabel>结束时间</FieldLabel>
              <Input
                type="datetime-local"
                step={60}
                value={draft.endAt}
                onChange={(event) => {
                  updateDraft({ endAt: event.target.value });
                }}
                data-testid="exams-end-at"
              />
              <FieldError text={draftErrors.endAt} testId="exams-end-at-error" />
            </Col>
          </Row>

          <div data-testid="exams-duration-field">
            <FieldLabel>考试时长（{MIN_DURATION_MINUTES}–{MAX_DURATION_MINUTES} 分钟）</FieldLabel>
            <InputNumber
              min={MIN_DURATION_MINUTES}
              max={MAX_DURATION_MINUTES}
              precision={0}
              step={5}
              value={Number.isNaN(draft.durationMin) ? null : draft.durationMin}
              addonAfter="分钟"
              style={{ width: '100%' }}
              onChange={(value) => {
                updateDraft({ durationMin: typeof value === 'number' ? value : Number.NaN });
              }}
              data-testid="exams-duration"
            />
            <FieldError text={draftErrors.durationMin} testId="exams-duration-error" />
          </div>

          <Row gutter={16}>
            <Col span={12}>
              <Space size="small">
                <Switch
                  aria-label="题目乱序"
                  checked={draft.shuffleQuestions}
                  onChange={(checked) => {
                    updateDraft({ shuffleQuestions: checked });
                  }}
                  data-testid="exams-shuffle-questions"
                />
                <Typography.Text>题目乱序</Typography.Text>
              </Space>
            </Col>
            <Col span={12}>
              <Space size="small">
                <Switch
                  aria-label="选项乱序"
                  checked={draft.shuffleOptions}
                  onChange={(checked) => {
                    updateDraft({ shuffleOptions: checked });
                  }}
                  data-testid="exams-shuffle-options"
                />
                <Typography.Text>选项乱序</Typography.Text>
              </Space>
            </Col>
          </Row>

          <div data-testid="exams-publish-field">
            <Checkbox
              checked={draft.publish}
              onChange={(event) => {
                updateDraft({ publish: event.target.checked });
              }}
              data-testid="exams-publish-now"
            >
              创建后立即发布（发布后试卷自动冻结，状态不可回退）
            </Checkbox>
          </div>
        </Space>
      </Modal>

      <Modal
        open={confirming !== null}
        title={modalTitle}
        onCancel={() => {
          if (!transitionMutation.isPending) setConfirming(null);
        }}
        maskClosable={!transitionMutation.isPending}
        footer={
          <Space>
            <Button
              disabled={transitionMutation.isPending}
              onClick={() => {
                setConfirming(null);
              }}
            >
              取消
            </Button>
            <Button
              type="primary"
              danger={confirming?.action === 'close'}
              loading={transitionMutation.isPending}
              onClick={confirmTransition}
              data-testid={confirmTestId}
            >
              {confirming?.action === 'publish' ? '确认发布' : '确认结束'}
            </Button>
          </Space>
        }
      >
        <Typography.Paragraph>
          确定要{confirming?.action === 'publish' ? '发布' : '结束'}考试「
          {confirming?.exam.title ?? ''}」吗？此操作需要二次确认。
        </Typography.Paragraph>
        <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
          发布后试卷立即冻结并生成快照，状态不可回退；结束考试后学生无法再开始作答。
        </Typography.Paragraph>
      </Modal>
    </div>
  );
}

function FieldLabel({ children }: { children: ReactNode }): ReactElement {
  return (
    <Typography.Text type="secondary" style={{ display: 'block', fontSize: 12, marginBottom: 4 }}>
      {children}
    </Typography.Text>
  );
}

function FieldError({ text, testId }: { text: string | undefined; testId: string }): ReactElement | null {
  if (!text) return null;
  return (
    <Typography.Text type="danger" data-testid={testId} style={{ display: 'block', fontSize: 12, marginTop: 4 }}>
      {text}
    </Typography.Text>
  );
}
