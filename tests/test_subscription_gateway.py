import asyncio
import base64
import hashlib
import json
import logging
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet

from backend.common.codex_client import (
    DEFAULT_CODEX_CLIENT_VERSION,
    codex_client_version,
    normalize_codex_client_version,
)
from backend.common.subscription_providers import DEFAULT_MODELS, SUBSCRIPTION_PROVIDERS
from backend.protocol import subscription_adapter
from backend.service.credential_cipher_service import CredentialCipherService
from backend.service.openai_chat_compatibility_service import OpenAIChatCompatibilityService
from backend.service.subscription_account_pool_service import SubscriptionAccountPoolService
from backend.service.subscription_account_service import SubscriptionAccountService
from backend.service.subscription_gateway_service import SubscriptionGatewayService
from backend.service.subscription_gateway_service import SubscriptionGatewayResponse
from backend.service.subscription_oauth_service import SubscriptionOAuthService


class MemoryRepository:
    def __init__(self, rows):
        self.rows = {int(row["id"]): dict(row) for row in rows}

    def find(self, account_id):
        row = self.rows.get(int(account_id))
        return dict(row) if row else None

    def list_provider(self, provider):
        return [dict(row) for row in self.rows.values() if row["provider"] == provider and row["enabled"]]

    def update_credentials(self, account_id, **changes):
        self.rows[int(account_id)].update(changes)
        return self.find(account_id)

    def mark_result(self, account_id, **changes):
        self.rows[int(account_id)].update(changes)

    def disable_rate_limited(self, account_id, **changes):
        self.rows[int(account_id)].update({
            **changes,
            "enabled": 0,
            "status": "DISABLED",
            "cooldown_until": None,
        })


class JsonCipher:
    @staticmethod
    def encrypt(value):
        return json.dumps(value)

    @staticmethod
    def decrypt(value):
        return json.loads(value)


class NoopOAuth:
    async def refresh(self, provider, refresh_token):  # pragma: no cover - refresh is not needed in this fixture
        raise AssertionError("unexpected refresh")


def account(account_id, *, priority=0, weight=1, models=None):
    return {
        "id": account_id,
        "provider": "openai",
        "enabled": 1,
        "priority": priority,
        "weight": weight,
        "models_json": json.dumps(models or ["gpt-test"]),
        "model_pricing_json": "{}",
        "credentials_encrypted": json.dumps({"access_token": f"token-{account_id}"}),
        "expires_at": None,
        "cooldown_until": None,
        "error_count": 0,
        "input_price_cny": 2,
        "output_price_cny": 8,
        "price_multiplier": 1.5,
    }


def test_oauth_authorization_uses_pkce_and_binds_state_to_admin():
    oauth = SubscriptionOAuthService()
    oauth.init()
    generated = oauth.generate_authorization("openai", 17)
    query = parse_qs(urlparse(generated["authorization_url"]).query)

    assert query["code_challenge_method"] == ["S256"]
    assert query["state"][0]
    session = oauth._sessions[generated["session_id"]]
    expected = base64.urlsafe_b64encode(hashlib.sha256(session["verifier"].encode()).digest()).decode().rstrip("=")
    assert query["code_challenge"] == [expected]
    with pytest.raises(ValueError, match="state"):
        oauth._consume_session(generated["session_id"], "openai", 17, "wrong-state")
    with pytest.raises(ValueError, match="当前用户"):
        oauth._consume_session(generated["session_id"], "openai", 18, query["state"][0])


def test_credential_cipher_never_persists_plaintext():
    cipher = CredentialCipherService()
    secret = b"unit-test-credential-secret"
    cipher._fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret).digest()))
    encrypted = cipher.encrypt({"access_token": "top-secret", "refresh_token": "refresh-secret"})

    assert encrypted.startswith("v1:")
    assert "top-secret" not in encrypted
    assert cipher.decrypt(encrypted)["refresh_token"] == "refresh-secret"


def test_codex_auth_json_import_extracts_nested_credentials():
    auth_json = json.dumps({
        "tokens": {
            "access_token": "header.payload.signature",
            "refresh_token": "refresh-value",
            "id_token": "id-token",
            "expires_at": "2026-12-31T00:00:00Z",
        },
        "account_id": "account-17",
        "user": {"email": "owner@example.com"},
    })

    credentials = SubscriptionAccountService.normalize_imported_credentials(
        "openai", {"access_token": auth_json}, {},
    )

    assert credentials == {
        "access_token": "header.payload.signature",
        "refresh_token": "refresh-value",
        "id_token": "id-token",
        "expires_at": "2026-12-31T00:00:00Z",
        "account_id": "account-17",
        "email": "owner@example.com",
    }


@pytest.mark.parametrize("provider", ["grok", "kimi", "zhipu", "minimax"])
def test_extended_subscription_providers_have_safe_defaults(provider):
    assert SubscriptionAccountService.normalize_provider(provider) == provider
    assert SubscriptionAccountService.normalize_models(provider, "") == DEFAULT_MODELS[provider]
    assert SUBSCRIPTION_PROVIDERS[provider]["models"]


