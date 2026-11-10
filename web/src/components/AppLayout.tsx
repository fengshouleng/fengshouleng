import { useState } from 'react';
import { Avatar, Button, Layout, Menu, Space, Tag, Typography } from 'antd';
import type { MenuProps } from 'antd';
import { useQueryClient } from '@tanstack/react-query';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';

import { api } from '../api/client';
import { ROLE_LABELS } from '../api/types';
import type { Role } from '../api/types';
import { clearSession, useAuthState } from '../stores/auth';

const { Content, Header, Sider } = Layout;

function roleColor(role: Role): string {
  switch (role) {
    case 'admin':
      return 'gold';
    case 'teacher':
      return 'blue';
    default:
      return 'green';
  }
}

/**
 * 受保护布局：侧边栏 + 顶栏 + 用户信息 + 登出。
 * 业务菜单按角色接入：题库管理（TASK-FE-002）、组卷（TASK-FE-003）、
 * 考试管理（TASK-FE-007）、考试汇总（TASK-FE-005）对教师/管理员可见；
 * 我的考试（TASK-FE-004）、我的成绩与错题本（TASK-FE-005）仅学生可见；
 * 用户管理、审计日志（TASK-FE-006）仅管理员可见。
 * 学生不渲染教师入口（服务端仍强制 RBAC，前端隐藏只影响体验）。
 */
export default function AppLayout() {
  const { user } = useAuthState();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const [loggingOut, setLoggingOut] = useState(false);

  const isStudent = user?.role === 'student';
  const isTeacherOrAdmin = user?.role === 'teacher' || user?.role === 'admin';
  const isAdmin = user?.role === 'admin';

  const menuItems: NonNullable<MenuProps['items']> = [
    { key: '/', label: '工作台' },
    ...(isStudent
      ? [
          { key: '/me/exams', label: '我的考试' },
          { key: '/my-results', label: '我的成绩' },
          { key: '/wrong-questions', label: '错题本' },
        ]
      : []),
    ...(isTeacherOrAdmin
      ? [
          { key: '/questions', label: '题库管理' },
          { key: '/papers', label: '组卷' },
          { key: '/exams', label: '考试管理' },
          { key: '/exam-results', label: '考试汇总' },
        ]
      : []),
    ...(isAdmin
      ? [
          { key: '/users', label: '用户管理' },
          { key: '/audit-logs', label: '审计日志' },
        ]
      : []),
  ];

  const handleLogout = async (): Promise<void> => {
    setLoggingOut(true);
    try {
      await api.post<null>('/auth/logout', undefined, { redirectOn401: false });
    } catch {
      // 会话可能已失效：登出失败也必须清空本地状态
    } finally {
      clearSession();
      queryClient.clear();
      setLoggingOut(false);
      navigate('/login', { replace: true });
    }
  };

  return (
    <div data-testid="app-layout">
      <Layout style={{ minHeight: '100vh' }}>
        <Sider width={216} theme="dark" breakpoint="lg" collapsedWidth={64}>
          <div
            style={{
              height: 56,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: '#fff',
              fontSize: 15,
              fontWeight: 600,
              letterSpacing: 0.5,
            }}
          >
            选择题答题系统
          </div>
          <Menu
            theme="dark"
            mode="inline"
            selectedKeys={[location.pathname]}
            items={menuItems}
            onClick={({ key }) => {
              if (key !== location.pathname) navigate(key);
            }}
          />
        </Sider>

        <Layout>
          <Header
            style={{
              background: '#fff',
              paddingInline: 24,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'flex-end',
              boxShadow: '0 1px 4px rgba(0, 0, 0, 0.06)',
            }}
          >
            <Space size="middle">
              <Avatar style={{ backgroundColor: '#1677ff' }}>
                {user ? user.display_name.slice(0, 1) : '?'}
              </Avatar>
              <Typography.Text strong>{user ? user.display_name : '未登录'}</Typography.Text>
              {user ? <Tag color={roleColor(user.role)}>{ROLE_LABELS[user.role]}</Tag> : null}
              <Button
                size="small"
                danger
                loading={loggingOut}
                onClick={() => {
                  void handleLogout();
                }}
                data-testid="app-logout"
              >
                登出
              </Button>
            </Space>
          </Header>

          <Content style={{ margin: 24 }}>
            <Outlet />
          </Content>
        </Layout>
      </Layout>
    </div>
  );
}
