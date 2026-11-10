import { useEffect, useRef, useState, useSyncExternalStore } from 'react';

import { api } from '../api/client';
import type { MeResponse, Role, User } from '../api/types';
import { clearSession, getAuthState, setSession, subscribe } from '../stores/auth';

/**
 * 认证相关的应用级 hook。
 *
 * 真相来源仍是 `stores/auth.ts`（内存 + sessionStorage 快照）：
 * - sessionStorage 只用于「同一标签页刷新」时避免白屏，**不能**作为登录凭证；
 * - 启动时必须调用 `GET /auth/me` 让服务端 Session（HttpOnly Cookie）判定会话是否有效，
 *   成功后顺带刷新 CSRF token（docs/12-permission-matrix.md §3）。
 */
export type AuthStatus = 'loading' | 'authenticated' | 'anonymous';

export interface AuthSnapshot {
  user: User | null;
  csrfToken: string | null;
  isAuthenticated: boolean;
  role: Role | null;
}

/** 各角色登录后的默认落地页（同时也是 403 页「返回我的主页」的目标）。 */
export const DEFAULT_ROUTE_BY_ROLE: Record<Role, string> = {
  student: '/me/exams',
  teacher: '/questions',
  admin: '/users',
};

export function defaultRouteForRole(role: Role): string {
  return DEFAULT_ROUTE_BY_ROLE[role];
}

/**
 * 引导会话：`GET /auth/me`（Cookie 随请求自动携带）。
 *
 * 必须传 `redirectOn401: false`：启动时「未登录」是**正常状态**，若走 client.ts 的
 * 全局 401 处理会 `navigate('/login')`，而登录页挂载又可能再次触发引导，
 * 形成 /auth/me → 401 → /login → /auth/me 的跳转循环。这里只清空本地状态并返回
 * `anonymous`，由路由守卫决定是否渲染登录页（只跳一次，不循环）。
 */
export async function bootstrapSession(): Promise<AuthStatus> {
  try {
    const me = await api.get<MeResponse>('/auth/me', { redirectOn401: false });
    // 账号停用（AC-04）：即使服务端误返 200，也不在前端建立会话。
    if (!me.user.is_active) {
      clearSession();
      return 'anonymous';
    }
    setSession(me.user, me.csrf_token);
    return 'authenticated';
  } catch {
    // 401（未登录/会话过期/账号停用）与网络错误一律视为匿名：清掉快照，
    // 避免把过期用户展示成已登录；后续业务请求若再收到 401 由 client.ts 统一处理。
    clearSession();
    return 'anonymous';
  }
}

/**
 * 应用根组件调用一次：挂载时引导会话，返回引导状态。
 *
 * 用 ref 防止 React 18 StrictMode 的 effect 双调用重复请求 /auth/me。
 * 引导完成前根组件渲染全屏 loading（data-testid=auth-loading），
 * 避免受保护页面在「尚未确认会话」时先闪一下登录页。
 */
export function useBootstrapAuth(): AuthStatus {
  const [status, setStatus] = useState<AuthStatus>('loading');
  const startedRef = useRef(false);

  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    // 组件卸载后 setStatus 在 React 18 中是安全的 no-op；StrictMode 下实例不会真正卸载。
    void bootstrapSession().then(setStatus);
  }, []);

  return status;
}

/** 组件内订阅认证状态（登录/登出/401 清空都会触发重渲染）。 */
export function useAuth(): AuthSnapshot {
  const state = useSyncExternalStore(subscribe, getAuthState, getAuthState);
  return {
    user: state.user,
    csrfToken: state.csrfToken,
    isAuthenticated: state.user !== null,
    role: state.user?.role ?? null,
  };
}
