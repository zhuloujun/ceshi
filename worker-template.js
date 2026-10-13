/**
 * 审读 · 文本特征检测 —— Cloudflare Worker 单文件版
 *
 * 这一个文件同时负责：
 *   1. 设置了变量 BACKEND_URL 时：把网站转发到完整版检测服务（Modal），轻量版放在 /lite/
 *      没设置时：直接提供轻量版网页
 *   2. /api/detect：把轻量版的检测请求转发给 GPTZero 官方接口
 *
 * 部署：Cloudflare Dashboard → Workers & Pages → 创建 → Worker（从 Hello World 开始）
 *       → 编辑代码 → 把本文件全部内容粘贴进去替换 → 部署。详见 README。
 *
 * API Key 的两种提供方式：
 *   A. 用户在网页上填写自己的 Key → 随请求头 X-User-Api-Key 发来，本 Worker 只转发，不保存。
 *   B. 在 Worker 的 Settings → Variables and Secrets 设置 Secret：
 *        GPTZERO_API_KEY  你的 GPTZero Key
 *        ACCESS_PASSWORD  访问密码（必须设置，否则拒绝使用 B 方式，防止别人用掉你的额度）
 *
 * 免费版 Worker 每次请求只有 10 毫秒 CPU 时间；等待 GPTZero 返回的时间不算 CPU 时间，
 * 本文件做的只是转发，远低于这个限制。
 *
 * 注意：此文件由 build.mjs 生成，请修改 pages-static/ 下的源文件后重新生成。
 */

import { json as sjson, html, SECURITY_HEADERS, readJson, clientIp } from './worker-src/util.js';
import { handleAuthApi, getSession, adminSlug, SESSION_COOKIE } from './worker-src/auth.js';
import { handleBillingApi, planCharge, recordCharge, detectRateOk, mintBackendKey, hasAnyQuota, settleFileJob } from './worker-src/billing.js';
import { handleAdminApi } from './worker-src/admin.js';
import { i18nResponse, injectI18n } from './worker-src/i18n.js';
import { landingPage, authPage, accountPage, adminLoginPage, adminLinkRequestPage, adminConsolePage, notFoundPage, userBarHtml } from './worker-src/pages.js';

const INDEX_HTML = __INDEX_HTML__;
const STYLE_CSS = __STYLE_CSS__;
const APP_JS = __APP_JS__;

const GPTZERO_ENDPOINT = 'https://api.gptzero.me/v2/predict/text';
const MAX_CHARS_PER_CALL = 48000; // GPTZero 单次约 5 万字符上限，留出余量

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    // 注册登录功能需要：D1 数据库、Resend 邮件密钥、签发检测 Key 的密钥。三样都设置好后自动启用；
    // 紧急情况下可以把变量 AUTH_DISABLED 设为 1 临时关闭（恢复成原来的公开访问方式）。
    if (url.pathname === '/__i18n.js') return i18nResponse();      // 中英文界面切换脚本
    const resp = authEnabled(env) ? await gated(request, env, url) : await legacy(request, env, url);
    return withSecurity(injectI18n(resp));
  }
};

function authEnabled(env) {
  return !!(env.DB && env.RESEND_API_KEY && env.KEY_SIGNING_SECRET && env.ADMIN_EMAIL) && env.AUTH_DISABLED !== '1';
}

function withSecurity(resp) {
  const out = new Response(resp.body, resp);
  for (const [k, v] of Object.entries(SECURITY_HEADERS)) if (!out.headers.has(k)) out.headers.set(k, v);
  return out;
}

async function legacy(request, env, url) {
  // 轻量版的 GPTZero 转发接口（两种模式下都保留）
  if (url.pathname === '/api/detect') {
    if (request.method !== 'POST') {
      return json({ error: 'method_not_allowed', message: '此接口只接受 POST 请求' }, 405);
    }
    return handleDetect(request, env);
  }

  // 设置了 BACKEND_URL（完整版检测服务地址）时：
  //   /lite/...  → 轻量版（浏览器本地检测）
  //   其他路径    → 原样转发给完整版（Modal 上的检测服务）
  if (env.BACKEND_URL) {
    // 旧版管理页会每 15 秒查询一次标注状态，让 Modal 服务器一直不关机（每小时约 0.47 美元）。
    // 新版管理页带 ?v=2；不带的旧请求直接在这里挡掉，不转发给 Modal。
    if (url.pathname === '/admin/api/labels' && request.method === 'GET' && url.searchParams.get('v') !== '2') {
      return json({ error: 'stale_page', message: '管理页版本过旧，请刷新页面。' }, 410);
    }
    if (url.pathname === '/lite') return Response.redirect(url.origin + '/lite/', 301);
    if (url.pathname.startsWith('/lite/')) return serveLite(request, url.pathname.slice('/lite'.length));
    return proxy(request, env, url);
  }
  return serveLite(request, url.pathname);
}

