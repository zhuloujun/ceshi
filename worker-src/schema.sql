-- 注册登录 / 商用模块用的 Cloudflare D1 数据表（数据库 ai_text_checker_db）。重复执行无害。
CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'active', balance_chars INTEGER NOT NULL DEFAULT 0, plan_id TEXT, plan_until INTEGER NOT NULL DEFAULT 0, plan_daily_chars INTEGER NOT NULL DEFAULT 0, note TEXT, created_at INTEGER NOT NULL, last_login_at INTEGER, last_login_ip TEXT);
CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0, elevated_until INTEGER NOT NULL DEFAULT 0, expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS otp_codes (id TEXT PRIMARY KEY, email TEXT NOT NULL, code_hash TEXT NOT NULL, purpose TEXT NOT NULL, expires_at INTEGER NOT NULL, consumed INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS idx_otp_email ON otp_codes(email, purpose, created_at);
CREATE TABLE IF NOT EXISTS login_attempts (key TEXT PRIMARY KEY, count INTEGER NOT NULL, first_attempt_at INTEGER NOT NULL, locked_until INTEGER);
CREATE TABLE IF NOT EXISTS rate_limits (key TEXT PRIMARY KEY, count INTEGER NOT NULL, window_start INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS captchas (id TEXT PRIMARY KEY, answer_hash TEXT NOT NULL, expires_at INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS usage_daily (user_id TEXT NOT NULL, day TEXT NOT NULL, chars INTEGER NOT NULL DEFAULT 0, requests INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (user_id, day));
CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, plan_id TEXT NOT NULL, plan_name TEXT NOT NULL, amount_cents INTEGER NOT NULL, chars INTEGER NOT NULL DEFAULT 0, days INTEGER NOT NULL DEFAULT 0, daily_chars INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending', provider TEXT, provider_ref TEXT, created_at INTEGER NOT NULL, paid_at INTEGER);
CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id, created_at);
CREATE TABLE IF NOT EXISTS audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, at INTEGER NOT NULL, actor TEXT, action TEXT NOT NULL, detail TEXT, ip TEXT);
CREATE TABLE IF NOT EXISTS file_jobs (job_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at INTEGER NOT NULL);
