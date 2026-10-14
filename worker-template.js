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

import { json as sjson, html, SECURITY_HEADERS, readJson, clientIp, sameOrigin, BACKEND_PAGE_CSP, LITE_CSP, withStoredSecrets, handleSetupSecret } from './worker-src/util.js';
import { handleAuthApi, getSession, adminSlug, SESSION_COOKIE, clearCookie } from './worker-src/auth.js';
import { handleBillingApi, planCharge, recordCharge, detectRateOk, mintBackendKey, hasAnyQuota, settleFileJob } from './worker-src/billing.js';
import { handleAdminApi } from './worker-src/admin.js';
import { i18nResponse, injectI18n } from './worker-src/i18n.js';
import { landingPage, authPage, accountPage, adminLoginPage, adminLinkRequestPage, adminConsolePage, notFoundPage, userBarHtml, legalPage, setSiteContacts, siteFooterHtml } from './worker-src/pages.js';
import { LEGAL_PAGES, LEGAL_TITLES, legalConfig, legalText, renderLegal } from './worker-src/legal.js';
import { langOf } from './worker-src/i18n.js';

const INDEX_HTML = __INDEX_HTML__;
const STYLE_CSS = __STYLE_CSS__;
const APP_JS = __APP_JS__;
const BACKEND_STATIC = __BACKEND_STATIC__;

const GPTZERO_ENDPOINT = 'https://api.gptzero.me/v2/predict/text';
const MAX_CHARS_PER_CALL = 48000; // GPTZero 单次约 5 万字符上限，留出余量

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    // 注册登录功能需要：D1 数据库、Resend 邮件密钥、签发检测 Key 的密钥。三样都设置好后自动启用；
    // 紧急情况下可以把变量 AUTH_DISABLED 设为 1 临时关闭（恢复成原来的公开访问方式）。
    if (url.pathname === '/__i18n.js') return i18nResponse();      // 中英文界面切换脚本
    if (url.pathname === '/api/setup/secret') return withSecurity(await handleSetupSecret(request, env));
    env = Object.assign({}, await withStoredSecrets(env), { CTX: ctx });
    let resp;
    if (authEnabled(env)) {
      // 登录模式下缺少密钥（例如数据库暂时读不到）时暂停服务，而不是退回"无需登录"的公开模式
      if (!env.RESEND_API_KEY || !env.KEY_SIGNING_SECRET) resp = maintenance(url);
      else resp = await gated(request, env, url);
    } else resp = await legacy(request, env, url);
    return withSecurity(injectI18n(resp));
  }
};

// 绑定了数据库并设置了管理员邮箱，就是登录模式（AUTH_DISABLED=1 可在紧急情况下临时关闭）
function authEnabled(env) {
  return !!(env.DB && env.ADMIN_EMAIL) && env.AUTH_DISABLED !== '1';
}

