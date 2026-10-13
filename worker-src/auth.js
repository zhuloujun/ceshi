// 注册 / 登录 / 找回密码 / 修改密码 / 管理员登录：人机验证（算术题 + 可选 Cloudflare Turnstile）、
// 邮箱验证码（Resend）、PBKDF2 密码、D1 会话、登录失败锁定、频率限制。
import {
  json, esc, sha256Hex, hmacBytes, toHex, randomCode6, randomInt, randomToken, hashPassword, verifyPassword,
  passwordProblem, EMAIL_RE, normEmail, clientIp, sameOrigin, readJson, getCookie, hitRateLimit, waitText, LIMITS,
  audit, getSetting, setSetting, fromHex,
} from './util.js';
import { langOf } from './i18n.js';

export const SESSION_COOKIE = 'atc_session';
const USER_SESSION_MS = 30 * 24 * 3600 * 1000;
const ADMIN_SESSION_MS = 12 * 3600 * 1000;
export const ELEVATE_MS = 10 * 60 * 1000;      // 管理员通过邮箱验证码确认后，10 分钟内的后台修改无需再次输入验证码
const OTP_TTL_MS = 10 * 60 * 1000;
const OTP_MAX_ATTEMPTS = 5;
const OTP_COOLDOWN_MS = 60 * 1000;
const MAX_LOGIN_ATTEMPTS = 5;
const LOCK_WINDOW_MS = 10 * 60 * 1000;
const LOCK_MS = 30 * 60 * 1000;

const DUMMY_HASH = 'pbkdf2:100000:00000000000000000000000000000000:0000000000000000000000000000000000000000000000000000000000000000';

export const adminEmail = (env) => normEmail(env.ADMIN_EMAIL || '');

// ---------- 人机验证：算术题（答案只存哈希在 D1，用一次即作废） ----------
// 字符画成折线（不是 <text> 文字），每次随机抖动、旋转、加干扰线：程序不能直接从图片源码里读出题目
const GLYPH = {   // 7 段式笔画，坐标在 0..10 × 0..16 的格子里
  '0': [[0, 0, 10, 0, 10, 16, 0, 16, 0, 0]], '1': [[3, 3, 6, 0, 6, 16]], '2': [[0, 0, 10, 0, 10, 8, 0, 8, 0, 16, 10, 16]],
  '3': [[0, 0, 10, 0, 10, 16, 0, 16], [0, 8, 10, 8]], '4': [[0, 0, 0, 8, 10, 8], [10, 0, 10, 16]],
  '5': [[10, 0, 0, 0, 0, 8, 10, 8, 10, 16, 0, 16]], '6': [[10, 0, 0, 0, 0, 16, 10, 16, 10, 8, 0, 8]],
  '7': [[0, 0, 10, 0, 10, 16]], '8': [[0, 0, 10, 0, 10, 16, 0, 16, 0, 0], [0, 8, 10, 8]], '9': [[10, 8, 0, 8, 0, 0, 10, 0, 10, 16, 0, 16]],
  '+': [[1, 8, 9, 8], [5, 4, 5, 12]], '-': [[1, 8, 9, 8]], '×': [[1, 4, 9, 12], [9, 4, 1, 12]], '=': [[1, 6, 9, 6], [1, 11, 9, 11]],
  '?': [[0, 2, 2, 0, 9, 0, 10, 2, 10, 6, 5, 9, 5, 11], [5, 14, 5, 16]],
};