def test_grok_oauth_authorization_and_token_exchange(monkeypatch):
    oauth = SubscriptionOAuthService()
    oauth.init()
    generated = oauth.generate_authorization("grok", 17)
    query = parse_qs(urlparse(generated["authorization_url"]).query)

    assert urlparse(generated["authorization_url"]).netloc == "auth.x.ai"
    assert query["client_id"] == ["b1a00492-073a-47ea-816f-4c329264a828"]
    assert query["scope"] == ["openid profile email offline_access grok-cli:access api:access"]
    assert query["nonce"][0]
    captured = {}

    async def fake_post(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return {"access_token": "grok-access", "refresh_token": "grok-refresh", "expires_in": 3600}

    monkeypatch.setattr(oauth, "_post_token", fake_post)
    credentials = asyncio.run(oauth.exchange(
        "grok",
        session_id=generated["session_id"],
        callback_value=f"http://127.0.0.1:56121/callback?code=ok&state={query['state'][0]}",
        state="",
        admin_id=17,
    ))

    assert captured["url"] == "https://auth.x.ai/oauth2/token"
    assert captured["data"]["code_verifier"]
    assert credentials["access_token"] == "grok-access"
    assert credentials["base_url"] == "https://cli-chat-proxy.grok.com/v1"


@pytest.mark.parametrize(
    ("provider", "expected_url"),
    [
        ("grok", "https://cli-chat-proxy.grok.com/v1/responses"),
        ("kimi", "https://api.kimi.com/coding/v1/chat/completions"),
        ("zhipu", "https://open.bigmodel.cn/api/coding/paas/v4/chat/completions"),
        ("minimax", "https://api.minimaxi.com/v1/chat/completions"),
    ],
)
def test_extended_provider_request_targets_and_headers(provider, expected_url):
    gateway = SubscriptionGatewayService(None, None)
    gateway.grok_client_version = "0.2.120"
    url, headers = gateway._provider_request(
        provider, {"access_token": "secret"}, {"id": 1}, {}, True,
    )

    assert url == expected_url
    assert headers["Authorization"] == "Bearer secret"
    assert headers["Accept"] == "text/event-stream"
    if provider == "grok":
        assert headers["X-XAI-Token-Auth"] == "xai-grok-cli"
        assert headers["x-grok-client-version"] == "0.2.120"


def test_compatible_chat_route_is_direct_and_enables_stream_usage(monkeypatch):
    gateway = SubscriptionGatewayService(None, None)
    captured = {}

    async def fake_proxy(provider, model, payload, user_id, incoming_headers, **kwargs):
        captured.update({"provider": provider, "model": model, "payload": payload, "user_id": user_id})
        return SubscriptionGatewayResponse(
            200, {"content-type": "application/json"}, body=b'{"choices":[]}',
        )

    monkeypatch.setattr(gateway, "_proxy", fake_proxy)
    result = asyncio.run(gateway.proxy_chat("kimi", {
        "model": "kimi-for-coding", "messages": [{"role": "user", "content": "hi"}], "stream": True,
    }, 9))

    assert result.status_code == 200
    assert captured["provider"] == "kimi"
    assert captured["payload"]["stream_options"] == {"include_usage": True}


def test_openai_browser_session_jwe_is_rejected_before_saving():
    with pytest.raises(ValueError, match="浏览器会话 JWE"):
        SubscriptionAccountService.normalize_imported_credentials(
            "openai", {"access_token": "one.two.three.four.five"}, {},
        )


def test_account_scheduler_honors_priority_and_model_match():
    repository = MemoryRepository([
        account(1, priority=1, weight=100),
        account(2, priority=2, weight=1),
        account(3, priority=9, models=["different-model"]),
    ])
    service = SubscriptionAccountService(repository, JsonCipher(), NoopOAuth(), SubscriptionAccountPoolService())

    selected, credentials = asyncio.run(service.acquire("openai", "gpt-test"))

    assert selected["id"] == 2
    assert credentials["access_token"] == "token-2"
    assert service.has_route("openai", "gpt-test") is True
    assert service.has_route("openai", "missing") is False


def test_explicit_session_affinity_can_keep_a_healthy_lower_priority_account():
    repository = MemoryRepository([
        account(1, priority=10),
        account(2, priority=1),
    ])
    service = SubscriptionAccountService(repository, JsonCipher(), NoopOAuth(), SubscriptionAccountPoolService())

    selected, _ = asyncio.run(
        service.acquire("openai", "gpt-test", preferred_account_id=2)
    )

    assert selected["id"] == 2


def test_account_pool_uses_smooth_weighted_round_robin():
    pool = SubscriptionAccountPoolService()
    rows = [account(1, weight=2), account(2, weight=1)]

    selected = [pool.select(rows, "openai", "gpt-test", 0)["id"] for _ in range(6)]

    assert selected.count(1) == 4
    assert selected.count(2) == 2
    assert selected[:3] == [1, 2, 1]


def test_account_acquire_falls_back_to_lower_priority_when_refresh_fails():
    class FailingCipher(JsonCipher):
        @staticmethod
        def decrypt(value):
            data = json.loads(value)
            if data.get("access_token") == "token-2":
                raise ValueError("broken credential")
            return data

    repository = MemoryRepository([
        account(1, priority=1),
        account(2, priority=9),
    ])
    service = SubscriptionAccountService(
        repository, FailingCipher(), NoopOAuth(), SubscriptionAccountPoolService(),
    )

    selected, _ = asyncio.run(service.acquire("openai", "gpt-test"))

    assert selected["id"] == 1


def test_scheduled_token_refresh_only_refreshes_accounts_expiring_within_one_day():
    now = datetime.now(timezone.utc)
    due = account(1)
    due["expires_at"] = (now + timedelta(hours=23)).isoformat()
    due["credentials_encrypted"] = json.dumps({
        "access_token": "old-due-token",
        "refresh_token": "refresh-due",
        "expires_at": due["expires_at"],
    })
    later = account(2)
    later["expires_at"] = (now + timedelta(hours=25)).isoformat()
    later["credentials_encrypted"] = json.dumps({
        "access_token": "old-later-token",
        "refresh_token": "refresh-later",
        "expires_at": later["expires_at"],
    })

    class RecordingOAuth:
        def __init__(self):
            self.calls = []

        async def refresh(self, provider, refresh_token):
            self.calls.append((provider, refresh_token))
            return {
                "access_token": "fresh-token",
                "refresh_token": refresh_token,
                "expires_at": (now + timedelta(days=7)).isoformat(),
            }

    repository = MemoryRepository([due, later])
    oauth = RecordingOAuth()
    service = SubscriptionAccountService(
        repository, JsonCipher(), oauth, SubscriptionAccountPoolService(),
    )
    service.logger = logging.getLogger("test.subscription.token-refresh")

    asyncio.run(service.refresh_expiring_tokens())

    assert oauth.calls == [("openai", "refresh-due")]
    assert json.loads(repository.find(1)["credentials_encrypted"])["access_token"] == "fresh-token"
    assert json.loads(repository.find(2)["credentials_encrypted"])["access_token"] == "old-later-token"


def test_request_path_keeps_five_minute_refresh_fallback_to_avoid_refresh_storms():
    now = datetime.now(timezone.utc)
    row = account(1)
    row["expires_at"] = (now + timedelta(hours=23)).isoformat()
    row["credentials_encrypted"] = json.dumps({
        "access_token": "still-valid-token",
        "refresh_token": "refresh-token",
        "expires_at": row["expires_at"],
    })

    class RecordingOAuth:
        def __init__(self):
            self.calls = []

        async def refresh(self, provider, refresh_token):
            self.calls.append((provider, refresh_token))
            return {"access_token": "unexpected"}

    oauth = RecordingOAuth()
    service = SubscriptionAccountService(
        MemoryRepository([row]), JsonCipher(), oauth, SubscriptionAccountPoolService(),
    )

    result = asyncio.run(service.refresh_account(1, force=False))

    assert result["credentials"]["access_token"] == "still-valid-token"
    assert oauth.calls == []


def test_gateway_safety_limits_and_hashes_explicit_session_ids():
    gateway = SubscriptionGatewayService(None, None)
    gateway.per_account_rpm = 2
    gateway.session_affinity_ttl = 60

    assert gateway._reserve_rate_slot(7) is True
    assert gateway._reserve_rate_slot(7) is True
    assert gateway._reserve_rate_slot(7) is False
    key = gateway._session_key("openai", 9, {"prompt_cache_key": "private-session"})
    assert key and "private-session" not in key
    gateway._remember_account(key, 7)
    assert gateway._preferred_account(key) == 7
    gateway._forget_account(key, 7)
    assert gateway._preferred_account(key) is None


def test_gateway_queue_timeout_is_local_503_and_does_not_consume_rpm():
    class SingleAccount:
        async def acquire(self, provider, model, excluded, preferred_account_id=None):
            return account(3), {"access_token": "token-3"}

    async def scenario():
        gateway = SubscriptionGatewayService(SingleAccount(), None)
        gateway.per_account_concurrency = 1
        gateway.per_account_rpm = 20
        gateway.queue_timeout = 0.01
        gateway.max_attempts = 1
        gateway.session_affinity_ttl = 60
        gateway.logger = logging.getLogger("test.subscription_gateway")
        semaphore = gateway._account_semaphore(3)
        await semaphore.acquire()
        try:
            result = await gateway._proxy(
                "openai", "gpt-test", {"model": "gpt-test"}, 1, {},
            )
        finally:
            semaphore.release()

        assert result.status_code == 503
        assert result.headers["retry-after"] == "1"
        assert result.headers["x-rose-error-source"] == "local_queue"
        assert json.loads(result.body)["error"]["type"] == "local_queue_timeout"
        assert gateway._rate_windows == {}

    asyncio.run(scenario())


def test_gateway_zero_queue_timeout_waits_until_slot_is_available():
    async def scenario():
        gateway = SubscriptionGatewayService(None, None)
        gateway.queue_timeout = 0
        semaphore = asyncio.Semaphore(1)
        await semaphore.acquire()

        async def release_slot():
            await asyncio.sleep(0.01)
            semaphore.release()

        release_task = asyncio.create_task(release_slot())
        waited_ms = await gateway._acquire_account_slot(semaphore)
        await release_task
        semaphore.release()
        assert waited_ms >= 1

    asyncio.run(scenario())


def test_gateway_admin_metrics_identify_queued_and_active_users():
    class UserStore:
        @staticmethod
        def find_user(user_id):
            return {"id": user_id, "username": "alice"}

    async def scenario():
        gateway = SubscriptionGatewayService(None, UserStore())
        gateway.per_account_concurrency = 1
        gateway.per_account_rpm = 20
        gateway.queue_timeout = 0
        gateway.max_queued_requests = 200
        semaphore = gateway._account_semaphore(3)
        await semaphore.acquire()
        activity = gateway._new_activity("openai", "gpt-test", 9, 3, "high")

        waiter = asyncio.create_task(gateway._acquire_account_slot(semaphore, 3, activity))
        await asyncio.sleep(0.01)
        queued = gateway.metrics(include_users=True)
        public = gateway.metrics()

        assert queued["queue_waiting"] == 1
        assert queued["concurrent_tasks"] == 1
        assert queued["queued_users"][0]["username"] == "alice"
        assert queued["queued_users"][0]["model"] == "gpt-test"
        assert queued["queued_users"][0]["reasoning_effort"] == "high"
        assert queued["queued_users"][0]["user_concurrent_tasks"] == 0
        assert queued["queued_users"][0]["user_queued_tasks"] == 1
        assert "active_users" not in public
        assert "queued_users" not in public

        semaphore.release()
        await waiter
        active = gateway.metrics(include_users=True)
        assert active["queue_waiting"] == 0
        assert active["queued_users"] == []
        assert active["active_users"][0]["user_id"] == 9
        assert active["active_users"][0]["account_id"] == 3
        assert active["active_users"][0]["reasoning_effort"] == "high"
        assert active["active_users"][0]["user_concurrent_tasks"] == 1

        gateway._finish_activity(activity["request_id"])
        semaphore.release()
        assert gateway.metrics(include_users=True)["active_users"] == []

    asyncio.run(scenario())


def test_gateway_extracts_reasoning_effort_from_supported_request_shapes():
    assert SubscriptionGatewayService._reasoning_effort({"reasoning": {"effort": "xhigh"}}) == "xhigh"
    assert SubscriptionGatewayService._reasoning_effort({"reasoning_effort": "medium"}) == "medium"
    assert SubscriptionGatewayService._reasoning_effort({}) == ""


def test_usage_parsing_and_cost_for_both_protocols():
    openai_usage = {"input_tokens": 0, "output_tokens": 0}
    SubscriptionGatewayService._update_usage_from_sse(
        "openai",
        b'data: {"type":"response.completed","response":{"usage":{"input_tokens":120,"output_tokens":30}}}',
        openai_usage,
    )
    claude_usage = {"input_tokens": 0, "output_tokens": 0}
    SubscriptionGatewayService._update_usage_from_sse(
        "claude",
        b'data: {"type":"message_delta","usage":{"output_tokens":7}}',
        claude_usage,
    )

    assert openai_usage == {"input_tokens": 120, "output_tokens": 30}
    assert claude_usage == {"input_tokens": 0, "output_tokens": 7}
    assert SubscriptionAccountService.cost(account(1), 1_000_000, 500_000) == 9.0


def test_model_specific_pricing_overrides_account_defaults_and_falls_back():
    row = account(1, models=["gpt-cheap", "gpt-premium"])
    row["model_pricing_json"] = json.dumps({
        "gpt-premium": {
            "input_price_cny": 10,
            "output_price_cny": 30,
            "price_multiplier": 2,
        },
    })

    assert SubscriptionAccountService.cost(row, 1_000_000, 500_000, "gpt-premium") == 50.0
    assert SubscriptionAccountService.cost(row, 1_000_000, 500_000, "gpt-cheap") == 9.0
    assert SubscriptionAccountService.pricing_for_model(row, "gpt-premium") == {
        "input_price_cny": 10.0,
        "output_price_cny": 30.0,
        "price_multiplier": 2.0,
    }


def test_model_specific_pricing_only_accepts_models_on_the_account():
    normalized = SubscriptionAccountService.normalize_model_pricing(
        ["gpt-a", "gpt-b"],
        {"gpt-a": {"input_price_cny": 1.5, "output_price_cny": 6, "price_multiplier": 1.2}},
    )
    assert normalized["gpt-a"]["output_price_cny"] == 6
    with pytest.raises(ValueError, match="未配置的模型"):
        SubscriptionAccountService.normalize_model_pricing(
            ["gpt-a"], {"gpt-other": {"input_price_cny": 1}},
        )


class AsyncChunks(httpx.AsyncByteStream):
    def __init__(self, *chunks):
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


class SequenceClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.requests = []

    @staticmethod
    def build_request(method, url, **kwargs):
        return httpx.Request(method, url, headers=kwargs.get("headers"), json=kwargs.get("json"))

    async def send(self, request, stream=False):
        self.calls += 1
        self.requests.append(request)
        response = self.responses.pop(0)
        response.request = request
        return response


class RecordingAccounts:
    def __init__(self):
        self.successes = []
        self.failures = []
        self.weekly_disables = []

    async def acquire(self, provider, model, excluded, preferred_account_id=None):
        return account(3), {"access_token": "token-3"}

    def record_success(self, row):
        self.successes.append(int(row["id"]))

    def record_failure(self, row, status, detail, retry_after=None):
        self.failures.append((int(row["id"]), int(status), str(detail)))

    def disable_for_weekly_quota(self, row, remaining_percent, threshold=2.0):
        if float(remaining_percent) >= float(threshold):
            return False
        self.weekly_disables.append((int(row["id"]), float(remaining_percent), float(threshold)))
        return True

    @staticmethod
    def cost(row, input_tokens, output_tokens, model_id=None):
        return 0


class QuotaAccounts(RecordingAccounts):
    async def refresh_account(self, account_id, force=False):
        row = account(int(account_id))
        row["account_ref"] = "account-ref-3"
        return {
            "account": row,
            "credentials": {
                "access_token": "quota-token",
                "account_id": "account-ref-3",
            },
        }


class RefreshingQuotaAccounts(QuotaAccounts):
    def __init__(self):
        super().__init__()
        self.refresh_calls = []

    async def refresh_account(self, account_id, force=False):
        self.refresh_calls.append(bool(force))
        result = await super().refresh_account(account_id, force=force)
        result["credentials"]["refresh_token"] = "refresh-token"
        result["credentials"]["access_token"] = "fresh-token" if force else "expired-token"
        return result


class RecordingStore:
    def __init__(self):
        self.charges = []

    def charge(self, user_id, model, input_tokens, output_tokens, cost):
        self.charges.append((user_id, model, input_tokens, output_tokens, cost))
        return True, None


def configured_gateway(accounts, store, client, *, capacity_retries=2):
    gateway = SubscriptionGatewayService(accounts, store)
    gateway.max_attempts = 1
    gateway.capacity_retries = capacity_retries
    gateway.capacity_retry_base = 0
    gateway.stream_prefetch_bytes = 65536
    gateway.per_account_concurrency = 1
    gateway.per_account_rpm = 20
    gateway.queue_timeout = 0
    gateway.session_affinity_ttl = 60
    gateway.quota_cache_ttl = 300
    gateway.weekly_quota_disable_threshold = 2
    gateway.logger = logging.getLogger("test.subscription_gateway.capacity")
    gateway._client = lambda: client
    return gateway


def streaming_response(*chunks):
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        stream=AsyncChunks(*chunks),
    )


