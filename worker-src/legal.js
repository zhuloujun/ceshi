// 用户条款、隐私政策、Cookie 政策、联系方式。
// 默认文字写在这里；管理员可以在"管理后台 → 网站条款"里直接修改（保存在 D1，优先于这里的默认文字），不用改代码。
// 文字格式：以 "## " 开头的行是小标题，"- " 开头的行是列表，空行分段；{contact} 会替换成联系邮箱，{updated} 替换成更新日期。
import { esc, getSetting } from './util.js';

export const LEGAL_PAGES = ['terms', 'privacy', 'cookies'];
export const DEFAULT_CONTACTS = 'oxacuk@163.com, xcsmnmo@gmail.com';
export const DEFAULT_UPDATED = '2026-10-14';

export const LEGAL_TITLES = {
  terms: { zh: '用户条款与免责声明', en: 'Terms of Use and Disclaimer' },
  privacy: { zh: '隐私政策', en: 'Privacy Policy' },
  cookies: { zh: 'Cookie 政策', en: 'Cookie Policy' },
};

const DEFAULT_TEXT = {
  terms: {
    zh: `最近更新：{updated}

欢迎使用"审读 · AI 文本检测"（以下简称"本平台"）。注册、登录或使用本平台，即表示您已阅读并同意本条款、《隐私政策》和《Cookie 政策》。如不同意，请停止使用。

## 一、服务内容
本平台提供 AI 生成文本的检测（现代汉语、文言、诗词、英文）以及标点与排版格式检查。检测结果以"AI 率"和逐段概率的形式给出。

## 二、检测结果仅供参考（免责声明）
- 任何 AI 检测方法都存在误判和漏判，尤其是术语密集、表述规范的学术文字、古籍引文和经过改写的文字。
- 检测结果只能作为自查和参考，不能作为认定抄袭、学术不端、违纪或任何处分的唯一依据，也不构成任何法律、学术或专业意见。
- 因使用或依赖检测结果作出的任何决定及其后果，由使用者自行承担；在法律允许的最大范围内，本平台不承担由此产生的直接或间接损失。

## 三、账户
- 请使用真实有效的邮箱注册，妥善保管密码，不要与他人共用账户。账户下的一切操作视为您本人所为。
- 发现账户被盗用，请立即修改密码并联系我们。

## 四、使用规范
您不得：
- 上传违法、侵犯他人权利或含有恶意代码的内容；
- 以自动程序批量注册、批量检测、攻击、绕过额度限制或干扰本平台运行；
- 将本平台用于规避其他检测系统、代写代考等违反学术诚信的用途。
违反上述规定的，本平台有权暂停或注销账户。

## 五、您的内容
您提交的文字和文件的权利归您或原权利人所有。您需保证有权提交这些内容。本平台仅为完成检测而处理这些内容，处理方式见《隐私政策》。

## 六、收费与额度
本平台目前提供每日免费额度。如开通付费服务，价格、额度和有效期以下单页面显示为准；已开始使用的额度原则上不予退款，法律另有规定的除外。

## 七、服务变更与中断
本平台可能因维护、升级、第三方服务故障或不可抗力而暂停或调整服务，会尽量提前通知，但不对由此造成的损失承担责任。

## 八、条款修改
本条款可能不时更新，更新后在本页面公布并注明日期。更新后继续使用本平台，即视为同意更新后的条款。

## 九、联系我们
如有疑问、投诉或建议，请发邮件至：{contact}`,
    en: `Last updated: {updated}

Welcome to "Shendu · AI Text Detection" (the "Platform"). By signing up, logging in or using the Platform, you confirm that you have read and agree to these Terms, the Privacy Policy and the Cookie Policy. If you do not agree, please stop using the Platform.

## 1. The service
The Platform detects AI-generated text (modern Chinese, classical Chinese, poetry and English) and checks punctuation and layout. Results are given as an "AI rate" and per-paragraph probabilities.

## 2. Results are for reference only (disclaimer)
- Every AI detection method produces false positives and false negatives, especially on formal academic writing, quotations from classical texts and rewritten text.
- Results are for self-checking and reference only. They must not be used as the sole basis for any finding of plagiarism, academic misconduct or disciplinary action, and they are not legal, academic or professional advice.
- You are responsible for any decision made based on the results. To the maximum extent permitted by law, the Platform is not liable for any direct or indirect loss arising from them.

## 3. Your account
- Use a real email address, keep your password safe and do not share your account. Activity under your account is treated as yours.
- If your account is compromised, change your password immediately and contact us.

## 4. Acceptable use
You must not:
- upload unlawful content, content that infringes others' rights, or malicious code;
- use automated means to register or check in bulk, attack the Platform, bypass quotas or disrupt its operation;
- use the Platform to evade other detection systems or for ghost-writing or other breaches of academic integrity.
The Platform may suspend or close accounts that break these rules.

## 5. Your content
Rights in the text and files you submit remain with you or their owners, and you confirm you are entitled to submit them. The Platform processes them only to perform the check, as described in the Privacy Policy.

## 6. Fees and quotas
The Platform currently offers a free daily quota. If paid plans are offered, the price, quota and validity shown at checkout apply. Quotas already used are generally non-refundable unless the law requires otherwise.

## 7. Changes and interruptions
The service may be paused or changed for maintenance, upgrades, third-party outages or force majeure. We will try to give notice, but are not liable for resulting losses.

## 8. Changes to these Terms
These Terms may be updated from time to time; updates are published on this page with a date. Continuing to use the Platform after an update means you accept it.

## 9. Contact
For questions, complaints or suggestions, email: {contact}`,
  },
  privacy: {
    zh: `最近更新：{updated}

本政策说明"审读 · AI 文本检测"（以下简称"本平台"）收集哪些信息、如何使用和保护这些信息。

## 一、我们收集的信息
- 账户信息：邮箱地址；密码只保存经过加密处理的哈希值，我们看不到您的密码原文。
- 安全信息：登录时间、登录 IP 地址、操作记录（用于防止盗号和滥用）。
- 使用信息：每日检测字数和次数（用于额度统计）；如有订单，保存订单内容、金额和状态。
- 您提交检测的文字和文件：见下面第二条。

## 二、检测文字如何处理
- 您提交的文字只用于完成本次检测，在检测服务器的内存中处理，检测结果最多保留 1 小时后自动删除，不写入数据库。
- 我们不会把您提交的文字用于训练模型，也不会出售或公开。
- 上传的 Word / PDF 文件在您的浏览器里解析，只把提取出的文字发送去检测。
- 网页上的"本地文稿库"只保存在您自己电脑的浏览器里，不会上传到服务器；您可以随时在页面上清空。

## 三、信息的用途
用于提供登录、检测、额度和订单服务；发送注册、找回密码等验证码邮件；保障账户和平台安全、防止滥用；处理您的咨询和投诉。我们不会用于广告推送。

## 四、我们使用的第三方服务
为提供服务，以下服务商会在各自职责范围内处理部分信息：
- Cloudflare：网站托管、数据库（账户与额度数据）、人机验证（Turnstile）。
- Modal：检测计算服务（处理您提交检测的文字）。
- Resend：发送验证码邮件（处理您的邮箱地址）。
这些服务器可能位于您所在国家或地区以外。

## 五、信息的保存与安全
账户信息在您的账户存续期间保存；注销账户后删除账户、会话和用量记录，订单记录按法律要求保存。我们采取加密传输、密码哈希、登录失败锁定、重要操作邮箱验证等措施保护信息安全，但互联网上没有绝对安全的系统。

## 六、您的权利
您可以查看和修改自己的账户信息，修改密码，或要求查询、更正、删除您的个人信息、注销账户。请发邮件至 {contact}，我们会在核实身份后尽快处理。

## 七、未成年人
本平台主要面向成年人。未成年人请在监护人同意和指导下使用。

## 八、政策更新
本政策可能不时更新，更新后在本页面公布并注明日期。

## 九、联系我们
{contact}`,
    en: `Last updated: {updated}

This policy explains what information "Shendu · AI Text Detection" (the "Platform") collects and how it is used and protected.

## 1. Information we collect
- Account information: your email address. Passwords are stored only as salted hashes; we cannot see your password.
- Security information: login times, login IP addresses and an activity log (to prevent account theft and abuse).
- Usage information: characters checked and number of checks per day (for quotas); for orders, the item, amount and status.
- Text and files you submit for checking: see section 2.

## 2. How submitted text is handled
- Submitted text is used only to perform the check. It is processed in the memory of the detection server, and results are deleted automatically after at most 1 hour; it is not written to a database.
- We do not use your text to train models, and we do not sell or publish it.
- Uploaded Word / PDF files are parsed in your browser; only the extracted text is sent for checking.
- The "local document library" on the page is stored only in your own browser and is never uploaded; you can clear it at any time on the page.

## 3. How we use information
To provide login, checking, quotas and orders; to send verification emails (sign-up, password reset, etc.); to keep accounts and the Platform secure and prevent abuse; and to handle your questions and complaints. We do not use it for advertising.

## 4. Third-party services
The following providers process some information within their roles:
- Cloudflare: website hosting, database (account and usage data), human verification (Turnstile).
- Modal: detection computing (processes the text you submit).
- Resend: sending verification emails (processes your email address).
Their servers may be located outside your country or region.

## 5. Retention and security
Account information is kept while your account exists. When an account is deleted, its account, session and usage records are deleted; order records are kept as required by law. We use encrypted connections, password hashing, login lockout and email confirmation for important actions, but no system on the internet is perfectly secure.

## 6. Your rights
You can view and update your account and change your password, and you may ask us to access, correct or delete your personal information or close your account by emailing {contact}. We will act after verifying your identity.

## 7. Minors
The Platform is intended mainly for adults. Minors should use it only with the consent and guidance of a parent or guardian.

## 8. Updates
This policy may be updated from time to time; updates are published on this page with a date.

## 9. Contact
{contact}`,
  },
  cookies: {
    zh: `最近更新：{updated}

本平台只使用让网站正常运行所必需的 Cookie 和浏览器存储，不使用广告或跨站追踪 Cookie，也没有接入第三方统计工具。

## 我们使用的 Cookie
- atc_session：登录凭证。保持您的登录状态，最长 30 天（管理员 12 小时），退出登录后立即失效。必需。
- atc_lang：记住您选择的界面语言（中文 / 英文），保存 1 年。

## 第三方 Cookie
- Cloudflare 人机验证（Turnstile）和 Cloudflare 安全服务可能设置用于识别机器人和防御攻击的必要 Cookie，详见 Cloudflare 的隐私政策。

## 浏览器本地存储
- 检测页的"本地文稿库"把您粘贴的文字、上传的文档和检测结果保存在您浏览器的本地存储（IndexedDB / localStorage）中，只在本机，不会上传，可在页面上一键清空。

## 如何管理
您可以在浏览器设置中删除或阻止 Cookie。阻止必需 Cookie 后将无法登录和使用检测功能。

如有疑问，请联系：{contact}`,
    en: `Last updated: {updated}

The Platform uses only the cookies and browser storage needed for the website to work. We do not use advertising or cross-site tracking cookies, and we do not use third-party analytics.

## Cookies we use
- atc_session: login credential. Keeps you logged in for up to 30 days (12 hours for administrators) and stops working as soon as you log out. Required.
- atc_lang: remembers your interface language (Chinese / English) for 1 year.

## Third-party cookies
- Cloudflare human verification (Turnstile) and Cloudflare security services may set necessary cookies to detect bots and block attacks; see Cloudflare's privacy policy.

## Local browser storage
- The "local document library" on the checker page stores your pasted text, uploaded documents and results in your browser's local storage (IndexedDB / localStorage). It stays on your device, is never uploaded and can be cleared on the page with one click.

## Managing cookies
You can delete or block cookies in your browser settings. Blocking the required cookies will stop login and checking from working.

Questions? Contact: {contact}`,
  },
};

