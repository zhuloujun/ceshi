// 界面语言切换（默认英文）：页面文字仍按中文编写，浏览器里按对照表把界面文字换成英文。
// 对所有页面通用：Worker 自己的首页 / 登录 / 后台，以及转发自检测服务（Modal）的检测页和 /admin，
// 所以不需要改动、也不需要重新部署 Modal 上的检测服务。用户自己的文章内容（输入框、段落原文、文稿名）不翻译。
// 语言保存在 Cookie atc_lang（en / zh），右上角按钮切换。
import EN from './i18n-en.json';

export const LANG_COOKIE = 'atc_lang';

export function langOf(req) {
  const m = (req.headers.get('Cookie') || '').match(/(?:^|;\s*)atc_lang=(en|zh)/);
  return m ? m[1] : 'en';
}

function client(D) {
  var COOKIE = 'atc_lang';
  var m = document.cookie.match(/(?:^|;\s*)atc_lang=(en|zh)/);
  var lang = m ? m[1] : 'en';
  function setLang(l) {
    document.cookie = COOKIE + '=' + l + '; Path=/; Max-Age=31536000; SameSite=Lax' + (location.protocol === 'https:' ? '; Secure' : '');
    location.reload();
  }
  function button() {
    var b = document.getElementById('atc-lang');
    if (!b) {
      b = document.createElement('button');
      b.id = 'atc-lang';
      b.type = 'button';
      var bar = document.getElementById('atc-userbar');
      if (bar) bar.insertBefore(b, bar.firstChild);
      else {
        b.style.cssText = 'position:fixed;top:10px;right:12px;z-index:2147483000;';
        document.body.appendChild(b);
      }
    }
    b.setAttribute('data-noi18n', '');
    b.title = lang === 'en' ? '切换到中文' : 'Switch to English';
    b.textContent = lang === 'en' ? '中文' : 'EN';
    if (!b.className) b.style.cssText += 'font:13px/1 system-ui,sans-serif;padding:6px 12px;border-radius:99px;border:1px solid currentColor;background:transparent;color:inherit;cursor:pointer;min-height:30px;opacity:.85';
    b.onclick = function () { setLang(lang === 'en' ? 'zh' : 'en'); };
  }
  function whenBody(fn) { if (document.body) fn(); else document.addEventListener('DOMContentLoaded', fn); }
  window.__atcLang = lang;
  whenBody(button);
  if (lang !== 'en') return;
  document.documentElement.lang = 'en';

  var CJK = /[㐀-鿿]/;
  var NUM = /\d+(?:[.,]\d+)*/g;
  var UNIT = { '字': 'chars', '段': 'paragraphs', '篇': 'works', '分钟': 'min', '秒': 's', '天': 'days', '小时': 'h', '次': 'times', '位': 'chars', '人': 'users', '页': 'pages' };
  var cjkLen = function (s) { var n = 0; for (var i = 0; i < s.length; i++) if (CJK.test(s[i])) n++; return n; };
  var esc = function (s) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); };
  // 只用较长的条目做"句中替换"，短词（如"字""页"）只在整段文字完全相同时才换，避免把中文正文换得支离破碎
  var subs = Object.keys(D).filter(function (k) { return cjkLen(k) >= 3; }).sort(function (a, b) { return b.length - a.length; });
  var BIG = subs.length ? new RegExp(subs.map(function (k) { return esc(k).replace(/\\\{n\\\}/g, '\\d+(?:[.,]\\d+)*'); }).join('|'), 'g') : null;
  // {n} 按顺序填数字；英文语序不同时用 {1} {2} 指定第几个数字
  function fill(v, nums) { var i = 0; return v.replace(/\{(n|\d)\}/g, function (x, g) { if (g !== 'n') return nums[+g - 1] || ''; return nums[i++] !== undefined ? nums[i - 1] : ''; }); }
  function look(t) {
    if (D[t] !== undefined) return D[t];
    var nums = [];
    var k = t.replace(NUM, function (x) { nums.push(x); return '{n}'; });
    if (D[k] !== undefined) return fill(D[k], nums);
    return null;
  }
  function tr(s) {
    if (!s || !CJK.test(s)) return s;
    var lead = s.match(/^\s*/)[0], tail = s.match(/\s*$/)[0], t = s.trim();
    var v = look(t);
    if (v === null && BIG) {
      v = t.replace(BIG, function (x) { var y = look(x); return y === null ? x : ' ' + y + ' '; })
        .replace(/ {2,}/g, ' ').replace(/ ([,.;:!?)，。；：）])/g, '$1').trim();
      // 数字后面剩下的单位
      v = v.replace(/(\d[\d,.]*)\s*(字|段|篇|分钟|秒|天|小时|次|位|人|页)/g, function (x, n, u) { return n + ' ' + UNIT[u]; });
      if (v === t) return s;
    }
    return v === null ? s : lead + v + tail;
  }
  window.__atcT = tr;
  var SKIP = 'script,style,textarea,code,pre,noscript,[data-noi18n],[contenteditable],.para-text,.lib-name,#fileName,.works-table td:first-child';
  function skip(el) { return el && el.closest && el.closest(SKIP); }
  var ATTR = ['placeholder', 'title', 'alt', 'aria-label'];
  function doEl(el) {
    // 输入框里的内容不翻译，但输入框的提示文字（placeholder）要翻译
    if (skip(el) && !(el.tagName === 'TEXTAREA' && !(el.parentElement && skip(el.parentElement)))) return;
    for (var i = 0; i < ATTR.length; i++) {
      var a = el.getAttribute && el.getAttribute(ATTR[i]);
      if (a && CJK.test(a)) { var b = tr(a); if (b !== a) el.setAttribute(ATTR[i], b); }
    }
    if (el.tagName === 'INPUT' && /^(button|submit|reset)$/i.test(el.type) && CJK.test(el.value)) el.value = tr(el.value);
  }
  function walk(root) {
    if (root.nodeType === 3) { doText(root); return; }
    if (root.nodeType !== 1 || skip(root)) return;
    doEl(root);
    var all = root.querySelectorAll ? root.querySelectorAll('[placeholder],[title],[alt],[aria-label],input') : [];
    for (var i = 0; i < all.length; i++) doEl(all[i]);
    var w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null), n;
    while ((n = w.nextNode())) doText(n);
  }
  function doText(n) {
    var v = n.nodeValue;
    if (!v || !CJK.test(v) || skip(n.parentElement)) return;
    var t = tr(v);
    if (t !== v) n.nodeValue = t;
  }
  // 只在确实变化时才改标题：改标题本身也会触发观察器，否则会无限循环
  function fixTitle() { var t0 = document.title; if (CJK.test(t0)) { var t1 = tr(t0); if (t1 !== t0) document.title = t1; } }
  new MutationObserver(function (ms) {
    for (var i = 0; i < ms.length; i++) {
      var m = ms[i];
      if (m.type === 'characterData') doText(m.target);
      else if (m.type === 'attributes') doEl(m.target);
      else for (var j = 0; j < m.addedNodes.length; j++) walk(m.addedNodes[j]);
    }
    fixTitle();
  }).observe(document.documentElement, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTR });
  whenBody(function () { walk(document.body); fixTitle(); });
  ['alert', 'confirm'].forEach(function (f) { var o = window[f]; window[f] = function (msg) { return o.call(window, tr(String(msg == null ? '' : msg))); }; });
  var op = window.prompt; window.prompt = function (msg, def) { return op.call(window, tr(String(msg == null ? '' : msg)), def); };
}

// 打包工具会在函数里插入 __name(...) 调用（保留函数名用），转成字符串送到浏览器时需要一个同名的空函数
export const I18N_JS = `var __name=function(f){return f};(${client.toString()})(${JSON.stringify(EN)});`;

export function i18nResponse() {
  return new Response(I18N_JS, { headers: { 'Content-Type': 'application/javascript; charset=utf-8', 'Cache-Control': 'public, max-age=300',
    'X-Content-Type-Options': 'nosniff' } });
}

// 给转发来的 HTML 页面（检测页、/admin、轻量版）加上语言脚本
export function injectI18n(resp) {
  if (!(resp.headers.get('Content-Type') || '').includes('text/html')) return resp;
  return new HTMLRewriter().on('head', { element(el) { el.append('<script src="/__i18n.js"></script>', { html: true }); } }).transform(resp);
}
