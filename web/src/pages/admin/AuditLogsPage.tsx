import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Card, Input, Space, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';

import AdminGuard from './AdminGuard';
import { listAuditLogs } from './api';
import {
  ADMIN_AUDIT_PAGE_SIZE,
  AUDIT_ACTION_MAX_LENGTH,
  SYSTEM_ACTOR_LABEL,
  buildAuditFilter,
  describeAuditApiError,
  emptyAuditFilterDraft,
  formatActor,
  formatDateTime,
  formatTarget,
  hasAuditFilterErrors,
  validateAuditFilter,
} from './model';
import type {
  AuditFilterDraft,
  AuditFilterErrors,
  AuditFilterValues,
  AuditLog,
} from './model';

/**
 * 审计日志页（TASK-FE-006）——**只读**。
 *
 * - 列表：GET /audit-logs（action 关键字 / actor_id / from-to 闭区间 / 分页），
 *   列 时间/操作者/动作/目标/IP；默认最近 180 天，按 created_at 倒序（服务端保证）；
 * - 系统记录（`actor_id=null`、`ip="-"`，v1.0.3 / CHG-035）显示「系统」，不得渲染成 #null；
 * - 契约无写/删接口：页面上不提供任何删除入口（记录只增不改不删，docs/11）；
 * - 非 admin 由 AdminGuard 防御性渲染 403（路由层 RequireRole 已先拦一层）。
 *
 * data-testid：page-audit-logs、admin-audit-table、admin-audit-filter、
 * admin-audit-action、admin-audit-actor、admin-audit-from、admin-audit-to、
 * admin-audit-search、admin-audit-reset、admin-audit-row-{id}、admin-audit-list-error
 */
export default function AuditLogsPage() {
  return (
    <AdminGuard>
      <AuditLogsContent />
    </AdminGuard>
  );
}

