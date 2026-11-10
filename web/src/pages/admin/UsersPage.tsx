import { useState } from 'react';
import type { ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  Button,
  Card,
  Input,
  Modal,
  Radio,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';

import { ApiError, queryKeys } from '../../api/client';
import { ROLE_LABELS } from '../../api/types';
import type { Role, User } from '../../api/types';
import AdminGuard from './AdminGuard';
import { changeUserRole, createUser, listUsers, resetUserPassword, setUserActive } from './api';
import {
  ADMIN_USERS_PAGE_SIZE,
  DISPLAY_NAME_MAX_LENGTH,
  PASSWORD_MAX_LENGTH,
  PASSWORD_MIN_LENGTH,
  ROLE_FILTER_OPTIONS,
  ROLE_OPTIONS,
  ROLE_TAG_COLORS,
  USERNAME_MAX_LENGTH,
  USERNAME_MIN_LENGTH,
  buildUserCreate,
  describeUserApiError,
  emptyUserDraft,
  formatDateTime,
  hasUserDraftErrors,
  parseUserValidationError,
  validateUserDraft,
} from './model';
import type { RoleFilter, UserCreate, UserDraft, UserDraftErrors } from './model';

/**
 * 用户管理页（TASK-FE-006）。
 *
 * - 列表：GET /users（role 筛选 + 分页），列 用户名/显示名/角色/状态/创建时间/操作；
 * - 创建：POST /users（username/display_name/role/password，密码 8–128），
 *   422 字段级回显、409 用户名冲突、403 无权限；
 * - 修改角色：PATCH /users/{id} `{role}`（弹窗选择 + 二次确认）；
 * - 停用/启用：PATCH /users/{id} `{is_active}`（二次确认；停用使现有会话失效）；
 * - 重置密码：PATCH /users/{id} `{password}`（弹窗输入新密码，前端先校验长度）。
 * - 所有写操作成功后失效 `['users']` 前缀缓存（queryKeys.users 的所有筛选/分页组合）。
 *
 * 非 admin 由 AdminGuard 防御性渲染 403（路由层 RequireRole 已先拦一层）。
 *
 * data-testid：page-users、admin-users-table、admin-users-filter、admin-user-create、
 * admin-user-row-{id}、admin-user-status-{id}、admin-user-role-{id}、
 * admin-user-disable（停用按钮）/ admin-user-enable（启用按钮）、
 * admin-user-reset-{id}、admin-user-submit、admin-user-form-error、
 * admin-user-disable-confirm / admin-user-enable-confirm、admin-users-error
 */
export default function UsersPage() {
  return (
    <AdminGuard>
      <UsersContent />
    </AdminGuard>
  );
}

function UsersContent() {
  const queryClient = useQueryClient();

  const [roleFilter, setRoleFilter] = useState<RoleFilter>('all');
  const [page, setPage] = useState(1);
  const [pageError, setPageError] = useState<string | null>(null);

  // 创建账号弹窗
  const [createOpen, setCreateOpen] = useState(false);
  const [draft, setDraft] = useState<UserDraft>(emptyUserDraft);
  const [draftErrors, setDraftErrors] = useState<UserDraftErrors>({});
  const [formError, setFormError] = useState<string | null>(null);

  // 修改角色弹窗
  const [roleTarget, setRoleTarget] = useState<User | null>(null);
  const [roleDraft, setRoleDraft] = useState<Role>('student');
  const [roleError, setRoleError] = useState<string | null>(null);

  // 停用/启用确认弹窗
  const [toggleTarget, setToggleTarget] = useState<User | null>(null);

  // 重置密码弹窗
  const [resetTarget, setResetTarget] = useState<User | null>(null);
  const [resetPassword, setResetPassword] = useState('');
  const [resetError, setResetError] = useState<string | null>(null);

  const roleParam = roleFilter === 'all' ? undefined : roleFilter;

  const usersQuery = useQuery({
    queryKey: queryKeys.users({
      role: roleParam,
      page,
      page_size: ADMIN_USERS_PAGE_SIZE,
    }),
    queryFn: () =>
      listUsers({ role: roleParam, page, page_size: ADMIN_USERS_PAGE_SIZE }),
  });

  /** `['users']` 是 queryKeys.users(...) 所有组合的前缀，一次失效全部列表缓存。 */
  const invalidateUsers = (): void => {
    void queryClient.invalidateQueries({ queryKey: ['users'] });
  };

  const createMutation = useMutation({
    mutationFn: (payload: UserCreate) => createUser(payload),
    onSuccess: () => {
      setCreateOpen(false);
      setFormError(null);
      setDraftErrors({});
      invalidateUsers();
      message.success('账号已创建');
    },
    onError: (error) => {
      setFormError(describeUserApiError(error, '创建账号失败，请稍后重试'));
      if (error instanceof ApiError && error.status === 422) {
        setDraftErrors(parseUserValidationError(error.message));
      }
    },
  });

  const roleMutation = useMutation({
    mutationFn: (input: { id: number; role: Role }) => changeUserRole(input.id, input.role),
    onSuccess: (_user, input) => {
      setRoleTarget(null);
      setRoleError(null);
      invalidateUsers();
      message.success(`角色已改为${ROLE_LABELS[input.role]}`);
    },
    onError: (error) => {
      setRoleError(describeUserApiError(error, '修改角色失败，请稍后重试'));
    },
  });

  const toggleMutation = useMutation({
    mutationFn: (input: { user: User }) => setUserActive(input.user.id, !input.user.is_active),
    onSuccess: (_user, input) => {
      setToggleTarget(null);
      setPageError(null);
      invalidateUsers();
      message.success(input.user.is_active ? '账号已停用' : '账号已启用');
    },
    onError: (error) => {
      // 失败也关闭确认框：列表里的状态可能已过期，提示后让用户刷新重试。
      setToggleTarget(null);
      setPageError(describeUserApiError(error, '操作失败，请稍后重试'));
      invalidateUsers();
    },
  });

  const resetMutation = useMutation({
    mutationFn: (input: { id: number; password: string }) =>
      resetUserPassword(input.id, input.password),
    onSuccess: () => {
      setResetTarget(null);
      setResetError(null);
      setResetPassword('');
      invalidateUsers();
      message.success('密码已重置，请通知用户使用新密码登录');
    },
    onError: (error) => {
      setResetError(describeUserApiError(error, '重置密码失败，请稍后重试'));
    },
  });

  const updateDraft = (patch: Partial<UserDraft>): void => {
    setDraft((previous) => ({ ...previous, ...patch }));
    setFormError(null);
    setDraftErrors((previous) => {
      const next: UserDraftErrors = { ...previous };
      if (patch.username !== undefined) delete next.username;
      if (patch.displayName !== undefined) delete next.displayName;
      if (patch.role !== undefined) delete next.role;
      if (patch.password !== undefined) delete next.password;
      return next;
    });
  };

  const openCreate = (): void => {
    setDraft(emptyUserDraft());
    setDraftErrors({});
    setFormError(null);
    setCreateOpen(true);
  };

  const submitCreate = (): void => {
    if (createMutation.isPending) return;
    const errors = validateUserDraft(draft);
    setDraftErrors(errors);
    if (hasUserDraftErrors(errors)) {
      setFormError('请先修正表单中的错误');
      return;
    }
    setFormError(null);
    createMutation.mutate(buildUserCreate(draft));
  };

  const openRole = (user: User): void => {
    setRoleError(null);
    setRoleDraft(user.role);
    setRoleTarget(user);
  };

  const confirmRole = (): void => {
    if (!roleTarget || roleMutation.isPending) return;
    roleMutation.mutate({ id: roleTarget.id, role: roleDraft });
  };

  const openToggle = (user: User): void => {
    setPageError(null);
    setToggleTarget(user);
  };

  const confirmToggle = (): void => {
    if (!toggleTarget || toggleMutation.isPending) return;
    toggleMutation.mutate({ user: toggleTarget });
  };

  const openReset = (user: User): void => {
    setResetError(null);
    setResetPassword('');
    setResetTarget(user);
  };

  const submitReset = (): void => {
    if (!resetTarget || resetMutation.isPending) return;
    if (!resetPassword) {
      setResetError('请输入新密码');
      return;
    }
    if (resetPassword.length < PASSWORD_MIN_LENGTH) {
      setResetError(`密码至少 ${PASSWORD_MIN_LENGTH} 位`);
      return;
    }
    if (resetPassword.length > PASSWORD_MAX_LENGTH) {
      setResetError(`密码不能超过 ${PASSWORD_MAX_LENGTH} 位`);
      return;
    }
    setResetError(null);
    resetMutation.mutate({ id: resetTarget.id, password: resetPassword });
  };

  const items = usersQuery.data?.items ?? [];
  const total = usersQuery.data?.total ?? 0;
  const listError = usersQuery.isError
    ? describeUserApiError(usersQuery.error, '加载用户列表失败，请稍后重试')
    : null;

  const columns: ColumnsType<User> = [
    {
      title: '用户名',
      dataIndex: 'username',
      width: 160,
      render: (username: string, record: User) => (
        <div data-testid={`admin-user-row-${record.id}`}>
          <Typography.Text strong>{username}</Typography.Text>
          <Typography.Text type="secondary" style={{ marginLeft: 6, fontSize: 12 }}>
            #{record.id}
          </Typography.Text>
        </div>
      ),
    },
    {
      title: '显示名',
      dataIndex: 'display_name',
      width: 160,
    },
    {
      title: '角色',
      dataIndex: 'role',
      width: 110,
      render: (role: Role, record: User) => (
        <Tag color={ROLE_TAG_COLORS[role]} data-testid={`admin-user-role-tag-${record.id}`}>
          {ROLE_LABELS[role]}
        </Tag>
      ),
    },
    {
      title: '状态',
      dataIndex: 'is_active',
      width: 100,
      render: (isActive: boolean, record: User) => (
        <Tag color={isActive ? 'success' : 'default'} data-testid={`admin-user-status-${record.id}`}>
          {isActive ? '启用' : '停用'}
        </Tag>
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
      width: 240,
      render: (_value: unknown, record: User) => (
        <Space size="small" wrap>
          <Button
            type="link"
            size="small"
            onClick={() => {
              openRole(record);
            }}
            data-testid={`admin-user-role-${record.id}`}
          >
            修改角色
          </Button>
          <Button
            type="link"
            size="small"
            danger={record.is_active}
            onClick={() => {
              openToggle(record);
            }}
            data-testid={record.is_active ? 'admin-user-disable' : 'admin-user-enable'}
          >
            {record.is_active ? '停用' : '启用'}
          </Button>
          <Button
            type="link"
            size="small"
            onClick={() => {
              openReset(record);
            }}
            data-testid={`admin-user-reset-${record.id}`}
          >
            重置密码
          </Button>
        </Space>
      ),
    },
  ];

  const toggleActive = toggleTarget?.is_active ?? false;
  const toggleConfirmTestId = toggleActive
    ? 'admin-user-disable-confirm'
    : 'admin-user-enable-confirm';

  return (
    <div data-testid="page-users">
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Card
          title={
            <Space align="center">
              <Typography.Title level={4} style={{ margin: 0 }}>
                用户管理
              </Typography.Title>
              <Typography.Text type="secondary">账号总数 {total}</Typography.Text>
            </Space>
          }
          extra={
            <Button type="primary" onClick={openCreate} data-testid="admin-user-create">
              新建账号
            </Button>
          }
        >
          <Space wrap align="center">
            <Typography.Text type="secondary">角色筛选</Typography.Text>
            <div data-testid="admin-users-filter">
              <Radio.Group
                value={roleFilter}
                optionType="button"
                buttonStyle="solid"
                options={ROLE_FILTER_OPTIONS.map((option) => ({
                  label: option.label,
                  value: option.value,
                }))}
                onChange={(event) => {
                  setRoleFilter(event.target.value as RoleFilter);
                  setPage(1);
                }}
              />
            </div>
            <Typography.Text type="secondary">
              仅管理员可管理账号；停用账号会立即使其现有会话失效。
            </Typography.Text>
          </Space>
        </Card>

        {pageError ? (
          <div data-testid="admin-users-error">
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
          <div data-testid="admin-users-list-error">
            <Alert
              type="error"
              showIcon
              message={`加载用户列表失败：${listError}`}
              action={
                <Button
                  size="small"
                  onClick={() => {
                    void usersQuery.refetch();
                  }}
                >
                  重试
                </Button>
              }
            />
          </div>
        ) : null}

        <Card>
          <div data-testid="admin-users-table">
            <Table<User>
              rowKey="id"
              size="middle"
              loading={usersQuery.isLoading}
              dataSource={items}
              columns={columns}
              locale={{ emptyText: '暂无账号，点击「新建账号」创建第一个账号' }}
              pagination={{
                current: page,
                pageSize: ADMIN_USERS_PAGE_SIZE,
                total,
                showSizeChanger: false,
                showTotal: (count) => `共 ${count} 个账号`,
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
        title="新建账号"
        width={560}
        onCancel={() => {
          if (!createMutation.isPending) setCreateOpen(false);
        }}
        maskClosable={!createMutation.isPending}
        keyboard={!createMutation.isPending}
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
              data-testid="admin-user-submit"
            >
              创建账号
            </Button>
          </Space>
        }
      >
        {formError ? (
          <div data-testid="admin-user-form-error" style={{ marginBottom: 16 }}>
            <Alert type="error" showIcon message={formError} />
          </div>
        ) : null}

        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <div>
            <FieldLabel>
              用户名（{USERNAME_MAX_LENGTH} 字符以内，不少于 {USERNAME_MIN_LENGTH} 字符）
            </FieldLabel>
            <Input
              value={draft.username}
              maxLength={USERNAME_MAX_LENGTH}
              autoComplete="off"
              placeholder="登录用户名，例如：tea009"
              onChange={(event) => {
                updateDraft({ username: event.target.value });
              }}
              data-testid="admin-user-username"
            />
            <FieldError text={draftErrors.username} testId="admin-user-username-error" />
          </div>

          <div>
            <FieldLabel>显示名</FieldLabel>
            <Input
              value={draft.displayName}
              maxLength={DISPLAY_NAME_MAX_LENGTH}
              placeholder={`显示名（1–${DISPLAY_NAME_MAX_LENGTH} 字符）`}
              onChange={(event) => {
                updateDraft({ displayName: event.target.value });
              }}
              data-testid="admin-user-display-name"
            />
            <FieldError text={draftErrors.displayName} testId="admin-user-display-name-error" />
          </div>

          <div>
            <FieldLabel>角色</FieldLabel>
            <Radio.Group
              value={draft.role}
              onChange={(event) => {
                updateDraft({ role: event.target.value as Role });
              }}
              data-testid="admin-user-role-field"
            >
              {ROLE_OPTIONS.map((option) => (
                <Radio.Button key={option.value} value={option.value}>
                  {option.label}
                </Radio.Button>
              ))}
            </Radio.Group>
            <FieldError text={draftErrors.role} testId="admin-user-role-field-error" />
          </div>

          <div>
            <FieldLabel>
              初始密码（{PASSWORD_MIN_LENGTH}–{PASSWORD_MAX_LENGTH} 位）
            </FieldLabel>
            <Input.Password
              value={draft.password}
              maxLength={PASSWORD_MAX_LENGTH}
              autoComplete="new-password"
              placeholder={`至少 ${PASSWORD_MIN_LENGTH} 位`}
              onChange={(event) => {
                updateDraft({ password: event.target.value });
              }}
              data-testid="admin-user-password"
            />
            <FieldError text={draftErrors.password} testId="admin-user-password-error" />
          </div>
        </Space>
      </Modal>

      <Modal
        open={roleTarget !== null}
        title="修改角色"
        onCancel={() => {
          if (!roleMutation.isPending) setRoleTarget(null);
        }}
        maskClosable={!roleMutation.isPending}
        footer={
          <Space>
            <Button
              disabled={roleMutation.isPending}
              onClick={() => {
                setRoleTarget(null);
              }}
            >
              取消
            </Button>
            <Button
              type="primary"
              loading={roleMutation.isPending}
              onClick={confirmRole}
              data-testid="admin-user-role-confirm"
            >
              保存
            </Button>
          </Space>
        }
      >
        <Typography.Paragraph>
          账号「{roleTarget?.username ?? ''}」当前角色为
          {roleTarget ? ROLE_LABELS[roleTarget.role] : '—'}，请选择新角色。
        </Typography.Paragraph>
        <Radio.Group
          value={roleDraft}
          onChange={(event) => {
            setRoleDraft(event.target.value as Role);
            setRoleError(null);
          }}
          data-testid="admin-user-role-select"
        >
          {ROLE_OPTIONS.map((option) => (
            <Radio.Button key={option.value} value={option.value}>
              {option.label}
            </Radio.Button>
          ))}
        </Radio.Group>
        {roleError ? (
          <div data-testid="admin-user-role-error" style={{ marginTop: 12 }}>
            <Alert type="error" showIcon message={roleError} />
          </div>
        ) : null}
      </Modal>

      <Modal
        open={toggleTarget !== null}
        title={toggleActive ? '停用账号' : '启用账号'}
        onCancel={() => {
          if (!toggleMutation.isPending) setToggleTarget(null);
        }}
        maskClosable={!toggleMutation.isPending}
        footer={
          <Space>
            <Button
              disabled={toggleMutation.isPending}
              onClick={() => {
                setToggleTarget(null);
              }}
            >
              取消
            </Button>
            <Button
              type="primary"
              danger={toggleActive}
              loading={toggleMutation.isPending}
              onClick={confirmToggle}
              data-testid={toggleConfirmTestId}
            >
              {toggleActive ? '确认停用' : '确认启用'}
            </Button>
          </Space>
        }
      >
        <Typography.Paragraph>
          确定要{toggleActive ? '停用' : '启用'}账号「{toggleTarget?.username ?? ''}」吗？此操作需要二次确认。
        </Typography.Paragraph>
        <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
          停用后该账号现有会话立即失效（无法继续作答或管理），重新启用后即可再次登录。
        </Typography.Paragraph>
      </Modal>

      <Modal
        open={resetTarget !== null}
        title="重置密码"
        onCancel={() => {
          if (!resetMutation.isPending) setResetTarget(null);
        }}
        maskClosable={!resetMutation.isPending}
        footer={
          <Space>
            <Button
              disabled={resetMutation.isPending}
              onClick={() => {
                setResetTarget(null);
              }}
            >
              取消
            </Button>
            <Button
              type="primary"
              loading={resetMutation.isPending}
              onClick={submitReset}
              data-testid="admin-user-reset-confirm"
            >
              确认重置
            </Button>
          </Space>
        }
      >
        <Typography.Paragraph>
          为账号「{resetTarget?.username ?? ''}」设置新密码（{PASSWORD_MIN_LENGTH}–
          {PASSWORD_MAX_LENGTH} 位），重置后请通知用户使用新密码登录。
        </Typography.Paragraph>
        <Input.Password
          value={resetPassword}
          maxLength={PASSWORD_MAX_LENGTH}
          autoComplete="new-password"
          placeholder={`至少 ${PASSWORD_MIN_LENGTH} 位`}
          onChange={(event) => {
            setResetPassword(event.target.value);
            setResetError(null);
          }}
          data-testid="admin-user-reset-password"
        />
        {resetError ? (
          <div data-testid="admin-user-reset-error" style={{ marginTop: 12 }}>
            <Alert type="error" showIcon message={resetError} />
          </div>
        ) : null}
      </Modal>
    </div>
  );
}

function FieldLabel({ children }: { children: ReactNode }) {
  return (
    <Typography.Text type="secondary" style={{ display: 'block', fontSize: 12, marginBottom: 4 }}>
      {children}
    </Typography.Text>
  );
}

function FieldError({ text, testId }: { text: string | undefined; testId: string }) {
  if (!text) return null;
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