function maintenance(url) {
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/v1/') || url.pathname === '/health')
    return sjson({ error: 'maintenance', message: '网站正在维护，请稍后再试。' }, 503, { 'Retry-After': '120' });
  return html(notFoundPage().replace('页面不存在</h1>', '网站正在维护，请稍后再试。</h1>'), 503, { 'Retry-After': '120' });
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
    const pg = backendPage(request, url);
    if (pg) return pg;
    if (url.pathname === '/health' && request.method === 'GET' && !url.searchParams.has('live')) return cachedHealth(request, env, url, null);
    const r = await proxy(request, env, url);
    afterBackend(env, url, request, r);
    return r;
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
  const lc = await legalConfig(env);
  setSiteContacts(lc.contacts);
  if (isGet && LEGAL_PAGES.includes(p.slice(1))) {
    const pg = p.slice(1), lang = langOf(request);
    return html(legalPage(LEGAL_TITLES[pg][lang], renderLegal(await legalText(env, pg, lang), lc), !!s));
  }
  if (isGet && p === '/home') return html(landingPage(!!s));
  if (isGet && (p === '/login' || p === '/register' || p === '/forgot')) {
    if (s) return Response.redirect(url.origin + (s.isAdmin ? '/admin/console' : '/'), 302);
    return html(authPage(p.slice(1)));
  }
  if (isGet && p === '/admin-link') return html(adminLinkRequestPage());
  if (isGet && p === '/logout') {
    // 检测页顶部的"退出"链接（检测页的安全策略不允许内联脚本，所以用普通链接）
    if (s) await env.DB.prepare(`DELETE FROM sessions WHERE id = ?1`).bind(s.sid).run();
    return new Response(null, { status: 302, headers: { Location: url.origin + '/home', 'Set-Cookie': clearCookie, 'Cache-Control': 'no-store' } });
  }
  if (isGet && p.startsWith('/login/')) {
    const slug = p.slice('/login/'.length);
    if (slug && slug === await adminSlug(env)) return html(adminLoginPage(slug), 200, { 'Cache-Control': 'no-store' });
    return html(notFoundPage(), 404);
  }
  if (p === '/robots.txt') return new Response('User-agent: *\nAllow: /home\nAllow: /terms\nAllow: /privacy\nAllow: /cookies\nDisallow: /\n', { headers: { 'Content-Type': 'text/plain' } });
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
    if (p === '/admin' && isGet) return injectBar(backendPage(request, url), s);
    const r = await proxy(request, env, url);
    return r;
  }
  if (p === '/api/detect') {
    if (request.method !== 'POST') return json({ error: 'method_not_allowed', message: '此接口只接受 POST 请求' }, 405);
    return handleDetect(request, env);
  }
  if (p === '/lite') return Response.redirect(url.origin + '/lite/', 301);
  if (p.startsWith('/lite/')) return serveLite(request, p.slice('/lite'.length));
  if (!env.BACKEND_URL) return serveLite(request, p);

  // 页面和静态文件由 Worker 直接返回；状态信息用缓存回答：只有真正提交检测时才唤醒 Modal
  const pg = backendPage(request, url);
  if (pg) return (p === '/' || p === '/index.html') ? injectBar(pg, s) : pg;
  if (p === '/health' && isGet && !url.searchParams.has('live')) return cachedHealth(request, env, url, s);

  // 检测服务（Modal）：Worker 代用户带上检测 Key，并在提交检测时核对、扣减额度
  if (!isGet && !sameOrigin(request)) return sjson({ error: 'bad_origin', message: '请求来源不正确。' }, 403);
  let headers;
  try { headers = await backendRequestHeaders(request, env, s); } catch { return maintenance(url); }
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
    afterBackend(env, url, request, r);
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
  afterBackend(env, url, request, r);
  if (p === '/health' && r.ok) {
    // 登录用户不需要在页面上填 Key
    try { const h = await r.json(); return sjson({ ...h, requires_key: false, login: { email: s.email, admin: s.isAdmin } }); } catch { return r; }
  }
  if (isGet && (p === '/' || p === '/index.html')) return injectBar(r, s);
  return r;
}

// ---------- 省 Modal 费用：页面由 Worker 返回，/health 用缓存 ----------
// 以前每次有人打开检测页（包括搜索引擎爬虫），页面和 /health 都要转发给 Modal，把休眠的服务叫醒：
// 加载模型约 1–2 分钟 + 闲置 3 分钟才关机，即使不检测也要计费。现在只有提交检测、查询结果、管理操作才会唤醒它。
const STATIC_TYPES = { js: 'application/javascript; charset=utf-8', css: 'text/css; charset=utf-8', html: 'text/html; charset=utf-8' };

function backendPage(request, url) {
  if (request.method !== 'GET' && request.method !== 'HEAD') return null;
  const p = url.pathname;
  let name = null;
  if (p === '/' || p === '/index.html') name = 'index.html';
  else if (p === '/admin') name = 'admin.html';
  else if (p.startsWith('/static/')) name = p.slice('/static/'.length);
  const f = name && BACKEND_STATIC[name];
  if (!f) return null;
  const ext = name.split('.').pop();
  const cache = ext === 'html' ? 'no-cache' : (url.searchParams.get('v') === f.v ? 'public, max-age=31536000, immutable' : 'public, max-age=300');
  const h = { 'Content-Type': STATIC_TYPES[ext] || 'application/octet-stream', 'Cache-Control': cache };
  if (ext === 'html') h['Content-Security-Policy'] = BACKEND_PAGE_CSP;
  return new Response(f.body, { headers: h });
}

