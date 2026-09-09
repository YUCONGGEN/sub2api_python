"""User-group model permissions and per-member concurrency accounting."""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from springbootai import Service


_runtime: "UserGroupService | None" = None


def user_group_runtime() -> "UserGroupService | None":
    return _runtime


@dataclass
class UserConcurrencyLease:
    service: "UserGroupService"
    user_id: int
    released: bool = False

    def release(self) -> None:
        if self.released:
            return
        self.released = True
        self.service.release(self.user_id)


@Service("user_group_service")
class UserGroupService:
    """Apply the current database-backed group policy to one authenticated user.

    The counter is intentionally per user, not shared by all group members.
    Uvicorn currently runs one application process, so the in-memory counter
    covers every request handled by this deployment without database polling
    during an active stream.
    """

    def __init__(self):
        global _runtime
        self._lock = threading.Lock()
        self._active_by_user: dict[int, int] = {}
        self._waiters_by_user: dict[int, deque[dict[str, Any]]] = {}
        self._queue_waiting = 0
        self._queue_total = 0
        self._queue_rejected = 0
        self.max_queued_requests = 200
        self._sequence = 0
        _runtime = self

    @staticmethod
    def concurrency_limit(user: Mapping[str, Any]) -> int:
        try:
            return max(1, min(100, int(user.get("group_concurrency_limit") or 1)))
        except (TypeError, ValueError):
            return 1

    @staticmethod
    def allowed_models(user: Mapping[str, Any]) -> list[str]:
        value = user.get("group_allowed_models")
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if str(item or "").strip()]

    def model_allowed(self, user: Mapping[str, Any], model: str) -> bool:
        allowed = self.allowed_models(user)
        return "*" in allowed or str(model or "").strip() in allowed

    def filter_catalog(self, user: Mapping[str, Any], catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [item for item in catalog if self.model_allowed(user, str(item.get("id") or ""))]

    async def acquire(self, user: Mapping[str, Any]) -> UserConcurrencyLease:
        user_id = int(user["id"])
        limit = self.concurrency_limit(user)
        loop = asyncio.get_running_loop()
        waiter: dict[str, Any] | None = None
        with self._lock:
            current = int(self._active_by_user.get(user_id, 0))
            queued = self._waiters_by_user.get(user_id)
            if current < limit and not queued:
                self._active_by_user[user_id] = current + 1
                return UserConcurrencyLease(self, user_id)
            if self._queue_waiting >= self.max_queued_requests:
                self._queue_rejected += 1
                raise OverflowError("user group queue is full")
            self._sequence += 1
            waiter = {
                "request_id": f"group-{self._sequence}",
                "user_id": user_id,
                "username": str(user.get("username") or f"用户 #{user_id}"),
                "group_id": user.get("effective_group_id") or user.get("group_id"),
                "group_name": str(user.get("group_name") or "未分组"),
                "group_weight": int(user.get("group_weight") or 0),
                "group_source": str(user.get("group_source") or "ASSIGNED"),
                "concurrency_limit": limit,
                "provider": "用户组并发",
                "model": "等待用户并发名额",
                "reasoning_effort": "",
                "account_id": 0,
                "queued_at": datetime.now(timezone.utc).isoformat(),
                "future": loop.create_future(),
                "loop": loop,
            }
            self._waiters_by_user.setdefault(user_id, deque()).append(waiter)
            self._queue_waiting += 1
            self._queue_total += 1
        try:
            await waiter["future"]
            return UserConcurrencyLease(self, user_id)
        except asyncio.CancelledError:
            admitted = False
            with self._lock:
                queue = self._waiters_by_user.get(user_id)
                if queue and waiter in queue:
                    queue.remove(waiter)
                    self._queue_waiting = max(0, self._queue_waiting - 1)
                    if not queue:
                        self._waiters_by_user.pop(user_id, None)
                else:
                    # release() may already have reserved the slot and queued
                    # the wake-up on this loop when the client disconnects.
                    admitted = True
            if admitted:
                self.release(user_id)
            raise

    def release(self, user_id: int) -> None:
        wake: dict[str, Any] | None = None
        with self._lock:
            current = int(self._active_by_user.get(int(user_id), 0))
            if current <= 1:
                self._active_by_user.pop(int(user_id), None)
            else:
                self._active_by_user[int(user_id)] = current - 1
            queue = self._waiters_by_user.get(int(user_id))
            if queue:
                wake = queue.popleft()
                self._queue_waiting = max(0, self._queue_waiting - 1)
                self._active_by_user[int(user_id)] = int(self._active_by_user.get(int(user_id), 0)) + 1
                if not queue:
                    self._waiters_by_user.pop(int(user_id), None)
        if wake:
            def notify_waiter():
                # Cancellation may happen after release schedules this callback.
                # Check on the owning event loop, not before scheduling it.
                if not wake["future"].done():
                    wake["future"].set_result(None)

            wake["loop"].call_soon_threadsafe(notify_waiter)

    def active_for_user(self, user_id: int) -> int:
        with self._lock:
            return int(self._active_by_user.get(int(user_id), 0))

    def queue_metrics(self, include_users: bool = False) -> dict[str, Any]:
        with self._lock:
            snapshot = {
                "queue_waiting": self._queue_waiting,
                "queue_limit": self.max_queued_requests,
                "queued_total": self._queue_total,
                "queue_rejected": self._queue_rejected,
            }
            if include_users:
                counts = {user_id: len(queue) for user_id, queue in self._waiters_by_user.items()}
                snapshot["queued_users"] = [
                    {
                        **{key: value for key, value in waiter.items() if key not in {"future", "loop"}},
                        "user_concurrent_tasks": int(self._active_by_user.get(user_id, 0)),
                        "user_queued_tasks": counts.get(user_id, 0),
                    }
                    for user_id, queue in self._waiters_by_user.items()
                    for waiter in queue
                ]
        return snapshot


__all__ = ["UserGroupService", "UserConcurrencyLease", "user_group_runtime"]