def test_subscription_quota_normalizes_windows_credits_and_uses_cache():
    usage = {
        "plan_type": "pro",
        "rate_limit": {
            "allowed": True,
            "limit_reached": False,
            "primary_window": {
                "used_percent": 70,
                "limit_window_seconds": 604800,
                "reset_after_seconds": 3600,
            },
            "secondary_window": {
                "used_percent": 25,
                "limit_window_seconds": 18000,
                "reset_after_seconds": 600,
            },
        },
        "rate_limit_reset_credits": {"available_count": 4},
    }
    details = {
        "available_count": 2,
        "credits": [
            {"id": "secret-credit-id", "reset_type": "codex_rate_limits", "status": "available", "expires_at": "2026-10-01T00:00:00Z"},
            {"id": "used-credit", "reset_type": "codex_rate_limits", "status": "redeemed", "expires_at": "2026-09-01T00:00:00Z"},
            {"id": "other-credit", "reset_type": "other", "status": "available", "expires_at": "2026-11-01T00:00:00Z"},
        ],
    }
    client = SequenceClient([httpx.Response(200, json=usage), httpx.Response(200, json=details)])
    gateway = configured_gateway(QuotaAccounts(), RecordingStore(), client)

    first = asyncio.run(gateway.query_account_quota(3))
    second = asyncio.run(gateway.query_account_quota(3))

    assert first["cached"] is False
    assert first["plan_type"] == "pro"
    assert first["short_window"]["label"] == "5 小时"
    assert first["short_window"]["remaining_percent"] == 75
    assert first["long_window"]["label"] == "每周"
    assert first["long_window"]["remaining_percent"] == 30
    assert first["reset_credits"] == {
        "available_count": 2,
        "credits": [{"expires_at": "2026-10-01T00:00:00Z"}],
    }
    assert first["account_disabled"] is False
    assert gateway.accounts.weekly_disables == []
    assert "secret-credit-id" not in json.dumps(first)
    assert second["cached"] is True
    assert client.calls == 2
    assert [request.url for request in client.requests] == [
        httpx.URL("https://chatgpt.com/backend-api/wham/usage"),
        httpx.URL("https://chatgpt.com/backend-api/wham/rate-limit-reset-credits"),
    ]
    assert client.requests[0].headers["authorization"] == "Bearer quota-token"
    assert client.requests[0].headers["chatgpt-account-id"] == "account-ref-3"
    assert client.requests[0].headers["originator"] == "Codex Desktop"