export function defaultLegal(page, lang) { return DEFAULT_TEXT[page][lang]; }

export async function legalConfig(env) {
  const contacts = await getSetting(env, 'legal:contacts', DEFAULT_CONTACTS);
  const updated = await getSetting(env, 'legal:updated', DEFAULT_UPDATED);
  return { contacts, updated };
}

export async function legalText(env, page, lang) {
  return (await getSetting(env, `legal:${page}:${lang}`)) || DEFAULT_TEXT[page][lang];
}

export function contactLinks(contacts) {
  return String(contacts || '').split(/[,，;；\s]+/).filter((e) => /^[^\s@<>"']+@[^\s@<>"']+\.[^\s@<>"']+$/.test(e))
    .map((e) => `<a href="mailto:${esc(e)}">${esc(e)}</a>`).join(' · ');
}

// 把简单的文字格式转成网页（先转义，再加标题 / 列表 / 段落标签，用户写的内容不会变成脚本）
export function renderLegal(text, { contacts, updated }) {
  const out = [];
  let list = false;
  const close = () => { if (list) { out.push('</ul>'); list = false; } };
  for (const raw of String(text).split('\n')) {
    const line = raw.trimEnd();
    const fill = (s) => esc(s).replace(/\{contact\}/g, contactLinks(contacts)).replace(/\{updated\}/g, esc(updated));
    if (!line.trim()) { close(); continue; }
    if (line.startsWith('## ')) { close(); out.push(`<h2>${fill(line.slice(3))}</h2>`); continue; }
    if (line.startsWith('- ')) { if (!list) { out.push('<ul>'); list = true; } out.push(`<li>${fill(line.slice(2))}</li>`); continue; }
    close(); out.push(`<p>${fill(line)}</p>`);
  }
  close();
  return out.join('\n');
}
