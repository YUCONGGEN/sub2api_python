import sqlite3
import json
from datetime import datetime, timezone

from backend.controller.auth_controller import AuthController
from backend.controller.admin_controller import AdminController
from backend.repository.store import StoreRepository
from backend.service.store_service import StoreService
from backend.service.subscription_account_service import SubscriptionAccountService


def service_with(mapper):
    service = StoreService.__new__(StoreService)
    service.mapper = mapper
    return service


class SplitBillingMapper:
    def __init__(self):
        self.allocations = []
        self.usage = None

    @staticmethod
    def ensure_billing_lock(user_id, updated_at):
        return 1

    @staticmethod
    def acquire_billing_lock(user_id, updated_at):
        return 1

    @staticmethod
    def find_balance(user_id):
        return {"id": user_id, "balance": 0, "enabled": 1}

    @staticmethod
    def find_active_quota_policies(user_id, now):
        return [
            {"id": 11, "daily_amount": 0, "daily_tokens": 60, "hourly_tokens": 0},
            {"id": 12, "daily_amount": 0, "daily_tokens": 50, "hourly_tokens": 0},
        ]

    @staticmethod
    def quota_usage_totals(user_id, quota_id, start_at, end_at):
        return {"free_cost": 0, "free_tokens": 0}

    @staticmethod
    def find_active_subscriptions(user_id, now):
        return []

    def insert_usage(self, usage):
        usage["id"] = 91
        self.usage = dict(usage)
        return 1

    def insert_usage_allocation(self, allocation):
        self.allocations.append(dict(allocation))
        return 1

    def find_usage(self, usage_id):
        return self.usage


def test_one_request_can_span_multiple_free_grants_without_wallet_charge():
    mapper = SplitBillingMapper()
    accepted, usage = service_with(mapper).charge(7, "gpt-test", 70, 30, 1.0)

    assert accepted is True
    assert usage["billing_source"] == "FREE"
    assert usage["free_tokens"] == 100
    assert usage["free_cost"] == 1.0
    assert usage["wallet_cost"] == 0
    assert [(item["entitlement_id"], item["tokens"]) for item in mapper.allocations] == [(11, 60), (12, 40)]


def test_plaintext_api_keys_are_replaced_by_hashes():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("CREATE TABLE api_keys(id INTEGER PRIMARY KEY, api_key TEXT, api_key_hash TEXT, key_prefix TEXT, key_last4 TEXT)")
    connection.execute("INSERT INTO api_keys(id,api_key) VALUES(1,'sk-api-secret-value')")

    StoreRepository._migrate_sqlite_api_keys(connection)
    row = connection.execute("SELECT api_key,api_key_hash,key_prefix,key_last4 FROM api_keys WHERE id=1").fetchone()

    assert row["api_key"].startswith("hashed-")
    assert "secret-value" not in row["api_key"]
    assert len(row["api_key_hash"]) == 64
    assert row["key_prefix"] == "sk-api-secre"
    assert row["key_last4"] == "alue"


class LegacyAuth:
    store = object()

    @staticmethod
    def user_from_authorization(authorization):
        return {"id": 5}

    @staticmethod
    def claims_from_authorization(authorization):
        return {"sub": "5"}


def test_legacy_jwt_can_open_device_page_without_forced_logout():
    controller = AuthController(LegacyAuth.store, LegacyAuth())
    response = controller.sessions("Bearer legacy")

    assert response.data["legacy_session"] is True
    assert response.data["sessions"] == []
    assert response.data["pagination"] == {"page": 1, "page_size": 5, "total": 0, "pages": 1}


class SessionPageMapper:
    def __init__(self):
        self.query = None

    @staticmethod
    def count_active_user_sessions(user_id):
        return 7

    def list_user_sessions(self, user_id, current_session_id, limit, offset):
        self.query = (user_id, current_session_id, limit, offset)
        return [{"id": current_session_id}, {"id": "another-session"}]


def test_login_sessions_are_returned_as_a_server_side_page():
    mapper = SessionPageMapper()
    result = service_with(mapper).list_sessions(9, "current-session", page=2, page_size=5)

    assert mapper.query == (9, "current-session", 5, 5)
    assert result["pagination"] == {"page": 2, "page_size": 5, "total": 7, "pages": 2}
    assert result["sessions"][0]["current"] is True
    assert result["sessions"][1]["current"] is False


class UsagePageMapper:
    def __init__(self):
        self.query = None

    @staticmethod
    def count_usage(user_id):
        return 7

    def list_usage(self, user_id, offset, limit):
        self.query = (user_id, offset, limit)
        return [{"id": 6, "model": "gpt-test", "total_tokens": 120, "cost": 0.02, "status": "SUCCEEDED"}]


def test_recent_usage_uses_the_same_five_item_page_for_user_and_admin_views():
    mapper = UsagePageMapper()
    result = service_with(mapper).list_usage_page(9, page=2, page_size=20)

    assert mapper.query == (9, 5, 5)
    assert result["items"][0]["model"] == "gpt-test"
    assert result["page"] == 2
    assert result["page_size"] == 5
    assert result["total"] == 7
    assert result["pages"] == 2


class AdminUsageStore:
    def __init__(self):
        self.query = None

    @staticmethod
    def find_user(user_id):
        return {"id": user_id}

    def list_usage_page(self, user_id, page, page_size):
        self.query = (user_id, page, page_size)
        return {"items": [{"id": 31}], "page": page, "page_size": page_size, "total": 1, "pages": 1}