def test_subscription_quota_keeps_usage_when_credit_details_are_unavailable():
    usage = {
        "rate_limit": {
            "allowed": False,
            "limit_reached": True,
            "primary_window": {"used_percent": 100, "limit_window_seconds": 18000},
        },
        "rate_limit_reset_credits": {"available_count": 1},
    }
    client = SequenceClient([
        httpx.Response(200, json=usage),
        httpx.Response(401, json={"detail": "not available"}),
    ])
    gateway = configured_gateway(QuotaAccounts(), RecordingStore(), client)

    result = asyncio.run(gateway.query_account_quota(3))

    assert result["allowed"] is False
    assert result["limit_reached"] is True
    assert result["short_window"]["remaining_percent"] == 0
    assert result["reset_credits"]["available_count"] == 1
    assert result["warning"] == "重置次数详情暂不可用（HTTP 401）"
    assert result["account_disabled"] is False
    assert gateway.accounts.weekly_disables == []


def test_subscription_quota_refreshes_expired_oauth_token_once():
    usage = {
        "plan_type": "plus",
        "rate_limit": {
            "allowed": True,
            "limit_reached": False,
            "primary_window": {"used_percent": 8, "limit_window_seconds": 604800},
        },
    }
    accounts = RefreshingQuotaAccounts()
    client = SequenceClient([
        httpx.Response(401, json={"detail": "token expired"}),
        httpx.Response(200, json=usage),
        httpx.Response(200, json={"available_count": 0}),
    ])
    gateway = configured_gateway(accounts, RecordingStore(), client)

    result = asyncio.run(gateway.query_account_quota(3, force=True))

    assert result["long_window"]["remaining_percent"] == 92
    assert accounts.refresh_calls == [False, True]
    assert [request.headers["authorization"] for request in client.requests] == [
        "Bearer expired-token", "Bearer fresh-token", "Bearer fresh-token",
    ]


def test_subscription_quota_reset_consumes_one_credit_and_refreshes_snapshot():
    usage = {
        "plan_type": "pro",
        "rate_limit": {
            "allowed": True,
            "limit_reached": False,
            "primary_window": {"used_percent": 0, "limit_window_seconds": 18000},
            "secondary_window": {"used_percent": 0, "limit_window_seconds": 604800},
        },
    }
    client = SequenceClient([
        httpx.Response(200, json={"code": "reset", "windows_reset": 2}),
        httpx.Response(200, json=usage),
        httpx.Response(200, json={"available_count": 1, "credits": []}),
    ])
    gateway = configured_gateway(QuotaAccounts(), RecordingStore(), client)

    result = asyncio.run(gateway.reset_account_quota(3))

    assert result["ok"] is True
    assert result["windows_reset"] == 2
    assert result["quota"]["reset_credits"]["available_count"] == 1
    reset_request = client.requests[0]
    assert reset_request.method == "POST"
    assert reset_request.url == httpx.URL("https://chatgpt.com/backend-api/wham/rate-limit-reset-credits/consume")
    payload = json.loads(reset_request.content)
    assert str(uuid.UUID(payload["redeem_request_id"])) == payload["redeem_request_id"]
    assert reset_request.headers["authorization"] == "Bearer quota-token"
    assert [request.method for request in client.requests] == ["POST", "GET", "GET"]


def test_subscription_quota_reset_reuses_redeem_id_after_token_refresh():
    accounts = RefreshingQuotaAccounts()
    client = SequenceClient([
        httpx.Response(401, json={"detail": "token expired"}),
        httpx.Response(200, json={"code": "reset", "windows_reset": 1}),
        httpx.Response(200, json={"rate_limit": {"allowed": True}}),
        httpx.Response(200, json={"available_count": 0}),
    ])
    gateway = configured_gateway(accounts, RecordingStore(), client)

    result = asyncio.run(gateway.reset_account_quota(3))

    assert result["windows_reset"] == 1
    assert accounts.refresh_calls == [False, True, False]
    assert json.loads(client.requests[0].content)["redeem_request_id"] == json.loads(client.requests[1].content)["redeem_request_id"]
    assert [request.headers["authorization"] for request in client.requests[:2]] == ["Bearer expired-token", "Bearer fresh-token"]


def test_only_low_weekly_quota_disables_subscription_account():
    usage = {
        "rate_limit": {
            "allowed": True,
            "limit_reached": False,
            "primary_window": {"used_percent": 100, "limit_window_seconds": 18000},
            "secondary_window": {"used_percent": 98.5, "limit_window_seconds": 604800},
        },
    }
    client = SequenceClient([
        httpx.Response(200, json=usage),
        httpx.Response(404, json={"detail": "not available"}),
    ])
    accounts = QuotaAccounts()
    gateway = configured_gateway(accounts, RecordingStore(), client)

    result = asyncio.run(gateway.query_account_quota(3))

    assert result["short_window"]["remaining_percent"] == 0
    assert result["long_window"]["remaining_percent"] == 1.5
    assert result["account_disabled"] is True
    assert accounts.weekly_disables == [(3, 1.5, 2.0)]
    assert "等待管理员处理" in result["warning"]


def configured_chat_bridge(monkeypatch, client):
    """Exercise the real bridge and gateway without a database or live accounts."""
    accounts = RecordingAccounts()
    store = RecordingStore()
    gateway = configured_gateway(accounts, store, client)
    gateway.should_route = lambda provider, model: provider == "openai" and model == "gpt-6-astra"
    monkeypatch.setattr(subscription_adapter, "_beans", lambda request: (gateway, None))
    monkeypatch.setattr(subscription_adapter, "_chat_compatibility", lambda request: OpenAIChatCompatibilityService())
    return gateway, accounts, store


@pytest.mark.parametrize("limit_field", ["max_tokens", "max_completion_tokens", "max_output_tokens"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("tool_call", [False, True])
def test_trae_chat_bridge_sanitizes_actual_upstream_request(monkeypatch, limit_field, stream, tool_call):
    output = [{"type": "function_call", "call_id": "call_1", "name": "lookup", "arguments": '{"q":"x"}'}] if tool_call else [
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "hello"}]},
    ]
    completed = {
        "type": "response.completed",
        "response": {
            "id": "resp_trae", "model": "gpt-6-astra", "created_at": 123,
            "status": "completed", "output": output,
            "usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
        },
    }
    source = ("data: " + json.dumps(completed) + "\n\n").encode()
    source += b"data: [DONE]\n\n"
    client = SequenceClient([streaming_response(source[:31], source[31:])])
    _, accounts, store = configured_chat_bridge(monkeypatch, client)
    payload = {
        "model": "gpt-6-astra",
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}],
        "tool_choice": "auto", "parallel_tool_calls": True,
        "stream": stream, "stream_options": {"include_usage": True},
        limit_field: 256, "temperature": 1, "top_p": 1,
        "metadata": {"client": "trae"}, "truncation": "auto", "safety_identifier": "test-user",
        "reasoning_effort": "high", "prompt_cache_key": "test-session",
    }
    original = deepcopy(payload)

    async def scenario():
        response = await subscription_adapter.maybe_proxy_openai_chat_subscription(None, payload, {"id": 9})
        assert response.status_code == 200
        if stream:
            assert response.headers["content-type"].startswith("text/event-stream")
            raw = b"".join([chunk async for chunk in response.body_iterator]).decode()
            assert raw.endswith("data: [DONE]\n\n")
            frames = [json.loads(block[6:]) for block in raw.strip().split("\n\n") if block != "data: [DONE]"]
            assert frames[-1]["usage"]["total_tokens"] == 6
            assert frames[-2]["choices"][0]["finish_reason"] == ("tool_calls" if tool_call else "stop")
            deltas = [frame["choices"][0]["delta"] for frame in frames if frame["choices"]]
            if tool_call:
                calls = [call for delta in deltas for call in delta.get("tool_calls", [])]
                assert calls[0]["id"] == "call_1"
                assert calls[0]["function"]["name"] == "lookup"
                assert "".join(call["function"].get("arguments", "") for call in calls) == '{"q":"x"}'
            else:
                assert "".join(delta.get("content", "") for delta in deltas) == "hello"
        else:
            assert response.headers["content-type"].startswith("application/json")
            body = json.loads(response.body)
            assert body["object"] == "chat.completion"
            assert body["usage"]["total_tokens"] == 6
            assert body["choices"][0]["finish_reason"] == ("tool_calls" if tool_call else "stop")
            message = body["choices"][0]["message"]
            if tool_call:
                assert message["tool_calls"][0]["function"] == {"name": "lookup", "arguments": '{"q":"x"}'}
            else:
                assert message["content"] == "hello"

    asyncio.run(scenario())
    assert client.calls == 1
    forwarded = json.loads(client.requests[0].content)
    assert not {"max_tokens", "max_output_tokens", "max_completion_tokens", "temperature", "top_p",
                "metadata", "safety_identifier", "truncation", "stream_options"} & forwarded.keys()
    assert forwarded["model"] == "gpt-6-astra"
    assert forwarded["input"] == [{"role": "user", "content": "hello"}]
    assert forwarded["reasoning"] == {"effort": "high"}
    assert forwarded["prompt_cache_key"] == "test-session"
    assert forwarded["stream"] is True
    assert forwarded["tools"][0]["name"] == "lookup"
    assert forwarded["tool_choice"] == "auto"
    assert forwarded["parallel_tool_calls"] is True
    assert payload == original
    assert accounts.failures == []
    assert accounts.successes == [3]
    assert store.charges == [(9, "gpt-6-astra", 4, 2, 0)]


