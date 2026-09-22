"""PyMyBatis contract for encrypted upstream subscription accounts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from springbootai.orm import Mapper


@Mapper
class SubscriptionMapper:
    def count_accounts(self, provider: str = "") -> int:
        pass

    def list_accounts(self, provider: str = "", offset: int = 0, limit: int = 20) -> list[dict]:
        pass

    def find_account(self, account_id: int) -> Mapping[str, Any] | None:
        pass

    def list_provider_accounts(self, provider: str) -> list[dict]:
        pass

    def list_system_disabled_accounts(self) -> list[dict]:
        pass

    def insert_account(self, account: Mapping[str, Any]) -> int:
        pass

    def update_account(self, account_id: int, changes: Mapping[str, Any]) -> int:
        pass

    def update_credentials(self, account_id: int, credentials_encrypted: str, email: str, account_ref: str, expires_at: str | None, updated_at: str) -> int:
        pass

    def mark_account_result(self, account_id: int, status: str, error_count: int, last_error: str, cooldown_until: str | None, last_used_at: str | None, updated_at: str) -> int:
        pass

    def disable_rate_limited_account(self, account_id: int, error_count: int, last_error: str, last_used_at: str, updated_at: str) -> int:
        pass

    def enable_system_recovered_account(self, account_id: int, updated_at: str) -> int:
        pass

    def delete_account(self, account_id: int) -> int:
        pass

    def count_config_requests(self, user_id: int = 0, status: str = "") -> int:
        pass

    def list_config_requests(self, user_id: int = 0, status: str = "", offset: int = 0, limit: int = 20) -> list[dict]:
        pass

    def find_config_request(self, request_id: int) -> Mapping[str, Any] | None:
        pass

    def insert_config_request(self, request: Mapping[str, Any]) -> int:
        pass

    def update_config_request(self, request_id: int, status: str, admin_note: str, updated_at: str) -> int:
        pass


__all__ = ["SubscriptionMapper"]
