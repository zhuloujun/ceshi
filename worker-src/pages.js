// Worker 自己生成的页面：首页、登录 / 注册 / 找回密码、我的账户、管理员登录、管理后台。
import { esc } from './util.js';

const CSS = `
:root{--paper:#f7f3ea;--card:#fffdf8;--ink:#2b2620;--soft:#6b6257;--line:#e3dccd;--red:#b0413e;--red-soft:#f3e2df;--teal:#2f6f6a;--gold:#8a6d3b;
--font-d:"Noto Serif SC","Songti SC",serif;--font-b:"Noto Sans SC","PingFang SC","Microsoft YaHei",sans-serif}
@media (prefers-color-scheme: dark){:root{--paper:#1c1a17;--card:#24211d;--ink:#ece6da;--soft:#a79e90;--line:#3a352e;--red:#e0716c;--red-soft:#3a2523;--teal:#6fb5ad;--gold:#c9a86a}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--font-b);font-size:15px;line-height:1.75}
a{color:var(--red)}
.wrap{max-width:1040px;margin:0 auto;padding:0 16px}
header.top{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:18px 0;border-bottom:1px solid var(--line)}
.brand{display:flex;align-items:center;gap:10px;text-decoration:none;color:var(--ink)}
.seal{width:38px;height:38px;border:2px solid var(--red);color:var(--red);display:grid;place-items:center;font-family:var(--font-d);font-weight:700;border-radius:4px;transform:rotate(-4deg)}
.brand b{font-family:var(--font-d);font-size:22px;letter-spacing:2px}
nav.links{display:flex;gap:8px;flex-wrap:wrap}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;border:1px solid var(--ink);background:transparent;color:var(--ink);padding:8px 16px;border-radius:6px;font:inherit;font-size:14px;cursor:pointer;text-decoration:none;min-height:40px}
.btn.primary{background:var(--red);border-color:var(--red);color:#fff}
.btn.small{padding:4px 10px;font-size:13px;min-height:32px}
.btn:disabled{opacity:.5;cursor:not-allowed}
.hero{padding:56px 0 36px}
.hero h1{font-family:var(--font-d);font-size:clamp(28px,5vw,44px);line-height:1.3;margin:0 0 14px}
.hero p{color:var(--soft);max-width:640px;margin:0 0 22px;font-size:16px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px}
.card h3{font-family:var(--font-d);margin:0 0 6px;font-size:18px}
.card p{margin:0;color:var(--soft);font-size:14px}
h2.sec{font-family:var(--font-d);font-size:24px;margin:40px 0 6px}
p.sub{color:var(--soft);margin:0 0 16px}
.price{font-family:var(--font-d);font-size:28px;color:var(--red);margin:8px 0}
.price small{font-size:14px;color:var(--soft)}
.tag{display:inline-block;font-size:12px;padding:1px 8px;border-radius:99px;background:var(--red-soft);color:var(--red)}
.tag.ok{background:transparent;border:1px solid var(--teal);color:var(--teal)}
footer{color:var(--soft);font-size:13px;padding:30px 0;border-top:1px solid var(--line);margin-top:40px}
.auth{max-width:420px;margin:40px auto}
.auth h1{font-family:var(--font-d);font-size:26px;margin:0 0 4px}
label.f{display:block;font-size:13px;color:var(--soft);margin:14px 0 4px}
input.i,select.i,textarea.i{width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink);font:inherit;font-size:15px}
textarea.i{min-height:220px;font-family:ui-monospace,monospace;font-size:13px}
.row{display:flex;gap:8px;align-items:center}
.row .i{flex:1}
.cap img{height:44px;border:1px solid var(--line);border-radius:6px;cursor:pointer;display:block}
.msg{margin-top:12px;font-size:14px;min-height:1.5em}
.msg.bad{color:var(--red)}.msg.good{color:var(--teal)}
.hint{font-size:13px;color:var(--soft)}
.hidden{display:none!important}
table.t{width:100%;border-collapse:collapse;font-size:13px}
table.t th,table.t td{border-bottom:1px solid var(--line);padding:7px 6px;text-align:left;vertical-align:top}
table.t th{color:var(--soft);font-weight:500;white-space:nowrap}
.scroll{overflow-x:auto}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin:18px 0}
.tabs button{border:1px solid var(--line);background:var(--card);color:var(--ink);padding:6px 14px;border-radius:99px;font:inherit;font-size:14px;cursor:pointer}
.tabs button.on{background:var(--ink);color:var(--paper);border-color:var(--ink)}
.stat b{display:block;font-family:var(--font-d);font-size:24px}
.banner{background:var(--red-soft);border:1px solid var(--red);color:var(--ink);padding:10px 14px;border-radius:8px;font-size:14px;margin:12px 0}
@media (max-width:560px){header.top{flex-wrap:wrap}.hero{padding:32px 0 20px}}
`;