@pytest.mark.parametrize("model", ["gpt-5.6-sol", "gpt-6-astra"])
@pytest.mark.parametrize("chat", [False, True])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("explicit_effort", [None, "low", "none", "turbo"])
@pytest.mark.parametrize("configured_effort", ["high", "medium"])
def test_gpt_effective_effort_is_sent_upstream_and_displayed_in_activity(monkeypatch, model, chat, stream, explicit_effort, configured_effort):
    expected_effort = explicit_effort if explicit_effort in {"minimal", "low", "medium", "high", "xhigh", "max"} else configured_effort
    completed = {
        "id": "resp_high", "model": model, "status": "completed",
        "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "hello"}]}],
        "usage": {"input_tokens": 4, "output_tokens": 2},
    }
    source = ('data: ' + json.dumps({"type": "response.completed", "response": completed}) + '\n\n').encode()
    upstream = streaming_response(source[:17], source[17:]) if chat or stream else httpx.Response(200, json=completed)
    client = SequenceClient([upstream])
    gateway, accounts, store = configured_chat_bridge(monkeypatch, client)
    gateway.gpt_default_reasoning_effort = configured_effort
    gateway.should_route = lambda provider, requested: provider == "openai" and requested == model
    send = client.send

    async def inspect_active_request(request, stream=False):
        # Verify the real activity snapshot while forwarding, not a UI-only
        # fallback or a fabricated post-completion label.
        activity = gateway.metrics(include_users=True)["active_users"]
        assert len(activity) == 1
        assert activity[0]["model"] == model
        assert activity[0]["reasoning_effort"] == expected_effort
        assert activity[0]["user_id"] == 9
        return await send(request, stream=stream)

    monkeypatch.setattr(client, "send", inspect_active_request)
    payload = {
        "model": model, "stream": stream, "reasoning": {"summary": "auto"},
        "prompt_cache_key": "unchanged-session", "service_tier": "priority",
    }
    if chat:
        payload["messages"] = [{"role": "user", "content": "hello"}]
        if explicit_effort is not None:
            payload["reasoning_effort"] = explicit_effort
    else:
        payload["input"] = [{"role": "user", "content": "hello"}]
        if explicit_effort is not None:
            payload["reasoning"]["effort"] = explicit_effort
    original = deepcopy(payload)

    async def scenario():
        proxy = subscription_adapter.maybe_proxy_openai_chat_subscription if chat else subscription_adapter.maybe_proxy_openai_subscription
        response = await proxy(None, payload, {"id": 9})
        assert response.status_code == 200
        if stream:
            raw = b"".join([chunk async for chunk in response.body_iterator])
            if chat:
                assert raw.endswith(b"data: [DONE]\n\n")
                assert b'"content":"hello"' in raw
            else:
                assert raw == source  # native SSE remains byte-for-byte intact
        else:
            assert json.loads(response.body)["id"] == ("chatcmpl-high" if chat else "resp_high")
        assert gateway.metrics()["active_requests"] == 0
        assert not gateway._account_semaphore(3).locked()

    asyncio.run(scenario())
    forwarded = json.loads(client.requests[0].content)
    assert forwarded["reasoning"] == {"effort": expected_effort, "summary": "auto"}
    assert not {"reasoning_effort", "reasoning-effort"} & forwarded.keys()
    assert forwarded["model"] == model
    assert forwarded["prompt_cache_key"] == "unchanged-session"
    assert forwarded["service_tier"] == "priority"
    assert client.calls == 1
    assert payload == original
    assert accounts.failures == []
    assert accounts.successes == [3]
    assert store.charges == [(9, model, 4, 2, 0)]


def test_openai_subscription_routes_do_not_forward_downstream_client_headers(monkeypatch):
    source = b'data: {"type":"response.completed","response":{"usage":{"input_tokens":4,"output_tokens":2}}}\n\n'
    client = SequenceClient([streaming_response(source), streaming_response(source)])
    gateway, _, _ = configured_chat_bridge(monkeypatch, client)
    incoming = SimpleNamespace(headers={
        "User-Agent": "Trae/test-client", "Originator": "trae",
        "X-Trae-Request-Id": "client-only-id", "X-Client-Version": "client-only-version",
        "Authorization": "Bearer downstream-key", "Cookie": "client-only-cookie",
    })

    async def scenario():
        for chat in (True, False):
            payload = {"model": "gpt-6-astra", "stream": True}
            payload["messages" if chat else "input"] = [{"role": "user", "content": "hello"}]
            proxy = subscription_adapter.maybe_proxy_openai_chat_subscription if chat else subscription_adapter.maybe_proxy_openai_subscription
            response = await proxy(incoming, payload, {"id": 9})
            assert response.status_code == 200
            _ = [chunk async for chunk in response.body_iterator]

    asyncio.run(scenario())
    assert len(client.requests) == 2
    for request in client.requests:
        headers = request.headers
        assert headers["user-agent"] == f"codex-tui/{gateway.codex_client_version}"
        assert headers["originator"] == "codex-tui"
        assert headers["version"] == gateway.codex_client_version
        assert headers["authorization"] == "Bearer token-3"
        assert not {"x-trae-request-id", "x-client-version", "cookie"} & headers.keys()
        assert "trae" not in str(dict(headers)).lower()
        assert json.loads(request.content)["reasoning"]["effort"] == "high"


def test_invalid_trae_limit_fails_before_gateway_or_account_use(monkeypatch):
    client = SequenceClient([])
    gateway, accounts, store = configured_chat_bridge(monkeypatch, client)

    async def unexpected_proxy(*args, **kwargs):
        raise AssertionError("invalid request must not use the gateway")

    monkeypatch.setattr(gateway, "proxy_openai", unexpected_proxy)
    response = asyncio.run(subscription_adapter.maybe_proxy_openai_chat_subscription(None, {
        "model": "gpt-6-astra", "messages": [{"role": "user", "content": "hello"}], "max_tokens": True,
    }, {"id": 9}))
    assert response.status_code == 400
    assert "max_tokens must be a positive integer" in json.loads(response.body)["error"]["message"]
    assert client.calls == 0
    assert accounts.failures == []
    assert store.charges == []


def test_trae_stream_emits_text_before_upstream_completion_and_releases_account(monkeypatch):
    async def scenario():
        text_delivered = asyncio.Event()

        class GatedStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'data: {"type":"response.output_text.delta","delta":"hello"}\n\n'
                # A buffering regression cannot reach the terminal event.
                await asyncio.wait_for(text_delivered.wait(), timeout=1)
                yield b'data: {"type":"response.completed","response":{"usage":{"input_tokens":4,"output_tokens":2}}}\n\n'

        upstream = httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=GatedStream())
        client = SequenceClient([upstream])
        gateway, accounts, store = configured_chat_bridge(monkeypatch, client)
        response = await subscription_adapter.maybe_proxy_openai_chat_subscription(None, {
            "model": "gpt-6-astra", "messages": [{"role": "user", "content": "hello"}],
            "stream": True, "max_tokens": 256,
        }, {"id": 9})
        received = []
        async for chunk in response.body_iterator:
            received.append(chunk)
            if b'"content":"hello"' in chunk:
                text_delivered.set()

        assert text_delivered.is_set()
        assert b"".join(received).endswith(b"data: [DONE]\n\n")
        assert store.charges == [(9, "gpt-6-astra", 4, 2, 0)]
        assert accounts.successes == [3]
        assert accounts.failures == []
        assert not gateway._account_semaphore(3).locked()
        assert upstream.is_closed
        assert client.calls == 1

    asyncio.run(scenario())


