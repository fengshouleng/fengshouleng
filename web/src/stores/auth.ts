import { useSyncExternalStore } from 'react';

import type { Role, User } from '../api/types';

/**
 * 认证状态：内存为唯一真相，sessionStorage 仅用于「同一标签页刷新」恢复。
 * HttpOnly Cookie（session_id）由浏览器持有，JS 不可读，因此这里不存任何凭证，
 * 只存 user 快照与 CSRF token（docs/10-arch.md ADR-004）。
 */
export const AUTH_STORAGE_KEY = 'mcq.auth.session';

export interface AuthState {
  user: User | null;
  csrfToken: string | null;
}

const EMPTY_STATE: AuthState = { user: null, csrfToken: null };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function isRole(value: unknown): value is Role {
  return value === 'student' || value === 'teacher' || value === 'admin';
}

function isUser(value: unknown): value is User {
  if (!isRecord(value)) return false;
  return (
    typeof value.id === 'number' &&
    typeof value.username === 'string' &&
    typeof value.display_name === 'string' &&
    isRole(value.role) &&
    typeof value.is_active === 'boolean' &&
    typeof value.created_at === 'string'
  );
}

function loadFromStorage(): AuthState {
  if (typeof window === 'undefined') return EMPTY_STATE;
  try {
    const raw = window.sessionStorage.getItem(AUTH_STORAGE_KEY);
    if (!raw) return EMPTY_STATE;
    const parsed: unknown = JSON.parse(raw);
    if (!isRecord(parsed)) return EMPTY_STATE;
    const user = isUser(parsed.user) ? parsed.user : null;
    const csrfToken =
      typeof parsed.csrfToken === 'string' && parsed.csrfToken !== '' ? parsed.csrfToken : null;
    if (!user) return EMPTY_STATE;
    return { user, csrfToken };
  } catch {
    // sessionStorage 不可用（隐私模式 / 配额满）时退化为纯内存
    return EMPTY_STATE;
  }
}

let state: AuthState = loadFromStorage();
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

function persist(): void {
  if (typeof window === 'undefined') return;
  try {
    if (!state.user) {
      window.sessionStorage.removeItem(AUTH_STORAGE_KEY);
      return;
    }
    window.sessionStorage.setItem(
      AUTH_STORAGE_KEY,
      JSON.stringify({ user: state.user, csrfToken: state.csrfToken }),
    );
  } catch {
    // 持久化失败不影响内存中的会话
  }
}

export function getAuthState(): AuthState {
  return state;
}

export function getCsrfToken(): string | null {
  return state.csrfToken;
}

export function getCurrentUser(): User | null {
  return state.user;
}

export function isAuthenticated(): boolean {
  return state.user !== null;
}

export function hasRole(...roles: Role[]): boolean {
  const current = state.user;
  return current !== null && roles.includes(current.role);
}

/** 登录成功 / `GET /auth/me` 成功时写入（csrf_token 用于后续非 GET 请求）。 */
export function setSession(user: User, csrfToken: string | null): void {
  state = { user, csrfToken };
  persist();
  emit();
}

/** 仅刷新 CSRF token（会话续期等场景）。 */
export function setCsrfToken(csrfToken: string | null): void {
  state = { ...state, csrfToken };
  persist();
  emit();
}

/** 401 或主动登出时调用：清空内存与 sessionStorage。 */
export function clearSession(): void {
  state = EMPTY_STATE;
  if (typeof window !== 'undefined') {
    try {
      window.sessionStorage.removeItem(AUTH_STORAGE_KEY);
    } catch {
      // 忽略：内存已清空
    }
  }
  emit();
}

export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** 组件内订阅认证状态（AppLayout 等）。 */
export function useAuthState(): AuthState {
  return useSyncExternalStore(subscribe, getAuthState, getAuthState);
}