function captchaSvg(text) {
  const w = 200, h = 50, j = () => (randomInt(21) - 10) / 10;
  let s = `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><rect width="${w}" height="${h}" fill="#f4efe4"/>`;
  for (let i = 0; i < 7; i++)
    s += `<path d="M${randomInt(w)} ${randomInt(h)} Q${randomInt(w)} ${randomInt(h)} ${randomInt(w)} ${randomInt(h)}" stroke="#${['8a6d3b', 'b0413e', '2f6f6a', '777'][randomInt(4)]}" stroke-width="1.2" fill="none" opacity=".6"/>`;
  let x = 8;
  for (const ch of text) {
    if (ch === ' ') { x += 5; continue; }
    const g = GLYPH[ch];
    if (!g) continue;
    const sc = 1.25 + randomInt(4) / 10, y0 = 8 + randomInt(9), rot = randomInt(21) - 10;
    const d = g.map((pl) => 'M' + pl.reduce((acc, v, i) => acc.concat(i % 2 ? [] : [`${(x + pl[i] * sc + j()).toFixed(1)} ${(y0 + pl[i + 1] * sc * 0.95 + j()).toFixed(1)}`]), []).join(' L')).join(' ');
    s += `<path d="${d}" stroke="#2b2620" stroke-width="${(2.2 + randomInt(8) / 10).toFixed(1)}" fill="none" stroke-linecap="round" stroke-linejoin="round" transform="rotate(${rot} ${x + 8} ${y0 + 13})"/>`;
    x += 10 * sc + 5 + randomInt(3);
  }
  for (let i = 0; i < 30; i++) s += `<circle cx="${randomInt(w)}" cy="${randomInt(h)}" r="${(0.6 + randomInt(8) / 10).toFixed(1)}" fill="#555" opacity=".5"/>`;
  return s + '</svg>';
}

async function newCaptcha(env, req) {
  const rl = await hitRateLimit(env, 'cap:' + clientIp(req), ...LIMITS.captchaPerIp);
  if (!rl.ok) return json({ ok: false, message: `操作太频繁，请 ${waitText(rl.retryAfter)}后再试。` }, 429);
  // 答案范围约 0–200，乱猜一次只有约 1/150 的机会；每题只能答一次
  const kind = randomInt(3);
  let a = 11 + randomInt(89), b = 11 + randomInt(89), q, ans;
  if (kind === 0) { q = `${a} + ${b} = ?`; ans = a + b; }
  else if (kind === 1) { if (b > a) [a, b] = [b, a]; q = `${a} - ${b} = ?`; ans = a - b; }
  else { a = 3 + randomInt(17); b = 3 + randomInt(7); q = `${a} × ${b} = ?`; ans = a * b; }
  const id = randomToken(16);
  await env.DB.prepare(`INSERT INTO captchas (id, answer_hash, expires_at, used) VALUES (?1, ?2, ?3, 0)`)
    .bind(id, await sha256Hex(id + ':' + ans), Date.now() + 5 * 60 * 1000).run();
  // 顺手清理过期记录
  await env.DB.prepare(`DELETE FROM captchas WHERE expires_at < ?1`).bind(Date.now() - 3600 * 1000).run();
  return json({ ok: true, id, image: 'data:image/svg+xml;base64,' + btoa(captchaSvg(q)),
    turnstile_site_key: env.TURNSTILE_SITE_KEY && env.TURNSTILE_SECRET_KEY ? env.TURNSTILE_SITE_KEY : null });
}

async function checkHuman(env, req, body) {
  const id = String(body.captcha_id || ''), ans = String(body.captcha_answer || '').trim();
  if (!id || !/^-?\d{1,4}$/.test(ans)) return '请先回答图片里的算术题。';
  const row = await env.DB.prepare(`SELECT answer_hash, expires_at, used FROM captchas WHERE id = ?1`).bind(id).first();
  // 不论对错都作废这一题（防止反复试答案）
  const used = await env.DB.prepare(`UPDATE captchas SET used = 1 WHERE id = ?1 AND used = 0`).bind(id).run();
  if (!row || !used.meta?.changes || row.expires_at < Date.now()) return '算术题已过期，请换一题。';
  if (row.answer_hash !== await sha256Hex(id + ':' + parseInt(ans, 10))) return '算术题答案不对，请换一题再试。';
  if (env.TURNSTILE_SITE_KEY && env.TURNSTILE_SECRET_KEY) {
    const token = String(body.turnstile_token || '');
    if (!token) return '请完成人机验证。';
    const fd = new FormData();
    fd.append('secret', env.TURNSTILE_SECRET_KEY);
    fd.append('response', token);
    fd.append('remoteip', clientIp(req));
    const r = await fetch('https://challenges.cloudflare.com/turnstile/v0/siteverify', { method: 'POST', body: fd })
      .then((x) => x.json()).catch(() => ({}));
    if (!r.success) return '人机验证未通过，请刷新后重试。';
  }
  return null;
}