// 公用前端脚本：JSON 请求、算术验证码、Turnstile
const CLIENT = `
const $=(id)=>document.getElementById(id);
async function api(path, body, method){
  const opt={method:method||(body?'POST':'GET'),headers:{},credentials:'same-origin'};
  if(body){opt.headers['Content-Type']='application/json';opt.body=JSON.stringify(body);}
  let r; try{ r=await fetch(path,opt);}catch(e){ return {ok:false,message:'网络连接失败，请稍后再试。'};}
  let j={}; try{ j=await r.json(); }catch(e){ j={ok:false,message:'服务器返回了无法识别的内容（HTTP '+r.status+'）。'}; }
  if(!r.ok && j.ok===undefined) j.ok=false;
  return j;
}
function say(el,text,good){ el.textContent=text||''; el.className='msg '+(good?'good':'bad'); }
function Captcha(box){
  const st={id:null,tsToken:'',tsWidget:null};
  box.innerHTML='<label class="f">安全验证：请计算图片里的算术题（看不清可点图片换一题）</label><div class="row cap"><img alt="算术题"><input class="i" inputmode="numeric" autocomplete="off" placeholder="答案"></div><div class="ts" style="margin-top:8px"></div>';
  const img=box.querySelector('img'), inp=box.querySelector('input'), ts=box.querySelector('.ts');
  async function load(){ inp.value=''; const j=await api('/api/auth/captcha'); if(!j.ok){img.alt=j.message||'加载失败';return;} st.id=j.id; img.src=j.image;
    if(j.turnstile_site_key && !st.tsWidget){ const s=document.createElement('script'); s.src='https://challenges.cloudflare.com/turnstile/v0/api.js?onload=__tsReady'; s.async=true;
      window.__tsReady=()=>{ st.tsWidget=window.turnstile.render(ts,{sitekey:j.turnstile_site_key,callback:(t)=>{st.tsToken=t;}}); }; document.head.appendChild(s); }
    else if(st.tsWidget && window.turnstile){ window.turnstile.reset(st.tsWidget); st.tsToken=''; } }
  img.addEventListener('click',load); load();
  return { fields:()=>({captcha_id:st.id,captcha_answer:inp.value.trim(),turnstile_token:st.tsToken}), reload:load };
}
`;

function layout(title, body, { nav = '', script = '' } = {}) {
  return `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${esc(title)}</title><meta name="robots" content="noindex">
<link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=Noto+Serif+SC:wght@600;700&family=Noto+Sans+SC:wght@400;500&display=swap" rel="stylesheet">
<style>${CSS}</style></head><body>
<div class="wrap"><header class="top"><a class="brand" href="/home"><span class="seal">审</span><b>审读</b></a><nav class="links"><button class="btn small" id="atc-lang" type="button"></button>${nav}</nav></header>
${body}
<footer>审读 · AI 文本检测。检测结果只供参考，不能作为学术不端的判定依据。</footer></div>
<script>${CLIENT}${script}</script></body></html>`;
}

const guestNav = `<a class="btn small" href="/login">登录</a><a class="btn small primary" href="/register">免费注册</a>`;
const userNav = `<a class="btn small" href="/">开始检测</a><a class="btn small" href="/account">我的账户</a><button class="btn small" onclick="api('/api/auth/logout',{}).then(()=>location.href='/home')">退出</button>`;