class AdminUsageAuth:
    def __init__(self):
        self.store = AdminUsageStore()

    @staticmethod
    def user_from_authorization(authorization):
        return {"id": 1, "role": "ADMIN"}


def test_admin_can_page_a_users_recent_usage_without_loading_the_whole_detail():
    auth = AdminUsageAuth()
    controller = AdminController(auth, object(), object())

    response = controller.user_usage(7, "Bearer admin", page=2, page_size=5)

    assert auth.store.query == (7, 2, 5)
    assert response.data["ok"] is True
    assert response.data["usage"] == [{"id": 31}]
    assert response.data["pagination"]["page"] == 2


class RevocationOnlyMapper:
    def __init__(self):
        self.revoked = None

    def revoke_api_key(self, user_id, key_id):
        self.revoked = (user_id, key_id)
        return 1


def test_legacy_delete_key_endpoint_keeps_history_by_revoking():
    mapper = RevocationOnlyMapper()

    assert service_with(mapper).delete_api_key(3, 8) is True
    assert mapper.revoked == (3, 8)


class CatalogRepository:
    @staticmethod
    def list_provider(provider):
        if provider != "openai":
            return []
        return [{
            "id": 4,
            "provider": "openai",
            "models_json": json.dumps(["gpt-test"]),
            "credentials_encrypted": "encrypted",
            "enabled": 1,
            "status": "COOLDOWN",
            "cooldown_until": "2099-01-01T00:00:00+00:00",
            "last_error": "Selected model is at capacity",
            "input_price_cny": 0,
            "output_price_cny": 0,
        }]


class CatalogCipher:
    @staticmethod
    def decrypt(value):
        return {"access_token": "present"}


def test_subscription_catalog_reports_pool_cooldown_instead_of_fake_healthy():
    service = SubscriptionAccountService.__new__(SubscriptionAccountService)
    service.repository = CatalogRepository()
    service.cipher = CatalogCipher()

    model = service.catalog()[0]

    assert model["status"] == "冷却中"
    assert model["enabled"] is False
    assert "0/1 个订阅账号可调度" in model["health"]["detail"]


class RechargeCodeMapper:
    def __init__(self):
        self.row = None

    def insert_recharge_code(self, row):
        row["id"] = 1
        self.row = dict(row)
        return 1

    def find_recharge_code(self, code_hash):
        return self.row


def test_recharge_code_accepts_an_admin_selected_expiry():
    mapper = RechargeCodeMapper()
    row = service_with(mapper).create_recharge_code(10, 1, "CODE-TEST-TEST-TEST", 72)

    created_at = datetime.fromisoformat(row["created_at"]).astimezone(timezone.utc)
    expires_at = datetime.fromisoformat(row["expires_at"]).astimezone(timezone.utc)
    assert (expires_at - created_at).total_seconds() == 72 * 3600


class DefaultKeyStore:
    def __init__(self):
        self.created = None

    @staticmethod
    def find_by_username(username):
        return None

    def create_user_with_default_key(self, *args, **kwargs):
        self.created = (args, kwargs)
        return ({"id": 41, "username": args[0], "role": kwargs.get("role", args[3] if len(args) > 3 else "USER")}, {"id": 9, "api_key": "sk-api-once-only"})

    @staticmethod
    def public_user(user):
        return dict(user)


class DefaultKeyAuth:
    def __init__(self, admin=False):
        self.store = DefaultKeyStore()
        self.admin = admin

    @staticmethod
    def issue_token(user, user_agent, ip_address):
        return "signed-token"

    def user_from_authorization(self, authorization):
        return {"id": 1, "role": "ADMIN"} if self.admin else None


def test_registration_returns_the_first_api_key_once():
    auth = DefaultKeyAuth()
    response = AuthController(auth.store, auth).register({"username": "new_user", "password": "secret12", "email": ""}, "browser", "127.0.0.1")

    assert response.data["api_key"] == "sk-api-once-only"
    assert response.data["api_key_id"] == 9
    assert response.data["token"] == "signed-token"
    assert auth.store.created is not None
    assert auth.store.created[1]["balance"] == 50.0


def test_admin_user_creation_returns_the_first_api_key_once():
    auth = DefaultKeyAuth(admin=True)
    response = AdminController(auth, object(), object()).create_user({
        "username": "managed_user", "password": "secret12", "role": "USER", "group_id": 3,
    }, "Bearer admin")

    assert response.data["api_key"] == "sk-api-once-only"
    assert response.data["user"]["username"] == "managed_user"
    assert auth.store.created[1]["group_id"] == 3


def test_page_is_clamped_before_rows_are_queried():
    mapper = UsagePageMapper()
    result = service_with(mapper).list_usage_page(9, page=99, page_size=5)

    assert mapper.query == (9, 5, 5)
    assert result["page"] == 2


class ExhaustedEntitlementMapper:
    @staticmethod
    def find_balance(user_id):
        return {"id": user_id, "enabled": 1, "balance": 0}

    @staticmethod
    def find_active_quota_policies(user_id, now):
        return [{"id": 17, "daily_amount": 0, "daily_tokens": 10, "hourly_tokens": 0}]

    @staticmethod
    def quota_usage_totals(user_id, quota_id, start_at, end_at):
        return {"free_cost": 0, "free_tokens": 10}

    @staticmethod
    def find_active_subscriptions(user_id, now):
        return []


def test_exhausted_but_unexpired_entitlement_is_not_reported_as_usable():
    assert service_with(ExhaustedEntitlementMapper()).has_usable_balance(3) is False
