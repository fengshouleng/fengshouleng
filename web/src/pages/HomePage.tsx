import { Card, Space, Tag, Typography } from 'antd';

import { ROLE_LABELS } from '../api/types';
import { useAuthState } from '../stores/auth';

/**
 * 工作台占位页：脚手架阶段只展示当前登录身份与下一步交付说明。
 * 真正的业务首页（各角色仪表盘）随 TASK-FE-002 起的页面任务一起实现。
 */
export default function HomePage() {
  const { user } = useAuthState();

  return (
    <div data-testid="home-page">
      <Card title="工作台">
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <Typography.Paragraph style={{ marginBottom: 0 }}>
            欢迎{user ? `，${user.display_name}` : ''}。前端脚手架已就绪：登录、受保护布局、路由骨架、
            统一请求层（Cookie / CSRF / 401 / 错误码）与本页。
          </Typography.Paragraph>
          <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
            题库、组卷、考试与管理页面将在后续任务中接入，所有请求统一走
            <Typography.Text code>src/api/client.ts</Typography.Text>。
          </Typography.Paragraph>
          {user ? (
            <Space size="small" wrap>
              <Typography.Text type="secondary">账号</Typography.Text>
              <Typography.Text>{user.username}</Typography.Text>
              <Tag color="blue">{ROLE_LABELS[user.role]}</Tag>
            </Space>
          ) : null}
        </Space>
      </Card>
    </div>
  );
}