// 需要登录的模式
async function gated(request, env, url) {
  const p = url.pathname;
  const isGet = request.method === 'GET' || request.method === 'HEAD';

  // 本站自己的接口
  if (p.startsWith('/api/auth/')) return handleAuthApi(request, env, p);
  const s = await getSession(env, request);
  if (p.startsWith('/api/billing/')) return handleBillingApi(request, env, p, s);
  if (p.startsWith('/api/admin/')) return handleAdminApi(request, env, p, s);

  // 公开页面
  if (isGet && p === '/home') return html(landingPage(!!s));
  if (isGet && (p === '/login' || p === '/register' || p === '/forgot')) {
    if (s) return Response.redirect(url.origin + (s.isAdmin ? '/admin/console' : '/'), 302);
    return html(authPage(p.slice(1)));
  }
  if (isGet && p === '/admin-link') return html(adminLinkRequestPage());
  if (isGet && p.startsWith('/login/')) {
    const slug = p.slice('/login/'.length);
    if (slug && slug === await adminSlug(env)) return html(adminLoginPage(slug), 200, { 'Cache-Control': 'no-store' });
    return html(notFoundPage(), 404);
  }
  if (p === '/robots.txt') return new Response('User-agent: *\nAllow: /home\nDisallow: /\n', { headers: { 'Content-Type': 'text/plain' } });
  if (p === '/favicon.ico') return new Response(null, { status: 204 });

  // 没登录：首页显示介绍页；/health 由 Worker 自己回答（不叫醒 Modal）；其他一律要求登录
  if (!s) {
    if (request.headers.get('Authorization')?.startsWith('Bearer atc-') && (p.startsWith('/v1/') || p === '/health'))
      return proxy(request, env, url);         // 用 API Key 直接调用接口的程序照常可用（Key 由后端校验）
    if (isGet && (p === '/' || p === '/index.html')) return html(landingPage(false));
    if (p === '/health') return sjson({ ok: true, login_required: true });
    if (isGet && !p.startsWith('/v1/') && !p.startsWith('/static/') && !p.startsWith('/admin/api/'))
      return Response.redirect(url.origin + (p.startsWith('/admin') ? '/home' : '/login'), 302);
    return sjson({ error: 'login_required', message: '请先登录：' + url.origin + '/login' }, 401);
  }

  // 已登录
  if (isGet && p === '/account') return s.isAdmin ? Response.redirect(url.origin + '/admin/console', 302) : html(accountPage());
  if (p === '/admin/console') return s.isAdmin ? html(adminConsolePage()) : html(notFoundPage(), 404);
  if (p === '/admin' || p.startsWith('/admin/')) {
    if (!s.isAdmin) return html(notFoundPage(), 404);
    if (p === '/admin/api/labels' && request.method === 'GET' && url.searchParams.get('v') !== '2')
      return json({ error: 'stale_page', message: '管理页版本过旧，请刷新页面。' }, 410);
    const r = await proxy(request, env, url);
    return p === '/admin' ? injectBar(r, s) : r;
  }
  if (p === '/api/detect') {
    if (request.method !== 'POST') return json({ error: 'method_not_allowed', message: '此接口只接受 POST 请求' }, 405);
    return handleDetect(request, env);
  }
  if (p === '/lite') return Response.redirect(url.origin + '/lite/', 301);
  if (p.startsWith('/lite/')) return serveLite(request, p.slice('/lite'.length));
  if (!env.BACKEND_URL) return serveLite(request, p);

  // 检测服务（Modal）：Worker 代用户带上检测 Key，并在提交检测时核对、扣减额度
  const headers = new Headers(request.headers);
  headers.delete('Cookie');                                   // 本站登录凭证不发给后端
  if (!headers.get('Authorization') && !headers.get('X-API-Key')) headers.set('Authorization', 'Bearer ' + await mintBackendKey(env, s));
  if (p === '/v1/detect' && request.method === 'POST') {
    const bad = await detectRateOk(env, s);
    if (bad) return sjson({ error: 'rate_limited', message: bad }, 429);
    const raw = await request.text();
    let n = 0;
    try { n = String(JSON.parse(raw).text || '').length; } catch { return sjson({ error: 'bad_request', message: '请求内容不是合法的 JSON' }, 400); }
    const charge = await planCharge(env, s, n);
    if (!charge.ok) return sjson({ error: 'quota_exceeded', message: charge.message }, 402);
    const r = await proxy(new Request(request.url, { method: 'POST', headers, body: raw }), env, url);
    if (r.status < 400) await recordCharge(env, s, n, charge);
    return r;
  }
  if (p === '/v1/detect/file' && request.method === 'POST') {
    const bad = await detectRateOk(env, s);
    if (bad) return sjson({ error: 'rate_limited', message: bad }, 429);
    const q = await hasAnyQuota(env, s);
    if (!q.ok) return sjson({ error: 'quota_exceeded', message: q.message }, 402);
    const r = await proxy(new Request(request.url, { method: 'POST', headers, body: request.body, duplex: 'half' }), env, url);
    if (r.status < 400) { try { await settleFileJob(env, s, await r.clone().json(), true); } catch { /* 非 JSON */ } }
    return r;
  }
  const r = await proxy(new Request(request.url, { method: request.method, headers,
    body: isGet ? undefined : request.body, duplex: 'half' }), env, url);
  if (p.startsWith('/v1/jobs/') && r.ok) { try { await settleFileJob(env, s, await r.clone().json(), false); } catch { /* 非 JSON */ } }
  if (p === '/health' && r.ok) {
    // 登录用户不需要在页面上填 Key
    try { const h = await r.json(); return sjson({ ...h, requires_key: false, login: { email: s.email, admin: s.isAdmin } }); } catch { return r; }
  }
  if (isGet && (p === '/' || p === '/index.html')) return injectBar(r, s);
  return r;
}

