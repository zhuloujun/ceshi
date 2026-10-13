// 公用小工具：响应、安全响应头、哈希、密码、频率限制、Cookie。
// 所有数据都放在 Cloudflare D1（env.DB），不经过 Modal，不花 Modal 额度。

export const SECURITY_HEADERS = {
  'X-Content-Type-Options': 'nosniff',
  'X-Frame-Options': 'DENY',
  'Referrer-Policy': 'strict-origin-when-cross-origin',
  'Strict-Transport-Security': 'max-age=31536000; includeSubDomains',
  'Permissions-Policy': 'camera=(), microphone=(), geolocation=(), payment=()',
};

// Worker 自己生成的页面（首页、登录、后台）用的内容安全策略：只允许本站脚本和 Cloudflare 人机验证
export const PAGE_CSP = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline' https://challenges.cloudflare.com",
  "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
  "font-src 'self' https://fonts.gstatic.com",
  "img-src 'self' data:",
  "connect-src 'self'",
  "frame-src https://challenges.cloudflare.com",
  "frame-ancestors 'none'",
  "base-uri 'none'",
  "form-action 'self'",
].join('; ');

export function json(obj, status = 200, extra = {}) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', ...SECURITY_HEADERS, ...extra },
  });
}

export function html(body, status = 200, extra = {}) {
  return new Response(body, {
    status,
    headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store', 'Content-Security-Policy': PAGE_CSP,
      ...SECURITY_HEADERS, ...extra },
  });
}

export function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

export function toHex(buf) {
  return Array.from(buf instanceof Uint8Array ? buf : new Uint8Array(buf)).map((b) => b.toString(16).padStart(2, '0')).join('');
}

export function fromHex(hex) {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.substr(i * 2, 2), 16);
  return out;
}

export async function sha256Hex(s) {
  return toHex(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s)));
}