export function landingPage(loggedIn) {
  const body = `
<section class="hero"><h1>论文与古典文学的<br>AI 文本检测</h1>
<p>覆盖现代汉语论文、文言文、诗词与英文。逐段标出疑似 AI 生成的文字，给出"AI 率"和可下载的检测报告。注册后即可使用。</p>
<div class="row" style="flex-wrap:wrap">${loggedIn ? '<a class="btn primary" href="/">进入检测</a><a class="btn" href="/account">我的账户</a>'
    : '<a class="btn primary" href="/register">免费注册</a><a class="btn" href="/login">已有账户，登录</a>'}</div></section>
<div class="grid">
<div class="card"><h3>多种文体</h3><p>现代汉语、文言、诗词曲赋、对联、英文，按文体分别用专门的模型判断。</p></div>
<div class="card"><h3>逐段标注</h3><p>每段给出概率与"高 / 中 / 轻度疑似"，引文和参考文献不计入 AI 率。</p></div>
<div class="card"><h3>新一代大模型</h3><p>针对 DeepSeek、千问、豆包、Kimi、文心、ChatGPT、Gemini、Claude 等持续训练。</p></div>
<div class="card"><h3>账户安全</h3><p>邮箱验证码注册、登录失败锁定、重要操作二次验证。文本只用于本次检测。</p></div>
</div>
<h2 class="sec">套餐与价格</h2><p class="sub" id="planNote">正在读取……</p>
<div class="grid" id="plans"></div>`;
  const script = `
(async()=>{ const j=await api('/api/billing/plans'); const box=$('plans'), note=$('planNote');
  if(!j.ok){note.textContent='暂时无法读取套餐。';return;}
  note.textContent = j.billing_enabled ? ('注册后每天免费 '+(j.free_daily_chars||0).toLocaleString()+' 字；超出部分可购买字数或开通会员。'+(j.payment_ready?'':'（在线支付即将开通，目前可联系管理员开通。）'))
    : ('目前免费使用'+(j.free_daily_chars?'：每个账户每天 '+j.free_daily_chars.toLocaleString()+' 字':'')+'。以下付费套餐为预留，开通后生效。');
  box.innerHTML = j.plans.map(p=>'<div class="card"><h3>'+esc(p.name)+' '+(j.billing_enabled?'':'<span class="tag">即将开放</span>')+'</h3><div class="price">¥'+(p.price_cents/100).toFixed(p.price_cents%100?2:0)+'<small>'+(p.per_unit?' / '+(p.chars||0).toLocaleString()+' 字':'')+'</small></div><p>'+esc(p.desc||'')+'</p></div>').join('');
})();
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}`;
  return layout('审读 · AI 文本检测', body, { nav: loggedIn ? userNav : guestNav, script });
}

export function authPage(mode) {
  const titles = { login: '登录', register: '注册账户', forgot: '找回密码' };
  const body = `<div class="auth card">
<h1>${titles[mode]}</h1>
<p class="hint">${mode === 'login' ? '还没有账户？<a href="/register">免费注册</a>' : mode === 'register' ? '已有账户？<a href="/login">直接登录</a>' : '想起密码了？<a href="/login">返回登录</a>'}</p>
<div id="step1">
<label class="f" for="email">邮箱</label><input class="i" id="email" type="email" autocomplete="email" maxlength="120">
${mode === 'login' ? '<label class="f" for="pw">密码</label><input class="i" id="pw" type="password" autocomplete="current-password" maxlength="128">' : ''}
<div id="cap"></div>
<button class="btn primary" style="width:100%;margin-top:16px" id="go1">${mode === 'login' ? '登录' : '发送邮箱验证码'}</button>
${mode === 'login' ? '<p class="hint" style="margin-top:10px"><a href="/forgot">忘记密码？</a></p>' : ''}
</div>
<div id="step2" class="hidden">
<p class="hint">验证码已发到 <b id="em"></b>，10 分钟内有效。</p>
<label class="f" for="code">邮箱验证码</label><input class="i" id="code" inputmode="numeric" autocomplete="one-time-code" maxlength="6">
<label class="f" for="pw1">${mode === 'register' ? '设置密码' : '新密码'}（至少 8 位，包含字母和数字）</label><input class="i" id="pw1" type="password" autocomplete="new-password" maxlength="128">
<label class="f" for="pw2">再输一次</label><input class="i" id="pw2" type="password" autocomplete="new-password" maxlength="128">
<button class="btn primary" style="width:100%;margin-top:16px" id="go2">${mode === 'register' ? '完成注册' : '重设密码'}</button>
<p class="hint"><a href="#" id="back">返回上一步重新发送</a></p>
</div>
<div class="msg" id="msg"></div></div>`;
  const script = `
const cap=Captcha($('cap')), msg=$('msg'), mode=${JSON.stringify(mode)};
$('go1').onclick=async()=>{ const email=$('email').value.trim(); if(!email){say(msg,'请填写邮箱。');return;}
  $('go1').disabled=true; say(msg,'请稍候……',true);
  let j;
  if(mode==='login'){ j=await api('/api/auth/login',{email,password:$('pw').value,...cap.fields()}); if(j.ok){location.href='/';return;} }
  else { j=await api(mode==='register'?'/api/auth/register/request-otp':'/api/auth/forgot/request-otp',{email,...cap.fields()});
    if(j.ok){ $('em').textContent=email; $('step1').classList.add('hidden'); $('step2').classList.remove('hidden'); say(msg,j.message,true); $('go1').disabled=false; return; } }
  say(msg,j.message); cap.reload(); $('go1').disabled=false; };
$('back').onclick=(e)=>{e.preventDefault(); $('step2').classList.add('hidden'); $('step1').classList.remove('hidden'); cap.reload(); say(msg,'');};
if($('go2')) $('go2').onclick=async()=>{ if($('pw1').value!==$('pw2').value){say(msg,'两次输入的密码不一样。');return;}
  $('go2').disabled=true;
  const j=await api(mode==='register'?'/api/auth/register/verify':'/api/auth/forgot/reset',{email:$('email').value.trim(),code:$('code').value.trim(),password:$('pw1').value});
  $('go2').disabled=false;
  if(j.ok){ say(msg,j.message,true); setTimeout(()=>location.href=mode==='register'?'/':'/login',800); } else say(msg,j.message); };`;
  return layout(`审读 · ${titles[mode]}`, body, { nav: mode === 'login' ? '<a class="btn small" href="/home">首页</a>' : '<a class="btn small" href="/home">首页</a><a class="btn small" href="/login">登录</a>', script });
}

