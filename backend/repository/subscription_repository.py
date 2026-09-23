"""Repository boundary for upstream subscription account persistence."""

from __future__ import annotations

from typing import Any, Mapping

from springbootai import Autowired, Repository

from backend.common.subscription_providers import SUBSCRIPTION_PROVIDERS


@Repository("subscription_repository")
class SubscriptionRepository:
    @Autowired
    def __init__(self, subscription_mapper):
        self.mapper = subscription_mapper

    @staticmethod
    def _row(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
        return dict(value) if value is not None else None

    def list_page(self, provider: str, page: int, page_size: int) -> dict[str, Any]:
        page = max(1, int(page))
        page_size = max(1, min(100, int(page_size)))
        total = int(self.mapper.count_accounts(str(provider or "")) or 0)
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        rows = self.mapper.list_accounts(str(provider or ""), (page - 1) * page_size, page_size)
        return {
            "items": [dict(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": pages,
            "summary": {
                "total": int(self.mapper.count_accounts("") or 0),
                **{
                    item: int(self.mapper.count_accounts(item) or 0)
                    for item in SUBSCRIPTION_PROVIDERS
                },
            },
        }

    def find(self, account_id: int) -> dict[str, Any] | None:
        return self._row(self.mapper.find_account(int(account_id)))

    def list_provider(self, provider: str) -> list[dict[str, Any]]:
        return [dict(row) for row in self.mapper.list_provider_accounts(str(provider))]

    def list_system_disabled_accounts(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.mapper.list_system_disabled_accounts()]

    def create(self, account: dict[str, Any]) -> dict[str, Any]:
        self.mapper.insert_account(account)
        return self.find(int(account["id"])) or account

    def update(self, account_id: int, changes: dict[str, Any]) -> dict[str, Any] | None:
        if not self.mapper.update_account(int(account_id), changes):
            return None
        return self.find(int(account_id))

    def update_credentials(self, account_id: int, *, credentials_encrypted: str, email: str, account_ref: str, expires_at: str | None, updated_at: str) -> dict[str, Any] | None:
        if not self.mapper.update_credentials(int(account_id), credentials_encrypted, email, account_ref, expires_at, updated_at):
            return None
        return self.find(int(account_id))

    def mark_result(self, account_id: int, *, status: str, error_count: int, last_error: str, cooldown_until: str | None, last_used_at: str | None, updated_at: str) -> None:
        self.mapper.mark_account_result(int(account_id), status, int(error_count), str(last_error), cooldown_until, last_used_at, updated_at)

    def disable_rate_limited(self, account_id: int, *, error_count: int, last_error: str, recovery_next_at: str, last_used_at: str, updated_at: str) -> bool:
        return bool(self.mapper.disable_rate_limited_account(
            int(account_id), int(error_count), str(last_error), recovery_next_at, last_used_at, updated_at,
        ))

    def enable_system_recovered(self, account_id: int, *, updated_at: str) -> bool:
        return bool(self.mapper.enable_system_recovered_account(int(account_id), updated_at))

    def update_low_quota_recovery(self, account_id: int, *, attempts: int, next_at: str | None, updated_at: str) -> bool:
        return bool(self.mapper.update_low_quota_recovery(int(account_id), int(attempts), next_at, updated_at))

    def delete(self, account_id: int) -> bool:
        return bool(self.mapper.delete_account(int(account_id)))

    def list_config_requests(self, user_id: int, status: str, page: int, page_size: int) -> dict[str, Any]:
        page = max(1, int(page))
        page_size = max(1, min(100, int(page_size)))
        total = int(self.mapper.count_config_requests(int(user_id), str(status or "")) or 0)
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        rows = self.mapper.list_config_requests(int(user_id), str(status or ""), (page - 1) * page_size, page_size)
        return {"items": [dict(row) for row in rows], "total": total, "page": page, "page_size": page_size, "pages": pages}

    def find_config_request(self, request_id: int) -> dict[str, Any] | None:
        return self._row(self.mapper.find_config_request(int(request_id)))

    def create_config_request(self, request: dict[str, Any]) -> dict[str, Any]:
        self.mapper.insert_config_request(request)
        return self.find_config_request(int(request["id"])) or request

    def update_config_request(self, request_id: int, status: str, admin_note: str, updated_at: str) -> dict[str, Any] | None:
        if not self.mapper.update_config_request(int(request_id), str(status), str(admin_note), str(updated_at)):
            return None
        return self.find_config_request(int(request_id))


__all__ = ["SubscriptionRepository"]
