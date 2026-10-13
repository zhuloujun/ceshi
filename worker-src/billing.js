// 商用模块（预留）：套餐、订单、额度扣减。
// 现在没有接支付平台：下单只生成"待支付"订单，管理员可以在后台手动标记已付款并开通。
// 以后接入支付宝 / 微信支付 / Stripe 等时，只需要在 PAYMENT_PROVIDERS 里补上"创建支付"和"回调验签"两个函数，
// 回调验签通过后调用 markOrderPaid()，其余（开通、记账、后台显示）都已经做好。
import { json, getSetting, setSetting, sameOrigin, readJson, hitRateLimit, LIMITS, waitText, today, audit, clientIp,
  hmacBytes, fromHex, b64url, b64urlText } from './util.js';

export const DEFAULT_PLANS = [
  { id: 'per1k', name: '按字数购买', desc: '按需购买字数，不过期', price_cents: 100, chars: 1000, per_unit: true, max_qty: 500 },
  { id: 'pack100k', name: '10 万字包', desc: '一次购买 10 万字，不过期', price_cents: 6000, chars: 100000 },
  { id: 'month', name: '月度会员', desc: '30 天内每天可检测 20 万字', price_cents: 3900, days: 30, daily_chars: 200000 },
  { id: 'year', name: '年度会员', desc: '365 天内每天可检测 30 万字', price_cents: 29900, days: 365, daily_chars: 300000 },
];

export async function billingConfig(env) {
  let plans = DEFAULT_PLANS;
  try { const p = JSON.parse(await getSetting(env, 'plans', 'null')); if (Array.isArray(p) && p.length) plans = p; } catch { /* 用默认 */ }
  return {
    billing_enabled: (await getSetting(env, 'billing_enabled', '0')) === '1',
    free_daily_chars: parseInt(await getSetting(env, 'free_daily_chars', '50000'), 10) || 0,
    register_open: (await getSetting(env, 'register_open', '1')) === '1',
    plans,
    payment_ready: Object.keys(PAYMENT_PROVIDERS).some((k) => PAYMENT_PROVIDERS[k].configured(env)),
  };
}

export function validatePlans(plans) {
  if (!Array.isArray(plans) || !plans.length || plans.length > 20) return '套餐需要是 1–20 个的列表。';
  const ids = new Set();
  for (const p of plans) {
    if (!p || typeof p !== 'object') return '套餐格式不正确。';
    if (!/^[a-z0-9_-]{2,24}$/.test(String(p.id || ''))) return `套餐编号 ${p.id} 只能用小写字母、数字、下划线，2–24 位。`;
    if (ids.has(p.id)) return `套餐编号 ${p.id} 重复。`;
    ids.add(p.id);
    if (!p.name || String(p.name).length > 30) return '每个套餐都要有名称（30 字以内）。';
    if (!Number.isInteger(p.price_cents) || p.price_cents < 0 || p.price_cents > 10_000_000) return `套餐 ${p.id} 的价格（分）不正确。`;
    for (const k of ['chars', 'days', 'daily_chars', 'max_qty'])
      if (p[k] !== undefined && (!Number.isInteger(p[k]) || p[k] < 0 || p[k] > 1e9)) return `套餐 ${p.id} 的 ${k} 不正确。`;
    if (!p.chars && !p.days) return `套餐 ${p.id} 至少要有字数（chars）或天数（days）。`;
  }
  return null;
}

// ---------- 支付平台（预留） ----------
export const PAYMENT_PROVIDERS = {
  // 示例：接入时把 configured 改为检查对应密钥，并实现 create / verifyWebhook
  // alipay: { configured: (env) => !!env.ALIPAY_APP_ID && !!env.ALIPAY_PRIVATE_KEY, create: async (env, order, origin) => ({ pay_url }), verifyWebhook: async (env, req) => ({ order_id, provider_ref }) },
  // wechat: { ... }, stripe: { ... }
  manual: { configured: () => false },
};

export async function markOrderPaid(env, orderId, provider, ref, actor) {
  const o = await env.DB.prepare(`SELECT * FROM orders WHERE id = ?1`).bind(orderId).first();
  if (!o) return '订单不存在。';
  const done = await env.DB.prepare(`UPDATE orders SET status = 'paid', provider = ?2, provider_ref = ?3, paid_at = ?4 WHERE id = ?1 AND status = 'pending'`)
    .bind(orderId, provider, ref || null, Date.now()).run();
  if (!done.meta?.changes) return '订单不是"待支付"状态（可能已经开通过）。';
  await grant(env, o.user_id, { chars: o.chars, days: o.days, daily_chars: o.daily_chars, plan_id: o.plan_id });
  await audit(env, actor || provider, 'order_paid', `${orderId} ${o.plan_name} ${o.amount_cents / 100} 元`);
  return null;
}