export function accountPage() {
  const body = `<h2 class="sec">我的账户</h2><p class="sub" id="who">正在读取……</p>
<div class="grid" id="stats"></div>
<div id="buyBox"><h2 class="sec">购买字数 / 开通会员</h2><p class="sub" id="buyNote"></p><div class="grid" id="plans"></div><div class="msg" id="buyMsg"></div></div>
<h2 class="sec">我的订单</h2><div class="scroll card"><table class="t"><thead><tr><th>订单号</th><th>内容</th><th>金额</th><th>状态</th><th>时间</th></tr></thead><tbody id="orders"></tbody></table></div>
<h2 class="sec">修改密码</h2><div class="card" style="max-width:460px">
<label class="f">原密码</label><input class="i" id="old" type="password" autocomplete="current-password">
<label class="f">新密码（至少 8 位，包含字母和数字）</label><input class="i" id="n1" type="password" autocomplete="new-password">
<label class="f">再输一次</label><input class="i" id="n2" type="password" autocomplete="new-password">
<label class="f">邮箱验证码</label><div class="row"><input class="i" id="pcode" inputmode="numeric" maxlength="6"><button class="btn" id="sendp">获取验证码</button></div>
<button class="btn primary" style="margin-top:14px" id="chg">确认修改</button><div class="msg" id="pmsg"></div></div>`;
  const script = `
const ST={paid:'已付款',pending:'待支付',cancelled:'已取消'};
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
const fmt=(t)=>t?new Date(t).toLocaleString('zh-CN',{hour12:false}):'';
async function load(){ const j=await api('/api/billing/me'); if(!j.ok){ location.href='/login'; return; }
  $('who').textContent=j.email+' · 注册于 '+fmt(j.created_at);
  const cap = j.plan ? (j.plan.daily_chars||'不限') : (j.free_daily_chars||'不限');
  $('stats').innerHTML=[['今日已检测',j.today.chars.toLocaleString()+' 字'],['今日额度',(typeof cap==='number'?cap.toLocaleString()+' 字':cap)],
    ['已购字数余额',j.balance_chars.toLocaleString()+' 字'],['会员',j.plan?('有效期至 '+fmt(j.plan.until)):'未开通']].map(x=>'<div class="card stat"><span class="hint">'+x[0]+'</span><b>'+x[1]+'</b></div>').join('');
  $('orders').innerHTML=j.orders.length?j.orders.map(o=>'<tr><td>'+esc(o.id)+'</td><td>'+esc(o.plan_name)+'</td><td>¥'+(o.amount_cents/100).toFixed(2)+'</td><td>'+(ST[o.status]||o.status)+'</td><td>'+fmt(o.created_at)+'</td></tr>').join(''):'<tr><td colspan="5" class="hint">暂无订单</td></tr>';
  const p=await api('/api/billing/plans');
  $('buyNote').textContent = p.billing_enabled ? (p.payment_ready?'选择套餐后跳转到支付页面。':'在线支付通道尚未开通：下单后请联系管理员付款开通。') : '网站目前免费使用，付费套餐为预留功能，开通后即可购买。';
  $('plans').innerHTML=p.plans.map(x=>'<div class="card"><h3>'+esc(x.name)+'</h3><div class="price">¥'+(x.price_cents/100).toFixed(x.price_cents%100?2:0)+'<small>'+(x.per_unit?' / '+(x.chars||0).toLocaleString()+' 字':'')+'</small></div><p>'+esc(x.desc||'')+'</p>'
    +(x.per_unit?'<div class="row" style="margin-top:8px"><span class="hint">数量</span><input class="i" type="number" min="1" max="'+(x.max_qty||500)+'" value="1" id="q_'+esc(x.id)+'" style="max-width:90px"></div>':'')
    +'<button class="btn primary small" style="margin-top:10px" '+(p.billing_enabled?'':'disabled')+' onclick="buy(\\''+esc(x.id)+'\\')">'+(p.billing_enabled?'购买':'即将开放')+'</button></div>').join('');
}
async function buy(id){ const q=$('q_'+id); const j=await api('/api/billing/checkout',{plan_id:id,qty:q?parseInt(q.value,10):1});
  if(j.ok && j.pay_url){ location.href=j.pay_url; return; } say($('buyMsg'),j.message,j.ok); load(); }
$('sendp').onclick=async()=>{ const j=await api('/api/auth/password/request-otp',{}); say($('pmsg'),j.message,j.ok); };
$('chg').onclick=async()=>{ if($('n1').value!==$('n2').value){say($('pmsg'),'两次输入的新密码不一样。');return;}
  const j=await api('/api/auth/password/change',{old_password:$('old').value,password:$('n1').value,code:$('pcode').value.trim()}); say($('pmsg'),j.message,j.ok); };
load();`;
  return layout('审读 · 我的账户', body, { nav: userNav, script });
}

