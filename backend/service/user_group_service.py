"""User-group model permissions and per-member concurrency accounting."""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from springbootai import Service

from backend.common.reasoning import requested_reasoning_effort


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

    @staticmethod
    def preview_model_mapping(user: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
        outgoing, matched = UserGroupService.apply_model_mapping(user, payload)
        model = str(payload.get("model") or "").strip()
        effort = str(requested_reasoning_effort(dict(payload)) or "").strip().lower()
        rules = user.get("group_model_mappings") or []
        details = []
        for rule in rules:
            source = str(rule.get("source_model") or "").strip().lower()
            expected = str(rule.get("source_effort") or "").strip().lower()
            if not rule.get("enabled"):
                reason = "规则已停用"
            elif source and source != model.lower():
                reason = "请求模型不匹配"
            elif expected not in ("*", effort):
                reason = "只匹配未指定强度的请求" if not expected else f"只匹配 {expected} 强度"
            elif matched and rule.get("id") == matched.get("id"):
                reason = "已命中"
            else:
                reason = "匹配，但更具体的规则优先"
            details.append({"id": rule.get("id"), "name": rule.get("name"), "reason": reason})
        allowed_models = UserGroupService.allowed_models(user)
        allowed = "*" in allowed_models or model in allowed_models
        return {
            "group_id": user.get("effective_group_id"), "group_name": user.get("group_name"),
            "group_source": user.get("group_source"), "allowed": allowed,
            "requested_model": model, "requested_effort": effort,
            "mapped_model": outgoing.get("model"),
            "mapped_effort": requested_reasoning_effort(outgoing),
            "mapping_id": matched.get("id") if matched else None,
            "mapping_name": matched.get("name") if matched else None,
            "reason": "用户组不允许请求此模型" if not allowed else (
                "已命中映射" if matched else "没有命中的规则，将保留请求模型与强度"),
            "rules": details,
        }

    @staticmethod
    def apply_model_mapping(user: Mapping[str, Any], payload: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """Apply the effective group's most specific model/effort mapping.

        Permission checks intentionally happen before this method, against the
        client-facing model. Billing and upstream selection then use the mapped
        model because it is the model actually called. An empty source model
        matches every requested model, while an empty target model preserves
        the requested model.
        """
        outgoing = dict(payload)
        source_model = str(payload.get("model") or "").strip()
        raw_effort = requested_reasoning_effort(dict(payload))
        source_effort = "" if raw_effort is None else str(raw_effort).strip().lower()
        mappings = user.get("group_model_mappings")
        if not source_model or not isinstance(mappings, list):
            return outgoing, None
        candidates = [
            item for item in mappings
            if isinstance(item, Mapping)
            and bool(item.get("enabled"))
            and str(item.get("source_model") or "").strip().lower() in {"", source_model.lower()}
            and str(item.get("source_effort") or "").strip().lower() in {source_effort, "*"}
        ]
        if not candidates:
            return outgoing, None
        candidates.sort(key=lambda item: (
            0 if str(item.get("source_model") or "").strip() else 1,
            0 if str(item.get("source_effort") or "").strip().lower() == source_effort else 1,
        ))
        mapping = dict(candidates[0])
        outgoing["model"] = str(mapping.get("target_model") or source_model).strip()
        target_effort = str(mapping.get("target_effort") or "").strip().lower()
        reasoning = outgoing.get("reasoning")
        outgoing["reasoning"] = {**(reasoning if isinstance(reasoning, dict) else {}), "effort": target_effort}
        outgoing.pop("reasoning_effort", None)
        outgoing.pop("reasoning-effort", None)
        return outgoing, mapping

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
