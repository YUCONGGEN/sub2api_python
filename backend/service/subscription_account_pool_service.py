"""SpringBootAI-managed weighted round-robin scheduler for account pools."""

from __future__ import annotations

import threading
from typing import Any

from springbootai import Service, Slf4j


@Service("subscription_account_pool_service")
@Slf4j
class SubscriptionAccountPoolService:
    """Select one unique account using smooth weighted round robin.

    Pool state is isolated by provider, model, and priority. The caller keeps
    priority semantics and session affinity, while this service distributes
    new sessions across healthy accounts in the same priority tier.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current_weights: dict[tuple[str, str, int], dict[int, int]] = {}

    def select(
        self,
        rows: list[dict[str, Any]],
        provider: str,
        model: str,
        priority: int,
    ) -> dict[str, Any]:
        if not rows:
            raise LookupError("账号池中没有可轮询的账号")
        candidates = sorted(rows, key=lambda row: int(row.get("id") or 0))
        key = (str(provider), str(model), int(priority))
        with self._lock:
            if len(self._current_weights) > 2048 and key not in self._current_weights:
                self._current_weights.clear()
            scores = self._current_weights.setdefault(key, {})
            active_ids = {int(row.get("id") or 0) for row in candidates}
            for stale_id in set(scores) - active_ids:
                scores.pop(stale_id, None)

            total_weight = 0
            selected = candidates[0]
            selected_score: int | None = None
            for row in candidates:
                account_id = int(row.get("id") or 0)
                weight = max(1, min(100, int(row.get("weight") or 1)))
                total_weight += weight
                scores[account_id] = scores.get(account_id, 0) + weight
                score = scores[account_id]
                if selected_score is None or score > selected_score:
                    selected = row
                    selected_score = score
            selected_id = int(selected.get("id") or 0)
            scores[selected_id] = scores.get(selected_id, 0) - total_weight
            return selected

    def forget(self, account_id: int) -> None:
        """Remove a deleted account from every local scheduler state."""
        target = int(account_id)
        with self._lock:
            for scores in self._current_weights.values():
                scores.pop(target, None)


__all__ = ["SubscriptionAccountPoolService"]