export function adminLoginPage(slug) {
  const body = `<div class="auth card"><h1>管理员登录</h1><p class="hint">密码正确后，还需要输入发到管理员邮箱的验证码。</p>
<div id="s1"><label class="f">管理员邮箱</label><input class="i" id="email" type="email" autocomplete="username">
<label class="f">密码</label><input class="i" id="pw" type="password" autocomplete="current-password"><div id="cap"></div>
<button class="btn primary" style="width:100%;margin-top:16px" id="go">下一步</button>
<p class="hint" style="margin-top:10px"><a href="#" id="toReset">第一次使用 / 忘记密码：设置管理员密码</a></p></div>
<div id="s2" class="hidden"><label class="f">邮箱验证码</label><input class="i" id="code" inputmode="numeric" maxlength="6">
<button class="btn primary" style="width:100%;margin-top:16px" id="go2">登录</button></div>
<div id="r1" class="hidden"><label class="f">管理员邮箱</label><input class="i" id="remail" type="email"><div id="cap2"></div>
<button class="btn primary" style="width:100%;margin-top:16px" id="rgo">发送验证码</button></div>
<div id="r2" class="hidden"><label class="f">邮箱验证码</label><input class="i" id="rcode" inputmode="numeric" maxlength="6">
<label class="f">新管理员密码（至少 12 位，包含字母和数字）</label><input class="i" id="rp1" type="password" autocomplete="new-password">
<label class="f">再输一次</label><input class="i" id="rp2" type="password" autocomplete="new-password">
<button class="btn primary" style="width:100%;margin-top:16px" id="rgo2">设置密码</button></div>
<div class="msg" id="msg"></div></div>`;
  const script = `
const SLUG=${JSON.stringify(slug)}, msg=$('msg'); let cap=Captcha($('cap')), cap2=null;
const show=(id)=>['s1','s2','r1','r2'].forEach(x=>$(x).classList.toggle('hidden',x!==id));
$('go').onclick=async()=>{ const j=await api('/api/auth/admin/login',{slug:SLUG,email:$('email').value.trim(),password:$('pw').value,...cap.fields()});
  if(j.ok){ show('s2'); say(msg,j.message,true);} else { say(msg,j.message); cap.reload(); } };
$('go2').onclick=async()=>{ const j=await api('/api/auth/admin/verify-otp',{slug:SLUG,email:$('email').value.trim(),code:$('code').value.trim()});
  if(j.ok) location.href='/admin/console'; else say(msg,j.message); };
$('toReset').onclick=(e)=>{ e.preventDefault(); show('r1'); if(!cap2) cap2=Captcha($('cap2')); say(msg,''); };
$('rgo').onclick=async()=>{ const j=await api('/api/auth/admin/forgot/request-otp',{slug:SLUG,email:$('remail').value.trim(),...cap2.fields()});
  if(j.ok){ show('r2'); say(msg,j.message,true);} else { say(msg,j.message); cap2.reload(); } };
$('rgo2').onclick=async()=>{ if($('rp1').value!==$('rp2').value){say(msg,'两次输入不一样。');return;}
  const j=await api('/api/auth/admin/forgot/reset',{slug:SLUG,email:$('remail').value.trim(),code:$('rcode').value.trim(),password:$('rp1').value});
  say(msg,j.message,j.ok); if(j.ok){ show('s1'); $('email').value=$('remail').value; cap.reload(); } };`;
  return layout('管理员登录', body, { script });
}

export function adminLinkRequestPage() {
  const body = `<div class="auth card"><h1>找回管理员登录地址</h1><p class="hint">填写管理员邮箱，登录地址会发到这个邮箱。</p>
<label class="f">管理员邮箱</label><input class="i" id="email" type="email"><div id="cap"></div>
<button class="btn primary" style="width:100%;margin-top:16px" id="go">发送登录地址</button><div class="msg" id="msg"></div></div>`;
  const script = `const cap=Captcha($('cap'));
$('go').onclick=async()=>{ const j=await api('/api/auth/admin/send-login-link',{email:$('email').value.trim(),...cap.fields()}); say($('msg'),j.message,j.ok); cap.reload(); };`;
  return layout('找回管理员登录地址', body, { nav: '<a class="btn small" href="/home">首页</a>', script });
}

