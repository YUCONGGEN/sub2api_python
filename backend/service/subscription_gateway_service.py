"""Managed upstream transport for Claude/OpenAI subscription-backed API calls."""

import asyncio
import hashlib
import json
import math
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx
from springbootai import Autowired, PostConstruct, PreDestroy, Service, Slf4j, get_config

from backend.service.store_service import StoreService
from backend.service.subscription_account_service import SubscriptionAccountService


OPENAI_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
OPENAI_MODELS_URL = "https://chatgpt.com/backend-api/codex/models?client_version=0.146.0"
CLAUDE_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_COUNT_TOKENS_URL = "https://api.anthropic.com/v1/messages/count_tokens"
CLAUDE_MODELS_URL = "https://api.anthropic.com/v1/models"

CAPACITY_ERROR_MARKERS = (
    "selected model is at capacity",
    "model is at capacity",
    "currently at capacity",
    "model capacity",
)
OPENAI_STREAM_CONTROL_EVENTS = {
    "response.created",
    "response.in_progress",
    "response.queued",
}


@dataclass
class SubscriptionGatewayResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes | None = None
    stream: AsyncIterator[bytes] | None = None


@Service("subscription_gateway_service")
@Slf4j
class SubscriptionGatewayService:
    @Autowired
    def __init__(self, accounts: SubscriptionAccountService, store: StoreService):
        self.accounts = accounts
        self.store = store
        self._clients: dict[Any, httpx.AsyncClient] = {}
        self._clients_lock = threading.Lock()
        self._safety_lock = threading.RLock()
        self._account_semaphores: dict[tuple[Any, int], asyncio.Semaphore] = {}
        self._rate_windows: dict[int, deque[float]] = {}
        self._session_affinity: dict[str, tuple[int, float]] = {}

    @PostConstruct
    def init(self) -> None:
        # SpringBootAI 2.3.7 may discover the same lifecycle method through
        # both the original class and its AOP proxy. Keep initialization and
        # its log line idempotent.
        if getattr(self, "_gateway_initialized", False):
            return
        self._gateway_initialized = True
        cfg = get_config().get("rose", {}).get("subscription-gateway", {})
        self.enabled = str(cfg.get("enabled", True)).strip().lower() in {"1", "true", "yes", "on"}
        self.timeout = max(5.0, min(900.0, float(cfg.get("request-timeout-seconds", 600) or 600)))
        self.connect_timeout = max(1.0, min(60.0, float(cfg.get("connect-timeout-seconds", 10) or 10)))
        self.trust_env = str(cfg.get("trust-env", False)).strip().lower() in {"1", "true", "yes", "on"}
        self.max_attempts = max(1, min(10, int(cfg.get("max-account-attempts", 3) or 3)))
        self.max_connections = max(1, min(1000, int(cfg.get("max-connections", 100) or 100)))
        self.max_keepalive = max(0, min(self.max_connections, int(cfg.get("max-keepalive-connections", 20) or 20)))
        self.capacity_retries = max(0, min(5, int(cfg.get("capacity-retries", 2) or 0)))
        self.capacity_retry_base = max(0.0, min(30.0, float(cfg.get("capacity-retry-base-seconds", 1) or 0)))
        self.stream_prefetch_bytes = max(4096, min(1_048_576, int(cfg.get("stream-prefetch-bytes", 65536) or 65536)))
        # Conservative, deterministic protections.  They reduce accidental
        # bursts and parallel account use; they do not attempt to mimic a
        # browser or evade an upstream provider's enforcement.
        self.per_account_concurrency = max(1, min(20, int(cfg.get("per-account-max-concurrency", 1) or 1)))
        self.per_account_rpm = max(1, min(600, int(cfg.get("per-account-requests-per-minute", 20) or 20)))
        raw_queue_timeout = float(cfg.get("account-queue-timeout-seconds", 180) or 0)
        # A Codex response can legitimately keep the only account slot busy for
        # more than the old 30 second limit.  Zero explicitly means to wait
        # until a slot is available; positive values keep a bounded queue.
        self.queue_timeout = 0.0 if raw_queue_timeout <= 0 else min(3600.0, max(1.0, raw_queue_timeout))
        self.session_affinity_ttl = max(60.0, min(86400.0, float(cfg.get("session-affinity-ttl-seconds", 3600) or 3600)))
        self.logger.info(
            "订阅网关保护已启用 per_account_concurrency=%s per_account_rpm=%s queue_timeout=%s capacity_retries=%s session_affinity_ttl=%ss",
            self.per_account_concurrency, self.per_account_rpm,
            "unlimited" if self.queue_timeout <= 0 else f"{int(self.queue_timeout)}s",
            self.capacity_retries,
            int(self.session_affinity_ttl),
        )

    def _account_semaphore(self, account_id: int) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        key = (loop, int(account_id))
        with self._safety_lock:
            semaphore = self._account_semaphores.get(key)
            if semaphore is None:
                semaphore = asyncio.Semaphore(self.per_account_concurrency)
                self._account_semaphores[key] = semaphore
            return semaphore

    def _reserve_rate_slot(self, account_id: int) -> bool:
        now = time.monotonic()
        cutoff = now - 60.0
        with self._safety_lock:
            window = self._rate_windows.setdefault(int(account_id), deque())
            while window and window[0] <= cutoff:
                window.popleft()
            if len(window) >= self.per_account_rpm:
                return False
            window.append(now)
            return True

    async def _acquire_account_slot(self, semaphore: asyncio.Semaphore) -> float:
        """Wait for an account slot and return the queue duration in milliseconds."""
        started_at = time.monotonic()
        if self.queue_timeout <= 0:
            await semaphore.acquire()
        else:
            await asyncio.wait_for(semaphore.acquire(), timeout=self.queue_timeout)
        return (time.monotonic() - started_at) * 1000.0

    @staticmethod
    def _session_key(provider: str, user_id: int, payload: dict[str, Any]) -> str:
        """Hash an explicit client session hint without inspecting prompt text."""
        candidate: Any = ""
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        if provider == "openai":
            candidate = payload.get("prompt_cache_key") or metadata.get("session_id") or metadata.get("conversation_id")
            conversation = payload.get("conversation")
            if not candidate and isinstance(conversation, str):
                candidate = conversation
            elif not candidate and isinstance(conversation, dict):
                candidate = conversation.get("id")
        else:
            candidate = metadata.get("user_id") or metadata.get("session_id")
        value = str(candidate or "").strip()
        if not value:
            return ""
        return hashlib.sha256(f"{provider}:{int(user_id)}:{value}".encode("utf-8")).hexdigest()

    def _preferred_account(self, session_key: str) -> int | None:
        if not session_key:
            return None
        now = time.monotonic()
        with self._safety_lock:
            current = self._session_affinity.get(session_key)
            if not current:
                return None
            account_id, expires_at = current
            if expires_at <= now:
                self._session_affinity.pop(session_key, None)
                return None
            return int(account_id)

    def _remember_account(self, session_key: str, account_id: int) -> None:
        if not session_key:
            return
        now = time.monotonic()
        with self._safety_lock:
            if len(self._session_affinity) >= 10000:
                self._session_affinity = {
                    key: value for key, value in self._session_affinity.items() if value[1] > now
                }
            self._session_affinity[session_key] = (int(account_id), now + self.session_affinity_ttl)

    def _forget_account(self, session_key: str, account_id: int) -> None:
        if not session_key:
            return
        with self._safety_lock:
            current = self._session_affinity.get(session_key)
            if current and int(current[0]) == int(account_id):
                self._session_affinity.pop(session_key, None)

    def _client(self) -> httpx.AsyncClient:
        loop = asyncio.get_running_loop()
        client = self._clients.get(loop)
        if client and not client.is_closed:
            return client
        with self._clients_lock:
            client = self._clients.get(loop)
            if not client or client.is_closed:
                client = httpx.AsyncClient(
                    timeout=httpx.Timeout(self.timeout, connect=self.connect_timeout),
                    limits=httpx.Limits(max_connections=self.max_connections, max_keepalive_connections=self.max_keepalive),
                    trust_env=self.trust_env,
                    follow_redirects=False,
                )
                self._clients[loop] = client
        return client

    @PreDestroy
    def close(self) -> None:
        clients = list(self._clients.values())
        self._clients.clear()
        if not clients:
            return

        async def close_all():
            await asyncio.gather(*(client.aclose() for client in clients), return_exceptions=True)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(close_all())
        else:
            loop.create_task(close_all())

    def should_route(self, provider: str, model: str) -> bool:
        return bool(self.enabled and model and self.accounts.has_route(provider, model))

    def catalog(self) -> list[dict[str, Any]]:
        return self.accounts.catalog() if self.enabled else []

    @staticmethod
    def _openai_headers(credentials: dict[str, Any], account: dict[str, Any], stream: bool) -> dict[str, str]:
        headers = {
            "Authorization": "Bearer " + str(credentials.get("access_token") or ""),
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
            "OpenAI-Beta": "responses=experimental",
            "Originator": "codex-tui",
            "User-Agent": "codex-tui/0.146.0 (Ubuntu 22.4.0; x86_64) xterm-256color",
            "Version": "0.146.0",
        }
        account_ref = str(credentials.get("account_id") or credentials.get("chatgpt_account_id") or account.get("account_ref") or "").strip()
        if account_ref:
            headers["ChatGPT-Account-Id"] = account_ref
        return headers

    @staticmethod
    def _claude_headers(credentials: dict[str, Any], incoming: dict[str, str], stream: bool) -> dict[str, str]:
        required_betas = [
            "claude-code-20250219",
            "oauth-2025-04-20",
            "interleaved-thinking-2025-05-14",
            "fine-grained-tool-streaming-2025-05-14",
        ]
        incoming_beta = str(incoming.get("anthropic-beta") or "")
        betas: list[str] = []
        for item in required_betas + incoming_beta.split(","):
            item = item.strip()
            if item and item not in betas:
                betas.append(item)
        return {
            "Authorization": "Bearer " + str(credentials.get("access_token") or ""),
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
            "Anthropic-Version": str(incoming.get("anthropic-version") or "2023-06-01"),
            "Anthropic-Beta": ",".join(betas),
            "User-Agent": "claude-cli/2.1.220 (external, cli)",
            "X-App": "cli",
            "X-Stainless-Lang": "js",
            "X-Stainless-Package-Version": "0.94.0",
            "X-Stainless-Runtime": "node",
            "X-Stainless-Runtime-Version": "v24.3.0",
            "X-Stainless-Retry-Count": "0",
            "X-Stainless-Timeout": "600",
            "Anthropic-Dangerous-Direct-Browser-Access": "true",
        }

    @staticmethod
    def _safe_response_headers(response: httpx.Response) -> dict[str, str]:
        allowed = {
            "content-type", "cache-control", "retry-after", "x-request-id", "request-id", "openai-request-id",
            "anthropic-ratelimit-requests-limit", "anthropic-ratelimit-requests-remaining",
            "anthropic-ratelimit-tokens-limit", "anthropic-ratelimit-tokens-remaining",
            "x-ratelimit-limit-requests", "x-ratelimit-remaining-requests",
            "x-ratelimit-reset-requests", "x-ratelimit-limit-tokens", "x-ratelimit-remaining-tokens",
            "x-ratelimit-reset-tokens",
        }
        return {key: value for key, value in response.headers.items() if key.lower() in allowed}

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        try:
            return max(0.0, float(response.headers.get("retry-after", "")))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _error_detail(response: httpx.Response, body: bytes) -> str:
        try:
            payload = json.loads(body.decode("utf-8"))
            error = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(error, dict):
                return str(error.get("message") or error.get("type") or error)[:1000]
            return str(error or payload)[:1000]
        except (ValueError, UnicodeError, TypeError):
            return body.decode("utf-8", errors="replace")[:1000]

    async def proxy_openai(self, payload: dict[str, Any], user_id: int) -> SubscriptionGatewayResponse:
        model = str(payload.get("model") or "").strip()
        if not model:
            return self._json_error(400, "model is required", "invalid_request_error")
        outgoing = dict(payload)
        outgoing["store"] = False
        if not str(outgoing.get("instructions") or "").strip() and not self._has_instruction_input(outgoing.get("input")):
            outgoing["instructions"] = "You are a helpful coding assistant. Follow the user's instructions carefully."
        return await self._proxy("openai", model, outgoing, user_id, {})

    @staticmethod
    def _has_instruction_input(value: Any) -> bool:
        if not isinstance(value, list):
            return False
        return any(
            isinstance(item, dict)
            and str(item.get("role") or "").strip().lower() in {"system", "developer"}
            and bool(item.get("content"))
            for item in value
        )

    async def proxy_claude(self, payload: dict[str, Any], user_id: int, incoming_headers: dict[str, str], *, count_tokens: bool = False) -> SubscriptionGatewayResponse:
        model = str(payload.get("model") or "").strip()
        if not model:
            return self._json_error(400, "model is required", "invalid_request_error")
        return await self._proxy("claude", model, dict(payload), user_id, incoming_headers, count_tokens=count_tokens)

    async def _proxy(self, provider: str, model: str, payload: dict[str, Any], user_id: int, incoming_headers: dict[str, str], *, count_tokens: bool = False) -> SubscriptionGatewayResponse:
        stream_requested = bool(payload.get("stream")) and not count_tokens
        session_key = self._session_key(provider, user_id, payload)
        preferred_account_id = self._preferred_account(session_key)
        excluded: set[int] = set()
        last_status = 502
        last_detail = "上游订阅账号不可用"
        last_error_type = "upstream_error"
        last_headers: dict[str, str] = {}
        capacity_retries = max(0, int(getattr(self, "capacity_retries", 0) or 0))
        capacity_retries_left = capacity_retries
        total_attempts = self.max_attempts + capacity_retries
        for _ in range(total_attempts):
            try:
                account, credentials = await self.accounts.acquire(
                    provider, model, excluded, preferred_account_id=preferred_account_id,
                )
            except (LookupError, ValueError) as exc:
                # Preserve a concrete queue/rate-limit error after all selected
                # accounts have been tried instead of replacing it with the
                # account pool's generic "not available" message.
                if not excluded:
                    last_detail = str(exc)
                break
            account_id = int(account["id"])
            excluded.add(account_id)
            semaphore = self._account_semaphore(account_id)
            try:
                queue_wait_ms = await self._acquire_account_slot(semaphore)
            except TimeoutError:
                last_status = 503
                last_detail = "本地订阅账号队列等待超时，请稍后重试"
                last_error_type = "local_queue_timeout"
                last_headers = {
                    "content-type": "application/json; charset=utf-8",
                    "retry-after": str(max(1, min(60, math.ceil(self.queue_timeout / 6)))),
                    "x-rose-error-source": "local_queue",
                }
                self.logger.warning(
                    "订阅网关并发排队超时 provider=%s model=%s account_id=%s queue_timeout=%ss",
                    provider, model, account_id, int(self.queue_timeout),
                )
                continue
            release_in_stream = False
            try:
                if queue_wait_ms >= 500:
                    self.logger.info(
                        "订阅网关排队完成 provider=%s model=%s account_id=%s wait_ms=%.1f",
                        provider, model, account_id, queue_wait_ms,
                    )
                # Only completed queue admissions count toward the local RPM
                # budget.  The old order charged timed-out queue entries too.
                if not self._reserve_rate_slot(account_id):
                    last_status = 429
                    last_detail = "本地单账号请求频率已达到安全上限"
                    last_error_type = "local_rate_limit"
                    last_headers = {
                        "content-type": "application/json; charset=utf-8",
                        "retry-after": "60",
                        "x-rose-error-source": "local_rate_limit",
                    }
                    self.logger.warning(
                        "订阅网关本地限流 provider=%s model=%s account_id=%s rpm_limit=%s",
                        provider, model, account_id, self.per_account_rpm,
                    )
                    continue
                last_error_type = "upstream_error"
                last_headers = {}
                if provider == "openai":
                    url = OPENAI_RESPONSES_URL
                    headers = self._openai_headers(credentials, account, stream_requested)
                else:
                    url = CLAUDE_COUNT_TOKENS_URL if count_tokens else CLAUDE_MESSAGES_URL
                    headers = self._claude_headers(credentials, incoming_headers, stream_requested)
                request = self._client().build_request("POST", url, headers=headers, json=payload)
                try:
                    response = await self._client().send(request, stream=stream_requested)
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    last_detail = f"连接上游失败：{exc}"
                    last_error_type = "upstream_error"
                    last_headers = {}
                    self.accounts.record_failure(account, 502, last_detail)
                    self._forget_account(session_key, account_id)
                    self.logger.warning(
                        "订阅网关上游连接失败 provider=%s model=%s account_id=%s error=%s",
                        provider, model, account_id, type(exc).__name__,
                    )
                    continue
                if response.status_code < 200 or response.status_code >= 300:
                    body = await response.aread()
                    last_status = int(response.status_code)
                    last_detail = self._error_detail(response, body)
                    retry_after = self._retry_after(response)
                    response_headers = self._safe_response_headers(response)
                    await response.aclose()
                    if self._is_capacity_error(last_detail):
                        last_status = 503
                        last_error_type = "upstream_capacity"
                        last_headers = self._capacity_headers(retry_after)
                        self._forget_account(session_key, account_id)
                        self.logger.warning(
                            "订阅网关上游模型容量不足 provider=%s model=%s account_id=%s retries_left=%s",
                            provider, model, account_id, capacity_retries_left,
                        )
                        if capacity_retries_left > 0:
                            retry_index = capacity_retries - capacity_retries_left
                            capacity_retries_left -= 1
                            excluded.discard(account_id)
                            preferred_account_id = account_id
                            await asyncio.sleep(self._capacity_retry_delay(retry_index, retry_after))
                        continue
                    last_error_type = "upstream_error"
                    last_headers = response_headers
                    self.accounts.record_failure(account, last_status, last_detail, retry_after)
                    self._forget_account(session_key, account_id)
                    self.logger.warning(
                        "订阅网关上游拒绝 provider=%s model=%s account_id=%s status=%s",
                        provider, model, account_id, last_status,
                    )
                    if last_status in {401, 403, 408, 409, 429} or last_status >= 500:
                        continue
                    return SubscriptionGatewayResponse(last_status, response_headers, body=body)
                self._remember_account(session_key, account_id)
                if stream_requested:
                    stream_iterator = response.aiter_bytes()
                    stream_prefix = b""
                    if provider == "openai":
                        stream_prefix, stream_failure = await self._prefetch_openai_stream(stream_iterator)
                        if stream_failure and self._is_capacity_error(stream_failure):
                            last_status = 503
                            last_detail = stream_failure
                            last_error_type = "upstream_capacity"
                            last_headers = self._capacity_headers(None)
                            await response.aclose()
                            self._forget_account(session_key, account_id)
                            self.logger.warning(
                                "订阅网关流式模型容量不足 provider=%s model=%s account_id=%s retries_left=%s",
                                provider, model, account_id, capacity_retries_left,
                            )
                            if capacity_retries_left > 0:
                                retry_index = capacity_retries - capacity_retries_left
                                capacity_retries_left -= 1
                                excluded.discard(account_id)
                                preferred_account_id = account_id
                                await asyncio.sleep(self._capacity_retry_delay(retry_index, None))
                            continue
                    release_in_stream = True
                    self.logger.info(
                        "订阅网关流式请求已连接 provider=%s model=%s account_id=%s user_id=%s",
                        provider, model, account_id, user_id,
                    )
                    return SubscriptionGatewayResponse(
                        int(response.status_code), self._safe_response_headers(response),
                        stream=self._stream_and_bill(
                            response, provider, account, user_id, model, semaphore,
                            iterator=stream_iterator, prefix=stream_prefix,
                        ),
                    )
                body = await response.aread()
                await response.aclose()
                response_failure = self._failure_from_json_bytes(body)
                if response_failure:
                    if self._is_capacity_error(response_failure):
                        last_status = 503
                        last_detail = response_failure
                        last_error_type = "upstream_capacity"
                        last_headers = self._capacity_headers(None)
                        self._forget_account(session_key, account_id)
                        if capacity_retries_left > 0:
                            retry_index = capacity_retries - capacity_retries_left
                            capacity_retries_left -= 1
                            excluded.discard(account_id)
                            preferred_account_id = account_id
                            await asyncio.sleep(self._capacity_retry_delay(retry_index, None))
                        continue
                    self.accounts.record_failure(account, 502, response_failure)
                    self._forget_account(session_key, account_id)
                    return self._json_error(502, response_failure, "upstream_error")
                usage = self._usage_from_json(provider, body)
                billing_error = await self._bill(user_id, model, account, usage)
                if billing_error:
                    return billing_error
                self.accounts.record_success(account)
                self.logger.info(
                    "订阅网关请求完成 provider=%s model=%s account_id=%s user_id=%s status=%s input_tokens=%s output_tokens=%s",
                    provider, model, account_id, user_id, response.status_code,
                    usage["input_tokens"], usage["output_tokens"],
                )
                return SubscriptionGatewayResponse(int(response.status_code), self._safe_response_headers(response), body=body)
            finally:
                if not release_in_stream:
                    semaphore.release()
        status = last_status if 400 <= last_status < 500 else 502
        if last_error_type in {"local_queue_timeout", "upstream_capacity"}:
            status = 503
        return self._json_error(status, last_detail, last_error_type, headers=last_headers)

    async def _stream_and_bill(
        self,
        response: httpx.Response,
        provider: str,
        account: dict[str, Any],
        user_id: int,
        model: str,
        semaphore: asyncio.Semaphore,
        *,
        iterator: AsyncIterator[bytes] | None = None,
        prefix: bytes = b"",
    ) -> AsyncIterator[bytes]:
        line_buffer = b""
        usage = {"input_tokens": 0, "output_tokens": 0}
        terminal_failure = ""

        def observe(chunk: bytes) -> None:
            nonlocal line_buffer, terminal_failure
            line_buffer += chunk
            lines = line_buffer.split(b"\n")
            line_buffer = lines.pop()
            for line in lines:
                self._update_usage_from_sse(provider, line, usage)
                terminal_failure = self._failure_from_sse_line(line) or terminal_failure

        try:
            if prefix:
                observe(prefix)
                yield prefix
            stream_iterator = iterator or response.aiter_bytes()
            async for chunk in stream_iterator:
                if not chunk:
                    continue
                observe(chunk)
                yield chunk
            if line_buffer:
                self._update_usage_from_sse(provider, line_buffer, usage)
                terminal_failure = self._failure_from_sse_line(line_buffer) or terminal_failure
            if terminal_failure:
                failure_status = 503 if self._is_capacity_error(terminal_failure) else 502
                self.accounts.record_failure(account, failure_status, terminal_failure)
                self.logger.warning(
                    "订阅网关流式请求失败 provider=%s model=%s account_id=%s user_id=%s detail=%s",
                    provider, model, int(account["id"]), user_id, terminal_failure[:300],
                )
                return
            billing_error = await self._bill(user_id, model, account, usage)
            if billing_error:
                self.logger.warning(
                    "订阅网关流式请求完成但计费失败 provider=%s model=%s account_id=%s user_id=%s",
                    provider, model, int(account["id"]), user_id,
                )
            self.accounts.record_success(account)
            self.logger.info(
                "订阅网关流式请求完成 provider=%s model=%s account_id=%s user_id=%s input_tokens=%s output_tokens=%s",
                provider, model, int(account["id"]), user_id,
                usage["input_tokens"], usage["output_tokens"],
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.accounts.record_failure(account, 502, str(exc))
            raise
        finally:
            await response.aclose()
            semaphore.release()

    async def _prefetch_openai_stream(
        self,
        iterator: AsyncIterator[bytes],
    ) -> tuple[bytes, str]:
        """Hold only control events so an early capacity failure can be retried safely."""
        chunks: list[bytes] = []
        buffered_size = 0
        line_buffer = b""
        async for chunk in iterator:
            if not chunk:
                continue
            chunks.append(chunk)
            buffered_size += len(chunk)
            line_buffer += chunk
            lines = line_buffer.split(b"\n")
            line_buffer = lines.pop()
            for line in lines:
                payload = self._sse_payload(line)
                if not payload:
                    continue
                failure = self._failure_from_payload(payload)
                if failure:
                    return b"".join(chunks), failure
                event_type = str(payload.get("type") or "").strip().lower()
                if event_type and event_type not in OPENAI_STREAM_CONTROL_EVENTS:
                    return b"".join(chunks), ""
            if buffered_size >= int(getattr(self, "stream_prefetch_bytes", 65536) or 65536):
                return b"".join(chunks), ""
        if line_buffer:
            failure = self._failure_from_sse_line(line_buffer)
            if failure:
                return b"".join(chunks), failure
        return b"".join(chunks), ""

    @staticmethod
    def _sse_payload(line: bytes) -> dict[str, Any] | None:
        stripped = line.strip()
        if not stripped.startswith(b"data:"):
            return None
        raw = stripped[5:].strip()
        if not raw or raw == b"[DONE]":
            return None
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError):
            return None
        return payload if isinstance(payload, dict) else None

    @classmethod
    def _failure_from_payload(cls, payload: dict[str, Any]) -> str:
        event_type = str(payload.get("type") or "").strip().lower()
        response = payload.get("response") if isinstance(payload.get("response"), dict) else {}
        response_status = str(response.get("status") or "").strip().lower()
        if event_type not in {"response.failed", "error"} and response_status != "failed":
            return ""
        error = payload.get("error")
        if not isinstance(error, dict):
            error = response.get("error") if isinstance(response.get("error"), dict) else {}
        detail = error.get("message") or error.get("type") or payload.get("message")
        return str(detail or "上游流式响应失败")[:1000]

    @classmethod
    def _failure_from_sse_line(cls, line: bytes) -> str:
        payload = cls._sse_payload(line)
        return cls._failure_from_payload(payload) if payload else ""

    @classmethod
    def _failure_from_json_bytes(cls, body: bytes) -> str:
        try:
            payload = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeError):
            return ""
        return cls._failure_from_payload(payload) if isinstance(payload, dict) else ""

    @staticmethod
    def _is_capacity_error(detail: str) -> bool:
        normalized = str(detail or "").strip().lower()
        return any(marker in normalized for marker in CAPACITY_ERROR_MARKERS)

    def _capacity_retry_delay(self, retry_index: int, retry_after: float | None) -> float:
        if retry_after is not None and retry_after > 0:
            return min(60.0, retry_after)
        base = max(0.0, float(getattr(self, "capacity_retry_base", 1) or 0))
        return min(30.0, base * (2 ** max(0, int(retry_index))))

    def _capacity_headers(self, retry_after: float | None) -> dict[str, str]:
        base = max(0.0, float(getattr(self, "capacity_retry_base", 1) or 0))
        delay = retry_after if retry_after is not None and retry_after > 0 else max(1.0, base)
        return {
            "content-type": "application/json; charset=utf-8",
            "retry-after": str(max(1, min(60, math.ceil(delay)))),
            "x-rose-error-source": "upstream_capacity",
        }

    @staticmethod
    def _update_usage_from_sse(provider: str, line: bytes, usage: dict[str, int]) -> None:
        payload = SubscriptionGatewayService._sse_payload(line)
        if not payload:
            return
        if provider == "openai":
            source = payload.get("response") if isinstance(payload, dict) and isinstance(payload.get("response"), dict) else payload
            raw_usage = source.get("usage") if isinstance(source, dict) else None
            if isinstance(raw_usage, dict):
                usage["input_tokens"] = max(usage["input_tokens"], int(raw_usage.get("input_tokens") or 0))
                usage["output_tokens"] = max(usage["output_tokens"], int(raw_usage.get("output_tokens") or 0))
        elif isinstance(payload, dict):
            raw_usage = payload.get("usage")
            if not isinstance(raw_usage, dict) and isinstance(payload.get("message"), dict):
                raw_usage = payload["message"].get("usage")
            if isinstance(raw_usage, dict):
                usage["input_tokens"] = max(usage["input_tokens"], int(raw_usage.get("input_tokens") or 0))
                usage["output_tokens"] = max(usage["output_tokens"], int(raw_usage.get("output_tokens") or 0))

    @staticmethod
    def _usage_from_json(provider: str, body: bytes) -> dict[str, int]:
        try:
            payload = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeError):
            return {"input_tokens": 0, "output_tokens": 0}
        raw_usage = payload.get("usage") if isinstance(payload, dict) else {}
        if not isinstance(raw_usage, dict):
            raw_usage = {}
        return {
            "input_tokens": int(raw_usage.get("input_tokens") or raw_usage.get("prompt_tokens") or 0),
            "output_tokens": int(raw_usage.get("output_tokens") or raw_usage.get("completion_tokens") or 0),
        }

    async def _bill(self, user_id: int, model: str, account: dict[str, Any], usage: dict[str, int]) -> SubscriptionGatewayResponse | None:
        input_tokens = max(0, int(usage.get("input_tokens") or 0))
        output_tokens = max(0, int(usage.get("output_tokens") or 0))
        cost = self.accounts.cost(account, input_tokens, output_tokens)
        ok, _ = await asyncio.to_thread(self.store.charge, int(user_id), model, input_tokens, output_tokens, cost)
        if not ok:
            return self._json_error(402, "Insufficient balance", "insufficient_quota")
        return None

    async def test_account(self, account_id: int) -> dict[str, Any]:
        fresh = await self.accounts.refresh_account(account_id, force=False)
        account = fresh["account"]
        credentials = fresh["credentials"]
        provider = str(account["provider"])
        if provider == "openai":
            url = OPENAI_MODELS_URL
            headers = self._openai_headers(credentials, account, False)
        else:
            url = CLAUDE_MODELS_URL
            headers = self._claude_headers(credentials, {}, False)
        try:
            response = await self._client().get(url, headers=headers)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            self.accounts.record_failure(account, 502, str(exc))
            raise ValueError(f"连接测试失败：{exc}") from exc
        if response.status_code < 200 or response.status_code >= 300:
            detail = self._error_detail(response, response.content)
            self.accounts.record_failure(account, response.status_code, detail, self._retry_after(response))
            raise ValueError(f"上游返回 HTTP {response.status_code}：{detail}")
        self.accounts.record_success(account)
        model_count = 0
        try:
            data = response.json()
            models = data.get("models") if isinstance(data, dict) else None
            if not isinstance(models, list) and isinstance(data, dict):
                models = data.get("data")
            model_count = len(models) if isinstance(models, list) else 0
        except ValueError:
            pass
        return {"ok": True, "status_code": response.status_code, "model_count": model_count, "message": "订阅账号连接正常"}

    @staticmethod
    def _json_error(
        status: int,
        message: str,
        error_type: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> SubscriptionGatewayResponse:
        body = json.dumps({"error": {"message": str(message), "type": str(error_type)}}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        response_headers = {"content-type": "application/json; charset=utf-8"}
        response_headers.update(headers or {})
        return SubscriptionGatewayResponse(int(status), response_headers, body=body)


__all__ = ["SubscriptionGatewayService", "SubscriptionGatewayResponse"]
