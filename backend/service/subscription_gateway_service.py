"""Managed upstream transport for subscription and coding-plan API calls."""

import asyncio
import copy
import hashlib
import json
import math
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import httpx
from springbootai import Autowired, PostConstruct, PreDestroy, Scheduled, Service, Slf4j, get_config

from backend.common.codex_client import (
    DEFAULT_CODEX_CLIENT_VERSION,
    DEFAULT_CODEX_RELEASE_URL,
    codex_client_version,
    codex_client_version_is_pinned,
    codex_identity_headers,
    normalize_codex_client_version,
)
from backend.common.reasoning import (
    DEFAULT_GPT_REASONING_EFFORT,
    configured_gpt_reasoning_effort,
    with_responses_reasoning,
)
from backend.common.subscription_providers import (
    CHAT_PROVIDERS,
    RESPONSES_PROVIDERS,
    SUBSCRIPTION_PROVIDERS,
    provider_base_url,
)
from backend.service.store_service import StoreService
from backend.service.subscription_account_service import SubscriptionAccountService
from backend.service.user_group_service import user_group_runtime


OPENAI_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
OPENAI_MODELS_URL = "https://chatgpt.com/backend-api/codex/models"
OPENAI_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
OPENAI_RESET_CREDITS_URL = "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits"
OPENAI_RESET_CREDITS_CONSUME_URL = OPENAI_RESET_CREDITS_URL + "/consume"
CLAUDE_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_COUNT_TOKENS_URL = "https://api.anthropic.com/v1/messages/count_tokens"
CLAUDE_MODELS_URL = "https://api.anthropic.com/v1/models"
DEFAULT_CLAUDE_CLIENT_VERSION = "2.1.258"
DEFAULT_CLAUDE_RELEASE_URL = "https://api.github.com/repos/anthropics/claude-code/releases/latest"
DEFAULT_GROK_CLIENT_VERSION = "0.2.120"

CAPACITY_ERROR_MARKERS = (
    "selected model is at capacity",
    "model is at capacity",
    "currently at capacity",
    "model capacity",
    "currently overloaded",
)
OVERLOAD_ERROR_MARKERS = (
    "currently overloaded",
)
OPENAI_STREAM_CONTROL_EVENTS = {
    "response.created",
    "response.in_progress",
    "response.queued",
}