// 给用户加字数 / 开通（或续期）会员
export async function grant(env, userId, { chars = 0, days = 0, daily_chars = 0, plan_id = null }) {
  if (chars > 0) await env.DB.prepare(`UPDATE users SET balance_chars = balance_chars + ?2 WHERE id = ?1`).bind(userId, chars).run();
  if (days > 0) {
    const u = await env.DB.prepare(`SELECT plan_until FROM users WHERE id = ?1`).bind(userId).first();
    const start = Math.max(Date.now(), u?.plan_until || 0);
    await env.DB.prepare(`UPDATE users SET plan_id = ?2, plan_until = ?3, plan_daily_chars = ?4 WHERE id = ?1`)
      .bind(userId, plan_id, start + days * 86400000, daily_chars || 0).run();
  }
}

// ---------- 额度 ----------
export async function usageToday(env, userId) {
  const r = await env.DB.prepare(`SELECT chars, requests FROM usage_daily WHERE user_id = ?1 AND day = ?2`).bind(userId, today()).first();
  return { chars: r?.chars || 0, requests: r?.requests || 0 };
}

// 检测 n 个字之前先算能不能用、从哪里扣：先用当天额度（会员每日额度或免费额度），不够再扣购买的字数
export async function planCharge(env, s, n) {
  if (s.isAdmin) return { ok: true, fromDaily: n, fromBalance: 0 };
  const cfg = await billingConfig(env);
  const used = (await usageToday(env, s.user_id)).chars;
  const planActive = s.plan_until > Date.now();
  const cap = planActive ? (s.plan_daily_chars || Infinity) : (cfg.billing_enabled ? cfg.free_daily_chars : (cfg.free_daily_chars || Infinity));
  const fromDaily = Math.min(n, Math.max(0, cap - used));
  const rest = n - fromDaily;
  if (rest > 0) {
    if (cfg.billing_enabled && s.balance_chars >= rest) return { ok: true, fromDaily, fromBalance: rest };
    const left = Math.max(0, cap - used);
    return { ok: false, message: cfg.billing_enabled
      ? `额度不足：本次需要 ${n} 字，今日剩余 ${left === Infinity ? '不限' : left} 字，账户余额 ${s.balance_chars} 字。请到"我的账户"购买字数或开通会员。`
      : `今日免费额度已用完（每天 ${cap} 字，已用 ${used} 字），明天零点（北京时间）恢复。` };
  }
  return { ok: true, fromDaily, fromBalance: 0 };
}

export async function recordCharge(env, s, n, charge) {
  const stmts = [env.DB.prepare(`INSERT INTO usage_daily (user_id, day, chars, requests) VALUES (?1, ?2, ?3, 1)
     ON CONFLICT(user_id, day) DO UPDATE SET chars = chars + ?3, requests = requests + 1`).bind(s.user_id, today(), n)];
  if (charge.fromBalance > 0)
    stmts.push(env.DB.prepare(`UPDATE users SET balance_chars = MAX(0, balance_chars - ?2) WHERE id = ?1`).bind(s.user_id, charge.fromBalance));
  await env.DB.batch(stmts);
}

export async function detectRateOk(env, s) {
  if (s.isAdmin) return null;
  const rl = await hitRateLimit(env, 'det:' + s.user_id, ...LIMITS.detectPerUser);
  return rl.ok ? null : `提交太频繁，请 ${waitText(rl.retryAfter)}后再试。`;
}

// ---------- 给检测服务用的 Key：登录用户不用再手动填 Key，由 Worker 代为签发（与后端 keys.py 同一套签名） ----------
export async function mintBackendKey(env, s) {
  const now = Math.floor(Date.now() / 1000);
  const payload = { i: (s.isAdmin ? 'adm' : 'u') + s.user_id.replace(/-/g, '').slice(0, 7), n: String(s.email).slice(0, 40),
    e: now + 3600, q: 0, t: now };
  const p64 = b64urlText(JSON.stringify(payload));
  const sig = b64url((await hmacBytes(fromHex(env.KEY_SIGNING_SECRET), p64)).slice(0, 24));
  return `atc-${p64}.${sig}`;
}

