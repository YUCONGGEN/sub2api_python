import asyncio
import base64
import hashlib
import json
import logging
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet

from backend.service.credential_cipher_service import CredentialCipherService
from backend.service.subscription_account_pool_service import SubscriptionAccountPoolService
from backend.service.subscription_account_service import SubscriptionAccountService
from backend.service.subscription_gateway_service import SubscriptionGatewayService
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
    with pytest.raises(ValueError, match="管理员"):
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

    async def acquire(self, provider, model, excluded, preferred_account_id=None):
        return account(3), {"access_token": "token-3"}

    def record_success(self, row):
        self.successes.append(int(row["id"]))

    def record_failure(self, row, status, detail, retry_after=None):
        self.failures.append((int(row["id"]), int(status), str(detail)))

    @staticmethod
    def cost(row, input_tokens, output_tokens):
        return 0


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
    gateway.logger = logging.getLogger("test.subscription_gateway.capacity")
    gateway._client = lambda: client
    return gateway


def streaming_response(*chunks):
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        stream=AsyncChunks(*chunks),
    )


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
