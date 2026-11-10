import type { ReactNode } from 'react';
import { Result, Typography } from 'antd';

import { ROLE_LABELS } from '../../api/types';
import { useAuth } from '../../hooks/useAuth';

/**
 * 管理后台的**防御性**角色守卫（TASK-FE-006）。
 *
 * 正常路径上 `/users`、`/audit-logs` 已被路由层 `RequireRole roles={['admin']}` 拦住；
 * 这里再做一次页面级校验，避免将来有人把管理页挂到别的路由/布局下时，非 admin
 * 直接看到（并触发）账号管理与审计查询。服务端仍强制 RBAC（docs/12 开篇），
 * 前端守卫只影响体验，不构成安全边界。
 *
 * 刻意不依赖 react-router：页面组件测试可以脱离 Router 直接渲染。
 *
 * data-testid：admin-forbidden
 */
export default function AdminGuard({ children }: { children: ReactNode }) {
  const { user } = useAuth();

  if (!user || user.role !== 'admin') {
    return (
      <div data-testid="admin-forbidden">
        <Result
          status="403"
          title="403"
          subTitle={
            user
              ? `当前账号（${ROLE_LABELS[user.role]}）无权访问管理后台，仅管理员可管理账号与审计日志。`
              : '请先登录后再访问管理后台。'
          }
        >
          <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
            如需访问，请联系管理员调整账号角色。
          </Typography.Paragraph>
        </Result>
      </div>
    );
  }

  return <>{children}</>;
}