function injectBar(resp, s) {
  if (!(resp.headers.get('Content-Type') || '').includes('text/html')) return resp;
  return new HTMLRewriter().on('body', { element(el) { el.prepend(userBarHtml(s.email, s.isAdmin), { html: true }); } }).transform(resp);
}

function serveLite(request, path) {
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    return new Response('Method Not Allowed', { status: 405 });
  }
  switch (path) {
    case '/':
    case '/index.html':
      return asset(INDEX_HTML, 'text/html; charset=utf-8');
    case '/style.css':
      return asset(STYLE_CSS, 'text/css; charset=utf-8');
    case '/app.js':
      return asset(APP_JS, 'application/javascript; charset=utf-8');
    default:
      return new Response('Not Found', { status: 404 });
  }
}

async function proxy(request, env, url) {
  const backend = env.BACKEND_URL.replace(/\/+$/, '');
  const target = backend + url.pathname + url.search;
  const headers = new Headers(request.headers);
  headers.delete('host');
  headers.set('X-Forwarded-Host', url.host);
  headers.set('X-Forwarded-Proto', 'https');
  const ip = request.headers.get('CF-Connecting-IP');
  if (ip) headers.set('X-Forwarded-For', ip);
  const init = { method: request.method, headers, redirect: 'manual' };
  if (request.method !== 'GET' && request.method !== 'HEAD') init.body = request.body;

  // 打开首页时，如果完整版（Modal）暂时不可用（例如本月用量达到上限被暂停、服务出错），
  // 自动改用轻量版（浏览器本地检测，不花钱），而不是给用户看一个报错页面。
  const isHome = request.method === 'GET' && (url.pathname === '/' || url.pathname === '/index.html');
  const toLite = () => Response.redirect(url.origin + '/lite/?from=full', 302);
  let resp;
  try {
    resp = await fetch(target, init);
  } catch (e) {
    if (isHome) return toLite();
    return json({ error: 'backend_unreachable',
      message: '检测服务暂时连不上，可能正在启动（闲置后首次访问需要 1–3 分钟），请稍后再试；也可以先用轻量版：' + url.origin + '/lite/' }, 502);
  }
  if (isHome && resp.status >= 400) return toLite();
  // 检测接口遇到 402 / 429 / 502–504（额度用完被暂停、限流或服务异常）：给出能看懂的提示
  if (url.pathname.startsWith('/api/') && [402, 429, 502, 503, 504].includes(resp.status)) {
    return json({ error: 'backend_unavailable',
      message: '完整版检测服务暂时不可用（可能正在启动，或本月免费额度已用完）。请稍后再试，或先用轻量版：' + url.origin + '/lite/' }, 503);
  }
  const out = new Response(resp.body, resp);
  // 后端返回的跳转地址改回当前域名，避免把用户带到 modal.run
  const loc = out.headers.get('Location');
  if (loc && loc.startsWith(backend)) out.headers.set('Location', url.origin + loc.slice(backend.length));
  out.headers.set('X-Served-Via', 'cloudflare-worker');
  return out;
}

