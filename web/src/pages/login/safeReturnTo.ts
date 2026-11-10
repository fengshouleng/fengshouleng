/**
 * `returnTo` 清洗：只允许站内绝对路径，防止开放重定向（Open Redirect）。
 *
 * 攻击面来自 `/login?returnTo=...`：登录成功后若直接 `navigate(returnTo)`，
 * 攻击者可构造 `/login?returnTo=https://evil.com` 或 `//evil.com` 把刚登录的用户送去钓鱼站。
 *
 * 规则（宁可回退到角色默认页，也不冒险放行）：
 * - 必须是以单个 `/` 开头的站内路径；
 * - 拒绝 `//host`、`/\host`（浏览器会把反斜杠归一化成正斜杠）等协议相对写法；
 * - 拒绝任何反斜杠与控制字符（换行/制表符可用于绕过前缀检查）；
 * - 拒绝指回 `/login` 的路径，避免登录成功后原地打转。
 */

const FALLBACK = '/';

/** 控制字符（换行/制表符等）可用于绕过前缀检查，一律拒绝；避开正则以免触发 no-control-regex。 */
function hasControlChars(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code < 0x20 || code === 0x7f) return true;
  }
  return false;
}

function isSafeInternalPath(value: string): boolean {
  if (!value.startsWith('/')) return false;
  if (value.startsWith('//')) return false;
  if (value.includes('\\')) return false;
  if (hasControlChars(value)) return false;
  // 不把用户送回登录页（含 /login?returnTo=... 自引用）
  if (/^\/login(?:[/?#]|$)/.test(value)) return false;
  return true;
}

/**
 * @param raw      来自 query 的原始 returnTo（可能为 null）
 * @param fallback 非法/缺失时的兜底站内路径，默认 `/`；非法 fallback 再退回 `/`
 */
export function safeReturnTo(raw: string | null | undefined, fallback = FALLBACK): string {
  const safeFallback = isSafeInternalPath(fallback) ? fallback : FALLBACK;
  if (raw === null || raw === undefined) return safeFallback;

  const value = raw.trim();
  if (value === '') return safeFallback;
  return isSafeInternalPath(value) ? value : safeFallback;
}

/** 供单测/调试使用：判断一个字符串能否安全用于站内跳转。 */
export { isSafeInternalPath };