def test_non_subscription_chat_bypasses_trae_compatibility(monkeypatch):
    configured_chat_bridge(monkeypatch, SequenceClient([]))

    def unexpected_bridge(request):
        raise AssertionError("ordinary API and Claude models must bypass the OpenAI bridge")

    monkeypatch.setattr(subscription_adapter, "_chat_compatibility", unexpected_bridge)
    for model in ("deepseek-v4-flash", "claude-sonnet-4-6"):
        payload = {"model": model, "messages": [{"role": "user", "content": "hello"}], "max_tokens": 256}
        original = deepcopy(payload)
        assert asyncio.run(subscription_adapter.maybe_proxy_openai_chat_subscription(None, payload, {"id": 9})) is None
        assert payload == original


def test_codex_native_route_uses_shared_sanitizer_and_preserves_sse(monkeypatch):
    chunks = [
        b'data: {"type":"response.output_text.delta","delta":"hello"}\n\n',
        b'data: {"type":"response.completed","response":{"usage":{"input_tokens":4,"output_tokens":2}}}\n\n',
    ]
    client = SequenceClient([streaming_response(*chunks)])
    configured_chat_bridge(monkeypatch, client)

    def unexpected_bridge(request):
        raise AssertionError("Codex Responses must not enter Chat compatibility")

    monkeypatch.setattr(subscription_adapter, "_chat_compatibility", unexpected_bridge)
    payload = {
        "model": "gpt-6-astra", "stream": True, "store": False, "instructions": "Be concise.",
        "input": [{"role": "user", "content": "hello"}],
        "reasoning": {"effort": "high", "summary": "auto"},
        "include": ["reasoning.encrypted_content"], "prompt_cache_key": "codex-session",
        "tools": [{"type": "function", "name": "inspect", "parameters": {"type": "object"}, "async": True}],
        # Public Responses fields rejected by the subscription transport are
        # filtered on native and Chat compatibility routes alike.
        "metadata": {"test": "untouched"}, "truncation": "disabled", "temperature": 1,
    }
    original = deepcopy(payload)

    async def scenario():
        response = await subscription_adapter.maybe_proxy_openai_subscription(None, payload, {"id": 9})
        return b"".join([chunk async for chunk in response.body_iterator])

    assert asyncio.run(scenario()) == b"".join(chunks)
    forwarded = json.loads(client.requests[0].content)
    assert not {"metadata", "truncation", "temperature"} & forwarded.keys()
    assert forwarded == {key: value for key, value in original.items() if key not in {"metadata", "truncation", "temperature"}}
    assert payload == original
    assert client.calls == 1


def test_native_responses_promotes_instructions_and_legacy_tools_without_mutating_input():
    gateway = SubscriptionGatewayService(None, None)
    payload = {
        "model": "gpt-6-astra",
        "input": [
            {"role": "system", "content": "System rule"},
            {"role": "developer", "content": [{"type": "input_text", "text": "Developer rule"}]},
            {"role": "user", "content": "hello"},
        ],
        "instructions": "Existing rule",
        "functions": [{"name": "lookup", "description": "Lookup", "parameters": {"type": "object"}}],
        "function_call": {"name": "lookup"},
        "reasoning_effort": "HIGH",
        "metadata": {"client": "native"},
    }
    original = deepcopy(payload)

    outgoing = gateway._prepare_openai_subscription_payload(payload)

    assert outgoing["instructions"] == "System rule\n\nDeveloper rule\n\nExisting rule"
    assert outgoing["input"] == [{"role": "user", "content": "hello"}]
    assert outgoing["tools"] == [{
        "type": "function", "name": "lookup", "description": "Lookup",
        "parameters": {"type": "object"},
    }]
    assert outgoing["tool_choice"] == {"type": "function", "name": "lookup"}
    assert outgoing["reasoning"]["effort"] == "high"
    assert "metadata" not in outgoing
    assert outgoing["store"] is False
    assert payload == original


def test_native_responses_normalizes_new_client_compatibility_fields_without_mutating_input():
    gateway = SubscriptionGatewayService(None, None)
    payload = {
        "model": "  gpt-5.6-sol  ",
        "prompt": "legacy prompt",
        "commands": ["unsupported"],
        "reasoning": {"mode": "pro"},
        "input": [{
            "role": "user",
            "content": "hello",
            "internal_chat_message_metadata_passthrough": {"private": True},
        }],
        "text": {"format": {
            "type": "json_schema",
            "schema": {
                "properties": {
                    "items": {"items": {"type": "string"}, "uniqueItems": True},
                },
                "minProperties": 1,
            },
        }},
        "tools": [{
            "type": "image_generation", "model": "gpt-image-2",
            "format": "png", "compression": 80, "input_fidelity": "high",
        }],
        "prompt_cache_options": {"retention": "24h"},
    }
    original = deepcopy(payload)

    outgoing = gateway._prepare_openai_subscription_payload(payload)

    assert outgoing["model"] == "gpt-5.6-sol"
    assert "prompt" not in outgoing and "commands" not in outgoing
    assert "internal_chat_message_metadata_passthrough" not in outgoing["input"][0]
    assert outgoing["reasoning"]["effort"] == "max"
    assert "mode" not in outgoing["reasoning"]
    schema = outgoing["text"]["format"]["schema"]
    assert schema["type"] == "object"
    assert "minProperties" not in schema
    assert schema["properties"]["items"]["type"] == "array"
    assert "uniqueItems" not in schema["properties"]["items"]
    assert outgoing["tools"] == [{
        "type": "image_generation", "model": "gpt-image-2",
        "output_format": "png", "output_compression": 80,
    }]
    assert "prompt_cache_options" not in outgoing
    assert payload == original


def test_native_responses_uses_legacy_prompt_only_when_input_is_missing():
    gateway = SubscriptionGatewayService(None, None)
    from_prompt = gateway._prepare_openai_subscription_payload({
        "model": "gpt-5.6-sol", "prompt": "legacy",
    })
    explicit_input = gateway._prepare_openai_subscription_payload({
        "model": "gpt-5.6-sol", "prompt": "legacy", "input": "explicit",
    })
    assert from_prompt["input"] == "legacy"
    assert explicit_input["input"] == "explicit"
    assert "prompt" not in from_prompt and "prompt" not in explicit_input


def test_native_responses_preserves_astra_reasoning_mode():
    gateway = SubscriptionGatewayService(None, None)
    outgoing = gateway._prepare_openai_subscription_payload({
        "model": "gpt-6-astra", "input": "hello", "reasoning": {"mode": "pro"},
    })
    assert outgoing["reasoning"] == {"mode": "pro", "effort": "high"}


@pytest.mark.parametrize("effort", [None, "none", False, 0, "invalid", "turbo", "ultra"])
def test_native_responses_replaces_non_reasoning_and_invalid_effort_with_default(effort):
    gateway = SubscriptionGatewayService(None, None)
    gateway.gpt_default_reasoning_effort = "high"
    outgoing = gateway._prepare_openai_subscription_payload({
        "model": "gpt-6-astra", "input": "hello", "reasoning_effort": effort,
    })
    assert outgoing["reasoning"]["effort"] == "high"


@pytest.mark.parametrize("policy, supplied, expected", [
    ("pass", "fast", "priority"),
    ("pass", "flex", "flex"),
    ("filter", "priority", None),
    ("force_priority", None, "priority"),
    ("force_priority", "flex", "priority"),
])
def test_openai_service_tier_policy(policy, supplied, expected):
    gateway = SubscriptionGatewayService(None, None)
    gateway.openai_service_tier_policy = policy
    payload = {"model": "gpt-6-astra", "input": "hello"}
    if supplied is not None:
        payload["service_tier"] = supplied
    outgoing = gateway._prepare_openai_subscription_payload(payload)
    assert outgoing.get("service_tier") == expected