// ---------- 接口 ----------
export async function handleBillingApi(req, env, path, s) {
  if (path === '/api/billing/plans' && req.method === 'GET') {
    const c = await billingConfig(env);
    return json({ ok: true, billing_enabled: c.billing_enabled, free_daily_chars: c.free_daily_chars, payment_ready: c.payment_ready,
      register_open: c.register_open, plans: c.plans });
  }
  if (path.startsWith('/api/billing/webhook/')) {
    // 支付平台的异步通知地址（预留）：接入后在这里验签，再调用 markOrderPaid()
    const name = path.slice('/api/billing/webhook/'.length);
    const p = PAYMENT_PROVIDERS[name];
    if (!p || !p.configured(env) || !p.verifyWebhook) return json({ ok: false, message: '支付通道未开通。' }, 501);
    const r = await p.verifyWebhook(env, req);
    if (!r) return json({ ok: false }, 400);
    const e = await markOrderPaid(env, r.order_id, name, r.provider_ref, name);
    return json({ ok: !e, message: e || 'ok' });
  }
  if (!s) return json({ ok: false, message: '请先登录。' }, 401);
  if (path === '/api/billing/me' && req.method === 'GET') {
    const c = await billingConfig(env);
    const u = await usageToday(env, s.user_id);
    const { results } = await env.DB.prepare(`SELECT id, plan_name, amount_cents, status, created_at, paid_at FROM orders WHERE user_id = ?1 ORDER BY created_at DESC LIMIT 30`)
      .bind(s.user_id).all();
    const planActive = s.plan_until > Date.now();
    return json({ ok: true, email: s.email, created_at: s.created_at, billing_enabled: c.billing_enabled, free_daily_chars: c.free_daily_chars,
      balance_chars: s.balance_chars, plan: planActive ? { id: s.plan_id, until: s.plan_until, daily_chars: s.plan_daily_chars } : null,
      today: u, orders: results, payment_ready: c.payment_ready });
  }
  if (path === '/api/billing/checkout' && req.method === 'POST') {
    if (!sameOrigin(req)) return json({ ok: false, message: '请求来源不正确。' }, 403);
    const body = await readJson(req);
    const c = await billingConfig(env);
    if (!c.billing_enabled) return json({ ok: false, message: '网站目前免费使用，暂不需要购买。' }, 400);
    const plan = c.plans.find((p) => p.id === body?.plan_id);
    if (!plan) return json({ ok: false, message: '套餐不存在。' }, 400);
    const qty = plan.per_unit ? Math.max(1, Math.min(plan.max_qty || 500, parseInt(body.qty, 10) || 1)) : 1;
    const rl = await hitRateLimit(env, 'order:' + s.user_id, 20, 3600);
    if (!rl.ok) return json({ ok: false, message: '下单太频繁，请稍后再试。' }, 429);
    const id = 'o' + Date.now().toString(36) + crypto.randomUUID().slice(0, 6);
    await env.DB.prepare(`INSERT INTO orders (id, user_id, plan_id, plan_name, amount_cents, chars, days, daily_chars, status, created_at)
       VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, 'pending', ?9)`).bind(id, s.user_id, plan.id, plan.name + (qty > 1 ? ` ×${qty}` : ''),
      plan.price_cents * qty, (plan.chars || 0) * qty, (plan.days || 0) * qty, plan.daily_chars || 0, Date.now()).run();
    await audit(env, s.email, 'order_created', `${id} ${plan.name} ×${qty}`, clientIp(req));
    const provider = Object.entries(PAYMENT_PROVIDERS).find(([, p]) => p.configured(env) && p.create);
    if (provider) {
      const r = await provider[1].create(env, { id, amount_cents: plan.price_cents * qty, name: plan.name }, new URL(req.url).origin);
      return json({ ok: true, order_id: id, pay_url: r.pay_url });
    }
    return json({ ok: true, order_id: id, pay_url: null,
      message: `订单 ${id} 已创建（${(plan.price_cents * qty / 100).toFixed(2)} 元）。在线支付通道尚未开通，请联系管理员付款后由管理员为你开通。` });
  }
  return json({ ok: false, message: '接口不存在。' }, 404);
}

export { setSetting };

// ---------- 上传文件检测：提交前字数未知，检测完成后按结果里的总字数扣额度 ----------
// 提交时只要求还有剩余额度；结果在提交响应里（wait）或之后查询任务时才拿到，拿到后扣一次（同一任务只扣一次）
export async function hasAnyQuota(env, s) {
  return planCharge(env, s, 1);
}

export async function settleFileJob(env, s, payload, isSubmit) {
  try {
    const id = payload?.job_id || payload?.id;
    if (payload?.status === 'done') {
      const n = Number(payload?.result?.summary?.total_chars) || 0;
      if (!isSubmit) {
        const del = await env.DB.prepare(`DELETE FROM file_jobs WHERE job_id = ?1 AND user_id = ?2`).bind(String(id), s.user_id).run();
        if (!del.meta?.changes) return;          // 不是待结算的上传任务（文字检测已在提交时扣过）
      }
      if (n > 0) {
        const c = await planCharge(env, s, n);
        // 额度不够时也只扣到剩余为止（文件已经检测完了）
        const charge = c.ok ? c : { fromDaily: n, fromBalance: Math.min(s.balance_chars || 0, n) };
        await recordCharge(env, s, n, charge);
      }
    } else if (isSubmit && id) {
      await env.DB.prepare(`INSERT OR IGNORE INTO file_jobs (job_id, user_id, created_at) VALUES (?1, ?2, ?3)`)
        .bind(String(id), s.user_id, Date.now()).run();
      await env.DB.prepare(`DELETE FROM file_jobs WHERE created_at < ?1`).bind(Date.now() - 2 * 3600 * 1000).run();
    }
  } catch (e) { console.error('[settleFileJob]', e); }
}
