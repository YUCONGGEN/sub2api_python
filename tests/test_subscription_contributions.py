import asyncio
import base64
import json

from backend.controller.subscription_controller import SubscriptionController
from backend.service.subscription_account_service import SubscriptionAccountService


class JsonCipher:
    @staticmethod
    def encrypt(value):
        return "encrypted:" + base64.urlsafe_b64encode(json.dumps(value).encode()).decode()

    @staticmethod
    def decrypt(value):
        return json.loads(base64.urlsafe_b64decode(value.removeprefix("encrypted:")).decode())


class RequestRepository:
    def __init__(self):
        self.requests = {}

    def create_config_request(self, request):
        row = dict(request)
        row["id"] = len(self.requests) + 1
        request["id"] = row["id"]
        self.requests[row["id"]] = row
        return dict(row)

    def find_config_request(self, request_id):
        row = self.requests.get(int(request_id))
        return dict(row) if row else None


def account_row(owner_user_id):
    return {
        "id": 9,
        "owner_user_id": owner_user_id,
        "provider": "openai",
        "name": "共享账号",
        "auth_type": "oauth",
        "email": "owner@example.com",
        "account_ref": "account-secret",
        "credentials_encrypted": JsonCipher.encrypt({"access_token": "top-secret"}),
        "models_json": '["gpt-test"]',
        "model_pricing_json": '{"gpt-test":{"input_price_cny":2,"output_price_cny":8,"price_multiplier":1}}',
        "enabled": 1,
        "priority": 0,
        "weight": 1,
        "status": "READY",
        "last_error": "private upstream detail",
    }


def make_service(repository=None):
    service = SubscriptionAccountService.__new__(SubscriptionAccountService)
    service.repository = repository or RequestRepository()
    service.cipher = JsonCipher()
    service.user_contributions_enabled = True
    service.quota_visible_to_users = True
    service.cooldown_enabled = True
    return service


def test_other_users_see_shared_account_without_private_fields():
    service = make_service()
    visible = service._visible_to(account_row(12), {"id": 27, "role": "USER"})

    assert visible["can_manage"] is False
    assert visible["owner_label"] == "用户贡献"
    assert visible["credential_mask"] == "凭据已加密"
    assert "email" not in visible
    assert "account_ref" not in visible
    assert "last_error" not in visible


def test_owner_and_admin_can_manage_but_other_user_cannot():
    service = make_service()
    row = account_row(12)

    assert service.can_manage({"id": 12, "role": "USER"}, row)
    assert service.can_manage({"id": 99, "role": "ADMIN"}, row)
    assert not service.can_manage({"id": 13, "role": "USER"}, row)


def test_public_account_exposes_model_pricing_without_raw_json():
    public = make_service()._public(account_row(12))

    assert "model_pricing_json" not in public
    assert public["model_pricing"]["gpt-test"]["output_price_cny"] == 8


def test_regular_user_cannot_change_pricing_fields():
    cleaned = SubscriptionController._user_body({"id": 12, "role": "USER"}, {
        "name": "共享账号",
        "input_price_cny": 9,
        "output_price_cny": 99,
        "price_multiplier": 3,
        "model_pricing": {"gpt-test": {"input_price_cny": 10}},
    })

    assert cleaned["input_price_cny"] == 0
    assert cleaned["output_price_cny"] == 0
    assert cleaned["price_multiplier"] == 1
    assert "model_pricing" not in cleaned


def test_config_request_encrypts_key_and_defaults_to_direct_connection():
    repository = RequestRepository()
    service = make_service(repository)
    result = service.create_config_request(12, {
        "url": "https://api.example.com/v1/",
        "api_key": "sk-a-very-secret-value",
        "model_id": "model-a",
    })

    stored = repository.requests[1]
    assert "sk-a-very-secret-value" not in result["api_key_mask"]
    assert "sk-a-very-secret-value" not in stored["api_key_encrypted"]
    assert stored["use_proxy"] == 0
    assert service.config_request_secret(1)["use_proxy"] is False


def test_config_request_can_explicitly_use_proxy():
    service = make_service()
    result = service.create_config_request(12, {
        "url": "https://api.example.com/v1",
        "api_key": "sk-a-very-secret-value",
        "model_id": "model-a",
        "use_proxy": True,
    })

    assert result["use_proxy"] == 1
    assert service.config_request_secret(1)["use_proxy"] is True


def test_token_share_accepts_token_field_alias():
    repository = RequestRepository()
    service = make_service(repository)

    result = service.create_config_request(12, {
        "url": "https://api.deepseek.com/v1",
        "token": "sk-token-shared-by-owner",
        "model_id": "deepseek-chat",
    })

    assert "sk-token-shared-by-owner" not in result["api_key_mask"]
    assert "sk-token-shared-by-owner" not in repository.requests[1]["api_key_encrypted"]
    assert service.config_request_secret(1)["api_key"] == "sk-token-shared-by-owner"