async function handleDetect(request, env) {
  const userKey = (request.headers.get('X-User-Api-Key') || '').trim();
  let apiKey = userKey;

  if (!apiKey) {
    if (!env.GPTZERO_API_KEY) {
      return json({
        error: 'not_configured',
        message: '没有可用的 API Key：请在网页"官方 API 增强检测"里填写你自己的 GPTZero Key，或在 Worker 的 Settings → Variables and Secrets 中设置 GPTZERO_API_KEY。'
      }, 400);
    }
    if (!env.ACCESS_PASSWORD) {
      return json({
        error: 'password_not_set',
        message: 'Worker 里设置了 GPTZERO_API_KEY，但没有设置 ACCESS_PASSWORD。为防止别人通过你的网址用掉你的额度，请在 Settings → Variables and Secrets 里再加一个 Secret：ACCESS_PASSWORD，然后在网页上填写这个密码。'
      }, 403);
    }
    const pw = request.headers.get('X-Access-Password') || '';
    if (!(await safeEqual(pw, env.ACCESS_PASSWORD))) {
      return json({ error: 'forbidden', message: '访问密码不正确（或未填写）。请在网页"官方 API 增强检测"里填写 ACCESS_PASSWORD 对应的密码。' }, 403);
    }
    apiKey = env.GPTZERO_API_KEY;
  }

  let body;
  try {
    body = await request.json();
  } catch (e) {
    return json({ error: 'bad_request', message: '请求内容不是合法的 JSON' }, 400);
  }
  const text = String(body.text || '');
  if (!text.trim()) return json({ error: 'bad_request', message: '文本为空' }, 400);
  if (text.length > MAX_CHARS_PER_CALL) {
    return json({ error: 'too_long', message: `单次请求超过 ${MAX_CHARS_PER_CALL} 字符，请分段发送。` }, 413);
  }

  let upstream, data;
  try {
    upstream = await fetch(GPTZERO_ENDPOINT, {
      method: 'POST',
      headers: { 'Accept': 'application/json', 'Content-Type': 'application/json', 'x-api-key': apiKey },
      body: JSON.stringify({ document: text, multilingual: true })
    });
    data = await upstream.json().catch(() => ({}));
  } catch (err) {
    return json({ error: 'network_error', message: '连接 GPTZero 失败：' + err.message }, 502);
  }

  if (!upstream.ok) {
    const hint = upstream.status === 401 || upstream.status === 403
      ? 'Key 无效，或你的 GPTZero 套餐不含 API 权限（网页免费版不含 API）。'
      : upstream.status === 429 ? '请求太频繁或额度已用完，请稍后再试或检查套餐额度。' : '';
    return json({
      error: 'upstream_error',
      message: `GPTZero 返回错误（HTTP ${upstream.status}）。${hint}`,
      detail: data && (data.error || data.message) || null
    }, upstream.status === 429 ? 429 : 502);
  }

  const doc = (data.documents && data.documents[0]) || data;
  return json({
    completely_generated_prob: doc.completely_generated_prob ?? null,
    average_generated_prob: doc.average_generated_prob ?? null,
    predicted_class: doc.predicted_class ?? null,
    overall_burstiness: doc.overall_burstiness ?? null,
    sentences: (doc.sentences || []).map(s => ({ text: s.sentence, generated_prob: s.generated_prob }))
  });
}

// 常量时间比较，避免通过响应时间猜密码
async function safeEqual(a, b) {
  const enc = new TextEncoder();
  const [ha, hb] = await Promise.all([
    crypto.subtle.digest('SHA-256', enc.encode(a)),
    crypto.subtle.digest('SHA-256', enc.encode(b))
  ]);
  const x = new Uint8Array(ha), y = new Uint8Array(hb);
  let diff = 0;
  for (let i = 0; i < x.length; i++) diff |= x[i] ^ y[i];
  return diff === 0;
}

function asset(body, type) {
  return new Response(body, {
    headers: {
      'Content-Type': type,
      'Cache-Control': 'public, max-age=300',
      'X-Content-Type-Options': 'nosniff',
      'Referrer-Policy': 'no-referrer'
    }
  });
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' }
  });
}
