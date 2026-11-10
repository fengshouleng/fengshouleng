import { useState } from 'react';
import { Alert, Button, Card, Form, Input, Typography } from 'antd';
import { useNavigate, useSearchParams } from 'react-router-dom';

import { ApiError, api } from '../../api/client';
import { ERROR_CODES } from '../../api/types';
import type { LoginRequest, LoginResponse } from '../../api/types';
import { defaultRouteForRole } from '../../hooks/useAuth';
import { setSession } from '../../stores/auth';
import { safeReturnTo } from './safeReturnTo';

interface LoginFormValues {
  username: string;
  password: string;
}

/**
 * 登录失败文案：`AUTH_INVALID_CREDENTIALS`（以及兜底的 401）一律显示
 * 「用户名或密码错误」，**不区分账号是否存在**，避免账号枚举
 * （contracts/openapi.yaml#/paths/~1auth~1login、docs/12-permission-matrix.md §1）。
 */
function loginErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === ERROR_CODES.AUTH_INVALID_CREDENTIALS || error.status === 401) {
      return '用户名或密码错误';
    }
    return error.message || '登录失败，请稍后重试';
  }
  return '登录失败，请稍后重试';
}

/**
 * 登录页。
 *
 * - 请求一律走 client.ts：登录接口免 CSRF（`csrf: false`），且 401 表示
 *   「凭证错误」而非「会话失效」，因此必须 `redirectOn401: false`，
 *   否则会被全局 401 处理器跳去登录页（本页）。
 * - 成功后优先回跳 `returnTo`（仅站内路径，见 safeReturnTo.ts），
 *   否则按角色进入默认落地页。
 * - data-testid：login-page / login-username / login-password / login-submit / login-error
 */
export default function LoginPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [submitting, setSubmitting] = useState(false);
  const [errorText, setErrorText] = useState<string | null>(null);

  const submit = async (values: LoginFormValues): Promise<void> => {
    if (submitting) return;
    setSubmitting(true);
    setErrorText(null);
    const payload: LoginRequest = {
      username: values.username.trim(),
      password: values.password,
    };

    try {
      const result = await api.post<LoginResponse>('/auth/login', payload, {
        csrf: false,
        redirectOn401: false,
      });
      setSession(result.user, result.csrf_token);
      const target = safeReturnTo(
        searchParams.get('returnTo'),
        defaultRouteForRole(result.user.role),
      );
      navigate(target, { replace: true });
    } catch (error) {
      setErrorText(loginErrorMessage(error));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      data-testid="login-page"
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: '#f0f2f5',
        padding: 24,
      }}
    >
      <Card style={{ width: 400, boxShadow: '0 6px 24px rgba(0, 0, 0, 0.08)' }}>
        <Typography.Title level={4} style={{ textAlign: 'center', marginBottom: 8 }}>
          选择题答题系统
        </Typography.Title>
        <Typography.Paragraph type="secondary" style={{ textAlign: 'center', marginBottom: 24 }}>
          请使用学校下发的账号登录
        </Typography.Paragraph>

        {errorText ? (
          <div data-testid="login-error" role="alert" style={{ marginBottom: 16 }}>
            <Alert type="error" showIcon message={errorText} />
          </div>
        ) : null}

        <Form<LoginFormValues>
          layout="vertical"
          requiredMark={false}
          initialValues={{ username: '', password: '' }}
          onValuesChange={() => {
            setErrorText(null);
          }}
          onFinish={(values) => {
            void submit(values);
          }}
        >
          <Form.Item
            label="用户名"
            name="username"
            rules={[{ required: true, message: '请输入用户名' }]}
          >
            <Input
              size="large"
              autoComplete="username"
              placeholder="请输入用户名"
              maxLength={64}
              disabled={submitting}
              data-testid="login-username"
            />
          </Form.Item>

          <Form.Item label="密码" name="password" rules={[{ required: true, message: '请输入密码' }]}>
            <Input.Password
              size="large"
              autoComplete="current-password"
              placeholder="请输入密码"
              maxLength={128}
              disabled={submitting}
              data-testid="login-password"
            />
          </Form.Item>

          <Form.Item style={{ marginBottom: 8 }}>
            <Button
              type="primary"
              htmlType="submit"
              size="large"
              block
              loading={submitting}
              data-testid="login-submit"
            >
              登录
            </Button>
          </Form.Item>
        </Form>

        <Typography.Paragraph
          type="secondary"
          style={{ fontSize: 12, textAlign: 'center', marginBottom: 0 }}
        >
          登录态由服务端 Session（HttpOnly Cookie）维护，有效期 8 小时。
        </Typography.Paragraph>
      </Card>
    </div>
  );
}
