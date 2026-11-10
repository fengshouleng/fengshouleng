import type { ReactNode } from 'react';
import { Button, Result, Space, Typography } from 'antd';
import { Navigate, Outlet, useLocation, useNavigate } from 'react-router-dom';

import { loginRedirectUrl } from '../api/client';
import { ROLE_LABELS } from '../api/types';
import type { Role } from '../api/types';
import { defaultRouteForRole, useAuth } from '../hooks/useAuth';

export interface RequireRoleProps {
  /** 允许访问的角色白名单。管理员是超集权限，需要访问教师页时应显式加入 'admin'。 */
  roles: readonly Role[];
  /** 作为包裹组件使用；省略时按 pathless layout 渲染 <Outlet />。 */
  children?: ReactNode;
}

/**
 * 角色路由守卫（RBAC 前端层，仅用于体验；服务端仍强制校验，见 docs/12 开篇）。
 *
 * 无权限时的选择：**渲染 403 结果页，而不是自动重定向到角色默认页**。
 * 理由：
 * 1. 自动重定向会掩盖越权事实，用户不知道「为什么被弹走」，也不利于 QA 定位；
 * 2. 多个守卫/布局嵌套时，重定向链容易互相触发形成循环；
 * 3. 保留原 URL 便于把问题（含 returnTo）直接反馈给管理员/支持人员。
 * 403 页提供「返回我的主页」按钮，用户仍可一键离开。
 */
export default function RequireRole({ roles, children }: RequireRoleProps) {
  const { user } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();

  // 兜底：正常情况下外层 RequireAuth 已拦截，这里再防一次直接使用 RequireRole 的场景。
  if (!user) {
    const returnTo = `${location.pathname}${location.search}`;
    return <Navigate to={loginRedirectUrl(returnTo)} replace />;
  }

  if (!roles.includes(user.role)) {
    return (
      <div data-testid="forbidden-page" style={{ padding: 48 }}>
        <Result
          status="403"
          title="403"
          subTitle={`当前账号（${ROLE_LABELS[user.role]}）无权访问该页面。`}
          extra={
            <Space>
              <Button
                type="primary"
                data-testid="forbidden-home"
                onClick={() => {
                  navigate(defaultRouteForRole(user.role), { replace: true });
                }}
              >
                返回我的主页
              </Button>
              <Button
                data-testid="forbidden-back"
                onClick={() => {
                  navigate(-1);
                }}
              >
                返回上一页
              </Button>
            </Space>
          }
        >
          <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
            如需访问，请联系管理员调整账号角色。
          </Typography.Paragraph>
        </Result>
      </div>
    );
  }

  return <>{children ?? <Outlet />}</>;
}
