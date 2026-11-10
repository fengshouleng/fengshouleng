import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  Button,
  Card,
  Modal,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { Link, useNavigate } from 'react-router-dom';

import { queryKeys } from '../../api/client';
import { deletePaper, listPapers } from './api';
import {
  PAPER_STATUS_COLORS,
  PAPER_STATUS_LABELS,
  PAPER_FROZEN_NOTICE,
  describePaperApiError,
  formatDateTime,
  formatPoints,
} from './model';
import type { Paper } from './model';

const PAGE_SIZE = 20;

/**
 * 试卷列表页（TASK-FE-003）。
 *
 * - 列表：GET /papers（page/page_size），契约 PaperPage
 * - 新建/编辑：/papers/new、/papers/{id}/edit（组卷页）
 * - 查看：/papers/{id}（只读详情）
 * - 删除：二次确认后 DELETE /papers/{id}；
 *   409 PAPER_FROZEN / 409 被考试引用 → 中文提示（AC-10）
 * - frozen 行标记「已冻结」，编辑入口打开的是只读编辑页（保存按钮禁用 + 冻结提示）
 * - 写操作成功后失效 `['papers']` 前缀（queryKeys.papers 的所有分页组合）
 *
 * data-testid：page-papers、papers-create、papers-row-{id}、papers-status-{id}、
 * papers-total-{id}、papers-edit-{id}、papers-view-{id}、papers-delete-{id}
 */
export default function PapersPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [page, setPage] = useState(1);
  const [deleting, setDeleting] = useState<Paper | null>(null);
  const [pageError, setPageError] = useState<string | null>(null);

  const params = useMemo(() => ({ page, page_size: PAGE_SIZE }), [page]);

  const papersQuery = useQuery({
    queryKey: queryKeys.papers({ page: params.page, page_size: params.page_size }),
    queryFn: () => listPapers(params),
  });

  /** `['papers']` 是 queryKeys.papers(...) 所有组合的前缀，一次失效全部列表缓存。 */
  const invalidatePapers = (): void => {
    void queryClient.invalidateQueries({ queryKey: ['papers'] });
  };

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deletePaper(id),
    onSuccess: () => {
      setDeleting(null);
      setPageError(null);
      invalidatePapers();
      message.success('试卷已删除');
    },
    onError: (error) => {
      setDeleting(null);
      setPageError(describePaperApiError(error, '删除失败，请稍后重试'));
    },
  });

  const items = papersQuery.data?.items ?? [];
  const total = papersQuery.data?.total ?? 0;
  const listError = papersQuery.isError
    ? describePaperApiError(papersQuery.error, '请稍后重试')
    : null;

  const columns: ColumnsType<Paper> = [
    {
      title: '标题',
      dataIndex: 'title',
      render: (title: string, record: Paper) => (
        <div data-testid={`papers-row-${record.id}`} style={{ maxWidth: 420 }}>
          <Link to={`/papers/${record.id}`}>
            <Typography.Text ellipsis={{ tooltip: title }} style={{ maxWidth: 400 }}>
              {title}
            </Typography.Text>
          </Link>
        </div>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 110,
      render: (_value: unknown, record: Paper) => (
        <Tooltip title={record.status === 'frozen' ? PAPER_FROZEN_NOTICE : '草稿：可继续编辑'}>
          <Tag color={PAPER_STATUS_COLORS[record.status]} data-testid={`papers-status-${record.id}`}>
            {PAPER_STATUS_LABELS[record.status]}
          </Tag>
        </Tooltip>
      ),
    },
    {
      title: '总分',
      dataIndex: 'total_points',
      width: 110,
      render: (value: number, record: Paper) => (
        <Typography.Text data-testid={`papers-total-${record.id}`}>
          {formatPoints(value)} 分
        </Typography.Text>
      ),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 170,
      render: (value: string) => (
        <Typography.Text type="secondary">{formatDateTime(value)}</Typography.Text>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 180,
      render: (_value: unknown, record: Paper) => (
        <Space size="small">
          <Button
            type="link"
            size="small"
            onClick={() => {
              navigate(`/papers/${record.id}`);
            }}
            data-testid={`papers-view-${record.id}`}
          >
            查看
          </Button>
          <Button
            type="link"
            size="small"
            onClick={() => {
              navigate(`/papers/${record.id}/edit`);
            }}
            data-testid={`papers-edit-${record.id}`}
          >
            编辑
          </Button>
          <Button
            type="link"
            size="small"
            danger
            onClick={() => {
              setPageError(null);
              setDeleting(record);
            }}
            data-testid={`papers-delete-${record.id}`}
          >
            删除
          </Button>
        </Space>
      ),
    },
  ];

  const confirmDelete = (): void => {
    if (!deleting || deleteMutation.isPending) return;
    deleteMutation.mutate(deleting.id);
  };

  return (
    <div data-testid="page-papers">
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Card
          title={
            <Space align="center">
              <Typography.Title level={4} style={{ margin: 0 }}>
                组卷
              </Typography.Title>
              <Typography.Text type="secondary">试卷共 {total} 份</Typography.Text>
            </Space>
          }
          extra={
            <Button
              type="primary"
              onClick={() => {
                navigate('/papers/new');
              }}
              data-testid="papers-create"
            >
              新建试卷
            </Button>
          }
        >
          <Typography.Text type="secondary">
            组卷规则：单题 0–100 分（最多 2 位小数），总分 1–10000 分，单卷 1–100 题；
            发布考试后试卷自动冻结，不可再修改。
          </Typography.Text>
        </Card>

        {pageError ? (
          <div data-testid="papers-error">
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
          <div data-testid="papers-list-error">
            <Alert
              type="error"
              showIcon
              message={`加载试卷失败：${listError}`}
              action={
                <Button
                  size="small"
                  onClick={() => {
                    void papersQuery.refetch();
                  }}
                >
                  重试
                </Button>
              }
            />
          </div>
        ) : null}

        <Card>
          <Table<Paper>
            rowKey="id"
            size="middle"
            loading={papersQuery.isLoading}
            dataSource={items}
            columns={columns}
            locale={{ emptyText: '暂无试卷，点击「新建试卷」开始组卷' }}
            pagination={{
              current: page,
              pageSize: PAGE_SIZE,
              total,
              showSizeChanger: false,
              showTotal: (count) => `共 ${count} 份`,
              onChange: (nextPage) => {
                setPage(nextPage);
              },
            }}
          />
        </Card>
      </Space>

      <Modal
        open={deleting !== null}
        title="删除试卷"
        onCancel={() => {
          if (!deleteMutation.isPending) setDeleting(null);
        }}
        maskClosable={!deleteMutation.isPending}
        footer={
          <Space>
            <Button
              disabled={deleteMutation.isPending}
              onClick={() => {
                setDeleting(null);
              }}
            >
              取消
            </Button>
            <Button
              type="primary"
              danger
              loading={deleteMutation.isPending}
              onClick={confirmDelete}
              data-testid="papers-delete-confirm"
            >
              确认删除
            </Button>
          </Space>
        }
      >
        <Typography.Paragraph>
          确定要删除试卷「{deleting?.title ?? ''}」吗？此操作需要二次确认。
        </Typography.Paragraph>
        <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
          删除后试卷与其题目明细将一并移除；已冻结（用于已发布考试）或被考试引用的试卷不可删除。
        </Typography.Paragraph>
      </Modal>
    </div>
  );
}
