// 管理后台接口：用户列表、停用 / 启用 / 删除、加字数 / 开通会员、订单、商用设置、修改管理员登录地址。
// 所有修改都要先用管理员邮箱验证码确认（确认后 10 分钟内有效）。
import { json, sameOrigin, readJson, clientIp, audit, getSetting, setSetting, today, normEmail, esc } from './util.js';
import { requestOtp, verifyOtp, OtpError, ELEVATE_MS, adminSlug, SLUG_RE, sendEmail, adminEmail } from './auth.js';
import { billingConfig, validatePlans, markOrderPaid, grant } from './billing.js';
import { LEGAL_PAGES, legalConfig, defaultLegal } from './legal.js';

export async function handleAdminApi(req, env, path, s) {
  if (!s || !s.isAdmin) return json({ ok: false, message: '页面不存在。' }, 404);
  const ip = clientIp(req);
  const elevated = s.elevated_until > Date.now();
  try {
    if (req.method === 'GET') {
      const url = new URL(req.url);
      if (path === '/api/admin/overview') {
        const c = await billingConfig(env);
        const n = await env.DB.prepare(`SELECT COUNT(*) AS n, SUM(CASE WHEN created_at > ?1 THEN 1 ELSE 0 END) AS week FROM users WHERE is_admin = 0`)
          .bind(Date.now() - 7 * 86400000).first();
        const t = await env.DB.prepare(`SELECT COUNT(*) AS users, SUM(chars) AS chars, SUM(requests) AS req FROM usage_daily WHERE day = ?1`).bind(today()).first();
        const paid = await env.DB.prepare(`SELECT COUNT(*) AS n, SUM(amount_cents) AS sum FROM orders WHERE status = 'paid'`).first();
        const pend = await env.DB.prepare(`SELECT COUNT(*) AS n FROM orders WHERE status = 'pending'`).first();
        return json({ ok: true, elevated_until: elevated ? s.elevated_until : 0, admin_email: s.email,
          login_url: url.origin + '/login/' + await adminSlug(env), settings: c,
          stats: { users: n?.n || 0, users_week: n?.week || 0, today_users: t?.users || 0, today_chars: t?.chars || 0, today_requests: t?.req || 0,
            paid_orders: paid?.n || 0, paid_cents: paid?.sum || 0, pending_orders: pend?.n || 0 } });
      }
      if (path === '/api/admin/users') {
        const q = `%${(url.searchParams.get('q') || '').trim().toLowerCase()}%`;
        const page = Math.max(0, parseInt(url.searchParams.get('page') || '0', 10) || 0);
        const { results } = await env.DB.prepare(
          `SELECT u.id, u.email, u.status, u.balance_chars, u.plan_id, u.plan_until, u.plan_daily_chars, u.note, u.created_at,
                  u.last_login_at, u.last_login_ip, COALESCE(d.chars, 0) AS today_chars,
                  (SELECT COALESCE(SUM(chars), 0) FROM usage_daily x WHERE x.user_id = u.id) AS total_chars
           FROM users u LEFT JOIN usage_daily d ON d.user_id = u.id AND d.day = ?2
           WHERE u.is_admin = 0 AND u.email LIKE ?1 ORDER BY u.created_at DESC LIMIT 50 OFFSET ?3`).bind(q, today(), page * 50).all();
        const total = await env.DB.prepare(`SELECT COUNT(*) AS n FROM users WHERE is_admin = 0 AND email LIKE ?1`).bind(q).first();
        return json({ ok: true, users: results, total: total?.n || 0, page });
      }
      if (path === '/api/admin/orders') {
        const st = url.searchParams.get('status') || '';
        const { results } = await env.DB.prepare(
          `SELECT o.*, u.email FROM orders o LEFT JOIN users u ON u.id = o.user_id
           WHERE (?1 = '' OR o.status = ?1) ORDER BY o.created_at DESC LIMIT 100`).bind(st).all();
        return json({ ok: true, orders: results });
      }
      if (path === '/api/admin/legal') {
        const c = await legalConfig(env);
        const texts = {};
        for (const pg of LEGAL_PAGES) for (const lang of ['zh', 'en']) {
          const custom = await getSetting(env, `legal:${pg}:${lang}`);
          texts[`${pg}:${lang}`] = { text: custom || defaultLegal(pg, lang), custom: !!custom };
        }
        return json({ ok: true, contacts: c.contacts, updated: c.updated, texts });
      }
      if (path === '/api/admin/audit') {
        const { results } = await env.DB.prepare(`SELECT at, actor, action, detail, ip FROM audit_log ORDER BY id DESC LIMIT 200`).all();
        return json({ ok: true, items: results });
      }
      return json({ ok: false, message: '接口不存在。' }, 404);
    }

    if (req.method !== 'POST') return json({ ok: false, message: '不支持的请求方式。' }, 405);
    if (!sameOrigin(req)) return json({ ok: false, message: '请求来源不正确。' }, 403);
    const body = await readJson(req);
    if (!body) return json({ ok: false, message: '请求格式不正确。' }, 400);

    if (path === '/api/admin/elevate/request') {
      await requestOtp(env, req, s.email, 'admin_elevate');
      return json({ ok: true, message: '确认码已发到管理员邮箱。' });
    }
    if (path === '/api/admin/elevate/verify') {
      const e = await verifyOtp(env, s.email, 'admin_elevate', body.code);
      if (e) return json({ ok: false, message: e }, 400);
      const until = Date.now() + ELEVATE_MS;
      await env.DB.prepare(`UPDATE sessions SET elevated_until = ?2 WHERE id = ?1`).bind(s.sid, until).run();
      await audit(env, s.email, 'admin_elevate', null, ip);
      return json({ ok: true, elevated_until: until, message: '已确认，10 分钟内的修改无需再次输入验证码。' });
    }
    if (path === '/api/admin/logout-everywhere') {
      await env.DB.prepare(`DELETE FROM sessions WHERE user_id = ?1`).bind(s.user_id).run();
      return json({ ok: true });
    }

    // 以下都是修改操作：必须先通过邮箱确认
    if (!elevated) return json({ ok: false, need_elevate: true, message: '这项修改需要先用管理员邮箱验证码确认。' }, 403);

    if (path === '/api/admin/users/update') {
      const u = await env.DB.prepare(`SELECT id, email FROM users WHERE id = ?1 AND is_admin = 0`).bind(String(body.id || '')).first();
      if (!u) return json({ ok: false, message: '用户不存在。' }, 404);
      const act = body.action;
      let detail = u.email;
      if (act === 'disable' || act === 'enable') {
        await env.DB.prepare(`UPDATE users SET status = ?2 WHERE id = ?1`).bind(u.id, act === 'disable' ? 'disabled' : 'active').run();
        if (act === 'disable') await env.DB.prepare(`DELETE FROM sessions WHERE user_id = ?1`).bind(u.id).run();
      } else if (act === 'grant_chars') {
        const n = parseInt(body.chars, 10);
        if (!Number.isInteger(n) || n === 0 || Math.abs(n) > 1e8) return json({ ok: false, message: '字数不正确。' }, 400);
        await env.DB.prepare(`UPDATE users SET balance_chars = MAX(0, balance_chars + ?2) WHERE id = ?1`).bind(u.id, n).run();
        detail += ` ${n > 0 ? '+' : ''}${n} 字`;
      } else if (act === 'set_plan') {
        const days = parseInt(body.days, 10), daily = parseInt(body.daily_chars, 10) || 0;
        if (!Number.isInteger(days) || days < 0 || days > 3660) return json({ ok: false, message: '天数不正确。' }, 400);
        if (days === 0) await env.DB.prepare(`UPDATE users SET plan_id = NULL, plan_until = 0, plan_daily_chars = 0 WHERE id = ?1`).bind(u.id).run();
        else await grant(env, u.id, { days, daily_chars: daily, plan_id: String(body.plan_id || 'manual').slice(0, 24) });
        detail += ` 会员 ${days} 天，每天 ${daily || '不限'} 字`;
      } else if (act === 'note') {
        await env.DB.prepare(`UPDATE users SET note = ?2 WHERE id = ?1`).bind(u.id, String(body.note || '').slice(0, 200)).run();
      } else if (act === 'logout') {
        await env.DB.prepare(`DELETE FROM sessions WHERE user_id = ?1`).bind(u.id).run();
      } else if (act === 'delete') {
        await env.DB.batch([
          env.DB.prepare(`DELETE FROM sessions WHERE user_id = ?1`).bind(u.id),
          env.DB.prepare(`DELETE FROM usage_daily WHERE user_id = ?1`).bind(u.id),
          env.DB.prepare(`DELETE FROM otp_codes WHERE email = ?1`).bind(u.email),
          env.DB.prepare(`DELETE FROM login_attempts WHERE key LIKE ?1`).bind(`u:${u.email}|%`),
          env.DB.prepare(`UPDATE orders SET status = CASE WHEN status = 'pending' THEN 'cancelled' ELSE status END WHERE user_id = ?1`).bind(u.id),
          env.DB.prepare(`DELETE FROM users WHERE id = ?1`).bind(u.id),
        ]);
      } else return json({ ok: false, message: '未知操作。' }, 400);
      await audit(env, s.email, 'user_' + act, detail, ip);
      return json({ ok: true, message: '已完成。' });
    }
    if (path === '/api/admin/orders/update') {
      const id = String(body.id || '');
      if (body.action === 'mark_paid') {
        const e = await markOrderPaid(env, id, 'manual', String(body.ref || '').slice(0, 80), s.email);
        return e ? json({ ok: false, message: e }, 400) : json({ ok: true, message: '已标记为已付款，并已为用户开通。' });
      }
      if (body.action === 'cancel') {
        const r = await env.DB.prepare(`UPDATE orders SET status = 'cancelled' WHERE id = ?1 AND status = 'pending'`).bind(id).run();
        await audit(env, s.email, 'order_cancel', id, ip);
        return json({ ok: !!r.meta?.changes, message: r.meta?.changes ? '已取消。' : '只能取消"待支付"的订单。' });
      }
      return json({ ok: false, message: '未知操作。' }, 400);
    }
    if (path === '/api/admin/settings') {
      const ch = [];
      if (body.billing_enabled !== undefined) { await setSetting(env, 'billing_enabled', body.billing_enabled ? '1' : '0'); ch.push(`收费 ${body.billing_enabled ? '开' : '关'}`); }
      if (body.register_open !== undefined) { await setSetting(env, 'register_open', body.register_open ? '1' : '0'); ch.push(`注册 ${body.register_open ? '开' : '关'}`); }
      if (body.free_daily_chars !== undefined) {
        const n = parseInt(body.free_daily_chars, 10);
        if (!Number.isInteger(n) || n < 0 || n > 1e8) return json({ ok: false, message: '每日免费字数不正确。' }, 400);
        await setSetting(env, 'free_daily_chars', n); ch.push(`免费额度 ${n}`);
      }
      if (body.site_daily_chars !== undefined) {
        const n = parseInt(body.site_daily_chars, 10);
        if (!Number.isInteger(n) || n < 0 || n > 1e9) return json({ ok: false, message: '全站每日字数上限不正确。' }, 400);
        await setSetting(env, 'site_daily_chars', n); ch.push(`全站上限 ${n}`);
      }
      if (body.plans !== undefined) {
        const e = validatePlans(body.plans);
        if (e) return json({ ok: false, message: e }, 400);
        await setSetting(env, 'plans', JSON.stringify(body.plans)); ch.push(`套餐 ${body.plans.length} 个`);
      }
      await audit(env, s.email, 'settings', ch.join('，'), ip);
      return json({ ok: true, message: '已保存。' });
    }
    if (path === '/api/admin/legal') {
      const pg = String(body.page || ''), lang = String(body.lang || '');
      if (!LEGAL_PAGES.includes(pg) || !['zh', 'en'].includes(lang)) return json({ ok: false, message: '页面或语言不正确。' }, 400);
      if (body.contacts !== undefined) {
        const c = String(body.contacts).trim().slice(0, 300);
        if (!c || !/@/.test(c)) return json({ ok: false, message: '请至少填写一个联系邮箱。' }, 400);
        await setSetting(env, 'legal:contacts', c);
      }
      if (body.updated !== undefined && String(body.updated).trim()) await setSetting(env, 'legal:updated', String(body.updated).trim().slice(0, 20));
      if (body.reset) {
        await env.DB.prepare(`DELETE FROM settings WHERE key = ?1`).bind(`legal:${pg}:${lang}`).run();
        await audit(env, s.email, 'legal_reset', `${pg}:${lang}`, ip);
        return json({ ok: true, message: '已恢复默认文字。' });
      }
      const text = String(body.text || '');
      if (text.trim().length < 20 || text.length > 30000) return json({ ok: false, message: '正文太短或太长（20–30000 字）。' }, 400);
      await setSetting(env, `legal:${pg}:${lang}`, text);
      await audit(env, s.email, 'legal_update', `${pg}:${lang} ${text.length} 字`, ip);
      return json({ ok: true, message: '已保存，网站上立即生效。' });
    }
    if (path === '/api/admin/login-url') {
      const slug = String(body.slug || '').trim();
      if (!SLUG_RE.test(slug)) return json({ ok: false, message: '登录地址只能用字母、数字、下划线、短横线，长度 16–80 位。' }, 400);
      await setSetting(env, 'admin_login_slug', slug);
      // 旧地址立即失效；其他已登录的管理员会话全部退出，只保留当前这个
      await env.DB.prepare(`DELETE FROM sessions WHERE user_id = ?1 AND id != ?2`).bind(s.user_id, s.sid).run();
      const link = new URL(req.url).origin + '/login/' + slug;
      await audit(env, s.email, 'admin_login_url_changed', null, ip);
      await sendEmail(env, adminEmail(env), 'AI 文本检测 · 管理员登录地址已修改',
        `<div style="font-family:sans-serif;font-size:15px;line-height:1.8">管理员登录地址已改为：<br><a href="${esc(link)}">${esc(link)}</a><br>旧地址已经失效。如果这不是您本人的操作，请立即修改管理员密码。</div>`).catch(() => {});
      return json({ ok: true, url: link, message: '登录地址已修改，新地址也已发到管理员邮箱。' });
    }
    return json({ ok: false, message: '接口不存在。' }, 404);
  } catch (err) {
    if (err instanceof OtpError) return json({ ok: false, message: err.message }, err.status);
    console.error('[admin]', path, err);
    return json({ ok: false, message: '服务器出错：' + String(err.message || err).slice(0, 200) }, 500);
  }
}

export { normEmail };
