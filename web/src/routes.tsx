import type { ReactNode } from 'react';
import { Spin, Typography } from 'antd';
import { createBrowserRouter, Navigate, Outlet, useLocation } from 'react-router-dom';
import type { RouteObject } from 'react-router-dom';

import { loginRedirectUrl } from './api/client';
import AppLayout from './components/AppLayout';
import RequireRole from './components/RequireRole';
import { useAuth, useBootstrapAuth } from './hooks/useAuth';
import AuditLogsPage from './pages/admin/AuditLogsPage';
import UsersPage from './pages/admin/UsersPage';
import ExamPage from './pages/exam/ExamPage';
import HomePage from './pages/HomePage';
import LoginPage from './pages/login/LoginPage';
import NotFound from './pages/NotFound';
import PaperDetailPage from './pages/papers/PaperDetailPage';
import PaperEditorPage from './pages/papers/PaperEditorPage';
import PapersPage from './pages/papers/PapersPage';
import QuestionsPage from './pages/questions/QuestionsPage';
import MyExamsPage from './pages/student/MyExamsPage';
import MyResultsPage from './pages/result/MyResultsPage';
import ResultPage from './pages/result/ResultPage';
import TeacherExamResultsPage from './pages/teacher/TeacherExamResultsPage';
import ExamManagePage from './pages/teacher/exams/ExamManagePage';
import WrongQuestionsPage from './pages/wrong/WrongQuestionsPage';

/**
 * 启动引导：应用挂载即调用 GET /auth/me 恢复会话（Cookie 由 client.ts 统一携带）。
 * 在结果返回前渲染全屏 loading，避免未确认会话时先闪登录页/受保护页。
 * 401 由 useAuth.bootstrapSession 内部清空会话并落为 anonymous，不会触发全局跳转循环。
 */
function AuthGate() {
  const status = useBootstrapAuth();

  if (status === 'loading') {
    return (
      <div
        data-testid="auth-loading"
        style={{
          minHeight: '100vh',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 16,
          background: '#f5f6f8',
        }}
      >
        <Spin size="large" />
        <Typography.Text type="secondary">正在恢复登录状态…</Typography.Text>
      </div>
    );
  }

  return <Outlet />;
}

/** 未登录统一跳登录页，并带上站内 returnTo（供登录成功后原路返回）。 */
function RequireAuth({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const location = useLocation();

  if (!user) {
    const returnTo = `${location.pathname}${location.search}`;
    return <Navigate to={loginRedirectUrl(returnTo)} replace />;
  }
  return <>{children}</>;
}

/**
 * 路由表（同时导出给组件测试用 createMemoryRouter 复用，保证测的就是真实路由）。
 *
 * - /login 公开（AuthGate 下，未登录时正常渲染）；
 * - / 由 RequireAuth 包住 AppLayout；
 * - 业务路由按角色分组：student（考试/成绩）、teacher（题库/组卷/考试管理）、
 *   admin（用户/审计）。管理员拥有全量可见权限（docs/12-permission-matrix.md §1），
 *   因此教师路由组额外放行 admin。
 */
export const routes: RouteObject[] = [
  {
    element: <AuthGate />,
    children: [
      {
        path: '/login',
        element: <LoginPage />,
      },
      {
        path: '/',
        element: (
          <RequireAuth>
            <AppLayout />
          </RequireAuth>
        ),
        children: [
          { index: true, element: <HomePage /> },
          {
            element: <RequireRole roles={['student']} />,
            children: [
              {
                // 我的考试（TASK-FE-004）：服务器校时 + 会话状态 + 成绩
                path: 'me/exams',
                element: <MyExamsPage />,
              },
              {
                // 任务卡约定入口，与 /me/exams 并存（旧菜单保持兼容）
                path: 'my-exams',
                element: <MyExamsPage />,
              },
              {
                // 考试作答页：进入即 POST 创建/恢复会话（TASK-FE-004 关键路径）
                path: 'exams/:examId',
                element: <ExamPage />,
              },
              {
                // 兼容旧入口：成绩列表统一放在 /my-results（TASK-FE-005）
                path: 'me/results',
                element: <Navigate to="/my-results" replace />,
              },
              {
                path: 'my-results',
                element: <MyResultsPage />,
              },
              {
                path: 'wrong-questions',
                element: <WrongQuestionsPage />,
              },
            ],
          },
          {
            // 逐题成绩：学生本人；admin 按权限矩阵可读全部。
            // 教师不可读单会话结果（v1.0.2 CHG-030），命中守卫 403 而不是 404。
            element: <RequireRole roles={['student', 'admin']} />,
            children: [
              {
                path: 'results/:sessionId',
                element: <ResultPage />,
              },
            ],
          },
          {
            // 管理员按权限矩阵可查看教师侧题库/组卷/考试
            element: <RequireRole roles={['teacher', 'admin']} />,
            children: [
              {
                path: 'questions',
                element: <QuestionsPage />,
              },
              {
                path: 'papers',
                element: <PapersPage />,
              },
              {
                // 组卷：new 必须在 :paperId 之前，React Router v6 按段排序静态优先
                path: 'papers/new',
                element: <PaperEditorPage />,
              },
              {
                path: 'papers/:paperId/edit',
                element: <PaperEditorPage />,
              },
              {
                path: 'papers/:paperId',
                element: <PaperDetailPage />,
              },
              {
                // 考试管理（TASK-FE-007）：创建/发布/关闭考试 + 状态筛选列表
                path: 'exams',
                element: <ExamManagePage />,
              },
              {
                // 考试汇总：教师选择自己的考试（TASK-FE-005）
                path: 'exam-results',
                element: <TeacherExamResultsPage />,
              },
              {
                path: 'exam-results/:examId',
                element: <TeacherExamResultsPage />,
              },
            ],
          },
          {
            element: <RequireRole roles={['admin']} />,
            children: [
              {
                // 用户管理（TASK-FE-006）：列表/筛选/创建/改角色/停用启用/重置密码
                path: 'users',
                element: <UsersPage />,
              },
              {
                // 审计日志（TASK-FE-006）：只读列表 + action/actor/时间范围筛选 + 分页
                path: 'audit-logs',
                element: <AuditLogsPage />,
              },
            ],
          },
        ],
      },
      {
        // 未匹配路径统一落到 404，不把用户丢进空白页
        path: '*',
        element: <NotFound />,
      },
    ],
  },
];

export const router = createBrowserRouter(routes);
