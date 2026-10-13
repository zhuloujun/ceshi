// 把 pages-static/ 里的三个网页文件打包进 worker.js（单文件，便于在 Dashboard 里直接粘贴）
// 用法：node build.mjs
import { readFileSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';

const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const tpl = read('./worker-template.js');

// JSON.stringify 生成合法的 JS 字符串字面量（ES2019 起 U+2028/2029 在字符串中也合法）
const lit = (s) => JSON.stringify(s);

const out = tpl
  .replace('__INDEX_HTML__', () => lit(read('./pages-static/index.html')))
  .replace('__STYLE_CSS__', () => lit(read('./pages-static/style.css')))
  .replace('__APP_JS__', () => lit(read('./pages-static/app.js')))
  // 完整版检测页面（server/static）也打包进 Worker：打开页面时由 Worker 直接返回，不唤醒 Modal（省钱）
  .replace('__BACKEND_STATIC__', () => {
    const files = ['index.html', 'admin.html', 'app.js', 'library.js', 'admin.js', 'style.css'];
    const out = {};
    for (const f of files) {
      const body = read('./server/static/' + f);
      out[f] = { body, v: createHash('sha1').update(body).digest('hex').slice(0, 10) };
    }
    // 与后端 main.py 一样，给页面里引用的 /static/ 文件加上内容指纹，文件一变浏览器就会重新下载
    for (const f of ['index.html', 'admin.html'])
      out[f].body = out[f].body.replace(/\/static\/([\w.-]+)"/g, (m, n) => out[n] ? `/static/${n}?v=${out[n].v}"` : m);
    return lit(out);
  });

writeFileSync(new URL('./worker.js', import.meta.url), out);
console.log('worker.js written,', out.length, 'chars');