// ---------- 邮件（Resend） ----------
const PURPOSES = {
  register: ['注册验证码', '您正在注册 AI 文本检测网站账户', 'Sign-up code', 'You are creating an account on the AI text detection site'],
  reset_password: ['找回密码验证码', '您正在重设 AI 文本检测网站的登录密码', 'Password reset code', 'You are resetting your password on the AI text detection site'],
  change_password: ['修改密码验证码', '您正在修改 AI 文本检测网站的登录密码', 'Password change code', 'You are changing your password on the AI text detection site'],
  admin_login: ['管理员登录验证码', '有人正在登录 AI 文本检测网站的管理后台', 'Administrator login code', 'Someone is logging in to the admin console of the AI text detection site'],
  admin_reset_password: ['管理员设置 / 找回密码验证码', '您正在设置或重设 AI 文本检测网站的管理员密码', 'Administrator password setup / reset code', 'You are setting or resetting the administrator password of the AI text detection site'],
  admin_elevate: ['后台修改确认码', '您正在修改 AI 文本检测网站的后台设置或用户数据', 'Admin change confirmation code', 'You are changing settings or user data in the admin console of the AI text detection site'],
};

export async function sendEmail(env, to, subject, htmlBody) {
  // 只在本机测试（.dev.vars 里同时设置 DEV_LOCAL=1 和 DEV_EMAIL_SINK=1）时把邮件写进 dev_mail 表，不真正发送
  if (env.DEV_LOCAL === '1' && env.DEV_EMAIL_SINK === '1') {
    await env.DB.prepare(`CREATE TABLE IF NOT EXISTS dev_mail (at INTEGER, to_addr TEXT, subject TEXT, body TEXT)`).run();
    await env.DB.prepare(`INSERT INTO dev_mail VALUES (?1, ?2, ?3, ?4)`).bind(Date.now(), to, subject, htmlBody).run();
    return;
  }
  if (!env.RESEND_API_KEY) throw new Error('未设置 RESEND_API_KEY');
  const r = await fetch('https://api.resend.com/emails', {
    method: 'POST',
    headers: { Authorization: `Bearer ${env.RESEND_API_KEY}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ from: env.RESEND_FROM_EMAIL || 'Shendu AI Text Detection <noreply@9369369.xyz>', to, subject, html: htmlBody }),
  });
  if (!r.ok) throw new Error(`邮件发送失败（${r.status}）：${(await r.text().catch(() => '')).slice(0, 200)}`);
}

class OtpError extends Error { constructor(msg, status = 429) { super(msg); this.status = status; } }

export async function requestOtp(env, req, email, purpose) {
  email = normEmail(email);
  const now = Date.now();
  const recent = await env.DB.prepare(`SELECT created_at FROM otp_codes WHERE email = ?1 AND purpose = ?2 ORDER BY created_at DESC LIMIT 1`)
    .bind(email, purpose).first();
  if (recent && now - recent.created_at < OTP_COOLDOWN_MS)
    throw new OtpError(`请等待 ${Math.ceil((OTP_COOLDOWN_MS - (now - recent.created_at)) / 1000)} 秒后再重新发送验证码。`);
  for (const [k, rule] of [[`otp-e:${purpose}:${email}`, LIMITS.otpPerEmail], [`otp-ip:${clientIp(req)}`, LIMITS.otpPerIp],
    ['otp-all-h', LIMITS.otpGlobalHour], ['otp-all-d', LIMITS.otpGlobalDay]]) {
    const rl = await hitRateLimit(env, k, ...rule);
    if (!rl.ok) throw new OtpError(`发送验证码过于频繁，请 ${waitText(rl.retryAfter)}后再试。`);
  }
  const code = randomCode6();
  await env.DB.prepare(`INSERT INTO otp_codes (id, email, code_hash, purpose, expires_at, consumed, attempts, created_at)
     VALUES (?1, ?2, ?3, ?4, ?5, 0, 0, ?6)`).bind(crypto.randomUUID(), email, await sha256Hex(code), purpose, now + OTP_TTL_MS, now).run();
  await env.DB.prepare(`DELETE FROM otp_codes WHERE created_at < ?1`).bind(now - 7 * 24 * 3600 * 1000).run();
  const [subject, what, subjectEn, whatEn] = PURPOSES[purpose];
  const en = langOf(req) === 'en';
  // 邮件按用户当前选择的界面语言发送，另附另一种语言的说明
  const zhPart = `${esc(what)}。<br>验证码：<b style="font-size:22px;letter-spacing:3px">${code}</b><br>10 分钟内有效，只能使用一次。<br><span style="color:#888">如果这不是您本人的操作，请忽略此邮件；验证码不要告诉任何人（包括自称客服的人）。</span>`;
  const enPart = `${esc(whatEn)}.<br>Code: <b style="font-size:22px;letter-spacing:3px">${code}</b><br>Valid for 10 minutes, single use.<br><span style="color:#888">If this was not you, please ignore this email. Never share the code with anyone (including people claiming to be support).</span>`;
  await sendEmail(env, email, en ? `AI Text Detection · ${subjectEn}` : `AI 文本检测 · ${subject}`,
    `<div style="font-family:sans-serif;font-size:15px;line-height:1.8">${en ? enPart : zhPart}<hr style="border:0;border-top:1px solid #ddd;margin:16px 0">${en ? zhPart : enPart}</div>`);
}

export async function verifyOtp(env, email, purpose, code) {
  email = normEmail(email);
  const row = await env.DB.prepare(`SELECT id, code_hash, expires_at, consumed, attempts FROM otp_codes
     WHERE email = ?1 AND purpose = ?2 ORDER BY created_at DESC LIMIT 1`).bind(email, purpose).first();
  if (!row) return '请先获取验证码。';
  if (row.consumed) return '验证码已使用，请重新获取。';
  if (Date.now() > row.expires_at) return '验证码已过期，请重新获取。';
  const reserved = await env.DB.prepare(`UPDATE otp_codes SET attempts = attempts + 1 WHERE id = ?1 AND attempts < ?2 AND consumed = 0`)
    .bind(row.id, OTP_MAX_ATTEMPTS).run();
  if (!reserved.meta?.changes) return '验证码错误次数过多，请重新获取。';
  if (await sha256Hex(String(code || '').trim()) !== row.code_hash) return '验证码不正确。';
  const used = await env.DB.prepare(`UPDATE otp_codes SET consumed = 1 WHERE id = ?1 AND consumed = 0`).bind(row.id).run();
  if (!used.meta?.changes) return '验证码已使用，请重新获取。';
  return null;
}

// ---------- 登录失败锁定（按邮箱 + IP） ----------
async function lockStatus(env, key) {
  const r = await env.DB.prepare(`SELECT locked_until FROM login_attempts WHERE key = ?1`).bind(key).first();
  return r?.locked_until && Date.now() < r.locked_until ? Math.ceil((r.locked_until - Date.now()) / 60000) : 0;
}

async function countAttempt(env, key) {
  const now = Date.now();
  const r = await env.DB.prepare(`INSERT INTO login_attempts (key, count, first_attempt_at, locked_until) VALUES (?1, 1, ?2, NULL)
     ON CONFLICT(key) DO UPDATE SET
       count = CASE WHEN ?2 - login_attempts.first_attempt_at <= ?3 THEN login_attempts.count + 1 ELSE 1 END,
       first_attempt_at = CASE WHEN ?2 - login_attempts.first_attempt_at <= ?3 THEN login_attempts.first_attempt_at ELSE ?2 END
     RETURNING count`).bind(key, now, LOCK_WINDOW_MS).first();
  const n = r?.count ?? 1;
  if (n >= MAX_LOGIN_ATTEMPTS)
    await env.DB.prepare(`UPDATE login_attempts SET locked_until = ?2 WHERE key = ?1 AND (locked_until IS NULL OR locked_until < ?3)`)
      .bind(key, now + LOCK_MS, now).run();
  return n;
}

const clearAttempts = (env, key) => env.DB.prepare(`DELETE FROM login_attempts WHERE key = ?1`).bind(key).run();

// ---------- 会话 ----------
export async function createSession(env, userId, isAdmin) {
  const id = randomToken(32), now = Date.now(), ttl = isAdmin ? ADMIN_SESSION_MS : USER_SESSION_MS;
  await env.DB.prepare(`INSERT INTO sessions (id, user_id, is_admin, elevated_until, expires_at, created_at) VALUES (?1, ?2, ?3, 0, ?4, ?5)`)
    .bind(await sha256Hex(id), userId, isAdmin ? 1 : 0, now + ttl, now).run();
  return `${SESSION_COOKIE}=${id}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${Math.floor(ttl / 1000)}`;
}

export const clearCookie = `${SESSION_COOKIE}=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0`;

// 只在 D1 里保存会话编号的哈希：即使数据库泄露，也拿不到能用的登录凭证
export async function getSession(env, req) {
  const raw = getCookie(req, SESSION_COOKIE);
  if (!raw || raw.length < 20 || raw.length > 100) return null;
  const sid = await sha256Hex(raw);
  const s = await env.DB.prepare(
    `SELECT s.id, s.user_id, s.is_admin, s.elevated_until, s.expires_at, u.email, u.status, u.is_admin AS u_admin,
            u.balance_chars, u.plan_id, u.plan_until, u.plan_daily_chars, u.created_at
     FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.id = ?1`).bind(sid).first();
  if (!s) return null;
  if (Date.now() > s.expires_at || s.status !== 'active' || (s.is_admin && !s.u_admin)) {
    await env.DB.prepare(`DELETE FROM sessions WHERE id = ?1`).bind(sid).run();
    return null;
  }
  return { ...s, sid, isAdmin: !!s.is_admin && !!s.u_admin && normEmail(s.email) === adminEmail(env) };
}

// ---------- 管理员登录地址（不写在公开代码里：默认值由服务器密钥算出） ----------
export async function adminSlug(env) {
  const v = await getSetting(env, 'admin_login_slug');
  if (v) return v;
  return toHex(await hmacBytes(fromHex(env.KEY_SIGNING_SECRET), 'admin-login-slug/v1')).slice(0, 32);
}

export const SLUG_RE = /^[A-Za-z0-9_-]{16,80}$/;

// ---------- 接口 ----------
export async function handleAuthApi(req, env, path) {
  if (path === '/api/auth/captcha' && req.method === 'GET') return newCaptcha(env, req);
  if (path === '/api/auth/me' && req.method === 'GET') {
    const s = await getSession(env, req);
    return json({ ok: true, logged_in: !!s, email: s?.email || null, is_admin: !!s?.isAdmin });
  }
  if (req.method !== 'POST') return json({ ok: false, message: '不支持的请求方式。' }, 405);
  if (!sameOrigin(req)) return json({ ok: false, message: '请求来源不正确。' }, 403);
  const body = await readJson(req);
  if (!body) return json({ ok: false, message: '请求格式不正确。' }, 400);
  const ip = clientIp(req);
  const rl = await hitRateLimit(env, 'auth:' + ip, ...LIMITS.authPerIp);
  if (!rl.ok) return json({ ok: false, message: `操作太频繁，请 ${waitText(rl.retryAfter)}后再试。` }, 429);

  try {
    switch (path) {
      case '/api/auth/register/request-otp': {
        if ((await getSetting(env, 'register_open', '1')) !== '1') return json({ ok: false, message: '网站暂时关闭了新用户注册。' }, 403);
        const email = normEmail(body.email);
        if (!EMAIL_RE.test(email)) return json({ ok: false, message: '请输入有效的邮箱地址。' }, 400);
        const h = await checkHuman(env, req, body);
        if (h) return json({ ok: false, message: h, captcha_failed: true }, 400);
        if (email === adminEmail(env)) return json({ ok: false, message: '该邮箱不能注册普通账户。' }, 400);
        if (await env.DB.prepare(`SELECT 1 FROM users WHERE email = ?1`).bind(email).first())
          return json({ ok: false, message: '该邮箱已注册，请直接登录；忘记密码可以用"找回密码"。' }, 409);
        await requestOtp(env, req, email, 'register');
        return json({ ok: true, message: '验证码已发到你的邮箱（10 分钟内有效）。没收到请看看垃圾邮件箱。' });
      }
      case '/api/auth/register/verify': {
        if ((await getSetting(env, 'register_open', '1')) !== '1') return json({ ok: false, message: '网站暂时关闭了新用户注册。' }, 403);
        const email = normEmail(body.email);
        const bad = passwordProblem(body.password, email);
        if (bad) return json({ ok: false, message: bad }, 400);
        if (email === adminEmail(env)) return json({ ok: false, message: '该邮箱不能注册普通账户。' }, 400);
        const e = await verifyOtp(env, email, 'register', body.code);
        if (e) return json({ ok: false, message: e }, 400);
        if (await env.DB.prepare(`SELECT 1 FROM users WHERE email = ?1`).bind(email).first())
          return json({ ok: false, message: '该邮箱已注册。' }, 409);
        const rlr = await hitRateLimit(env, 'reg:' + ip, ...LIMITS.registerPerIp);
        if (!rlr.ok) return json({ ok: false, message: '这个网络今天注册的账户太多了，请明天再试。' }, 429);
        const id = crypto.randomUUID();
        await env.DB.prepare(`INSERT INTO users (id, email, password_hash, is_admin, status, created_at, last_login_at, last_login_ip)
           VALUES (?1, ?2, ?3, 0, 'active', ?4, ?4, ?5)`).bind(id, email, await hashPassword(body.password), Date.now(), ip).run();
        await audit(env, email, 'register', null, ip);
        return json({ ok: true, message: '注册成功。' }, 200, { 'Set-Cookie': await createSession(env, id, false) });
      }
      case '/api/auth/login': {
        const email = normEmail(body.email);
        if (!EMAIL_RE.test(email) || typeof body.password !== 'string') return json({ ok: false, message: '请填写邮箱和密码。' }, 400);
        const h = await checkHuman(env, req, body);
        if (h) return json({ ok: false, message: h, captcha_failed: true }, 400);
        if (email === adminEmail(env)) return json({ ok: false, message: '邮箱或密码错误。' }, 401);
        const key = `u:${email}|${ip}`;
        const locked = await lockStatus(env, key);
        if (locked) return json({ ok: false, message: `尝试次数过多，请 ${locked} 分钟后再试，或用"找回密码"重设密码。` }, 423);
        const n = await countAttempt(env, key);
        const u = await env.DB.prepare(`SELECT id, password_hash, status FROM users WHERE email = ?1 AND is_admin = 0`).bind(email).first();
        // 邮箱不存在时也算一次哈希，响应时间一样，别人无法据此判断某个邮箱是否注册过
        if (!u) await verifyPassword(body.password, DUMMY_HASH);
        if (!u || !await verifyPassword(body.password, u.password_hash))
          return json({ ok: false, message: `邮箱或密码错误（第 ${n} / ${MAX_LOGIN_ATTEMPTS} 次）。` }, 401);
        if (u.status !== 'active') return json({ ok: false, message: '该账户已被停用，如有疑问请联系管理员。' }, 403);
        await clearAttempts(env, key);
        await env.DB.prepare(`UPDATE users SET last_login_at = ?2, last_login_ip = ?3 WHERE id = ?1`).bind(u.id, Date.now(), ip).run();
        return json({ ok: true, message: '登录成功。' }, 200, { 'Set-Cookie': await createSession(env, u.id, false) });
      }
      case '/api/auth/logout': {
        const s = await getSession(env, req);
        if (s) await env.DB.prepare(`DELETE FROM sessions WHERE id = ?1`).bind(s.sid).run();
        return json({ ok: true }, 200, { 'Set-Cookie': clearCookie });
      }
      case '/api/auth/forgot/request-otp': {
        const email = normEmail(body.email);
        if (!EMAIL_RE.test(email)) return json({ ok: false, message: '请输入有效的邮箱地址。' }, 400);
        const h = await checkHuman(env, req, body);
        if (h) return json({ ok: false, message: h, captcha_failed: true }, 400);
        const u = await env.DB.prepare(`SELECT id FROM users WHERE email = ?1 AND is_admin = 0 AND status = 'active'`).bind(email).first();
        if (u) await requestOtp(env, req, email, 'reset_password');
        // 不论邮箱是否注册都给同样的回答，避免被用来探测谁注册过
        return json({ ok: true, message: '如果这个邮箱已注册，验证码已经发出（10 分钟内有效）。' });
      }
      case '/api/auth/forgot/reset': {
        const email = normEmail(body.email);
        const bad = passwordProblem(body.password, email);
        if (bad) return json({ ok: false, message: bad }, 400);
        const e = await verifyOtp(env, email, 'reset_password', body.code);
        if (e) return json({ ok: false, message: e }, 400);
        const u = await env.DB.prepare(`SELECT id FROM users WHERE email = ?1 AND is_admin = 0`).bind(email).first();
        if (!u) return json({ ok: false, message: '账户不存在。' }, 404);
        await env.DB.batch([
          env.DB.prepare(`UPDATE users SET password_hash = ?2 WHERE id = ?1`).bind(u.id, await hashPassword(body.password)),
          env.DB.prepare(`DELETE FROM sessions WHERE user_id = ?1`).bind(u.id),   // 其他设备上的登录全部失效
          env.DB.prepare(`DELETE FROM login_attempts WHERE key LIKE ?1`).bind(`u:${email}|%`),
        ]);
        await audit(env, email, 'reset_password', null, ip);
        return json({ ok: true, message: '密码已重设，请用新密码登录。' });
      }
      case '/api/auth/password/request-otp': {
        const s = await getSession(env, req);
        if (!s || s.isAdmin) return json({ ok: false, message: '请先登录。' }, 401);
        await requestOtp(env, req, s.email, 'change_password');
        return json({ ok: true, message: '验证码已发到你的注册邮箱。' });
      }
      case '/api/auth/password/change': {
        const s = await getSession(env, req);
        if (!s || s.isAdmin) return json({ ok: false, message: '请先登录。' }, 401);
        const u = await env.DB.prepare(`SELECT password_hash FROM users WHERE id = ?1`).bind(s.user_id).first();
        if (!u || !await verifyPassword(String(body.old_password || ''), u.password_hash)) return json({ ok: false, message: '原密码不正确。' }, 400);
        const bad = passwordProblem(body.password, s.email);
        if (bad) return json({ ok: false, message: bad }, 400);
        const e = await verifyOtp(env, s.email, 'change_password', body.code);
        if (e) return json({ ok: false, message: e }, 400);
        await env.DB.batch([
          env.DB.prepare(`UPDATE users SET password_hash = ?2 WHERE id = ?1`).bind(s.user_id, await hashPassword(body.password)),
          env.DB.prepare(`DELETE FROM sessions WHERE user_id = ?1 AND id != ?2`).bind(s.user_id, s.sid),
        ]);
        await audit(env, s.email, 'change_password', null, ip);
        return json({ ok: true, message: '密码已修改，其他设备上的登录已退出。' });
      }

      // ---- 管理员 ----
      case '/api/auth/admin/send-login-link': {
        // 忘记管理员登录地址：把当前地址发到管理员邮箱（不论填的是不是管理员邮箱都给同样的回答）
        const email = normEmail(body.email);
        const h = await checkHuman(env, req, body);
        if (h) return json({ ok: false, message: h, captcha_failed: true }, 400);
        if (email && email === adminEmail(env)) {
          const rl2 = await hitRateLimit(env, 'admin-link', 5, 3600);
          if (rl2.ok) {
            const link = new URL(req.url).origin + '/login/' + await adminSlug(env);
            await sendEmail(env, email, 'AI 文本检测 · 管理员登录地址',
              `<div style="font-family:sans-serif;font-size:15px;line-height:1.8">管理员登录地址：<br><a href="${esc(link)}">${esc(link)}</a><br><span style="color:#888">请妥善保存，不要公开。如果这不是您本人的操作，说明有人知道您的管理员邮箱，但他无法收到这封邮件；可以在后台修改登录地址。</span></div>`);
            await audit(env, email, 'admin_send_login_link', null, ip);
          }
        }
        return json({ ok: true, message: '如果这是管理员邮箱，登录地址已经发过去了。' });
      }
      case '/api/auth/admin/login':
      case '/api/auth/admin/verify-otp':
      case '/api/auth/admin/forgot/request-otp':
      case '/api/auth/admin/forgot/reset': {
        // 只有知道正确登录地址的人才能用这些接口
        if (String(body.slug || '') !== await adminSlug(env)) return json({ ok: false, message: '页面不存在。' }, 404);
        const email = normEmail(body.email), admin = adminEmail(env);
        if (!admin) return json({ ok: false, message: '尚未设置管理员邮箱 ADMIN_EMAIL。' }, 503);
        if (path.endsWith('/forgot/request-otp')) {
          const h = await checkHuman(env, req, body);
          if (h) return json({ ok: false, message: h, captcha_failed: true }, 400);
          if (email === admin) await requestOtp(env, req, email, 'admin_reset_password');
          return json({ ok: true, message: '如果这是管理员邮箱，验证码已经发出（10 分钟内有效）。' });
        }
        if (path.endsWith('/forgot/reset')) {
          if (email !== admin) return json({ ok: false, message: '验证码不正确。' }, 400);
          const bad = passwordProblem(body.password, email);
          if (bad) return json({ ok: false, message: bad }, 400);
          if (String(body.password).length < 12) return json({ ok: false, message: '管理员密码至少 12 位。' }, 400);
          const e = await verifyOtp(env, email, 'admin_reset_password', body.code);
          if (e) return json({ ok: false, message: e }, 400);
          const hash = await hashPassword(body.password);
          const ex = await env.DB.prepare(`SELECT id FROM users WHERE email = ?1`).bind(email).first();
          if (ex) {
            await env.DB.batch([
              env.DB.prepare(`UPDATE users SET password_hash = ?2, is_admin = 1, status = 'active' WHERE id = ?1`).bind(ex.id, hash),
              env.DB.prepare(`DELETE FROM sessions WHERE user_id = ?1`).bind(ex.id),
            ]);
          } else {
            await env.DB.prepare(`INSERT INTO users (id, email, password_hash, is_admin, status, created_at) VALUES (?1, ?2, ?3, 1, 'active', ?4)`)
              .bind(crypto.randomUUID(), email, hash, Date.now()).run();
          }
          await audit(env, email, 'admin_set_password', null, ip);
          return json({ ok: true, message: '管理员密码已设置，请用新密码登录。' });
        }
        const key = `a:${email}|${ip}`;
        if (path.endsWith('/admin/login')) {
          const h = await checkHuman(env, req, body);
          if (h) return json({ ok: false, message: h, captcha_failed: true }, 400);
          const locked = await lockStatus(env, key);
          if (locked) return json({ ok: false, message: `尝试次数过多，请 ${locked} 分钟后再试。` }, 423);
          const n = await countAttempt(env, key);
          const a = email === admin ? await env.DB.prepare(`SELECT password_hash FROM users WHERE email = ?1 AND is_admin = 1`).bind(email).first() : null;
          if (!a || !await verifyPassword(String(body.password || ''), a.password_hash)) {
            await audit(env, email, 'admin_login_failed', null, ip);
            return json({ ok: false, message: `邮箱或密码错误（第 ${n} / ${MAX_LOGIN_ATTEMPTS} 次）。第一次使用请点"设置 / 找回管理员密码"。` }, 401);
          }
          await clearAttempts(env, key);
          await requestOtp(env, req, email, 'admin_login');
          return json({ ok: true, message: '密码正确，验证码已发到管理员邮箱。' });
        }
        // verify-otp
        if (email !== admin) return json({ ok: false, message: '验证码不正确。' }, 400);
        const e = await verifyOtp(env, email, 'admin_login', body.code);
        if (e) return json({ ok: false, message: e }, 400);
        const a = await env.DB.prepare(`SELECT id FROM users WHERE email = ?1 AND is_admin = 1`).bind(email).first();
        if (!a) return json({ ok: false, message: '管理员账户不存在。' }, 404);
        await env.DB.prepare(`UPDATE users SET last_login_at = ?2, last_login_ip = ?3 WHERE id = ?1`).bind(a.id, Date.now(), ip).run();
        await audit(env, email, 'admin_login', null, ip);
        return json({ ok: true, message: '登录成功。' }, 200, { 'Set-Cookie': await createSession(env, a.id, true) });
      }
    }
    return json({ ok: false, message: '接口不存在。' }, 404);
  } catch (err) {
    if (err instanceof OtpError) return json({ ok: false, message: err.message }, err.status);
    console.error('[auth]', path, err);
    return json({ ok: false, message: '服务器出错，请稍后再试。' + (String(err.message || '').includes('RESEND') || String(err.message || '').includes('邮件') ? '（邮件发送失败）' : '') }, 500);
  }
}

export { OtpError };