def test_openai_service_tier_rejects_unknown_value():
    gateway = SubscriptionGatewayService(None, None)
    with pytest.raises(ValueError, match="service_tier"):
        gateway._prepare_openai_subscription_payload({
            "model": "gpt-6-astra", "input": "hello", "service_tier": "turbo",
        })


@pytest.mark.parametrize("tag, expected", [
    ("rust-v0.153.2", "0.153.2"),
    ("v1.2.3", "1.2.3"),
    ("0.200.1-alpha.4", "0.200.1-alpha.4"),
    ("latest", ""),
    ("0.1.2\r\nInjected: value", ""),
])
def test_codex_release_version_normalization(tag, expected):
    assert normalize_codex_client_version(tag) == expected


def test_codex_version_sync_updates_valid_release_and_keeps_fallback_on_error(monkeypatch):
    gateway = SubscriptionGatewayService(None, None)
    gateway.enabled = True
    gateway.codex_version_sync_enabled = True
    gateway.codex_release_url = "https://example.test/latest"
    gateway.connect_timeout = 5
    gateway.logger = logging.getLogger("test.codex.version.sync")

    class ReleaseResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"tag_name": "rust-v0.200.1"}

    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: ReleaseResponse())
    gateway.sync_subscription_client_versions()
    assert gateway.codex_client_version == "0.200.1"

    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: (_ for _ in ()).throw(httpx.ConnectError("offline")))
    gateway.sync_subscription_client_versions()
    assert gateway.codex_client_version == "0.200.1"


def test_claude_version_sync_uses_the_same_validated_release_path(monkeypatch):
    gateway = SubscriptionGatewayService(None, None)
    gateway.enabled = True
    gateway.claude_version_sync_enabled = True
    gateway.claude_release_url = "https://example.test/claude/latest"
    gateway.connect_timeout = 5
    gateway.logger = logging.getLogger("test.claude.version.sync")

    class ReleaseResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"tag_name": "v2.1.300"}

    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: ReleaseResponse())
    gateway.sync_subscription_client_versions()
    assert gateway.claude_client_version == "2.1.300"