export function adminConsolePage() {
  const body = `<h2 class="sec">管理后台</h2><p class="sub" id="who"></p>
<div class="banner hidden" id="elev">修改用户、订单和设置之前，需要先用管理员邮箱验证码确认（确认后 10 分钟内有效）。
<div class="row" style="margin-top:8px;flex-wrap:wrap"><button class="btn small" id="eSend">发送确认码</button><input class="i" id="eCode" inputmode="numeric" maxlength="6" placeholder="确认码" style="max-width:140px"><button class="btn small primary" id="eGo">确认</button><span class="hint" id="eMsg"></span></div></div>
<p class="hint" id="elevOk"></p>
<div class="tabs" id="tabs"><button data-t="users" class="on">注册用户</button><button data-t="orders">订单</button><button data-t="settings">商用设置</button><button data-t="security">登录地址与安全</button><button data-t="audit">操作记录</button><a class="btn small" href="/admin">检测服务管理（校准 / Key）</a></div>
<section data-p="users"><div class="grid" id="stats"></div>
<div class="row" style="margin:14px 0"><input class="i" id="q" placeholder="按邮箱搜索"><button class="btn" id="qGo">搜索</button></div>
<div class="scroll card"><table class="t"><thead><tr><th>邮箱</th><th>状态</th><th>注册时间</th><th>最近登录</th><th>今日 / 累计字数</th><th>余额</th><th>会员到期</th><th>备注</th><th>操作</th></tr></thead><tbody id="users"></tbody></table></div>
<div class="row" style="margin-top:10px"><button class="btn small" id="prev">上一页</button><span class="hint" id="pg"></span><button class="btn small" id="next">下一页</button></div></section>
<section data-p="orders" class="hidden"><div class="row" style="margin-bottom:10px"><select class="i" id="ost" style="max-width:180px"><option value="">全部</option><option value="pending">待支付</option><option value="paid">已付款</option><option value="cancelled">已取消</option></select></div>
<div class="scroll card"><table class="t"><thead><tr><th>订单号</th><th>用户</th><th>内容</th><th>金额</th><th>状态</th><th>下单时间</th><th>操作</th></tr></thead><tbody id="orders"></tbody></table></div></section>
<section data-p="settings" class="hidden"><div class="card">
<label class="row"><input type="checkbox" id="sBill"> 开启收费（关闭时网站免费使用，只受每日免费额度限制）</label>
<label class="row" style="margin-top:8px"><input type="checkbox" id="sReg"> 开放新用户注册</label>
<label class="f">每个用户每天免费字数（0 = 收费关闭时不限；收费开启时没有免费额度）</label><input class="i" id="sFree" type="number" min="0" style="max-width:220px">
<label class="f">全站每天最多检测多少字（所有用户合计，防止被刷光 Modal 额度；0 = 不限）</label><input class="i" id="sSite" type="number" min="0" style="max-width:220px">
<label class="f">套餐列表（JSON）：price_cents 价格（分）、chars 字数、days 会员天数、daily_chars 会员每日字数、per_unit 按份购买、max_qty 最多份数</label>
<textarea class="i" id="sPlans" spellcheck="false"></textarea>
<p class="hint" id="payInfo"></p>
<button class="btn primary" id="sSave" style="margin-top:10px">保存设置</button><div class="msg" id="sMsg"></div></div></section>
<section data-p="security" class="hidden"><div class="card">
<p>当前管理员登录地址（请保密，不要公开）：<br><code id="curUrl" style="word-break:break-all"></code></p>
<label class="f">新的登录地址后缀（字母、数字、下划线、短横线，16–80 位）</label>
<div class="row"><input class="i" id="slug" maxlength="80"><button class="btn" id="rand">随机生成</button></div>
<button class="btn primary" id="slugGo" style="margin-top:10px">修改登录地址</button><div class="msg" id="slugMsg"></div>
<p class="hint">修改后旧地址立即失效，新地址会发到管理员邮箱。忘记地址时可以在 <a href="/admin-link">/admin-link</a> 让系统把地址发到管理员邮箱。</p>
<button class="btn" id="outAll" style="margin-top:10px">退出所有设备上的管理员登录</button></div></section>
<section data-p="audit" class="hidden"><div class="scroll card"><table class="t"><thead><tr><th>时间</th><th>操作人</th><th>操作</th><th>内容</th><th>IP</th></tr></thead><tbody id="audit"></tbody></table></div></section>`;
  const script = `
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
const fmt=(t)=>t?new Date(t).toLocaleString('zh-CN',{hour12:false}):'—';
let page=0, ov=null;
async function overview(){ ov=await api('/api/admin/overview'); if(!ov.ok){ location.href='/home'; return; }
  $('who').textContent='管理员 '+ov.admin_email;
  const el=ov.elevated_until>Date.now(); $('elev').classList.toggle('hidden',el); $('elevOk').textContent=el?('已确认身份，'+fmt(ov.elevated_until)+' 前可直接修改。'):'';
  const s=ov.stats; $('stats').innerHTML=[['注册用户',s.users],['近 7 天新增',s.users_week],['今日活跃',s.today_users],['今日检测字数',(s.today_chars||0).toLocaleString()],['已付款订单',s.paid_orders+' 笔 / ¥'+((s.paid_cents||0)/100).toFixed(2)],['待支付订单',s.pending_orders]]
    .map(x=>'<div class="card stat"><span class="hint">'+x[0]+'</span><b>'+x[1]+'</b></div>').join('');
  $('curUrl').textContent=ov.login_url;
  const c=ov.settings; $('sBill').checked=c.billing_enabled; $('sReg').checked=c.register_open; $('sFree').value=c.free_daily_chars; $('sSite').value=c.site_daily_chars; $('sPlans').value=JSON.stringify(c.plans,null,2);
  $('payInfo').textContent=c.payment_ready?'已接入在线支付。':'尚未接入在线支付平台：用户下单后生成"待支付"订单，你确认收款后在"订单"里点"标记已付款"即可自动开通。';
}
async function act(path,body){ const j=await api(path,body); if(j.need_elevate){ $('elev').classList.remove('hidden'); $('elev').scrollIntoView({behavior:'smooth'}); }
  return j; }
$('eSend').onclick=async()=>{ const j=await api('/api/admin/elevate/request',{}); $('eMsg').textContent=j.message; };
$('eGo').onclick=async()=>{ const j=await api('/api/admin/elevate/verify',{code:$('eCode').value.trim()}); $('eMsg').textContent=j.message; if(j.ok) overview(); };
async function users(){ const j=await api('/api/admin/users?page='+page+'&q='+encodeURIComponent($('q').value.trim())); if(!j.ok) return;
  $('pg').textContent='第 '+(page+1)+' 页，共 '+j.total+' 人';
  $('users').innerHTML=j.users.length?j.users.map(u=>'<tr><td>'+esc(u.email)+'</td><td>'+(u.status==='active'?'<span class="tag ok">正常</span>':'<span class="tag">已停用</span>')+'</td><td>'+fmt(u.created_at)+'</td><td>'+fmt(u.last_login_at)+'<br><span class="hint">'+esc(u.last_login_ip||'')+'</span></td><td>'+u.today_chars.toLocaleString()+' / '+u.total_chars.toLocaleString()+'</td><td>'+u.balance_chars.toLocaleString()+'</td><td>'+(u.plan_until>Date.now()?fmt(u.plan_until)+'<br><span class="hint">每天 '+(u.plan_daily_chars||'不限')+' 字</span>':'—')+'</td><td>'+esc(u.note||'')+'</td><td style="white-space:nowrap">'
    +'<select class="i" style="min-width:120px" onchange="uact(\\''+u.id+'\\',this)"><option value="">选择操作</option><option value="'+(u.status==='active'?'disable':'enable')+'">'+(u.status==='active'?'停用':'启用')+'</option><option value="grant_chars">加 / 减字数</option><option value="set_plan">开通 / 取消会员</option><option value="note">修改备注</option><option value="logout">强制退出登录</option><option value="delete">删除账户</option></select></td></tr>').join(''):'<tr><td colspan="9" class="hint">没有用户</td></tr>'; }
async function uact(id,sel){ const a=sel.value; sel.value=''; if(!a) return; const b={id,action:a};
  if(a==='grant_chars'){ const n=prompt('增加多少字？（减少请填负数）','10000'); if(!n) return; b.chars=parseInt(n,10); }
  if(a==='set_plan'){ const d=prompt('会员天数（0 = 取消会员）','30'); if(d===null) return; b.days=parseInt(d,10); if(b.days>0){ const q=prompt('会员每天可检测多少字（0 = 不限）','200000'); if(q===null) return; b.daily_chars=parseInt(q,10);} }
  if(a==='note'){ const n=prompt('备注（200 字以内）',''); if(n===null) return; b.note=n; }
  if(a==='delete' && !confirm('确定删除这个账户？此操作不能撤销。')) return;
  const j=await act('/api/admin/users/update',b); alert(j.message); users(); }
async function orders(){ const j=await api('/api/admin/orders?status='+$('ost').value); if(!j.ok) return; const ST={paid:'已付款',pending:'待支付',cancelled:'已取消'};
  $('orders').innerHTML=j.orders.length?j.orders.map(o=>'<tr><td>'+esc(o.id)+'</td><td>'+esc(o.email||'(已删除)')+'</td><td>'+esc(o.plan_name)+'</td><td>¥'+(o.amount_cents/100).toFixed(2)+'</td><td>'+(ST[o.status]||o.status)+(o.provider?'<br><span class="hint">'+esc(o.provider)+' '+esc(o.provider_ref||'')+'</span>':'')+'</td><td>'+fmt(o.created_at)+'</td><td>'
    +(o.status==='pending'?'<button class="btn small" onclick="oact(\\''+o.id+'\\',\\'mark_paid\\')">标记已付款</button> <button class="btn small" onclick="oact(\\''+o.id+'\\',\\'cancel\\')">取消</button>':'')+'</td></tr>').join(''):'<tr><td colspan="7" class="hint">没有订单</td></tr>'; }
async function oact(id,a){ const b={id,action:a}; if(a==='mark_paid'){ const r=prompt('确认已收到这笔款？可填写收款流水号（可留空）',''); if(r===null) return; b.ref=r; }
  const j=await act('/api/admin/orders/update',b); alert(j.message); orders(); overview(); }
$('sSave').onclick=async()=>{ let plans; try{ plans=JSON.parse($('sPlans').value);}catch(e){ say($('sMsg'),'套餐 JSON 格式有误：'+e.message); return; }
  const j=await act('/api/admin/settings',{billing_enabled:$('sBill').checked,register_open:$('sReg').checked,free_daily_chars:parseInt($('sFree').value,10)||0,site_daily_chars:parseInt($('sSite').value,10)||0,plans}); say($('sMsg'),j.message,j.ok); };
$('rand').onclick=()=>{ const a=new Uint8Array(24); crypto.getRandomValues(a); $('slug').value=Array.from(a,b=>'abcdefghijkmnpqrstuvwxyz23456789'[b%32]).join(''); };
$('slugGo').onclick=async()=>{ const j=await act('/api/admin/login-url',{slug:$('slug').value.trim()}); say($('slugMsg'),j.message+(j.url?' 新地址：'+j.url:''),j.ok); if(j.ok) overview(); };
$('outAll').onclick=async()=>{ if(!confirm('退出所有设备（包括当前）？')) return; await api('/api/admin/logout-everywhere',{}); location.href='/home'; };
async function audit(){ const j=await api('/api/admin/audit'); if(!j.ok) return; $('audit').innerHTML=j.items.map(x=>'<tr><td>'+fmt(x.at)+'</td><td>'+esc(x.actor||'')+'</td><td>'+esc(x.action)+'</td><td>'+esc(x.detail||'')+'</td><td>'+esc(x.ip||'')+'</td></tr>').join(''); }
document.querySelectorAll('#tabs button').forEach(b=>b.onclick=()=>{ document.querySelectorAll('#tabs button').forEach(x=>x.classList.toggle('on',x===b));
  document.querySelectorAll('section[data-p]').forEach(s=>s.classList.toggle('hidden',s.dataset.p!==b.dataset.t));
  ({users,orders,audit})[b.dataset.t]?.(); });
$('qGo').onclick=()=>{page=0;users();}; $('prev').onclick=()=>{ if(page>0){page--;users();} }; $('next').onclick=()=>{page++;users();}; $('ost').onchange=orders;
overview().then(users);`;
  return layout('审读 · 管理后台', body, { nav: `<a class="btn small" href="/">检测页面</a><button class="btn small" onclick="api('/api/auth/logout',{}).then(()=>location.href='/home')">退出</button>`, script });
}

export function notFoundPage() {
  return layout('页面不存在', '<div class="auth card"><h1>页面不存在</h1><p class="hint"><a href="/home">返回首页</a></p></div>');
}

// 注入到检测页面（Modal 提供的页面）顶部的账户栏；登录用户不需要再手动填 API Key
export function userBarHtml(email, isAdmin) {
  return `<div id="atc-userbar" style="display:flex;justify-content:flex-end;gap:12px;align-items:center;flex-wrap:wrap;font:13px/1.6 'Noto Sans SC',sans-serif;padding:8px 16px;border-bottom:1px solid rgba(0,0,0,.08)">
<span style="opacity:.7">${esc(email)}</span>${isAdmin ? '<a href="/admin/console">管理后台</a>' : '<a href="/account">我的账户</a>'}<a href="/home">首页</a>
<a href="/logout">退出</a></div>
<style>.key-zone{display:none!important}</style>`;
}
