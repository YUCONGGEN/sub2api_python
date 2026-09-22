-- Schema is kept as a resource so persistence SQL is not embedded in Python.
-- Existing installations are upgraded by the mapper-backed repository.
CREATE TABLE IF NOT EXISTS user_groups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  description TEXT NOT NULL DEFAULT '',
  weight INTEGER NOT NULL DEFAULT 0,
  concurrency_limit INTEGER NOT NULL DEFAULT 1,
  allowed_models_json TEXT NOT NULL DEFAULT '["*"]',
  is_default INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_mappings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  source_model TEXT NOT NULL,
  source_effort TEXT NOT NULL DEFAULT '',
  target_model TEXT NOT NULL,
  target_effort TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(source_model, source_effort)
);

CREATE TABLE IF NOT EXISTS user_group_model_mappings (
  group_id INTEGER NOT NULL,
  mapping_id INTEGER NOT NULL,
  PRIMARY KEY(group_id, mapping_id)
);

CREATE TABLE IF NOT EXISTS announcements (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_by INTEGER NOT NULL,
  expires_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS announcement_reads (
  announcement_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  read_at TEXT NOT NULL,
  PRIMARY KEY(announcement_id, user_id)
);

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  email TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL DEFAULT 'USER',
  balance REAL NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1,
  api_key TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  last_login TEXT,
  session_version INTEGER NOT NULL DEFAULT 0,
  deleted_at TEXT,
  group_id INTEGER
);

-- Fresh installs include the initial administrator.  The stored value is a
-- bcrypt hash for the requested password ``admin``; the API key is revoked.
INSERT OR IGNORE INTO users (username, password_hash, email, role, balance, enabled, api_key, created_at, last_login)
VALUES ('admin', '$2b$12$0UpiJaXUzZRVElJctpiQKef0wrm1fDg5wNuuuJWyYfSHo8g0xtuHS',
        'admin@example.com', 'ADMIN', 0, 1, 'revoked-bootstrap-admin', datetime('now'), NULL);

