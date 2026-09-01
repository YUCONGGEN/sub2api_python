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

    def insert_account(self, account: Mapping[str, Any]) -> int:
        pass

    def update_account(self, account_id: int, changes: Mapping[str, Any]) -> int:
        pass

    def update_credentials(self, account_id: int, credentials_encrypted: str, email: str, account_ref: str, expires_at: str | None, updated_at: str) -> int:
        pass

    def mark_account_result(self, account_id: int, status: str, error_count: int, last_error: str, cooldown_until: str | None, last_used_at: str | None, updated_at: str) -> int:
        pass

    def delete_account(self, account_id: int) -> int:
        pass


__all__ = ["SubscriptionMapper"]
