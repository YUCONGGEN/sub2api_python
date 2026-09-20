-- Fresh MySQL schema for the SpringBootAI proxy. Existing SQLite data is not migrated.
CREATE TABLE IF NOT EXISTS user_groups (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(120) NOT NULL UNIQUE,
  description VARCHAR(500) NOT NULL DEFAULT '',
  weight INT NOT NULL DEFAULT 0,
  concurrency_limit INT NOT NULL DEFAULT 1,
  allowed_models_json LONGTEXT NOT NULL,
  is_default TINYINT NOT NULL DEFAULT 0,
  created_at VARCHAR(40) NOT NULL,
  updated_at VARCHAR(40) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS model_mappings (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(120) NOT NULL,
  source_model VARCHAR(160) NOT NULL,
  source_effort VARCHAR(20) NOT NULL DEFAULT '',
  target_model VARCHAR(160) NOT NULL,
  target_effort VARCHAR(20) NOT NULL,
  enabled TINYINT NOT NULL DEFAULT 1,
  created_at VARCHAR(40) NOT NULL,
  updated_at VARCHAR(40) NOT NULL,
  UNIQUE KEY uq_model_mapping_source (source_model, source_effort)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS user_group_model_mappings (
  group_id BIGINT NOT NULL,
  mapping_id BIGINT NOT NULL,
  PRIMARY KEY(group_id, mapping_id),
  KEY idx_group_mapping_mapping (mapping_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS announcements (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  title VARCHAR(120) NOT NULL,
  content TEXT NOT NULL,
  enabled TINYINT NOT NULL DEFAULT 1,
  created_by BIGINT NOT NULL,
  expires_at VARCHAR(40),
  created_at VARCHAR(40) NOT NULL,
  updated_at VARCHAR(40) NOT NULL,
  KEY idx_announcements_current (enabled, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS announcement_reads (
  announcement_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  read_at VARCHAR(40) NOT NULL,
  PRIMARY KEY(announcement_id, user_id),
  KEY idx_announcement_reads_user (user_id, announcement_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS users (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  username VARCHAR(64) NOT NULL UNIQUE,
  password_hash VARCHAR(255) NOT NULL,
  email VARCHAR(255) NOT NULL DEFAULT '',
  role VARCHAR(20) NOT NULL DEFAULT 'USER',
  balance DECIMAL(20,8) NOT NULL DEFAULT 0,
  enabled TINYINT NOT NULL DEFAULT 1,
  api_key VARCHAR(255) NOT NULL UNIQUE,
  created_at VARCHAR(40) NOT NULL,
  last_login VARCHAR(40),
  session_version BIGINT NOT NULL DEFAULT 0,
  deleted_at VARCHAR(40),
  group_id BIGINT,
  KEY idx_users_group (group_id, deleted_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Fresh installs include the initial administrator.  The stored value is a
-- bcrypt hash for the requested password ``admin``, and the revoked API key keeps
-- the account from being used as an implicit API credential.
INSERT INTO users (username, password_hash, email, role, balance, enabled, api_key, created_at, last_login)
SELECT 'admin', '$2b$12$0UpiJaXUzZRVElJctpiQKef0wrm1fDg5wNuuuJWyYfSHo8g0xtuHS',
       'admin@example.com', 'ADMIN', 0, 1, 'revoked-bootstrap-admin', UTC_TIMESTAMP(), NULL
FROM (SELECT 1) AS bootstrap
WHERE NOT EXISTS (SELECT 1 FROM users WHERE username = 'admin');

CREATE TABLE IF NOT EXISTS usage_records (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT NOT NULL,
  model VARCHAR(160) NOT NULL,
  prompt_tokens BIGINT NOT NULL DEFAULT 0,
  completion_tokens BIGINT NOT NULL DEFAULT 0,
  total_tokens BIGINT NOT NULL DEFAULT 0,
  cost DECIMAL(20,8) NOT NULL DEFAULT 0,
  status VARCHAR(30) NOT NULL DEFAULT 'SUCCEEDED',
  created_at VARCHAR(40) NOT NULL,
  billing_source VARCHAR(30) NOT NULL DEFAULT 'WALLET',
  free_cost DECIMAL(20,8) NOT NULL DEFAULT 0,
  free_tokens BIGINT NOT NULL DEFAULT 0,
  subscription_cost DECIMAL(20,8) NOT NULL DEFAULT 0,
  subscription_tokens BIGINT NOT NULL DEFAULT 0,
  wallet_cost DECIMAL(20,8) NOT NULL DEFAULT 0,
  wallet_tokens BIGINT NOT NULL DEFAULT 0,
  quota_id BIGINT,
  subscription_id BIGINT,
  KEY idx_usage_user_created (user_id, created_at),
  KEY idx_usage_model_created (model, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
-- Privacy mode keeps this legacy table for usage charts only.  The runtime
-- always writes empty content columns; prompts, answers and images are not
-- stored.  Actual billing counters are also available in usage_records.
CREATE TABLE IF NOT EXISTS conversation_records (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT NOT NULL,
  request_id VARCHAR(160) NOT NULL UNIQUE,
  protocol VARCHAR(40) NOT NULL DEFAULT 'chat_completions',
  model VARCHAR(160) NOT NULL DEFAULT '',
  request_json LONGTEXT NOT NULL,
  messages_json LONGTEXT NOT NULL,
  response_json LONGTEXT,
  answer_text LONGTEXT NOT NULL,
  prompt_tokens BIGINT NOT NULL DEFAULT 0,
  completion_tokens BIGINT NOT NULL DEFAULT 0,
  total_tokens BIGINT NOT NULL DEFAULT 0,
  cost DECIMAL(20,8) NOT NULL DEFAULT 0,
  status VARCHAR(30) NOT NULL DEFAULT 'PROCESSING',
  error_message TEXT,
  created_at VARCHAR(40) NOT NULL,
  completed_at VARCHAR(40),
  latency_ms BIGINT,
  KEY idx_conversation_user_created (user_id, created_at),
  KEY idx_conversation_status_created (status, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS conversation_assets (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  conversation_id BIGINT,
  request_id VARCHAR(160) NOT NULL,
  asset_index INT NOT NULL,
  source_type VARCHAR(30) NOT NULL,
  mime_type VARCHAR(120) NOT NULL,
  storage_path VARCHAR(1024) NOT NULL,
  sha256 CHAR(64) NOT NULL,
  size_bytes BIGINT NOT NULL,
  created_at VARCHAR(40) NOT NULL,
  UNIQUE KEY uniq_conversation_asset_request (request_id, asset_index),
  KEY idx_conversation_assets_conversation (conversation_id, created_at),
  KEY idx_conversation_assets_request (request_id, asset_index)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS admin_event_logs (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  level VARCHAR(20) NOT NULL DEFAULT 'INFO', event_type VARCHAR(40) NOT NULL DEFAULT 'REQUEST',
  message TEXT NOT NULL, method VARCHAR(20) NOT NULL DEFAULT '', path VARCHAR(255) NOT NULL DEFAULT '',
  status_code INT, latency_ms BIGINT, request_id VARCHAR(160), created_at VARCHAR(40) NOT NULL,
  KEY idx_admin_event_created (created_at, id), KEY idx_admin_event_level_created (level, created_at, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS payment_orders (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT NOT NULL, provider VARCHAR(40) NOT NULL, trade_no VARCHAR(100) NOT NULL UNIQUE,
  amount DECIMAL(20,8) NOT NULL, credits DECIMAL(20,8) NOT NULL, status VARCHAR(30) NOT NULL DEFAULT 'PENDING',
  qr_code TEXT, created_at VARCHAR(40) NOT NULL, paid_at VARCHAR(40), payment_amount DECIMAL(20,8), qr_asset VARCHAR(255),
  KEY idx_orders_user_created (user_id, created_at), KEY idx_orders_payment_amount (provider, payment_amount, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS api_keys (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY, user_id BIGINT NOT NULL, name VARCHAR(64) NOT NULL DEFAULT '未命名密钥',
  api_key VARCHAR(255) NOT NULL UNIQUE, enabled TINYINT NOT NULL DEFAULT 1, created_at VARCHAR(40) NOT NULL,
  last_used VARCHAR(40), expires_at VARCHAR(40), api_key_hash CHAR(64) UNIQUE,
  key_prefix VARCHAR(32) NOT NULL DEFAULT '', key_last4 VARCHAR(8) NOT NULL DEFAULT '',
  KEY idx_api_keys_user (user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS recharge_codes (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY, code_hash VARCHAR(128) NOT NULL UNIQUE, code VARCHAR(128),
  amount DECIMAL(20,8) NOT NULL, created_by BIGINT NOT NULL, created_at VARCHAR(40) NOT NULL, expires_at VARCHAR(40),
  redeemed_by BIGINT, redeemed_at VARCHAR(40), status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE', KEY idx_recharge_codes_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS payment_callbacks (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY, callback_hash VARCHAR(128) NOT NULL UNIQUE, provider VARCHAR(40) NOT NULL,
  payment_amount DECIMAL(20,8) NOT NULL, trade_no VARCHAR(100), received_at VARCHAR(40) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS payment_listener_status (
  id BIGINT NOT NULL PRIMARY KEY, available TINYINT NOT NULL DEFAULT 0, reason VARCHAR(500) NOT NULL DEFAULT '',
  updated_at VARCHAR(40) NOT NULL, last_alert_at VARCHAR(40)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS subscription_plans (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY, name VARCHAR(120) NOT NULL UNIQUE, description TEXT NOT NULL,
  price DECIMAL(20,8) NOT NULL DEFAULT 0, duration_days INT NOT NULL DEFAULT 30, daily_amount DECIMAL(20,8) NOT NULL DEFAULT 0,
  daily_tokens BIGINT NOT NULL DEFAULT 0, group_id BIGINT, enabled TINYINT NOT NULL DEFAULT 1, created_at VARCHAR(40) NOT NULL, updated_at VARCHAR(40) NOT NULL,
  KEY idx_subscription_plans_group (group_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS user_subscriptions (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY, user_id BIGINT NOT NULL, plan_id BIGINT NOT NULL, starts_at VARCHAR(40) NOT NULL,
  ends_at VARCHAR(40) NOT NULL, status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE', auto_renew TINYINT NOT NULL DEFAULT 0, created_at VARCHAR(40) NOT NULL,
  KEY idx_subscriptions_user_period (user_id, status, starts_at, ends_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS user_quota_policies (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY, user_id BIGINT NOT NULL, name VARCHAR(120) NOT NULL DEFAULT '免费额度',
  daily_amount DECIMAL(20,8) NOT NULL DEFAULT 0, daily_tokens BIGINT NOT NULL DEFAULT 0, hourly_tokens BIGINT NOT NULL DEFAULT 0,
  hourly_window_hours DECIMAL(10,2) NOT NULL DEFAULT 1, starts_at VARCHAR(40) NOT NULL, ends_at VARCHAR(40), enabled TINYINT NOT NULL DEFAULT 1,
  created_at VARCHAR(40) NOT NULL, updated_at VARCHAR(40) NOT NULL, KEY idx_quota_user_period (user_id, enabled, starts_at, ends_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS usage_allocations (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  usage_id BIGINT NOT NULL, user_id BIGINT NOT NULL, kind VARCHAR(20) NOT NULL,
  entitlement_id BIGINT, cost DECIMAL(20,8) NOT NULL DEFAULT 0,
  tokens BIGINT NOT NULL DEFAULT 0, created_at VARCHAR(40) NOT NULL,
  KEY idx_usage_allocations_usage (usage_id, kind),
  KEY idx_usage_allocations_entitlement (user_id, kind, entitlement_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS billing_locks (
  user_id BIGINT NOT NULL PRIMARY KEY, version BIGINT NOT NULL DEFAULT 0,
  updated_at VARCHAR(40) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS user_sessions (
  id VARCHAR(64) NOT NULL PRIMARY KEY, user_id BIGINT NOT NULL,
  user_agent VARCHAR(500) NOT NULL DEFAULT '', ip_address VARCHAR(128) NOT NULL DEFAULT '',
  created_at VARCHAR(40) NOT NULL, last_seen_at VARCHAR(40) NOT NULL, revoked_at VARCHAR(40),
  KEY idx_user_sessions_user (user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS upstream_subscription_accounts (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  owner_user_id BIGINT,
  provider VARCHAR(20) NOT NULL,
  name VARCHAR(120) NOT NULL,
  auth_type VARCHAR(30) NOT NULL DEFAULT 'oauth',
  email VARCHAR(255) NOT NULL DEFAULT '',
  account_ref VARCHAR(255) NOT NULL DEFAULT '',
  credentials_encrypted LONGTEXT NOT NULL,
  models_json LONGTEXT NOT NULL,
  model_pricing_json LONGTEXT NOT NULL,
  enabled TINYINT NOT NULL DEFAULT 1,
  priority INT NOT NULL DEFAULT 0,
  weight INT NOT NULL DEFAULT 1,
  input_price_cny DECIMAL(20,8) NOT NULL DEFAULT 0,
  output_price_cny DECIMAL(20,8) NOT NULL DEFAULT 0,
  price_multiplier DECIMAL(12,4) NOT NULL DEFAULT 1,
  status VARCHAR(30) NOT NULL DEFAULT 'READY',
  error_count INT NOT NULL DEFAULT 0,
  last_error TEXT NOT NULL,
  expires_at VARCHAR(40),
  cooldown_until VARCHAR(40),
  last_used_at VARCHAR(40),
  compliance_confirmed_at VARCHAR(40) NOT NULL,
  created_at VARCHAR(40) NOT NULL,
  updated_at VARCHAR(40) NOT NULL,
  KEY idx_upstream_subscription_provider (provider, enabled, priority),
  KEY idx_upstream_subscription_cooldown (provider, cooldown_until),
  KEY idx_upstream_subscription_owner (owner_user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS upstream_config_requests (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT NOT NULL,
  base_url VARCHAR(1000) NOT NULL,
  api_key_encrypted LONGTEXT NOT NULL,
  model_id VARCHAR(200) NOT NULL,
  use_proxy TINYINT NOT NULL DEFAULT 0,
  status VARCHAR(30) NOT NULL DEFAULT 'PENDING',
  admin_note VARCHAR(1000) NOT NULL DEFAULT '',
  created_at VARCHAR(40) NOT NULL,
  updated_at VARCHAR(40) NOT NULL,
  KEY idx_upstream_config_request_user (user_id, created_at),
  KEY idx_upstream_config_request_status (status, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