CREATE TABLE IF NOT EXISTS usage_records (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  model TEXT NOT NULL,
  prompt_tokens INTEGER NOT NULL DEFAULT 0,
  completion_tokens INTEGER NOT NULL DEFAULT 0,
  total_tokens INTEGER NOT NULL DEFAULT 0,
  cost REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'SUCCEEDED',
  created_at TEXT NOT NULL,
  billing_source TEXT NOT NULL DEFAULT 'WALLET',
  free_cost REAL NOT NULL DEFAULT 0,
  free_tokens INTEGER NOT NULL DEFAULT 0,
  subscription_cost REAL NOT NULL DEFAULT 0,
  subscription_tokens INTEGER NOT NULL DEFAULT 0,
  wallet_cost REAL NOT NULL DEFAULT 0,
  wallet_tokens INTEGER NOT NULL DEFAULT 0,
  quota_id INTEGER,
  subscription_id INTEGER
);
-- Privacy mode keeps this legacy table for usage charts only.  The runtime
-- always writes empty content columns; prompts, answers and images are not
-- stored.  Actual billing counters are also available in usage_records.
CREATE TABLE IF NOT EXISTS conversation_records (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  request_id TEXT NOT NULL UNIQUE,
  protocol TEXT NOT NULL DEFAULT 'chat_completions',
  model TEXT NOT NULL DEFAULT '',
  request_json TEXT NOT NULL,
  messages_json TEXT NOT NULL DEFAULT '[]',
  response_json TEXT,
  answer_text TEXT NOT NULL DEFAULT '',
  prompt_tokens INTEGER NOT NULL DEFAULT 0,
  completion_tokens INTEGER NOT NULL DEFAULT 0,
  total_tokens INTEGER NOT NULL DEFAULT 0,
  cost REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'PROCESSING',
  error_message TEXT,
  created_at TEXT NOT NULL,
  completed_at TEXT,
  latency_ms INTEGER
);
CREATE TABLE IF NOT EXISTS conversation_assets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id INTEGER,
  request_id TEXT NOT NULL,
  asset_index INTEGER NOT NULL,
  source_type TEXT NOT NULL,
  mime_type TEXT NOT NULL,
  storage_path TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(request_id, asset_index)
);
CREATE TABLE IF NOT EXISTS admin_event_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  level TEXT NOT NULL DEFAULT 'INFO',
  event_type TEXT NOT NULL DEFAULT 'REQUEST',
  message TEXT NOT NULL DEFAULT '',
  method TEXT NOT NULL DEFAULT '',
  path TEXT NOT NULL DEFAULT '',
  status_code INTEGER,
  latency_ms INTEGER,
  request_id TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS payment_orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  provider TEXT NOT NULL,
  trade_no TEXT NOT NULL UNIQUE,
  amount REAL NOT NULL,
  credits REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'PENDING',
  qr_code TEXT,
  created_at TEXT NOT NULL,
  paid_at TEXT,
  payment_amount REAL,
  qr_asset TEXT
);
CREATE TABLE IF NOT EXISTS api_keys (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  name TEXT NOT NULL DEFAULT '未命名密钥',
  api_key TEXT NOT NULL UNIQUE,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  last_used TEXT,
  expires_at TEXT,
  api_key_hash TEXT,
  key_prefix TEXT NOT NULL DEFAULT '',
  key_last4 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS recharge_codes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code_hash TEXT NOT NULL UNIQUE,
  amount REAL NOT NULL,
  created_by INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  redeemed_by INTEGER,
  redeemed_at TEXT,
  status TEXT NOT NULL DEFAULT 'ACTIVE',
  code TEXT,
  expires_at TEXT
);
CREATE TABLE IF NOT EXISTS payment_callbacks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  callback_hash TEXT NOT NULL UNIQUE,
  provider TEXT NOT NULL,
  payment_amount REAL NOT NULL,
  trade_no TEXT,
  received_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS payment_listener_status (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  available INTEGER NOT NULL DEFAULT 0,
  reason TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL,
  last_alert_at TEXT
);
CREATE TABLE IF NOT EXISTS subscription_plans (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  description TEXT NOT NULL DEFAULT '',
  price REAL NOT NULL DEFAULT 0,
  duration_days INTEGER NOT NULL DEFAULT 30,
  daily_amount REAL NOT NULL DEFAULT 0,
  daily_tokens INTEGER NOT NULL DEFAULT 0,
  group_id INTEGER,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_subscriptions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  plan_id INTEGER NOT NULL,
  starts_at TEXT NOT NULL,
  ends_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'ACTIVE',
  auto_renew INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_quota_policies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  name TEXT NOT NULL DEFAULT '免费额度',
  daily_amount REAL NOT NULL DEFAULT 0,
  daily_tokens INTEGER NOT NULL DEFAULT 0,
  hourly_tokens INTEGER NOT NULL DEFAULT 0,
  hourly_window_hours REAL NOT NULL DEFAULT 1,
  starts_at TEXT NOT NULL,
  ends_at TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
-- Every entitlement portion used by one request is recorded independently.
-- This allows a request to span several grants/plans without losing audit
-- information in the legacy single quota_id/subscription_id columns.
CREATE TABLE IF NOT EXISTS usage_allocations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  usage_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  kind TEXT NOT NULL,
  entitlement_id INTEGER,
  cost REAL NOT NULL DEFAULT 0,
  tokens INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
-- Updating this row at the start of charge() serializes billing for one user
-- in both SQLite and MySQL while still allowing different users in parallel.
CREATE TABLE IF NOT EXISTS billing_locks (
  user_id INTEGER PRIMARY KEY,
  version INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_sessions (
  id TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL,
  user_agent TEXT NOT NULL DEFAULT '',
  ip_address TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS upstream_subscription_accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner_user_id INTEGER,
  provider TEXT NOT NULL,
  name TEXT NOT NULL,
  auth_type TEXT NOT NULL DEFAULT 'oauth',
  email TEXT NOT NULL DEFAULT '',
  account_ref TEXT NOT NULL DEFAULT '',
  credentials_encrypted TEXT NOT NULL,
  models_json TEXT NOT NULL DEFAULT '[]',
  model_pricing_json TEXT NOT NULL DEFAULT '{}',
  enabled INTEGER NOT NULL DEFAULT 1,
  priority INTEGER NOT NULL DEFAULT 0,
  weight INTEGER NOT NULL DEFAULT 1,
  input_price_cny REAL NOT NULL DEFAULT 0,
  output_price_cny REAL NOT NULL DEFAULT 0,
  price_multiplier REAL NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'READY',
  error_count INTEGER NOT NULL DEFAULT 0,
  last_error TEXT NOT NULL DEFAULT '',
  disable_reason TEXT NOT NULL DEFAULT '',
  expires_at TEXT,
  cooldown_until TEXT,
  last_used_at TEXT,
  compliance_confirmed_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS upstream_config_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  base_url TEXT NOT NULL,
  api_key_encrypted TEXT NOT NULL,
  model_id TEXT NOT NULL,
  use_proxy INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'PENDING',
  admin_note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_user_created ON usage_records(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_usage_model_created ON usage_records(model, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_conversation_user_created ON conversation_records(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_conversation_status_created ON conversation_records(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_conversation_assets_conversation ON conversation_assets(conversation_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_conversation_assets_request ON conversation_assets(request_id, asset_index);
CREATE INDEX IF NOT EXISTS idx_admin_event_created ON admin_event_logs(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_admin_event_level_created ON admin_event_logs(level, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_orders_user_created ON payment_orders(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_recharge_codes_status ON recharge_codes(status);
CREATE INDEX IF NOT EXISTS idx_orders_payment_amount ON payment_orders(provider, payment_amount, status);
CREATE INDEX IF NOT EXISTS idx_subscriptions_user_period ON user_subscriptions(user_id, status, starts_at, ends_at);
CREATE INDEX IF NOT EXISTS idx_quota_user_period ON user_quota_policies(user_id, enabled, starts_at, ends_at);
CREATE INDEX IF NOT EXISTS idx_usage_allocations_usage ON usage_allocations(usage_id, kind);
CREATE INDEX IF NOT EXISTS idx_usage_allocations_entitlement ON usage_allocations(user_id, kind, entitlement_id, created_at);
CREATE INDEX IF NOT EXISTS idx_user_sessions_user ON user_sessions(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_upstream_subscription_provider ON upstream_subscription_accounts(provider, enabled, priority DESC);
CREATE INDEX IF NOT EXISTS idx_upstream_subscription_cooldown ON upstream_subscription_accounts(provider, cooldown_until);
CREATE INDEX IF NOT EXISTS idx_upstream_config_request_user ON upstream_config_requests(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_upstream_config_request_status ON upstream_config_requests(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_announcements_current ON announcements(enabled, id DESC);
CREATE INDEX IF NOT EXISTS idx_announcement_reads_user ON announcement_reads(user_id, announcement_id);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_pending_payment_amount
  ON payment_orders(provider, payment_amount)
  WHERE status = 'PENDING' AND payment_amount IS NOT NULL;
