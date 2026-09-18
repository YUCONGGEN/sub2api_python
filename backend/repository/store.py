"""Persistence adapter: Mapper injection and SQLite compatibility bootstrap."""

from __future__ import annotations

import sqlite3
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from springbootai import Autowired, PostConstruct, Repository, get_config


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@Repository("store_repository")
class StoreRepository:
    """Own database resources and schema compatibility, never business rules."""

    @Autowired
    def __init__(self, store_mapper):
        self.mapper = store_mapper
        database_cfg = get_config().get("database", {})
        self.database_cfg = database_cfg if isinstance(database_cfg, dict) else {}
        self.driver = str(self.database_cfg.get("driver", "sqlite")).strip().lower()
        if self.driver in {"mysql", "pymysql", "mariadb"}:
            self.driver = "mysql"
            self.path = None
        else:
            self.driver = "sqlite"
            db_path = self.database_cfg.get("database") or get_config().get("rose", {}).get("database", {}).get("path", "./data/rose.db")
            self.path = Path(str(db_path)).expanduser()
            if not self.path.is_absolute():
                self.path = Path.cwd() / self.path

    @PostConstruct
    def init(self) -> None:
        """Create/upgrade schema and run idempotent credential migration."""
        if self.driver == "mysql":
            self._init_mysql()
        else:
            assert self.path is not None
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.connect() as conn:
                schema_path = Path(__file__).resolve().parents[1] / "resources" / "schema.sql"
                conn.executescript(schema_path.read_text(encoding="utf-8"))
                self._ensure_sqlite_compatibility(conn)
                self._ensure_sqlite_user_groups(conn)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_users_group ON users(group_id, deleted_at)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_subscription_plans_group ON subscription_plans(group_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_upstream_subscription_owner ON upstream_subscription_accounts(owner_user_id, created_at DESC)")
                self._migrate_sqlite_api_keys(conn)
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_api_key_hash "
                    "ON api_keys(api_key_hash)"
                )

        billing = get_config().get("rose", {}).get("billing", {})
        try:
            expire_hours = max(1, min(float(billing.get("recharge-code-expire-hours", 48)), 24 * 30))
        except (TypeError, ValueError):
            expire_hours = 48
        for legacy in self.mapper.find_recharge_codes_without_expiry():
            try:
                created = datetime.fromisoformat(str(legacy["created_at"]).replace("Z", "+00:00"))
                expires_at = (created + timedelta(hours=expire_hours)).isoformat()
            except (TypeError, ValueError):
                expires_at = (datetime.now(timezone.utc) + timedelta(hours=expire_hours)).isoformat()
            self.mapper.set_recharge_code_expiry(int(legacy["id"]), expires_at)
        self.mapper.revoke_legacy_user_keys()

    def connect(self):
        """Open a bootstrap connection; runtime SQL remains Mapper-owned."""
        if self.driver == "mysql":
            try:
                import pymysql
            except ImportError as exc:  # pragma: no cover - dependency guard
                raise RuntimeError("MySQL 模式需要安装 pymysql：python -m pip install pymysql") from exc
            return pymysql.connect(
                host=str(self.database_cfg.get("host", "127.0.0.1")),
                port=int(self.database_cfg.get("port", 3306)),
                user=str(self.database_cfg.get("username", "")),
                password=str(self.database_cfg.get("password", "")),
                database=str(self.database_cfg.get("database", "rose")),
                charset=str(self.database_cfg.get("charset", "utf8mb4")),
                autocommit=True,
                connect_timeout=5,
            )
        assert self.path is not None
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_mysql(self) -> None:
        """Create a fresh MySQL schema without touching the SQLite database."""
        schema_path = Path(__file__).resolve().parents[1] / "resources" / "schema_mysql.sql"
        with self.connect() as conn:
            with conn.cursor() as cursor:
                for statement in schema_path.read_text(encoding="utf-8").split(";"):
                    statement = "\n".join(
                        line for line in statement.splitlines()
                        if not line.strip().startswith("--")
                    ).strip()
                    if statement:
                        cursor.execute(statement)
                self._ensure_mysql_compatibility(cursor)
                self._ensure_mysql_user_groups(cursor)
                self._migrate_mysql_api_keys(cursor)
                self._validate_mysql_schema(cursor)

    def _validate_mysql_schema(self, cursor) -> None:
        """Reject an unrelated existing database with a useful startup error.

        ``CREATE TABLE IF NOT EXISTS`` cannot upgrade a table that happens to
        have the same name but belongs to another application.  In that case
        MyBatis would fail later with an opaque ``Unknown column`` warning
        while SpringBootAI is wiring controllers.  Validate the application
        contract immediately and tell the operator to use a dedicated schema
        instead of mutating an unrelated database in place.
        """
        required_columns = {
            "id",
            "username",
            "password_hash",
            "email",
            "role",
            "balance",
            "enabled",
            "api_key",
            "created_at",
            "last_login",
            "group_id",
        }
        cursor.execute("SHOW COLUMNS FROM users")
        existing_columns = {str(row[0]) for row in cursor.fetchall()}
        missing = sorted(required_columns - existing_columns)
        if missing:
            database_name = str(self.database_cfg.get("database", "<unknown>"))
            missing_text = ", ".join(missing)
            raise RuntimeError(
                f"MySQL 数据库 {database_name!r} 不是本项目数据库，users 表缺少字段: "
                f"{missing_text}。请新建独立数据库（例如 rose_proxy），不要使用已有的 springpy 数据库。"
            )

    def _ensure_sqlite_compatibility(self, conn: sqlite3.Connection) -> None:
        """Apply additive upgrades used by existing SQLite installations."""
        self._ensure_column(conn, "users", "session_version", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column(conn, "users", "deleted_at", "TEXT")
        self._ensure_column(conn, "users", "group_id", "INTEGER")
        self._ensure_column(conn, "user_groups", "weight", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column(conn, "subscription_plans", "group_id", "INTEGER")
        self._ensure_column(conn, "payment_orders", "payment_amount", "REAL")
        self._ensure_column(conn, "payment_orders", "qr_asset", "TEXT")
        self._ensure_column(conn, "recharge_codes", "code", "TEXT")
        self._ensure_column(conn, "recharge_codes", "expires_at", "TEXT")
        self._ensure_column(conn, "user_subscriptions", "auto_renew", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column(conn, "api_keys", "api_key_hash", "TEXT")
        self._ensure_column(conn, "api_keys", "key_prefix", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column(conn, "api_keys", "key_last4", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column(conn, "upstream_subscription_accounts", "owner_user_id", "INTEGER")
        self._ensure_column(conn, "upstream_subscription_accounts", "model_pricing_json", "TEXT NOT NULL DEFAULT '{}'")
        self._ensure_column(conn, "upstream_config_requests", "use_proxy", "INTEGER NOT NULL DEFAULT 0")
        for column, definition in {
            "billing_source": "TEXT NOT NULL DEFAULT 'WALLET'",
            "free_cost": "REAL NOT NULL DEFAULT 0",
            "free_tokens": "INTEGER NOT NULL DEFAULT 0",
            "subscription_cost": "REAL NOT NULL DEFAULT 0",
            "subscription_tokens": "INTEGER NOT NULL DEFAULT 0",
            "wallet_cost": "REAL NOT NULL DEFAULT 0",
            "wallet_tokens": "INTEGER NOT NULL DEFAULT 0",
            "quota_id": "INTEGER",
            "subscription_id": "INTEGER",
        }.items():
            self._ensure_column(conn, "usage_records", column, definition)
        conversation_columns = {
            "messages_json": "TEXT NOT NULL DEFAULT '[]'",
            "response_json": "TEXT",
            "answer_text": "TEXT NOT NULL DEFAULT ''",
            "prompt_tokens": "INTEGER NOT NULL DEFAULT 0",
            "completion_tokens": "INTEGER NOT NULL DEFAULT 0",
            "total_tokens": "INTEGER NOT NULL DEFAULT 0",
            "cost": "REAL NOT NULL DEFAULT 0",
            "status": "TEXT NOT NULL DEFAULT 'PROCESSING'",
            "error_message": "TEXT",
            "completed_at": "TEXT",
            "latency_ms": "INTEGER",
        }
        for column, definition in conversation_columns.items():
            self._ensure_column(conn, "conversation_records", column, definition)

    @staticmethod
    def _migrate_sqlite_api_keys(conn: sqlite3.Connection) -> None:
        """Replace legacy plaintext API keys with irreversible SHA-256 hashes."""
        rows = conn.execute(
            "SELECT id, api_key FROM api_keys WHERE api_key_hash IS NULL OR api_key_hash = ''"
        ).fetchall()
        for row in rows:
            plaintext = str(row["api_key"] or "")
            digest = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
            conn.execute(
                "UPDATE api_keys SET api_key_hash=?, key_prefix=?, key_last4=?, api_key=? WHERE id=?",
                (digest, plaintext[:12], plaintext[-4:], "hashed-" + digest, int(row["id"])),
            )

    @staticmethod
    def _ensure_mysql_compatibility(cursor) -> None:
        """Apply small additive upgrades for existing MySQL installations."""
        additions = {
            "users": {
                "session_version": "BIGINT NOT NULL DEFAULT 0",
                "deleted_at": "VARCHAR(40)",
                "group_id": "BIGINT",
            },
            "user_groups": {
                "weight": "INT NOT NULL DEFAULT 0",
            },
            "subscription_plans": {
                "group_id": "BIGINT",
            },
            "api_keys": {
                "api_key_hash": "CHAR(64)",
                "key_prefix": "VARCHAR(32) NOT NULL DEFAULT ''",
                "key_last4": "VARCHAR(8) NOT NULL DEFAULT ''",
            },
            "upstream_subscription_accounts": {
                "owner_user_id": "BIGINT",
                "model_pricing_json": "LONGTEXT NOT NULL",
            },
            "upstream_config_requests": {
                "use_proxy": "TINYINT NOT NULL DEFAULT 0",
            },
        }
        for table, columns in additions.items():
            cursor.execute(f"SHOW COLUMNS FROM {table}")
            existing = {str(row[0]) for row in cursor.fetchall()}
            for column, definition in columns.items():
                if column not in existing:
                    cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        cursor.execute("SHOW INDEX FROM api_keys WHERE Key_name='idx_api_key_hash'")
        if not cursor.fetchone():
            cursor.execute("CREATE UNIQUE INDEX idx_api_key_hash ON api_keys(api_key_hash)")
        cursor.execute("SHOW INDEX FROM users WHERE Key_name='idx_users_group'")
        if not cursor.fetchone():
            cursor.execute("CREATE INDEX idx_users_group ON users(group_id, deleted_at)")
        cursor.execute("SHOW INDEX FROM subscription_plans WHERE Key_name='idx_subscription_plans_group'")
        if not cursor.fetchone():
            cursor.execute("CREATE INDEX idx_subscription_plans_group ON subscription_plans(group_id)")
        cursor.execute("SHOW INDEX FROM upstream_subscription_accounts WHERE Key_name='idx_upstream_subscription_owner'")
        if not cursor.fetchone():
            cursor.execute("CREATE INDEX idx_upstream_subscription_owner ON upstream_subscription_accounts(owner_user_id, created_at)")

    @staticmethod
    def _ensure_sqlite_user_groups(conn: sqlite3.Connection) -> None:
        """Seed default policies and attach legacy users without overwriting assignments."""
        now = utc_now()
        conn.execute(
            "INSERT OR IGNORE INTO user_groups(name,description,weight,concurrency_limit,allowed_models_json,is_default,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ("默认用户组", "新注册用户自动加入；每位用户只允许 1 个并发请求。", 0, 1, '["*"]', 1, now, now),
        )
        conn.execute(
            "INSERT OR IGNORE INTO user_groups(name,description,weight,concurrency_limit,allowed_models_json,is_default,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ("管理员组", "管理员默认组。", 1000, 20, '["*"]', 0, now, now),
        )
        conn.execute("UPDATE user_groups SET weight=0 WHERE name='默认用户组'")
        conn.execute("UPDATE user_groups SET weight=1000 WHERE name='管理员组' AND weight=0")
        default_row = conn.execute("SELECT id FROM user_groups WHERE is_default=1 ORDER BY id LIMIT 1").fetchone()
        if not default_row:
            conn.execute("UPDATE user_groups SET is_default=1 WHERE name='默认用户组'")
            default_row = conn.execute("SELECT id FROM user_groups WHERE name='默认用户组' LIMIT 1").fetchone()
        admin_row = conn.execute("SELECT id FROM user_groups WHERE name='管理员组' LIMIT 1").fetchone()
        conn.execute(
            "UPDATE users SET group_id=? WHERE group_id IS NULL AND role='ADMIN'",
            (int(admin_row[0]),),
        )
        conn.execute(
            "UPDATE users SET group_id=? WHERE group_id IS NULL",
            (int(default_row[0]),),
        )

    @staticmethod
    def _ensure_mysql_user_groups(cursor) -> None:
        now = utc_now()
        cursor.execute(
            "INSERT IGNORE INTO user_groups(name,description,weight,concurrency_limit,allowed_models_json,is_default,created_at,updated_at) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
            ("默认用户组", "新注册用户自动加入；每位用户只允许 1 个并发请求。", 0, 1, '["*"]', 1, now, now),
        )
        cursor.execute(
            "INSERT IGNORE INTO user_groups(name,description,weight,concurrency_limit,allowed_models_json,is_default,created_at,updated_at) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
            ("管理员组", "管理员默认组。", 1000, 20, '["*"]', 0, now, now),
        )
        cursor.execute("UPDATE user_groups SET weight=0 WHERE name='默认用户组'")
        cursor.execute("UPDATE user_groups SET weight=1000 WHERE name='管理员组' AND weight=0")
        cursor.execute("SELECT id FROM user_groups WHERE is_default=1 ORDER BY id LIMIT 1")
        default_row = cursor.fetchone()
        if not default_row:
            cursor.execute("UPDATE user_groups SET is_default=1 WHERE name='默认用户组'")
            cursor.execute("SELECT id FROM user_groups WHERE name='默认用户组' LIMIT 1")
            default_row = cursor.fetchone()
        cursor.execute("SELECT id FROM user_groups WHERE name='管理员组' LIMIT 1")
        admin_row = cursor.fetchone()
        cursor.execute("UPDATE users SET group_id=%s WHERE group_id IS NULL AND role='ADMIN'", (int(admin_row[0]),))
        cursor.execute("UPDATE users SET group_id=%s WHERE group_id IS NULL", (int(default_row[0]),))

    @staticmethod
    def _migrate_mysql_api_keys(cursor) -> None:
        cursor.execute("SELECT id, api_key FROM api_keys WHERE api_key_hash IS NULL OR api_key_hash = ''")
        for key_id, raw_key in cursor.fetchall():
            plaintext = str(raw_key or "")
            digest = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
            cursor.execute(
                "UPDATE api_keys SET api_key_hash=%s,key_prefix=%s,key_last4=%s,api_key=%s WHERE id=%s",
                (digest, plaintext[:12], plaintext[-4:], "hashed-" + digest, int(key_id)),
            )

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


__all__ = ["StoreRepository"]