def test_runtime_gateway_metrics_are_admin_only():
    controller = SubscriptionController.__new__(SubscriptionController)
    controller.auth = type("Auth", (), {"user_from_authorization": lambda self, token: {"id": 7, "role": "USER"}})()
    controller.gateway = type("Gateway", (), {"metrics": lambda self, include_users=False: {"active_requests": 4}})()

    denied = controller.gateway_metrics("Bearer user-token")

    assert denied.code == 403
    assert denied.data is None


def test_quota_visibility_defaults_to_users_and_always_allows_admin():
    service = make_service()

    assert service.quota_visible({"id": 7, "role": "USER"}) is True
    service.quota_visible_to_users = False
    assert service.quota_visible({"id": 7, "role": "USER"}) is False
    assert service.quota_visible({"id": 1, "role": "ADMIN"}) is True


def test_quota_visibility_reads_disabled_yaml_switch(monkeypatch):
    service = make_service()
    service.logger = type("Logger", (), {"info": lambda *args: None})()
    monkeypatch.setattr(
        "backend.service.subscription_account_service.get_config",
        lambda: {"rose": {"subscription-gateway": {"quota-visible-to-users": False}}},
    )

    service.init()

    assert service.quota_visible_to_users is False


def test_quota_endpoint_blocks_regular_user_when_yaml_switch_is_disabled():
    controller = SubscriptionController.__new__(SubscriptionController)
    controller.auth = type("Auth", (), {"user_from_authorization": lambda self, token: {"id": 7, "role": "USER"}})()
    controller.accounts = make_service()
    controller.accounts.quota_visible_to_users = False
    controller.gateway = type("Gateway", (), {})()

    denied = asyncio.run(controller.account_quota(9, authorization="Bearer user-token"))

    assert denied.code == 403
    assert denied.message == "订阅剩余量当前仅管理员可见"


def test_regular_user_can_read_other_shared_account_quota_without_forcing_refresh():
    controller = SubscriptionController.__new__(SubscriptionController)
    user = {"id": 7, "role": "USER"}
    controller.auth = type("Auth", (), {"user_from_authorization": lambda self, token: user})()
    controller.accounts = make_service()
    controller.accounts.find_public = lambda account_id: account_row(12)
    calls = []

    class Gateway:
        async def query_account_quota(self, account_id, force=False):
            calls.append((account_id, force))
            return {"plan_type": "pro", "long_window": {"remaining_percent": 80}}

    controller.gateway = Gateway()
    result = asyncio.run(controller.account_quota(9, refresh="true", authorization="Bearer user-token"))

    assert result.code == 200
    assert result.data["quota"]["long_window"]["remaining_percent"] == 80
    assert calls == [(9, False)]


def test_regular_account_owner_cannot_bypass_five_minute_quota_cache():
    controller = SubscriptionController.__new__(SubscriptionController)
    user = {"id": 7, "role": "USER"}
    controller.auth = type("Auth", (), {"user_from_authorization": lambda self, token: user})()
    controller.accounts = make_service()
    controller.accounts.find_public = lambda account_id: account_row(7)
    calls = []

    class Gateway:
        async def query_account_quota(self, account_id, force=False):
            calls.append((account_id, force))
            return {"plan_type": "pro"}

    controller.gateway = Gateway()
    result = asyncio.run(controller.account_quota(9, refresh="true", authorization="Bearer owner-token"))

    assert result.code == 200
    assert calls == [(9, False)]


def test_account_owner_can_reset_own_openai_quota():
    controller = SubscriptionController.__new__(SubscriptionController)
    user = {"id": 7, "role": "USER"}
    controller.auth = type("Auth", (), {"user_from_authorization": lambda self, token: user})()
    controller.accounts = make_service()
    controller.accounts.repository.find = lambda account_id: account_row(7)
    calls = []

    class Gateway:
        async def reset_account_quota(self, account_id):
            calls.append(account_id)
            return {"ok": True, "windows_reset": 2, "quota": {"reset_credits": {"available_count": 0}}}

    controller.gateway = Gateway()
    result = asyncio.run(controller.reset_account_quota(9, authorization="Bearer owner-token"))

    assert result.code == 200
    assert result.data["windows_reset"] == 2
    assert calls == [9]


def test_regular_user_cannot_reset_another_users_openai_quota():
    controller = SubscriptionController.__new__(SubscriptionController)
    user = {"id": 7, "role": "USER"}
    controller.auth = type("Auth", (), {"user_from_authorization": lambda self, token: user})()
    controller.accounts = make_service()
    controller.accounts.repository.find = lambda account_id: account_row(12)
    controller.gateway = type("Gateway", (), {})()

    result = asyncio.run(controller.reset_account_quota(9, authorization="Bearer user-token"))

    assert result.code == 403
    assert result.message == "只能管理自己添加的订阅账号"