def test_cancel_before_first_semantic_stream_event_closes_response_and_account_slot():
    async def scenario():
        waiting = asyncio.Event()

        class PendingStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'data: {"type":"response.created"}\n\n'
                waiting.set()
                await asyncio.Event().wait()

        upstream = httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=PendingStream())
        client = SequenceClient([upstream])
        accounts = RecordingAccounts()
        store = RecordingStore()
        gateway = configured_gateway(accounts, store, client)
        task = asyncio.create_task(gateway.proxy_openai({"model": "gpt-6-astra", "input": "hello", "stream": True}, 9))
        await asyncio.wait_for(waiting.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert upstream.is_closed
        assert not gateway._account_semaphore(3).locked()
        assert gateway.metrics()["active_requests"] == 0
        assert accounts.failures == []
        assert store.charges == []

    asyncio.run(scenario())


def test_codex_version_defaults_and_overrides(monkeypatch):
    monkeypatch.delenv("ROSE_CODEX_CLIENT_VERSION", raising=False)
    assert codex_client_version({}) == DEFAULT_CODEX_CLIENT_VERSION
    monkeypatch.setenv("ROSE_CODEX_CLIENT_VERSION", "0.153.3")
    assert codex_client_version({}) == "0.153.3"
    assert codex_client_version({"codex-client-version": "0.153.4"}) == "0.153.4"
    with pytest.raises(ValueError, match="版本号"):
        codex_client_version({"codex-client-version": "0.153.2\r\nInjected: value"})


def test_codex_model_query_and_headers_use_same_configured_version():
    class Accounts(RecordingAccounts):
        async def refresh_account(self, account_id, force=False):
            return {"account": account(account_id), "credentials": {"access_token": "test"}}

    class Client:
        async def get(self, url, headers):
            assert parse_qs(urlparse(url).query)["client_version"] == ["0.153.3"]
            assert headers["Version"] == "0.153.3"
            assert headers["User-Agent"] == "codex-tui/0.153.3"
            return httpx.Response(200, json={"models": [{"slug": "gpt-6-astra"}]})

    gateway = configured_gateway(Accounts(), RecordingStore(), Client())
    gateway.codex_client_version = "0.153.3"
    result = asyncio.run(gateway.test_account(3))
    assert result["model_count"] == 1


def test_codex_oauth_exchange_and_refresh_use_same_version(monkeypatch):
    monkeypatch.setenv("ROSE_CODEX_CLIENT_VERSION", "0.153.3")
    oauth = SubscriptionOAuthService()
    oauth.init()
    calls = []

    async def post_token(url, **kwargs):
        calls.append(kwargs["headers"])
        return {"access_token": "access", "refresh_token": "refresh"}

    oauth._post_token = post_token
    generated = oauth.generate_authorization("openai", 17)
    state = parse_qs(urlparse(generated["authorization_url"]).query)["state"][0]
    asyncio.run(oauth.exchange("openai", session_id=generated["session_id"],
                               callback_value="code", state=state, admin_id=17))
    asyncio.run(oauth.refresh("openai", "refresh"))
    assert len(calls) == 2
    assert all(headers["Version"] == "0.153.3" for headers in calls)
    assert all(headers["User-Agent"] == "codex-tui/0.153.3" for headers in calls)


def test_astra_request_and_split_stream_events_are_preserved():
    completed = b'data: {"type":"response.completed","response":{"usage":{"input_tokens":4,"output_tokens":2}}}\n\n'
    chunks = [
        b'data: {"type":"response.created"}\n\n',
        b'data: {"type":"response.output_text.delta","delta":"ok"}\n\n',
        completed[:23], completed[23:],
    ]
    client = SequenceClient([streaming_response(*chunks)])
    store = RecordingStore()
    gateway = configured_gateway(RecordingAccounts(), store, client)
    payload = {
        "model": "gpt-6-astra", "stream": True,
        "input": [{"role": "user", "content": "hello"}],
        "reasoning": {"effort": "high"},
        "tools": [{"type": "function", "name": "inspect", "parameters": {"type": "object"}, "async": True}],
    }

    async def scenario():
        response = await gateway.proxy_openai(payload, 9)
        return b"".join([chunk async for chunk in response.stream])

    assert asyncio.run(scenario()) == b"".join(chunks)
    forwarded = json.loads(client.requests[0].content)
    for key, value in payload.items():
        assert forwarded[key] == value
    assert client.requests[0].headers["version"] == DEFAULT_CODEX_CLIENT_VERSION
    assert store.charges == [(9, "gpt-6-astra", 4, 2, 0)]
    assert "store" not in payload  # Caller-owned request remains unchanged.


def test_astra_version_rejection_is_returned_without_retry_or_cooldown():
    detail = "The 'gpt-6-astra' model requires a newer version of Codex."
    client = SequenceClient([httpx.Response(400, json={"detail": detail})])
    accounts = RecordingAccounts()
    store = RecordingStore()
    gateway = configured_gateway(accounts, store, client)
    response = asyncio.run(gateway.proxy_openai({"model": "gpt-6-astra", "input": "hello"}, 9))
    assert response.status_code == 400
    assert json.loads(response.body) == {"detail": detail}
    assert response.headers["x-rose-error-source"] == "upstream_request"
    assert client.calls == 1
    assert accounts.failures == []
    assert store.charges == []


@pytest.mark.parametrize("field", ["max_tokens", "max_output_tokens", "max_completion_tokens"])
def test_responses_token_limit_is_normalized_for_subscription_transport(field):
    response = httpx.Response(200, json={
        "id": "resp_test",
        "status": "completed",
        "usage": {"input_tokens": 4, "output_tokens": 2},
    })
    client = SequenceClient([response])
    accounts = RecordingAccounts()
    gateway = configured_gateway(accounts, RecordingStore(), client)

    result = asyncio.run(gateway.proxy_openai({
        "model": "gpt-test",
        "input": "hello",
        field: 256,
    }, 9))

    forwarded = json.loads(client.requests[0].content)
    assert result.status_code == 200
    assert forwarded["max_tokens"] == 256
    assert "max_output_tokens" not in forwarded
    assert "max_completion_tokens" not in forwarded
    assert accounts.failures == []


def test_invalid_responses_max_tokens_is_rejected_before_using_an_account():
    client = SequenceClient([])
    accounts = RecordingAccounts()
    gateway = configured_gateway(accounts, RecordingStore(), client)

    result = asyncio.run(gateway.proxy_openai({
        "model": "gpt-test",
        "input": "hello",
        "max_tokens": "not-a-number",
    }, 9))

    assert result.status_code == 400
    assert client.calls == 0
    assert accounts.failures == []


def test_upstream_request_400_does_not_cool_or_retry_subscription_account():
    rejected = httpx.Response(400, json={
        "error": {"message": "Unknown parameter: max_tokens"},
    })
    client = SequenceClient([rejected])
    accounts = RecordingAccounts()
    gateway = configured_gateway(accounts, RecordingStore(), client)

    result = asyncio.run(gateway._proxy(
        "openai", "gpt-test", {"model": "gpt-test", "unexpected": True}, 9, {},
    ))

    assert result.status_code == 400
    assert result.headers["x-rose-error-source"] == "upstream_request"
    assert client.calls == 1
    assert accounts.failures == []
    assert accounts.successes == []


def test_upstream_429_is_reported_without_changing_subscription_account_state():
    rejected = httpx.Response(429, json={
        "error": {"message": "Rate limit reached for this subscription"},
    }, headers={"retry-after": "60"})
    client = SequenceClient([rejected])
    accounts = RecordingAccounts()
    gateway = configured_gateway(accounts, RecordingStore(), client, capacity_retries=0)

    result = asyncio.run(gateway._proxy(
        "openai", "gpt-test", {"model": "gpt-test", "input": "hello"}, 9, {},
    ))

    assert result.status_code == 429
    assert result.headers["x-rose-error-source"] == "upstream_rate_limit"
    assert client.calls == 1
    assert accounts.failures == []
    assert accounts.weekly_disables == []
    assert accounts.successes == []


def test_capacity_failure_before_output_is_retried_without_leaking_failed_stream():
    capacity = streaming_response(
        b'data: {"type":"response.created"}\n\n',
        b'data: {"type":"response.failed","error":{"message":"Selected model is at capacity. Please try a different model."}}\n\n',
    )
    success = streaming_response(
        b'data: {"type":"response.created"}\n\n',
        b'data: {"type":"response.output_text.delta","delta":"ok"}\n\n',
        b'data: {"type":"response.completed","response":{"usage":{"input_tokens":11,"output_tokens":2}}}\n\n',
    )
    client = SequenceClient([capacity, success])
    accounts = RecordingAccounts()
    store = RecordingStore()
    gateway = configured_gateway(accounts, store, client)

    async def scenario():
        result = await gateway._proxy(
            "openai", "gpt-test", {"model": "gpt-test", "stream": True}, 9, {},
        )
        body = b"".join([chunk async for chunk in result.stream])
        return result, body

    result, body = asyncio.run(scenario())

    assert result.status_code == 200
    assert client.calls == 2
    assert b"at capacity" not in body
    assert b'"delta":"ok"' in body
    assert accounts.failures == []
    assert accounts.successes == [3]
    assert store.charges == [(9, "gpt-test", 11, 2, 0)]


def test_capacity_failure_after_output_is_not_replayed_and_is_not_marked_success():
    late_failure = streaming_response(
        b'data: {"type":"response.created"}\n\n',
        b'data: {"type":"response.output_text.delta","delta":"partial"}\n\n',
        b'data: {"type":"response.failed","error":{"message":"Selected model is at capacity. Please try a different model."}}\n\n',
    )
    client = SequenceClient([late_failure])
    accounts = RecordingAccounts()
    store = RecordingStore()
    gateway = configured_gateway(accounts, store, client)

    async def scenario():
        result = await gateway._proxy(
            "openai", "gpt-test", {"model": "gpt-test", "stream": True}, 9, {},
        )
        return b"".join([chunk async for chunk in result.stream])

    body = asyncio.run(scenario())

    assert client.calls == 1
    assert b"partial" in body and b"at capacity" in body
    assert accounts.successes == []
    assert accounts.failures == [(3, 503, "Selected model is at capacity. Please try a different model.")]
    assert store.charges == []


def test_currently_overloaded_after_output_does_not_cool_account():
    late_failure = streaming_response(
        b'data: {"type":"response.created"}\n\n',
        b'data: {"type":"response.output_text.delta","delta":"partial"}\n\n',
        b'data: {"type":"response.failed","error":{"message":"Our servers are currently overloaded. Please try again later."}}\n\n',
    )
    client = SequenceClient([late_failure])
    accounts = RecordingAccounts()
    store = RecordingStore()
    gateway = configured_gateway(accounts, store, client)

    async def scenario():
        result = await gateway._proxy(
            "openai", "gpt-test", {"model": "gpt-test", "stream": True}, 9, {},
        )
        return b"".join([chunk async for chunk in result.stream])

    body = asyncio.run(scenario())

    assert b"currently overloaded" in body
    assert accounts.successes == []
    assert accounts.failures == []
    assert gateway._upstream_capacity_failures == 1
    assert store.charges == []


def test_currently_overloaded_never_updates_account_failure_state():
    repository = MemoryRepository([account(3)])
    service = SubscriptionAccountService(
        repository, JsonCipher(), NoopOAuth(), SubscriptionAccountPoolService(),
    )
    before = repository.find(3)

    service.record_failure(
        before,
        503,
        "Our servers are currently overloaded. Please try again later.",
    )

    assert repository.find(3) == before


def test_upstream_429_never_changes_account_state():
    repository = MemoryRepository([account(3)])
    service = SubscriptionAccountService(
        repository, JsonCipher(), NoopOAuth(), SubscriptionAccountPoolService(),
    )
    service.cooldown_enabled = False
    before = repository.find(3)

    service.record_failure(
        repository.find(3),
        429,
        "Our servers are currently overloaded. Please try again later.",
    )

    assert repository.find(3) == before
    assert service.has_route("openai", "gpt-test") is True


def test_confirmed_low_weekly_quota_permanently_disables_account():
    repository = MemoryRepository([account(3)])
    service = SubscriptionAccountService(
        repository, JsonCipher(), NoopOAuth(), SubscriptionAccountPoolService(),
    )

    assert service.disable_for_weekly_quota(repository.find(3), 2.0, 2.0) is False
    assert repository.find(3)["enabled"] == 1
    assert service.disable_for_weekly_quota(repository.find(3), 1.99, 2.0) is True

    saved = repository.find(3)
    assert saved["enabled"] == 0
    assert saved["status"] == "DISABLED"
    assert "每周订阅剩余量 1.99%" in saved["last_error"]
    assert service.has_route("openai", "gpt-test") is False


def test_client_request_error_never_updates_account_failure_state():
    repository = MemoryRepository([account(3)])
    service = SubscriptionAccountService(
        repository, JsonCipher(), NoopOAuth(), SubscriptionAccountPoolService(),
    )
    before = repository.find(3)

    service.record_failure(
        before,
        400,
        "Unknown parameter: max_tokens",
    )

    assert repository.find(3) == before


def test_disabled_cooldown_records_failure_without_removing_account_from_pool():
    cooling = account(3)
    cooling.update({
        "status": "COOLDOWN",
        "error_count": 2,
        "cooldown_until": "2099-01-01T00:00:00+00:00",
        "last_error": "old transport failure",
    })
    repository = MemoryRepository([cooling])
    service = SubscriptionAccountService(
        repository, JsonCipher(), NoopOAuth(), SubscriptionAccountPoolService(),
    )
    service.cooldown_enabled = False

    selected, _ = asyncio.run(service.acquire("openai", "gpt-test"))
    service.record_failure(selected, 502, "temporary proxy disconnect")

    saved = repository.find(3)
    assert saved["status"] == "READY"
    assert saved["error_count"] == 3
    assert saved["cooldown_until"] is None
    assert saved["last_error"] == "temporary proxy disconnect"
    assert service.find_public(3)["status"] == "READY"


def test_disabled_cooldown_still_invalidates_bad_credentials():
    repository = MemoryRepository([account(3)])
    service = SubscriptionAccountService(
        repository, JsonCipher(), NoopOAuth(), SubscriptionAccountPoolService(),
    )
    service.cooldown_enabled = False

    service.record_failure(repository.find(3), 401, "token expired")

    saved = repository.find(3)
    assert saved["status"] == "INVALID"
    assert saved["cooldown_until"] is None