export async function hmacBytes(keyBytes, msg) {
  const k = await crypto.subtle.importKey('raw', keyBytes, { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  return new Uint8Array(await crypto.subtle.sign('HMAC', k, new TextEncoder().encode(msg)));
}

export function b64url(bytes) {
  let s = '';
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export function b64urlText(text) {
  return b64url(new TextEncoder().encode(text));
}

export function constEq(a, b) {
  if (a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

export function randomCode6() {
  const a = new Uint32Array(1);
  crypto.getRandomValues(a);
  return String(a[0] % 1_000_000).padStart(6, '0');
}

export function randomInt(n) {
  const a = new Uint32Array(1);
  crypto.getRandomValues(a);
  return a[0] % n;
}

export function randomToken(bytes = 24) {
  return b64url(crypto.getRandomValues(new Uint8Array(bytes)));
}

// ---------- 密码（PBKDF2-SHA256，Workers 原生支持） ----------
const PBKDF2_ITER = 100_000;

async function pbkdf2(password, salt, iter) {
  const km = await crypto.subtle.importKey('raw', new TextEncoder().encode(password), 'PBKDF2', false, ['deriveBits']);
  return crypto.subtle.deriveBits({ name: 'PBKDF2', salt, iterations: iter, hash: 'SHA-256' }, km, 256);
}

export async function hashPassword(password) {
  const salt = crypto.getRandomValues(new Uint8Array(16));
  return `pbkdf2:${PBKDF2_ITER}:${toHex(salt)}:${toHex(await pbkdf2(password, salt, PBKDF2_ITER))}`;
}

export async function verifyPassword(password, stored) {
  const p = String(stored || '').split(':');
  if (p.length !== 4 || p[0] !== 'pbkdf2') return false;
  return constEq(toHex(await pbkdf2(password, fromHex(p[2]), parseInt(p[1], 10))), p[3]);
}

export const MIN_PASSWORD_LENGTH = 8;

export function passwordProblem(pw, email) {
  if (typeof pw !== 'string' || pw.length < MIN_PASSWORD_LENGTH) return `密码至少 ${MIN_PASSWORD_LENGTH} 位。`;
  if (pw.length > 128) return '密码太长（最多 128 位）。';
  if (!/[A-Za-z]/.test(pw) || !/[0-9]/.test(pw)) return '密码需要同时包含字母和数字。';
  if (email && pw.toLowerCase().includes(String(email).split('@')[0].toLowerCase()) && String(email).split('@')[0].length >= 4)
    return '密码不要包含邮箱用户名。';
  return null;
}

export const EMAIL_RE = /^[^\s@<>"']{1,64}@[^\s@<>"']+\.[^\s@<>"']{2,}$/;
export const normEmail = (e) => String(e || '').trim().toLowerCase();

// ---------- 请求信息 ----------
export const clientIp = (req) => (req.headers.get('CF-Connecting-IP') || '').trim() || 'unknown';

// 防跨站请求伪造：本站接口只接受同源发来的 JSON 请求
export function sameOrigin(req) {
  const url = new URL(req.url);
  const origin = req.headers.get('Origin');
  if (origin) return origin === url.origin;
  const ref = req.headers.get('Referer');
  if (ref) { try { return new URL(ref).origin === url.origin; } catch { return false; } }
  return false;
}

export async function readJson(req) {
  const ct = req.headers.get('Content-Type') || '';
  if (!ct.includes('application/json')) return null;
  try {
    const t = await req.text();
    if (t.length > 200_000) return null;
    return JSON.parse(t);
  } catch { return null; }
}

export function getCookie(req, name) {
  const c = req.headers.get('Cookie') || '';
  for (const part of c.split(';')) {
    const i = part.indexOf('=');
    if (i > 0 && part.slice(0, i).trim() === name) return decodeURIComponent(part.slice(i + 1).trim());
  }
  return null;
}

// ---------- 频率限制（D1 固定窗口计数，一条语句原子完成） ----------
export async function hitRateLimit(env, key, limit, windowSeconds) {
  try {
    const now = Date.now(), win = windowSeconds * 1000;
    const row = await env.DB.prepare(
      `INSERT INTO rate_limits (key, count, window_start) VALUES (?1, 1, ?2)
       ON CONFLICT(key) DO UPDATE SET
         count = CASE WHEN ?2 - rate_limits.window_start < ?3 THEN rate_limits.count + 1 ELSE 1 END,
         window_start = CASE WHEN ?2 - rate_limits.window_start < ?3 THEN rate_limits.window_start ELSE ?2 END
       RETURNING count, window_start`).bind(key, now, win).first();
    if (row && row.count > limit) return { ok: false, retryAfter: Math.max(1, Math.ceil((row.window_start + win - now) / 1000)) };
    return { ok: true };
  } catch (e) {
    console.error('[rateLimit]', e);
    return { ok: true };
  }
}

export const waitText = (s) => (s >= 60 ? `${Math.ceil(s / 60)} 分钟` : `${s} 秒`);

export const LIMITS = {
  otpPerEmail: [10, 3600],
  otpPerIp: [20, 3600],
  authPerIp: [60, 600],      // 登录 / 注册 / 找回密码接口，每个 IP 每 10 分钟
  captchaPerIp: [120, 600],
  detectPerUser: [600, 3600], // 每个账户每小时最多提交检测的次数
};

export async function audit(env, actor, action, detail, ip) {
  try {
    await env.DB.prepare(`INSERT INTO audit_log (at, actor, action, detail, ip) VALUES (?1, ?2, ?3, ?4, ?5)`)
      .bind(Date.now(), actor || null, action, detail ? String(detail).slice(0, 1000) : null, ip || null).run();
  } catch (e) { console.error('[audit]', e); }
}

// ---------- 设置（D1 键值表） ----------
export async function getSetting(env, key, fallback = null) {
  const r = await env.DB.prepare(`SELECT value FROM settings WHERE key = ?1`).bind(key).first();
  return r ? r.value : fallback;
}

export async function setSetting(env, key, value) {
  await env.DB.prepare(`INSERT INTO settings (key, value) VALUES (?1, ?2) ON CONFLICT(key) DO UPDATE SET value = ?2`)
    .bind(key, String(value)).run();
}

export const today = () => new Date(Date.now() + 8 * 3600 * 1000).toISOString().slice(0, 10); // 按北京时间计日