const HEALTH_KEY = 'backend_health';
const backendHeaders = (env) => (env.BACKEND_PROXY_SECRET ? { 'X-ATC-Proxy': env.BACKEND_PROXY_SECRET } : {});

async function cachedHealth(request, env, url, s) {
  let h = null;
  try { const v = env.DB && await env.DB.prepare(`SELECT value FROM settings WHERE key = ?1`).bind(HEALTH_KEY).first(); h = v ? JSON.parse(v.value) : null; } catch { /* 没有缓存 */ }
  if (!h) {
    // 第一次（还没有缓存）：真正问一次检测服务并记下来
    try {
      const r = await fetch(env.BACKEND_URL.replace(/\/+$/, '') + '/health', { headers: backendHeaders(env) });
      if (r.ok) { h = await r.json(); await saveHealth(env, h); }
    } catch { /* 连不上 */ }
  }
  if (!h) return sjson({ ok: false, error: 'backend_unreachable', message: '检测服务暂时连不上，请稍后再试。' }, 502);
  // 缓存里的状态只表示"服务能用哪些模型"；服务可能正在休眠，提交检测后会自动启动（约 1–2 分钟）
  const out = { ...h, loading: false, cached: true, queue: 0 };
  if (s) Object.assign(out, { requires_key: false, login: { email: s.email, admin: s.isAdmin } });
  return sjson(out);
}

async function saveHealth(env, h) {
  if (!env.DB || !h || h.loading) return;
  const keep = { ...h }; delete keep.queue;
  await env.DB.prepare(`INSERT INTO settings (key, value) VALUES (?1, ?2) ON CONFLICT(key) DO UPDATE SET value = ?2`)
    .bind(HEALTH_KEY, JSON.stringify(keep)).run();
}

// 检测服务刚被用过（已经醒着）：顺便更新一次状态缓存，不会额外唤醒它
function afterBackend(env, url, request, r) {
  try {
    if (!env.CTX || !env.BACKEND_URL || !r || r.status >= 400) return;
    const p = url.pathname;
    if (!(p === '/v1/detect' || p === '/v1/detect/file' || p.startsWith('/v1/jobs/'))) return;
    // 只在检测完成时更新（查询进度时页面每隔几秒请求一次，不必每次都更新）
    env.CTX.waitUntil(r.clone().json().then((j) => {
      if (!j || j.status !== 'done') return null;
      return fetch(env.BACKEND_URL.replace(/\/+$/, '') + '/health', { headers: backendHeaders(env) }).then((x) => x.ok ? x.json() : null).then((h) => h && saveHealth(env, h));
    }).catch(() => {}));
  } catch { /* 忽略 */ }
}

async function backendRequestHeaders(request, env, s) {
  const headers = new Headers(request.headers);
  headers.delete('Cookie');                                   // 本站登录凭证不发给后端
  // 一律用 Worker 为当前账户签发的 Key（不采用用户自己带的 Key，避免绕过本站的额度统计）
  headers.delete('X-API-Key');
  headers.set('Authorization', 'Bearer ' + await mintBackendKey(env, s));
  return headers;
}

function injectBar(resp, s) {
  if (!(resp.headers.get('Content-Type') || '').includes('text/html')) return resp;
  return new HTMLRewriter().on('body', { element(el) { el.prepend(userBarHtml(s.email, s.isAdmin), { html: true }); el.append(siteFooterHtml(), { html: true }); } }).transform(resp);
}

function serveLite(request, path) {
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    return new Response('Method Not Allowed', { status: 405 });
  }
  switch (path) {
    case '/':
    case '/index.html':
      { const r = asset(INDEX_HTML, 'text/html; charset=utf-8'); r.headers.set('Content-Security-Policy', LITE_CSP); return r; }
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
  headers.delete('X-ATC-Proxy');
  if (env.BACKEND_PROXY_SECRET) headers.set('X-ATC-Proxy', env.BACKEND_PROXY_SECRET);   // 证明请求来自本站 Worker
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