function AuditLogsContent() {
  const [draft, setDraft] = useState<AuditFilterDraft>(emptyAuditFilterDraft);
  const [filter, setFilter] = useState<AuditFilterValues>({});
  const [errors, setErrors] = useState<AuditFilterErrors>({});
  const [page, setPage] = useState(1);

  const params = { ...filter, page, page_size: ADMIN_AUDIT_PAGE_SIZE };

  const auditQuery = useQuery({
    queryKey: ['audit-logs', params],
    queryFn: () => listAuditLogs(params),
  });

  const updateDraft = (patch: Partial<AuditFilterDraft>): void => {
    setDraft((previous) => ({ ...previous, ...patch }));
    setErrors((previous) => {
      const next: AuditFilterErrors = { ...previous };
      if (patch.actorId !== undefined) delete next.actorId;
      if (patch.from !== undefined) {
        delete next.from;
        delete next.range;
      }
      if (patch.to !== undefined) {
        delete next.to;
        delete next.range;
      }
      return next;
    });
  };

  const applyFilter = (): void => {
    const nextErrors = validateAuditFilter(draft);
    setErrors(nextErrors);
    if (hasAuditFilterErrors(nextErrors)) return;
    setFilter(buildAuditFilter(draft));
    setPage(1);
  };

  const resetFilter = (): void => {
    setDraft(emptyAuditFilterDraft());
    setErrors({});
    setFilter({});
    setPage(1);
  };

  const items = auditQuery.data?.items ?? [];
  const total = auditQuery.data?.total ?? 0;
  const listError = auditQuery.isError
    ? describeAuditApiError(auditQuery.error, '加载审计日志失败，请稍后重试')
    : null;

  const columns: ColumnsType<AuditLog> = [
    {
      title: '时间',
      dataIndex: 'created_at',
      width: 170,
      render: (value: string, record: AuditLog) => (
        <div data-testid={`admin-audit-row-${record.id}`}>
          <Typography.Text type="secondary">{formatDateTime(value)}</Typography.Text>
        </div>
      ),
    },
    {
      title: '操作者',
      dataIndex: 'actor_id',
      width: 110,
      render: (actorId: number | null, record: AuditLog) =>
        actorId === null ? (
          <Tag data-testid={`admin-audit-actor-${record.id}`}>{SYSTEM_ACTOR_LABEL}</Tag>
        ) : (
          <Typography.Text data-testid={`admin-audit-actor-${record.id}`}>
            {formatActor(actorId)}
          </Typography.Text>
        ),
    },
    {
      title: '动作',
      dataIndex: 'action',
      width: 220,
      render: (action: string) => <Typography.Text code>{action}</Typography.Text>,
    },
    {
      title: '目标',
      dataIndex: 'target_type',
      render: (_value: unknown, record: AuditLog) => (
        <Typography.Text>{formatTarget(record.target_type, record.target_id)}</Typography.Text>
      ),
    },
    {
      title: 'IP',
      dataIndex: 'ip',
      width: 150,
      render: (ip: string) => <Typography.Text code>{ip}</Typography.Text>,
    },
  ];

  return (
    <div data-testid="page-audit-logs">
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Card
          title={
            <Space align="center">
              <Typography.Title level={4} style={{ margin: 0 }}>
                审计日志
              </Typography.Title>
              <Typography.Text type="secondary">共 {total} 条记录</Typography.Text>
            </Space>
          }
          extra={
            <Tag color="default" data-testid="admin-audit-readonly">
              只读
            </Tag>
          }
        >
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <div data-testid="admin-audit-filter">
              <Space wrap align="center">
                <Input
                  allowClear
                  value={draft.action}
                  maxLength={AUDIT_ACTION_MAX_LENGTH}
                  placeholder="动作关键字，如 question.create"
                  style={{ width: 240 }}
                  onChange={(event) => {
                    updateDraft({ action: event.target.value });
                  }}
                  onPressEnter={applyFilter}
                  data-testid="admin-audit-action"
                />
                <Input
                  allowClear
                  value={draft.actorId}
                  inputMode="numeric"
                  placeholder="操作者 ID"
                  style={{ width: 120 }}
                  onChange={(event) => {
                    updateDraft({ actorId: event.target.value });
                  }}
                  onPressEnter={applyFilter}
                  data-testid="admin-audit-actor"
                />
                <Typography.Text type="secondary">时间范围</Typography.Text>
                <Input
                  type="datetime-local"
                  step={60}
                  value={draft.from}
                  style={{ width: 200 }}
                  onChange={(event) => {
                    updateDraft({ from: event.target.value });
                  }}
                  data-testid="admin-audit-from"
                />
                <Typography.Text type="secondary">至</Typography.Text>
                <Input
                  type="datetime-local"
                  step={60}
                  value={draft.to}
                  style={{ width: 200 }}
                  onChange={(event) => {
                    updateDraft({ to: event.target.value });
                  }}
                  data-testid="admin-audit-to"
                />
                <Button type="primary" onClick={applyFilter} data-testid="admin-audit-search">
                  查询
                </Button>
                <Button onClick={resetFilter} data-testid="admin-audit-reset">
                  重置
                </Button>
              </Space>

              {errors.actorId ? (
                <FieldError text={errors.actorId} testId="admin-audit-actor-error" />
              ) : null}
              {errors.from ? (
                <FieldError text={errors.from} testId="admin-audit-from-error" />
              ) : null}
              {errors.to ? <FieldError text={errors.to} testId="admin-audit-to-error" /> : null}
              {errors.range ? (
                <div data-testid="admin-audit-filter-error" style={{ marginTop: 8 }}>
                  <Alert type="warning" showIcon message={errors.range} />
                </div>
              ) : null}
            </div>

            <Typography.Text type="secondary">
              只读视图：默认展示最近 180 天，可按动作关键字、操作者与时间范围（闭区间）筛选；
              系统操作的操作者为「系统」、IP 记为「-」。
            </Typography.Text>
          </Space>
        </Card>

        {listError ? (
          <div data-testid="admin-audit-list-error">
            <Alert
              type="error"
              showIcon
              message={`加载审计日志失败：${listError}`}
              action={
                <Button
                  size="small"
                  onClick={() => {
                    void auditQuery.refetch();
                  }}
                >
                  重试
                </Button>
              }
            />
          </div>
        ) : null}

        <Card>
          <div data-testid="admin-audit-table">
            <Table<AuditLog>
              rowKey="id"
              size="middle"
              loading={auditQuery.isLoading}
              dataSource={items}
              columns={columns}
              locale={{ emptyText: '暂无审计记录（默认查询最近 180 天）' }}
              pagination={{
                current: page,
                pageSize: ADMIN_AUDIT_PAGE_SIZE,
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
      </Space>
    </div>
  );
}

function FieldError({ text, testId }: { text: string; testId: string }) {
  return (
    <Typography.Text
      type="danger"
      data-testid={testId}
      style={{ display: 'block', fontSize: 12, marginTop: 4 }}
    >
      {text}
    </Typography.Text>
  );
}