OPENAI_SUBSCRIPTION_UNSUPPORTED_FIELDS = {
    "chat_template_kwargs",
    "user",
    "metadata",
    "prompt_cache_retention",
    "prompt_cache_options",
    "safety_identifier",
    "stream_options",
    "truncation",
    "stop_sequences",
    "temperature",
    "top_p",
    "frequency_penalty",
    "presence_penalty",
}
OPENAI_SERVICE_TIERS = {"auto", "default", "fast", "flex", "priority", "scale", "ultrafast"}
OPENAI_SERVICE_TIER_POLICIES = {"pass", "filter", "force_priority"}


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
        self._queue_waiters = 0
        self._queue_total = 0
        self._queue_rejected = 0
        self._queue_timeouts = 0
        self._local_rate_limits = 0
        self._upstream_capacity_failures = 0
        self._activity_sequence = 0
        self._queued_activities: dict[int, dict[str, Any]] = {}
        self._active_activities: dict[int, dict[str, Any]] = {}
        self._quota_cache: dict[int, tuple[float, dict[str, Any]]] = {}
        self._quota_locks: dict[tuple[Any, int], asyncio.Lock] = {}
        self._quota_tasks: set[asyncio.Task] = set()
        self._quota_task_accounts: set[int] = set()
        self.max_queued_requests = 200
        self.codex_client_version = DEFAULT_CODEX_CLIENT_VERSION
        self.claude_client_version = DEFAULT_CLAUDE_CLIENT_VERSION
        self.grok_client_version = DEFAULT_GROK_CLIENT_VERSION
        self.gpt_default_reasoning_effort = DEFAULT_GPT_REASONING_EFFORT

    @PostConstruct
    def init(self) -> None:
        # SpringBootAI 2.3.7 may discover the same lifecycle method through
        # both the original class and its AOP proxy. Keep initialization and
        # its log line idempotent.
        if getattr(self, "_gateway_initialized", False):
            return
        self._gateway_initialized = True
        config = get_config()
        self.gpt_default_reasoning_effort = configured_gpt_reasoning_effort(config)
        cfg = config.get("rose", {}).get("subscription-gateway", {})
        self.codex_client_version = codex_client_version(cfg)
        self.codex_client_version_pinned = codex_client_version_is_pinned(cfg)
        self.codex_version_sync_enabled = (
            str(cfg.get("codex-version-auto-sync", True)).strip().lower() in {"1", "true", "yes", "on"}
            and not self.codex_client_version_pinned
        )
        self.codex_release_url = str(cfg.get("codex-version-release-url") or DEFAULT_CODEX_RELEASE_URL).strip()
        raw_claude_version = str(cfg.get("claude-client-version") or "").strip()
        if raw_claude_version:
            normalized_claude_version = normalize_codex_client_version(raw_claude_version)
            if not normalized_claude_version:
                raise ValueError("claude-client-version 必须是有效版本号，例如 2.1.258")
            self.claude_client_version = normalized_claude_version
        self.claude_version_sync_enabled = (
            str(cfg.get("claude-version-auto-sync", True)).strip().lower() in {"1", "true", "yes", "on"}
            and not raw_claude_version
        )
        self.claude_release_url = str(cfg.get("claude-version-release-url") or DEFAULT_CLAUDE_RELEASE_URL).strip()
        self.grok_client_version = str(cfg.get("grok-client-version") or DEFAULT_GROK_CLIENT_VERSION).strip()
        self.openai_service_tier_policy = str(cfg.get("openai-service-tier-policy") or "pass").strip().lower()
        if self.openai_service_tier_policy not in OPENAI_SERVICE_TIER_POLICIES:
            raise ValueError("openai-service-tier-policy 只能是 pass、filter 或 force_priority")
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
        self.max_queued_requests = max(1, min(10000, int(cfg.get("max-queued-requests", 200) or 200)))
        self.session_affinity_ttl = max(60.0, min(86400.0, float(cfg.get("session-affinity-ttl-seconds", 3600) or 3600)))
        self.quota_cache_ttl = max(30.0, min(3600.0, float(cfg.get("quota-cache-seconds", 300) or 300)))
        self.weekly_quota_disable_threshold = max(0.0, min(100.0, float(cfg.get("weekly-quota-disable-threshold-percent", 2) or 0)))
        self.logger.info(
            "订阅网关保护已启用 per_account_concurrency=%s per_account_rpm=%s queue_timeout=%s max_queue=%s capacity_retries=%s session_affinity_ttl=%ss",
            self.per_account_concurrency, self.per_account_rpm,
            "unlimited" if self.queue_timeout <= 0 else f"{int(self.queue_timeout)}s",
            self.max_queued_requests,
            self.capacity_retries,
            int(self.session_affinity_ttl),
        )

    @Scheduled(fixed_rate=21600000, initial_delay=5000)
    def sync_subscription_client_versions(self) -> None:
        """Refresh outbound CLI identities without blocking startup/requests."""
        if not getattr(self, "enabled", False):
            return
        targets = []
        if getattr(self, "codex_version_sync_enabled", False):
            targets.append(("Codex", "codex_client_version", self.codex_release_url))
        if getattr(self, "claude_version_sync_enabled", False):
            targets.append(("Claude Code", "claude_client_version", self.claude_release_url))
        for label, attribute, url in targets:
            try:
                response = httpx.get(
                    url,
                    headers={"Accept": "application/vnd.github+json", "User-Agent": "rose-ai-proxy"},
                    timeout=min(10.0, max(2.0, float(getattr(self, "connect_timeout", 5.0)))),
                    follow_redirects=True,
                )
                response.raise_for_status()
                release = response.json()
                version = normalize_codex_client_version(
                    release.get("tag_name") if isinstance(release, dict) else ""
                )
                if not version:
                    raise ValueError("最新版发布标签不包含有效版本")
                previous = str(getattr(self, attribute))
                setattr(self, attribute, version)
                if previous != version:
                    self.logger.info("%s 客户端版本已自动同步 %s -> %s", label, previous, version)
            except Exception as exc:
                self.logger.warning(
                    "%s 客户端版本自动同步失败，继续使用 %s error=%s",
                    label,
                    getattr(self, attribute),
                    str(exc)[:300],
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

    def _quota_lock(self, account_id: int) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        key = (loop, int(account_id))
        with self._safety_lock:
            lock = self._quota_locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._quota_locks[key] = lock
            return lock

    def _schedule_quota_check(self, account_id: int) -> None:
        """Check quota after a 429 without delaying the current failover path."""
        account_id = int(account_id)
        with self._safety_lock:
            if account_id in self._quota_task_accounts:
                return
            self._quota_task_accounts.add(account_id)
        task = asyncio.create_task(self.query_account_quota(account_id, force=True))
        with self._safety_lock:
            self._quota_tasks.add(task)

        def finished(completed: asyncio.Task) -> None:
            with self._safety_lock:
                self._quota_tasks.discard(completed)
                self._quota_task_accounts.discard(account_id)
            try:
                completed.result()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                self.logger.warning(
                    "订阅网关429后台用量查询失败 account_id=%s error=%s",
                    account_id, str(exc)[:300],
                )

        task.add_done_callback(finished)

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

    def _new_activity(
        self,
        provider: str,
        model: str,
        user_id: int,
        account_id: int,
        reasoning_effort: str = "",
    ) -> dict[str, Any]:
        with self._safety_lock:
            self._activity_sequence += 1
            activity_id = self._activity_sequence
        return {
            "request_id": activity_id,
            "user_id": int(user_id),
            "provider": str(provider),
            "model": str(model),
            "reasoning_effort": str(reasoning_effort or ""),
            "account_id": int(account_id),
            "requested_at": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _reasoning_effort(payload: dict[str, Any]) -> str:
        reasoning = payload.get("reasoning")
        if isinstance(reasoning, dict):
            value = reasoning.get("effort")
            if value is not None:
                return str(value).strip()[:40]
        value = payload.get("reasoning_effort")
        if value is None:
            value = payload.get("reasoning-effort")
        return str(value or "").strip()[:40]

    def _finish_activity(self, activity_id: int) -> None:
        if not activity_id:
            return
        with self._safety_lock:
            self._queued_activities.pop(int(activity_id), None)
            self._active_activities.pop(int(activity_id), None)

    async def _acquire_account_slot(self, semaphore: asyncio.Semaphore, account_id: int = 0, activity: dict[str, Any] | None = None) -> float:
        """Wait for an account slot and return the queue duration in milliseconds."""
        started_at = time.monotonic()
        queued = semaphore.locked()
        activity_id = int((activity or {}).get("request_id") or 0)
        if queued:
            with self._safety_lock:
                if self._queue_waiters >= self.max_queued_requests:
                    self._queue_rejected += 1
                    raise OverflowError("subscription queue is full")
                self._queue_waiters += 1
                self._queue_total += 1
                if activity_id:
                    self._queued_activities[activity_id] = {
                        **activity,
                        "queued_at": datetime.now(timezone.utc).isoformat(),
                    }
        try:
            if self.queue_timeout <= 0:
                await semaphore.acquire()
            else:
                await asyncio.wait_for(semaphore.acquire(), timeout=self.queue_timeout)
        finally:
            if queued:
                with self._safety_lock:
                    self._queue_waiters = max(0, self._queue_waiters - 1)
                    if activity_id:
                        self._queued_activities.pop(activity_id, None)
        wait_ms = (time.monotonic() - started_at) * 1000.0
        if activity_id:
            with self._safety_lock:
                self._active_activities[activity_id] = {
                    **activity,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "queue_wait_ms": round(wait_ms, 1),
                }
        return wait_ms

    def metrics(self, include_users: bool = False) -> dict[str, Any]:
        """Return queue counters and, for admins, current user activity."""
        with self._safety_lock:
            semaphores = list(self._account_semaphores.values())
            now_mono = time.monotonic()
            for window in self._rate_windows.values():
                while window and window[0] <= now_mono - 60:
                    window.popleft()
            rpm_used = sum(len(window) for window in self._rate_windows.values())
            active_capacity = len(semaphores) * int(getattr(self, "per_account_concurrency", 1) or 1)
            available = sum(max(0, int(getattr(item, "_value", 0))) for item in semaphores)
            snapshot = {
                "queue_waiting": self._queue_waiters,
                "queue_limit": self.max_queued_requests,
                "queued_total": self._queue_total,
                "queue_rejected": self._queue_rejected,
                "queue_timeouts": self._queue_timeouts,
                "active_requests": max(0, active_capacity - available),
                "local_rate_limits": self._local_rate_limits,
                "upstream_capacity_failures": self._upstream_capacity_failures,
                "queue_timeout_seconds": self.queue_timeout,
                "rpm_used": rpm_used,
            }
            snapshot["concurrent_tasks"] = snapshot["active_requests"]
            active_activities = [dict(item) for item in self._active_activities.values()]
            queued_activities = [dict(item) for item in self._queued_activities.values()]
        if include_users:
            usernames: dict[int, str] = {}
            for user_id in {int(item["user_id"]) for item in active_activities + queued_activities}:
                try:
                    user = self.store.find_user(user_id) if self.store else None
                except Exception:
                    user = None
                usernames[user_id] = str((user or {}).get("username") or f"用户 #{user_id}")

            active_counts: dict[int, int] = {}
            queued_counts: dict[int, int] = {}
            for item in active_activities:
                user_id = int(item["user_id"])
                active_counts[user_id] = active_counts.get(user_id, 0) + 1
            for item in queued_activities:
                user_id = int(item["user_id"])
                queued_counts[user_id] = queued_counts.get(user_id, 0) + 1

            def with_username(item: dict[str, Any]) -> dict[str, Any]:
                user_id = int(item["user_id"])
                return {
                    **item,
                    "username": usernames[user_id],
                    "user_concurrent_tasks": active_counts.get(user_id, 0),
                    "user_queued_tasks": queued_counts.get(user_id, 0),
                }

            snapshot["active_users"] = [
                with_username(item) for item in sorted(active_activities, key=lambda row: row.get("started_at") or "")
            ]
            snapshot["queued_users"] = [
                with_username(item) for item in sorted(queued_activities, key=lambda row: row.get("queued_at") or "")
            ]
        group_runtime = user_group_runtime()
        if group_runtime:
            group_queue = group_runtime.queue_metrics(include_users=include_users)
            snapshot["queue_waiting"] += int(group_queue.get("queue_waiting") or 0)
            snapshot["queued_total"] += int(group_queue.get("queued_total") or 0)
            snapshot["queue_rejected"] += int(group_queue.get("queue_rejected") or 0)
            if include_users:
                snapshot["queued_users"] = list(snapshot.get("queued_users") or []) + list(group_queue.get("queued_users") or [])
        try:
            rows = self.accounts.repository.list_provider("openai") + self.accounts.repository.list_provider("claude")
            now = datetime.now(timezone.utc)
            cooling = 0
            if getattr(self.accounts, "cooldown_enabled", True):
                for row in rows:
                    raw = str(row.get("cooldown_until") or "").strip()
                    if not raw:
                        continue
                    try:
                        until = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                        if until.tzinfo is None:
                            until = until.replace(tzinfo=timezone.utc)
                        cooling += int(until > now)
                    except ValueError:
                        continue
            account_count = len(rows)
            snapshot.update({
                "account_pool_total": account_count,
                "cooldown_accounts": cooling,
                "slot_capacity": account_count * self.per_account_concurrency,
                "rpm_capacity": account_count * self.per_account_rpm,
            })
        except Exception:
            snapshot.update({"account_pool_total": 0, "cooldown_accounts": 0, "slot_capacity": active_capacity, "rpm_capacity": 0})
        return snapshot

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
        with self._safety_lock:
            quota_tasks = list(self._quota_tasks)
            self._quota_tasks.clear()
            self._quota_task_accounts.clear()
        for task in quota_tasks:
            task.cancel()
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

    def _openai_headers(self, credentials: dict[str, Any], account: dict[str, Any], stream: bool) -> dict[str, str]:
        headers = {
            "Authorization": "Bearer " + str(credentials.get("access_token") or ""),
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
            "OpenAI-Beta": "responses=experimental",
            **codex_identity_headers(self.codex_client_version),
        }
        account_ref = str(credentials.get("account_id") or credentials.get("chatgpt_account_id") or account.get("account_ref") or "").strip()
        if account_ref:
            headers["ChatGPT-Account-Id"] = account_ref
        return headers

    def _openai_quota_headers(self, credentials: dict[str, Any], account: dict[str, Any]) -> dict[str, str]:
        headers = self._openai_headers(credentials, account, False)
        headers.update({
            "Accept": "application/json",
            "OpenAI-Beta": "codex-1",
            "OAI-Language": "zh-CN",
            "Originator": "Codex Desktop",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Dest": "empty",
            "Priority": "u=4, i",
        })
        return headers

    def _claude_headers(self, credentials: dict[str, Any], incoming: dict[str, str], stream: bool) -> dict[str, str]:
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
            "User-Agent": f"claude-cli/{self.claude_client_version} (external, cli)",
            "X-App": "cli",
            "X-Stainless-Lang": "js",
            "X-Stainless-Package-Version": "0.94.0",
            "X-Stainless-Runtime": "node",
            "X-Stainless-Runtime-Version": "v24.3.0",
            "X-Stainless-Retry-Count": "0",
            "X-Stainless-Timeout": "600",
            "Anthropic-Dangerous-Direct-Browser-Access": "true",
        }

    def _grok_headers(self, credentials: dict[str, Any], stream: bool) -> dict[str, str]:
        return {
            "Authorization": "Bearer " + str(credentials.get("access_token") or ""),
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
            "X-XAI-Token-Auth": "xai-grok-cli",
            "x-grok-client-version": self.grok_client_version,
            "x-grok-client-identifier": "grok-shell",
            "User-Agent": f"xai-grok-workspace/{self.grok_client_version}",
        }

    @staticmethod
    def _compatible_headers(credentials: dict[str, Any], stream: bool) -> dict[str, str]:
        return {
            "Authorization": "Bearer " + str(credentials.get("access_token") or ""),
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
            "User-Agent": "Rose-Subscription-Gateway/1.0",
        }

    def _provider_request(self, provider: str, credentials: dict[str, Any], account: dict[str, Any], incoming: dict[str, str], stream: bool, *, count_tokens: bool = False) -> tuple[str, dict[str, str]]:
        if provider == "openai":
            return OPENAI_RESPONSES_URL, self._openai_headers(credentials, account, stream)
        if provider == "claude":
            url = CLAUDE_COUNT_TOKENS_URL if count_tokens else CLAUDE_MESSAGES_URL
            return url, self._claude_headers(credentials, incoming, stream)
        base_url = provider_base_url(provider)
        if provider == "grok":
            return base_url + "/responses", self._grok_headers(credentials, stream)
        if provider in CHAT_PROVIDERS:
            return base_url + "/chat/completions", self._compatible_headers(credentials, stream)
        raise ValueError(f"不支持的订阅供应商：{provider}")

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

    def _apply_openai_service_tier_policy(self, payload: dict[str, Any]) -> None:
        policy = getattr(self, "openai_service_tier_policy", "pass")
        raw = payload.get("service_tier")
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            payload.pop("service_tier", None)
            if policy == "force_priority":
                payload["service_tier"] = "priority"
            return
        if not isinstance(raw, str):
            raise ValueError("service_tier 必须是字符串")
        tier = raw.strip().lower()
        if tier not in OPENAI_SERVICE_TIERS:
            raise ValueError(
                "service_tier 必须是 auto、default、fast、flex、priority、scale 或 ultrafast"
            )
        if policy == "filter":
            payload.pop("service_tier", None)
        elif policy == "force_priority":
            payload["service_tier"] = "priority"
        else:
            # ChatGPT/Codex uses priority as the upstream spelling for the
            # client-facing fast alias.
            payload["service_tier"] = "priority" if tier == "fast" else tier

    @staticmethod
    def _responses_text_content(content: Any) -> str | None:
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return None
        text_parts: list[str] = []
        for part in content:
            if not isinstance(part, dict) or str(part.get("type") or "") not in {"text", "input_text"}:
                return None
            text_parts.append(str(part.get("text") or ""))
        return "\n".join(text_parts)

    @classmethod
    def _promote_openai_instructions(cls, payload: dict[str, Any]) -> None:
        source = payload.get("input")
        if not isinstance(source, list):
            return
        promoted: list[str] = []
        remaining: list[Any] = []
        changed = False
        for item in source:
            if not isinstance(item, dict):
                remaining.append(item)
                continue
            role = str(item.get("role") or "").strip().lower()
            if role not in {"system", "developer"}:
                remaining.append(item)
                continue
            text = cls._responses_text_content(item.get("content"))
            if text is None:
                # The internal Codex transport rejects system, but developer
                # can preserve non-text structured content without dropping it.
                copy = dict(item)
                copy["role"] = "developer"
                remaining.append(copy)
                changed = changed or role == "system"
                continue
            if text.strip():
                promoted.append(text.strip())
            changed = True
        if promoted:
            existing = str(payload.get("instructions") or "").strip()
            payload["instructions"] = "\n\n".join(promoted + ([existing] if existing else []))
        if changed:
            payload["input"] = remaining

    @staticmethod
    def _normalize_openai_legacy_tools(payload: dict[str, Any]) -> None:
        functions = payload.pop("functions", None)
        if functions is not None and "tools" not in payload:
            if not isinstance(functions, list):
                raise ValueError("functions 必须是数组")
            tools: list[dict[str, Any]] = []
            for function in functions:
                if not isinstance(function, dict) or not str(function.get("name") or "").strip():
                    raise ValueError("每个 function 都必须包含 name")
                tool = {
                    "type": "function",
                    "name": str(function["name"]).strip(),
                    "parameters": function.get("parameters") if isinstance(function.get("parameters"), dict) else {"type": "object"},
                }
                if function.get("description") is not None:
                    tool["description"] = str(function.get("description") or "")
                if function.get("strict") is not None:
                    tool["strict"] = bool(function.get("strict"))
                tools.append(tool)
            payload["tools"] = tools

        function_call = payload.pop("function_call", None)
        if function_call is None or "tool_choice" in payload:
            return
        if isinstance(function_call, str):
            if function_call not in {"auto", "none", "required"}:
                raise ValueError("function_call 字符串必须是 auto、none 或 required")
            payload["tool_choice"] = function_call
        elif isinstance(function_call, dict) and str(function_call.get("name") or "").strip():
            payload["tool_choice"] = {"type": "function", "name": str(function_call["name"]).strip()}
        else:
            raise ValueError("function_call 格式无效")

    @staticmethod
    def _normalize_openai_compatibility_fields(payload: dict[str, Any]) -> None:
        """Normalize legacy/custom-provider fields accepted by newer clients."""
        if "prompt" in payload:
            if payload.get("input") is None and payload.get("prompt") is not None:
                payload["input"] = payload["prompt"]
            payload.pop("prompt", None)
        payload.pop("commands", None)

        source = payload.get("input")
        if isinstance(source, list):
            for item in source:
                if isinstance(item, dict):
                    item.pop("internal_chat_message_metadata_passthrough", None)

    @staticmethod
    def _normalize_openai_reasoning_mode(payload: dict[str, Any]) -> None:
        reasoning = payload.get("reasoning")
        if not isinstance(reasoning, dict) or "mode" not in reasoning:
            return
        model = str(payload.get("model") or "").strip().lower()
        # Astra supports mode and effort as independent native parameters.
        if model == "gpt-6" or model == "gpt-6-astra" or model.startswith("gpt-6-astra-"):
            return
        mode = reasoning.pop("mode", None)
        if not str(reasoning.get("effort") or "").strip() and str(mode or "").strip().lower() == "pro":
            reasoning["effort"] = "max"
        if not reasoning:
            payload.pop("reasoning", None)

    @classmethod
    def _normalize_openai_json_schema(cls, schema: dict[str, Any]) -> None:
        """Remove schema keywords rejected by the internal Responses endpoint."""
        schema.pop("uniqueItems", None)
        schema.pop("minProperties", None)
        if not schema.get("type"):
            if schema.get("properties") is not None:
                schema["type"] = "object"
            elif schema.get("items") is not None:
                schema["type"] = "array"

        properties = schema.get("properties")
        if isinstance(properties, dict):
            for child in properties.values():
                if isinstance(child, dict):
                    cls._normalize_openai_json_schema(child)

        items = schema.get("items")
        if isinstance(items, dict):
            cls._normalize_openai_json_schema(items)
        elif isinstance(items, list):
            for child in items:
                if isinstance(child, dict):
                    cls._normalize_openai_json_schema(child)

        for key in (
            "additionalProperties", "additionalItems", "contains", "not", "if", "then", "else",
            "propertyNames", "unevaluatedProperties", "unevaluatedItems",
        ):
            child = schema.get(key)
            if isinstance(child, dict):
                cls._normalize_openai_json_schema(child)
        for key in ("anyOf", "oneOf", "allOf", "prefixItems"):
            children = schema.get(key)
            if isinstance(children, list):
                for child in children:
                    if isinstance(child, dict):
                        cls._normalize_openai_json_schema(child)
        for key in ("$defs", "definitions", "patternProperties", "dependentSchemas"):
            children = schema.get(key)
            if isinstance(children, dict):
                for child in children.values():
                    if isinstance(child, dict):
                        cls._normalize_openai_json_schema(child)
        dependencies = schema.get("dependencies")
        if isinstance(dependencies, dict):
            for child in dependencies.values():
                if isinstance(child, dict):
                    cls._normalize_openai_json_schema(child)

    @classmethod
    def _normalize_openai_response_formats(cls, payload: dict[str, Any]) -> None:
        candidates: list[Any] = []
        text = payload.get("text")
        if isinstance(text, dict):
            candidates.append(text.get("format"))
        candidates.append(payload.get("response_format"))
        for value in candidates:
            if not isinstance(value, dict) or str(value.get("type") or "").strip() != "json_schema":
                continue
            schema = value.get("schema")
            if isinstance(schema, dict):
                cls._normalize_openai_json_schema(schema)
            legacy = value.get("json_schema")
            if isinstance(legacy, dict) and isinstance(legacy.get("schema"), dict):
                cls._normalize_openai_json_schema(legacy["schema"])

    @staticmethod
    def _normalize_openai_image_tools(payload: dict[str, Any]) -> None:
        tools = payload.get("tools")
        if not isinstance(tools, list):
            return
        for tool in tools:
            if not isinstance(tool, dict) or str(tool.get("type") or "") != "image_generation":
                continue
            if "output_format" not in tool and str(tool.get("format") or "").strip():
                tool["output_format"] = str(tool["format"]).strip()
            if "output_compression" not in tool and tool.get("compression") is not None:
                tool["output_compression"] = tool["compression"]
            tool.pop("format", None)
            tool.pop("compression", None)
            if str(tool.get("model") or "").strip().lower().startswith("gpt-image-2"):
                tool.pop("input_fidelity", None)

    def _prepare_openai_subscription_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        normalized = copy.deepcopy(payload)
        model = str(normalized.get("model") or "").strip()
        if model:
            normalized["model"] = model
        self._normalize_openai_compatibility_fields(normalized)
        self._normalize_openai_reasoning_mode(normalized)
        reasoning = normalized.get("reasoning")
        if reasoning is not None and not isinstance(reasoning, dict):
            raise ValueError("reasoning must be a JSON object")
        outgoing = with_responses_reasoning(normalized, self.gpt_default_reasoning_effort)
        outgoing = dict(outgoing)

        raw_max_tokens = outgoing.get("max_output_tokens")
        if raw_max_tokens is None:
            raw_max_tokens = outgoing.get("max_completion_tokens")
        if raw_max_tokens is None:
            raw_max_tokens = outgoing.get("max_tokens")
        outgoing.pop("max_output_tokens", None)
        outgoing.pop("max_tokens", None)
        outgoing.pop("max_completion_tokens", None)
        if raw_max_tokens is not None:
            try:
                if isinstance(raw_max_tokens, bool):
                    raise ValueError
                max_tokens = int(raw_max_tokens)
                if max_tokens <= 0 or (isinstance(raw_max_tokens, float) and not raw_max_tokens.is_integer()):
                    raise ValueError
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError("max_tokens/max_output_tokens must be a positive integer") from exc
            outgoing["max_tokens"] = max_tokens

        for key in OPENAI_SUBSCRIPTION_UNSUPPORTED_FIELDS:
            outgoing.pop(key, None)
        self._normalize_openai_legacy_tools(outgoing)
        self._promote_openai_instructions(outgoing)
        self._normalize_openai_response_formats(outgoing)
        self._normalize_openai_image_tools(outgoing)
        self._apply_openai_service_tier_policy(outgoing)
        outgoing["store"] = False
        if not str(outgoing.get("instructions") or "").strip():
            outgoing["instructions"] = "You are a helpful coding assistant. Follow the user's instructions carefully."
        return outgoing

    async def proxy_openai(self, payload: dict[str, Any], user_id: int) -> SubscriptionGatewayResponse:
        model = str(payload.get("model") or "").strip()
        if not model:
            return self._json_error(400, "model is required", "invalid_request_error")
        try:
            outgoing = self._prepare_openai_subscription_payload(payload)
        except ValueError as exc:
            return self._json_error(400, str(exc), "invalid_request_error")
        return await self._proxy("openai", model, outgoing, user_id, {})

    async def proxy_responses(self, provider: str, payload: dict[str, Any], user_id: int) -> SubscriptionGatewayResponse:
        provider = str(provider or "").strip().lower()
        if provider not in SUBSCRIPTION_PROVIDERS:
            return self._json_error(400, "provider 不受支持", "invalid_request_error")
        if provider == "openai":
            return await self.proxy_openai(payload, user_id)
        if provider not in RESPONSES_PROVIDERS:
            return self._json_error(400, f"{provider} 不支持 Responses 协议", "invalid_request_error")
        model = str(payload.get("model") or "").strip()
        if not model:
            return self._json_error(400, "model is required", "invalid_request_error")
        return await self._proxy(provider, model, copy.deepcopy(payload), user_id, {})

    async def proxy_chat(self, provider: str, payload: dict[str, Any], user_id: int) -> SubscriptionGatewayResponse:
        provider = str(provider or "").strip().lower()
        if provider not in SUBSCRIPTION_PROVIDERS:
            return self._json_error(400, "provider 不受支持", "invalid_request_error")
        if provider not in CHAT_PROVIDERS:
            return self._json_error(400, f"{provider} 不支持 Chat Completions 协议", "invalid_request_error")
        model = str(payload.get("model") or "").strip()
        if not model:
            return self._json_error(400, "model is required", "invalid_request_error")
        outgoing = copy.deepcopy(payload)
        if outgoing.get("stream"):
            stream_options = outgoing.get("stream_options")
            if not isinstance(stream_options, dict):
                stream_options = {}
            outgoing["stream_options"] = {**stream_options, "include_usage": True}
        return await self._proxy(provider, model, outgoing, user_id, {})

    async def proxy_claude(self, payload: dict[str, Any], user_id: int, incoming_headers: dict[str, str], *, count_tokens: bool = False) -> SubscriptionGatewayResponse:
        model = str(payload.get("model") or "").strip()
        if not model:
            return self._json_error(400, "model is required", "invalid_request_error")
        return await self._proxy("claude", model, dict(payload), user_id, incoming_headers, count_tokens=count_tokens)

    async def _proxy(self, provider: str, model: str, payload: dict[str, Any], user_id: int, incoming_headers: dict[str, str], *, count_tokens: bool = False) -> SubscriptionGatewayResponse:
        stream_requested = bool(payload.get("stream")) and not count_tokens
        reasoning_effort = self._reasoning_effort(payload)
        session_key = self._session_key(provider, user_id, payload)
        preferred_account_id = self._preferred_account(session_key)
        excluded: set[int] = set()
        last_status = 502
        last_detail = "上游订阅账号不可用"
        last_error_type = "upstream_error"
        last_headers: dict[str, str] = {}
        capacity_retries = max(0, int(getattr(self, "capacity_retries", 0) or 0))
        capacity_retries_left = capacity_retries
        # One loop turn may be used to reset an exhausted account set, so
        # reserve two turns for each delayed capacity retry.
        total_attempts = self.max_attempts + capacity_retries * 2
        for _ in range(total_attempts):
            try:
                account, credentials = await self.accounts.acquire(
                    provider, model, excluded, preferred_account_id=preferred_account_id,
                )
            except (LookupError, ValueError) as exc:
                # Preserve a concrete queue/rate-limit error after all selected
                # accounts have been tried instead of replacing it with the
                # account pool's generic "not available" message.
                if last_error_type == "upstream_capacity" and excluded and capacity_retries_left > 0:
                    retry_index = capacity_retries - capacity_retries_left
                    capacity_retries_left -= 1
                    excluded.clear()
                    preferred_account_id = None
                    await asyncio.sleep(self._capacity_retry_delay(retry_index, None))
                    continue
                if not excluded:
                    last_detail = str(exc)
                break
            account_id = int(account["id"])
            excluded.add(account_id)
            semaphore = self._account_semaphore(account_id)
            activity = self._new_activity(provider, model, user_id, account_id, reasoning_effort)
            activity_id = int(activity["request_id"])
            try:
                queue_wait_ms = await self._acquire_account_slot(semaphore, account_id, activity)
            except OverflowError:
                last_status = 429
                last_detail = "本地订阅账号等待队列已满，请稍后重试"
                last_error_type = "local_queue_full"
                last_headers = {
                    "content-type": "application/json; charset=utf-8",
                    "retry-after": "5",
                    "x-rose-error-source": "local_queue",
                }
                continue
            except TimeoutError:
                with self._safety_lock:
                    self._queue_timeouts += 1
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
            response: httpx.Response | None = None
            try:
                if queue_wait_ms >= 500:
                    self.logger.info(
                        "订阅网关排队完成 provider=%s model=%s account_id=%s wait_ms=%.1f",
                        provider, model, account_id, queue_wait_ms,
                    )
                # Only completed queue admissions count toward the local RPM
                # budget.  The old order charged timed-out queue entries too.
                if not self._reserve_rate_slot(account_id):
                    with self._safety_lock:
                        self._local_rate_limits += 1
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
                url, headers = self._provider_request(
                    provider, credentials, account, incoming_headers, stream_requested,
                    count_tokens=count_tokens,
                )
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
                    if last_status == 429:
                        last_error_type = "upstream_rate_limit"
                        response_headers["x-rose-error-source"] = last_error_type
                        last_headers = response_headers
                        self._forget_account(session_key, account_id)
                        self._schedule_quota_check(account_id)
                        self.logger.warning(
                            "订阅网关收到上游429，不改变账号状态 provider=%s model=%s account_id=%s",
                            provider, model, account_id,
                        )
                        continue
                    if self._is_capacity_error(last_detail):
                        with self._safety_lock:
                            self._upstream_capacity_failures += 1
                        last_status = 503
                        last_error_type = "upstream_capacity"
                        last_headers = self._capacity_headers(retry_after)
                        self._forget_account(session_key, account_id)
                        self.logger.warning(
                            "订阅网关上游模型容量不足 provider=%s model=%s account_id=%s retries_left=%s",
                            provider, model, account_id, capacity_retries_left,
                        )
                        if capacity_retries_left > 0:
                            # Keep this account excluded so another healthy
                            # account is attempted before retrying the pool.
                            preferred_account_id = None
                        continue
                    # A normal 4xx request/schema rejection belongs to the
                    # caller, not the selected subscription account.  Returning
                    # it directly avoids poisoning the account health state and
                    # retrying the same bad payload against every account.
                    if 400 <= last_status < 500 and last_status not in {401, 403, 408, 429}:
                        response_headers["x-rose-error-source"] = "upstream_request"
                        self.logger.info(
                            "订阅网关请求参数被上游拒绝 provider=%s model=%s account_id=%s status=%s",
                            provider, model, account_id, last_status,
                        )
                        return SubscriptionGatewayResponse(last_status, response_headers, body=body)
                    last_error_type = "upstream_error"
                    last_headers = response_headers
                    self.accounts.record_failure(account, last_status, last_detail, retry_after)
                    self._forget_account(session_key, account_id)
                    self.logger.warning(
                        "订阅网关上游拒绝 provider=%s model=%s account_id=%s status=%s",
                        provider, model, account_id, last_status,
                    )
                    if last_status in {401, 403, 408, 429} or last_status >= 500:
                        continue
                    return SubscriptionGatewayResponse(last_status, response_headers, body=body)
                self._remember_account(session_key, account_id)
                if stream_requested:
                    stream_iterator = response.aiter_bytes()
                    stream_prefix = b""
                    if provider in RESPONSES_PROVIDERS:
                        stream_prefix, stream_failure = await self._prefetch_openai_stream(stream_iterator)
                        if stream_failure and self._is_capacity_error(stream_failure):
                            with self._safety_lock:
                                self._upstream_capacity_failures += 1
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
                                preferred_account_id = None
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
                            iterator=stream_iterator, prefix=stream_prefix, activity_id=activity_id,
                            fallback_input_tokens=self._estimate_input_tokens(payload),
                        ),
                    )
                body = await response.aread()
                await response.aclose()
                response_failure = self._failure_from_json_bytes(body)
                if response_failure:
                    if self._is_capacity_error(response_failure):
                        with self._safety_lock:
                            self._upstream_capacity_failures += 1
                        last_status = 503
                        last_detail = response_failure
                        last_error_type = "upstream_capacity"
                        last_headers = self._capacity_headers(None)
                        self._forget_account(session_key, account_id)
                        if capacity_retries_left > 0:
                            preferred_account_id = None
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
                    try:
                        # A disconnect can cancel header/body reads or SSE
                        # prefetch before ownership passes to _stream_and_bill.
                        # Close that response too, without touching live streams.
                        if response is not None and not response.is_closed:
                            await response.aclose()
                    finally:
                        self._finish_activity(activity_id)
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
        activity_id: int = 0,
        fallback_input_tokens: int = 0,
    ) -> AsyncIterator[bytes]:
        line_buffer = b""
        usage = {"input_tokens": 0, "output_tokens": 0}
        terminal_failure = ""
        observed_bytes = 0
        billing_started = False

        def observe(chunk: bytes) -> None:
            nonlocal line_buffer, terminal_failure, observed_bytes
            observed_bytes += len(chunk)
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
                overloaded = self._is_overload_error(terminal_failure)
                if overloaded:
                    with self._safety_lock:
                        self._upstream_capacity_failures += 1
                else:
                    self.accounts.record_failure(account, failure_status, terminal_failure)
                self.logger.warning(
                    "订阅网关流式请求失败 provider=%s model=%s account_id=%s user_id=%s account_cooldown=%s detail=%s",
                    provider, model, int(account["id"]), user_id, not overloaded, terminal_failure[:300],
                )
                return
            billing_started = True
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
        except (asyncio.CancelledError, GeneratorExit):
            # The terminal usage event is often absent after a disconnect.
            # Conservatively settle the observed stream so disconnecting does
            # not bypass billing; normal streaming remains unbuffered.
            usage["input_tokens"] = max(int(usage.get("input_tokens") or 0), int(fallback_input_tokens or 0))
            if not usage.get("output_tokens") and observed_bytes:
                usage["output_tokens"] = max(1, observed_bytes // 12)
            if not billing_started:
                try:
                    await self._bill(user_id, model, account, usage)
                except Exception:
                    self.logger.warning(
                        "订阅网关客户端断开后的计费失败 provider=%s model=%s account_id=%s user_id=%s",
                        provider, model, int(account["id"]), user_id,
                    )
            raise
        except Exception as exc:
            self.accounts.record_failure(account, 502, str(exc))
            raise
        finally:
            try:
                await response.aclose()
            finally:
                self._finish_activity(activity_id)
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

    @staticmethod
    def _estimate_input_tokens(payload: dict[str, Any]) -> int:
        """Cheap fallback used only when a disconnected stream has no usage event."""
        try:
            relevant = {
                key: payload.get(key)
                for key in ("input", "messages", "instructions", "system", "tools")
                if payload.get(key) not in (None, "", [])
            }
            size = len(json.dumps(relevant, ensure_ascii=False, separators=(",", ":")))
            return max(1, math.ceil(size / 4))
        except Exception:
            return 1

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

    @staticmethod
    def _is_overload_error(detail: str) -> bool:
        normalized = str(detail or "").strip().lower()
        return any(marker in normalized for marker in OVERLOAD_ERROR_MARKERS)

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
        if provider in RESPONSES_PROVIDERS:
            source = payload.get("response") if isinstance(payload, dict) and isinstance(payload.get("response"), dict) else payload
            raw_usage = source.get("usage") if isinstance(source, dict) else None
            if isinstance(raw_usage, dict):
                usage["input_tokens"] = max(usage["input_tokens"], int(raw_usage.get("input_tokens") or raw_usage.get("prompt_tokens") or 0))
                usage["output_tokens"] = max(usage["output_tokens"], int(raw_usage.get("output_tokens") or raw_usage.get("completion_tokens") or 0))
        elif isinstance(payload, dict):
            raw_usage = payload.get("usage")
            if not isinstance(raw_usage, dict) and isinstance(payload.get("message"), dict):
                raw_usage = payload["message"].get("usage")
            if isinstance(raw_usage, dict):
                usage["input_tokens"] = max(usage["input_tokens"], int(raw_usage.get("input_tokens") or raw_usage.get("prompt_tokens") or 0))
                usage["output_tokens"] = max(usage["output_tokens"], int(raw_usage.get("output_tokens") or raw_usage.get("completion_tokens") or 0))

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
        cost = self.accounts.cost(account, input_tokens, output_tokens, model)
        ok, _ = await asyncio.to_thread(self.store.charge, int(user_id), model, input_tokens, output_tokens, cost)
        if not ok:
            return self._json_error(402, "Insufficient balance", "insufficient_quota")
        return None

    @staticmethod
    def _quota_float(value: Any, default: float = 0.0) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError):
            return default
        return result if math.isfinite(result) else default

    @staticmethod
    def _quota_int(value: Any, default: int = 0) -> int:
        try:
            return int(float(value))
        except (TypeError, ValueError, OverflowError):
            return default

    @classmethod
    def _normalize_quota_window(cls, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        seconds = max(0, cls._quota_int(value.get("limit_window_seconds")))
        used = min(100.0, max(0.0, cls._quota_float(value.get("used_percent"))))
        reset_after = max(0, cls._quota_int(value.get("reset_after_seconds")))
        reset_raw = value.get("reset_at")
        reset_at = ""
        if isinstance(reset_raw, str) and reset_raw.strip() and not reset_raw.strip().replace(".", "", 1).isdigit():
            reset_at = reset_raw.strip()
        else:
            timestamp = cls._quota_float(reset_raw)
            if timestamp > 0:
                if timestamp > 10_000_000_000:
                    timestamp /= 1000.0
                try:
                    reset_at = datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
                except (OverflowError, OSError, ValueError):
                    reset_at = ""
        if not reset_at and reset_after:
            reset_at = datetime.fromtimestamp(time.time() + reset_after, timezone.utc).isoformat()
        if seconds >= 5 * 24 * 3600:
            label = "每周"
        elif seconds and seconds <= 6 * 3600:
            hours = max(1, round(seconds / 3600))
            label = f"{hours} 小时"
        elif seconds:
            hours = max(1, round(seconds / 3600))
            label = f"{hours} 小时"
        else:
            label = "用量窗口"
        return {
            "label": label,
            "used_percent": round(used, 2),
            "remaining_percent": round(100.0 - used, 2),
            "limit_window_seconds": seconds,
            "reset_after_seconds": reset_after,
            "reset_at": reset_at or None,
        }

    @classmethod
    def _credit_snapshot(cls, payload: Any) -> tuple[int | None, list[dict[str, str]], bool]:
        """Return count, sanitized expiry rows and whether a list was authoritative."""
        if payload is None:
            return None, [], False
        container = payload
        if isinstance(payload, dict) and isinstance(payload.get("rate_limit_reset_credits"), dict):
            container = payload["rate_limit_reset_credits"]
        count: int | None = None
        records: Any = None
        list_present = False
        if isinstance(container, list):
            records = container
            list_present = True
        elif isinstance(container, dict):
            for key in ("available_count", "availableCount"):
                if key in container:
                    parsed = cls._quota_int(container.get(key), -1)
                    if parsed >= 0:
                        count = parsed
                    break
            for key in ("credits", "rate_limit_reset_credits", "items", "data"):
                if key in container and isinstance(container.get(key), list):
                    records = container[key]
                    list_present = True
                    break
        sanitized: list[dict[str, str]] = []
        available_records = 0
        if isinstance(records, list):
            for item in records:
                if not isinstance(item, dict):
                    continue
                reset_type = str(item.get("reset_type") or item.get("resetType") or "").strip()
                status = str(item.get("status") or "").strip()
                if reset_type and reset_type.lower() != "codex_rate_limits":
                    continue
                if status and status.lower() != "available":
                    continue
                available_records += 1
                expires_at = str(item.get("expires_at") or item.get("expiresAt") or "").strip()
                if expires_at:
                    sanitized.append({"expires_at": expires_at})
        if count is None and list_present:
            count = available_records
        return count, sanitized, list_present

    @classmethod
    def _normalize_quota_payload(cls, usage: Any, details: Any = None) -> dict[str, Any]:
        if not isinstance(usage, dict):
            raise ValueError("订阅用量响应格式不正确")
        raw_limit = usage.get("rate_limit") if isinstance(usage.get("rate_limit"), dict) else {}
        windows = [
            cls._normalize_quota_window(raw_limit.get("primary_window")),
            cls._normalize_quota_window(raw_limit.get("secondary_window")),
        ]
        windows = [item for item in windows if item]
        windows.sort(key=lambda item: int(item.get("limit_window_seconds") or 0))
        short_window = windows[0] if windows and int(windows[0].get("limit_window_seconds") or 0) < 5 * 24 * 3600 else None
        long_window = windows[-1] if windows and (len(windows) > 1 or not short_window) else None

        usage_count, usage_credits, _ = cls._credit_snapshot(usage)
        detail_count, detail_credits, detail_list_present = cls._credit_snapshot(details)
        available_count = usage_count
        credits = usage_credits
        if detail_count is not None:
            available_count = detail_count
        if detail_list_present:
            credits = detail_credits

        return {
            "plan_type": str(usage.get("plan_type") or "").strip(),
            "allowed": bool(raw_limit.get("allowed", True)),
            "limit_reached": bool(raw_limit.get("limit_reached", False)),
            "short_window": short_window,
            "long_window": long_window,
            "reset_credits": {
                "available_count": available_count,
                "credits": credits,
            },
        }

    async def _quota_request(self, url: str, headers: dict[str, str]) -> tuple[int, bytes]:
        request = self._client().build_request("GET", url, headers=headers)
        response: httpx.Response | None = None
        try:
            response = await self._client().send(request, stream=False)
            body = await response.aread()
            return int(response.status_code), body
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ValueError(f"查询订阅用量失败：{exc}") from exc
        finally:
            if response is not None and not response.is_closed:
                await response.aclose()

    async def _quota_reset_request(self, headers: dict[str, str], redeem_request_id: str) -> tuple[int, bytes]:
        request_headers = {**headers, "Content-Type": "application/json"}
        request = self._client().build_request(
            "POST",
            OPENAI_RESET_CREDITS_CONSUME_URL,
            headers=request_headers,
            json={"redeem_request_id": redeem_request_id},
        )
        response: httpx.Response | None = None
        try:
            response = await self._client().send(request, stream=False)
            body = await response.aread()
            return int(response.status_code), body
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            # The upstream may have consumed the non-refundable credit before
            # the connection failed. Make the ambiguity explicit so callers do
            # not immediately retry and accidentally spend another credit.
            raise ValueError("重置请求结果未知，请先刷新订阅余量确认，不要重复点击") from exc
        finally:
            if response is not None and not response.is_closed:
                await response.aclose()

    async def reset_account_quota(self, account_id: int) -> dict[str, Any]:
        """Consume one OpenAI reset credit and return a fresh quota snapshot."""
        account_id = int(account_id)
        redeem_request_id = str(uuid.uuid4())
        async with self._quota_lock(account_id):
            fresh = await self.accounts.refresh_account(account_id, force=False)
            account = fresh["account"]
            if str(account.get("provider") or "") != "openai":
                raise ValueError("只有 OpenAI / Codex 订阅支持额度重置")
            headers = self._openai_quota_headers(fresh["credentials"], account)
            status, body = await self._quota_reset_request(headers, redeem_request_id)
            if status == 401 and str(fresh["credentials"].get("refresh_token") or "").strip():
                fresh = await self.accounts.refresh_account(account_id, force=True)
                account = fresh["account"]
                headers = self._openai_quota_headers(fresh["credentials"], account)
                status, body = await self._quota_reset_request(headers, redeem_request_id)
            if status < 200 or status >= 300:
                detail = self._error_detail(httpx.Response(status), body)
                raise ValueError(f"额度重置失败（HTTP {status}）：{detail}")
            try:
                upstream = json.loads(body.decode("utf-8")) if body.strip() else {}
            except (UnicodeDecodeError, ValueError):
                upstream = {}
            if not isinstance(upstream, dict):
                upstream = {}
            with self._safety_lock:
                self._quota_cache.pop(account_id, None)

        result = {
            "ok": True,
            "code": str(upstream.get("code") or "").strip(),
            "windows_reset": max(0, self._quota_int(upstream.get("windows_reset"))),
            "quota": None,
        }
        try:
            result["quota"] = await self.query_account_quota(account_id, force=True)
        except ValueError:
            result["warning"] = "重置次数已提交，但最新余量回查失败；请稍后刷新确认，不要重复点击"
        return result

    async def query_account_quota(self, account_id: int, force: bool = False) -> dict[str, Any]:
        account_id = int(account_id)
        now_mono = time.monotonic()
        with self._safety_lock:
            cached = self._quota_cache.get(account_id)
            if cached and not force and cached[0] > now_mono:
                return {**cached[1], "cached": True}

        async with self._quota_lock(account_id):
            now_mono = time.monotonic()
            with self._safety_lock:
                cached = self._quota_cache.get(account_id)
                if cached and not force and cached[0] > now_mono:
                    return {**cached[1], "cached": True}

            fresh = await self.accounts.refresh_account(account_id, force=False)
            account = fresh["account"]
            if str(account.get("provider") or "") != "openai":
                raise ValueError("只有 OpenAI / Codex 订阅支持用量查询")
            headers = self._openai_quota_headers(fresh["credentials"], account)
            status, body = await self._quota_request(OPENAI_USAGE_URL, headers)
            if status == 401 and str(fresh["credentials"].get("refresh_token") or "").strip():
                # Quota reads are allowed to refresh an expired OAuth token,
                # but retry at most once and never touch gateway failure state.
                fresh = await self.accounts.refresh_account(account_id, force=True)
                account = fresh["account"]
                headers = self._openai_quota_headers(fresh["credentials"], account)
                status, body = await self._quota_request(OPENAI_USAGE_URL, headers)
            if status < 200 or status >= 300:
                detail = self._error_detail(httpx.Response(status), body)
                raise ValueError(f"订阅用量上游返回 HTTP {status}：{detail}")
            try:
                usage = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise ValueError("订阅用量响应不是有效 JSON") from exc

            details: Any = None
            detail_warning = ""
            try:
                detail_status, detail_body = await self._quota_request(OPENAI_RESET_CREDITS_URL, headers)
                if 200 <= detail_status < 300:
                    try:
                        details = json.loads(detail_body.decode("utf-8")) if detail_body.strip() else None
                    except (UnicodeDecodeError, ValueError):
                        detail_warning = "重置次数详情响应格式不正确"
                else:
                    detail_warning = f"重置次数详情暂不可用（HTTP {detail_status}）"
            except ValueError:
                detail_warning = "重置次数详情暂不可用"

            result = self._normalize_quota_payload(usage, details)
            threshold = float(getattr(self, "weekly_quota_disable_threshold", 2.0) or 0)
            long_window = result.get("long_window") if isinstance(result.get("long_window"), dict) else None
            weekly_remaining = self._quota_float(long_window.get("remaining_percent"), 100.0) if long_window else None
            account_disabled = bool(
                threshold > 0
                and long_window
                and self._quota_int(long_window.get("limit_window_seconds")) >= 5 * 24 * 3600
                and weekly_remaining is not None
                and self.accounts.disable_for_weekly_quota(account, weekly_remaining, threshold)
            )
            result.update({
                "account_id": account_id,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "cache_seconds": int(getattr(self, "quota_cache_ttl", 300) or 300),
                "weekly_disable_threshold_percent": threshold,
                "account_disabled": account_disabled,
                "cached": False,
            })
            warnings: list[str] = []
            if account_disabled:
                warnings.append(f"每周订阅剩余量低于 {threshold:g}%，账号已停用并等待管理员处理")
            if detail_warning:
                warnings.append(detail_warning)
            if warnings:
                result["warning"] = "；".join(warnings)
            with self._safety_lock:
                self._quota_cache[account_id] = (
                    time.monotonic() + float(getattr(self, "quota_cache_ttl", 300) or 300),
                    dict(result),
                )
            return result

    async def validate_candidate(self, body: dict[str, Any], supplied: dict[str, Any] | None = None) -> dict[str, Any]:
        """Verify credentials before persistence so broken accounts never enter the pool."""
        provider = self.accounts.normalize_provider(body.get("provider"))
        credentials = self.accounts.normalize_imported_credentials(
            provider, body, dict(supplied or body.get("credentials") or {}),
        )
        if not str(credentials.get("access_token") or "").strip():
            raise ValueError("access_token 不能为空")
        account = {
            "account_ref": str(
                credentials.get("account_id") or credentials.get("chatgpt_account_id")
                or credentials.get("organization_id") or body.get("account_ref") or ""
            ).strip(),
        }
        if provider == "openai":
            url = f"{OPENAI_MODELS_URL}?client_version={self.codex_client_version}"
            headers = self._openai_headers(credentials, account, False)
        elif provider == "claude":
            url = CLAUDE_MODELS_URL
            headers = self._claude_headers(credentials, {}, False)
        else:
            base_url = provider_base_url(provider)
            url = base_url + "/models"
            headers = self._grok_headers(credentials, False) if provider == "grok" else self._compatible_headers(credentials, False)
        request = self._client().build_request("GET", url, headers=headers)
        response: httpx.Response | None = None
        try:
            response = await self._client().send(request, stream=False)
            body_bytes = await response.aread()
            if response.status_code < 200 or response.status_code >= 300:
                detail = self._error_detail(response, body_bytes)
                raise ValueError(f"账号连通性验证失败（HTTP {response.status_code}）：{detail}")
            model_count = 0
            try:
                payload = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
                models = payload.get("data") if isinstance(payload, dict) else None
                model_count = len(models) if isinstance(models, list) else 0
            except (UnicodeDecodeError, ValueError):
                model_count = 0
            return {"provider": provider, "credentials": credentials, "model_count": model_count}
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ValueError(f"账号连通性验证失败：{exc}") from exc
        finally:
            if response is not None and not response.is_closed:
                await response.aclose()

    async def test_account(self, account_id: int) -> dict[str, Any]:
        fresh = await self.accounts.refresh_account(account_id, force=False)
        account = fresh["account"]
        credentials = fresh["credentials"]
        provider = str(account["provider"])
        if provider == "openai":
            url = f"{OPENAI_MODELS_URL}?client_version={self.codex_client_version}"
            headers = self._openai_headers(credentials, account, False)
        elif provider == "claude":
            url = CLAUDE_MODELS_URL
            headers = self._claude_headers(credentials, {}, False)
        else:
            base_url = provider_base_url(provider)
            url = base_url + "/models"
            headers = self._grok_headers(credentials, False) if provider == "grok" else self._compatible_headers(credentials, False)
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
