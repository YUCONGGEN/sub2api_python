"""Persistence contract for the proxy database.

Only method signatures live here.  The corresponding XML mapper contains all
SQL, following the structure of ``E:\\work819\\welding_app``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from springbootai.orm import Mapper


@Mapper
class StoreMapper:
    # users
    def find_user(self, user_id: int) -> Mapping[str, Any] | None:
        pass

    def find_user_by_username(self, username: str) -> Mapping[str, Any] | None:
        pass

    def find_user_by_api_key(self, api_key_hash: str) -> Mapping[str, Any] | None:
        pass

    def touch_api_key(self, api_key_hash: str, last_used: str) -> int:
        pass

    def insert_user(self, user: Mapping[str, Any]) -> int:
        pass

    def update_login(self, user_id: int, last_login: str) -> int:
        pass

    def update_password(self, user_id: int, password_hash: str) -> int:
        pass

    def update_user(self, user_id: int, changes: Mapping[str, Any]) -> int:
        pass

    def update_user_api_key(self, user_id: int, api_key: str) -> int:
        pass

    def count_users(self, keyword: str = "") -> int:
        pass

    def count_active_users(self) -> int:
        pass

    def find_recharge_codes_without_expiry(self) -> list[dict]:
        pass

    def set_recharge_code_expiry(self, code_id: int, expires_at: str) -> int:
        pass

    def revoke_legacy_user_keys(self) -> int:
        pass

    def list_users(self, keyword: str = "", offset: int = 0, limit: int = 5) -> list[dict]:
        pass

    def soft_delete_user(self, user_id: int, anonymized_username: str, password_hash: str, revoked_key: str, deleted_at: str) -> int:
        pass

    def disable_user_entitlements(self, user_id: int, updated_at: str) -> int:
        pass

    def cancel_user_entitlements(self, user_id: int) -> int:
        pass

    def revoke_user_sessions(self, user_id: int, revoked_at: str) -> int:
        pass

    def count_user_usage(self, user_id: int, start_at: str | None = None) -> Mapping[str, Any]:
        pass

    def user_usage_daily(self, user_id: int, start_at: str | None = None) -> list[dict]:
        pass

    def user_usage_by_model(self, user_id: int, start_at: str) -> list[dict]:
        """Aggregate a user's usage by model from a timestamp onward."""
        pass

    def list_user_orders(self, user_id: int, offset: int = 0, limit: int = 5) -> list[dict]:
        pass

    def count_user_orders(self, user_id: int) -> int:
        pass

    # subscription plans, user subscriptions and free quota policies
    def count_subscription_plans(self, enabled_only: int = 0) -> int:
        pass

    def list_subscription_plans(self, enabled_only: int = 0, offset: int = 0, limit: int = 5) -> list[dict]:
        pass

    def find_subscription_plan(self, plan_id: int) -> Mapping[str, Any] | None:
        pass

    def insert_subscription_plan(self, plan: Mapping[str, Any]) -> int:
        pass

    def update_subscription_plan(self, plan_id: int, changes: Mapping[str, Any]) -> int:
        pass

    def delete_subscription_plan(self, plan_id: int, updated_at: str) -> int:
        pass

    def list_user_subscriptions(self, user_id: int, offset: int = 0, limit: int = 5) -> list[dict]:
        pass

    def count_user_subscriptions(self, user_id: int) -> int:
        pass

    def list_user_entitlements(self, user_id: int, now: str, offset: int = 0, limit: int = 5) -> list[dict]:
        """List paid plans and free grants in one consistently ordered page."""
        pass

    def find_user_subscription(self, subscription_id: int, user_id: int | None = None) -> Mapping[str, Any] | None:
        pass

    def find_active_subscriptions(self, user_id: int, now: str) -> list[dict]:
        pass

    def find_active_subscription_for_plan(self, user_id: int, plan_id: int, now: str) -> Mapping[str, Any] | None:
        pass

    def insert_user_subscription(self, subscription: Mapping[str, Any]) -> int:
        pass

    def extend_user_subscription(self, subscription_id: int, user_id: int, plan_id: int, ends_at: str) -> int:
        pass

    def cancel_user_subscription(self, subscription_id: int, user_id: int) -> int:
        pass

    def set_subscription_auto_renew(self, subscription_id: int, user_id: int, enabled: int) -> int:
        pass

    def update_user_subscription(self, subscription_id: int, user_id: int, changes: Mapping[str, Any]) -> int:
        pass

    def delete_user_subscription(self, subscription_id: int, user_id: int) -> int:
        pass

    def list_due_auto_renew_subscriptions(self, now: str) -> list[dict]:
        pass

    def expire_subscriptions(self, now: str) -> int:
        pass

    def expire_subscription(self, subscription_id: int) -> int:
        pass

    def list_user_quota_policies(self, user_id: int, offset: int = 0, limit: int = 5) -> list[dict]:
        pass

    def count_user_quota_policies(self, user_id: int) -> int:
        pass

    def find_active_quota_policies(self, user_id: int, now: str) -> list[dict]:
        pass

    def insert_user_quota_policy(self, quota: Mapping[str, Any]) -> int:
        pass

    def update_user_quota_policy(self, quota_id: int, user_id: int, changes: Mapping[str, Any]) -> int:
        pass

    def delete_user_quota_policy(self, quota_id: int, user_id: int) -> int:
        pass

    def quota_usage_totals(self, user_id: int, quota_id: int, start_at: str, end_at: str | None = None) -> Mapping[str, Any]:
        pass

    def subscription_usage_totals(self, subscription_id: int, start_at: str, end_at: str | None = None) -> Mapping[str, Any]:
        pass

    def has_active_entitlement(self, user_id: int, now: str) -> int:
        pass

    # API keys
    def count_api_keys(self, user_id: int) -> int:
        pass

    def list_api_keys(self, user_id: int, offset: int = 0, limit: int = 5) -> list[dict]:
        pass

    def insert_api_key(self, key: Mapping[str, Any]) -> int:
        pass

    def find_api_key(self, user_id: int, key_id: int) -> Mapping[str, Any] | None:
        pass

    def revoke_api_key(self, user_id: int, key_id: int) -> int:
        pass

    # atomic billing and split entitlement audit
    def ensure_billing_lock(self, user_id: int, updated_at: str) -> int:
        pass

    def acquire_billing_lock(self, user_id: int, updated_at: str) -> int:
        pass

    def insert_usage_allocation(self, allocation: Mapping[str, Any]) -> int:
        pass

    # login sessions
    def insert_user_session(self, session: Mapping[str, Any]) -> int:
        pass

    def find_user_session(self, user_id: int, session_id: str) -> Mapping[str, Any] | None:
        pass

    def list_user_sessions(self, user_id: int, current_session_id: str, limit: int, offset: int) -> list[dict]:
        pass

    def count_active_user_sessions(self, user_id: int) -> int:
        pass

    def touch_user_session(self, user_id: int, session_id: str, last_seen_at: str) -> int:
        pass

    def revoke_user_session(self, user_id: int, session_id: str, revoked_at: str) -> int:
        pass

    def revoke_other_user_sessions(self, user_id: int, session_id: str, revoked_at: str) -> int:
        pass

    # recharge orders and codes
    def insert_order(self, order: Mapping[str, Any]) -> int:
        pass

    def find_order_by_id(self, order_id: int) -> Mapping[str, Any] | None:
        pass

    def find_order(self, trade_no: str, user_id: int | None = None) -> Mapping[str, Any] | None:
        pass

    def count_orders(self, user_id: int) -> int:
        pass

    def list_orders(self, user_id: int, offset: int = 0, limit: int = 5) -> list[dict]:
        pass

    def update_order_qr(self, trade_no: str, qr_code: str) -> int:
        pass

    def mark_order_paid(self, trade_no: str, paid_at: str) -> int:
        pass

    def cancel_order(self, trade_no: str, user_id: int) -> int:
        pass

    def insert_recharge_code(self, code: Mapping[str, Any]) -> int:
        pass

    def find_recharge_code(self, code_hash: str) -> Mapping[str, Any] | None:
        pass

    def count_recharge_codes(self) -> int:
        pass

    def list_recharge_codes(self, offset: int = 0, limit: int = 5) -> list[dict]:
        pass

    def expire_recharge_codes(self, now: str) -> int:
        pass

    def redeem_recharge_code(self, code_id: int, user_id: int, redeemed_at: str) -> int:
        pass

    def revoke_recharge_code(self, code_id: int) -> int:
        pass

    # Personal WeChat callback matching
    def find_pending_order_by_amount(self, provider: str, payment_amount: float) -> Mapping[str, Any] | None:
        pass

    def count_pending_amount(self, provider: str, payment_amount: float) -> int:
        pass

    def list_pending_orders_by_amount(self, payment_amount: float) -> list[dict]:
        pass

    def has_qr_asset(self, filename: str) -> int:
        pass

    def find_callback(self, callback_hash: str) -> Mapping[str, Any] | None:
        pass

    def insert_callback(self, callback: Mapping[str, Any]) -> int:
        pass

    def settle_order(self, order_id: int, paid_at: str) -> int:
        pass

    # usage / billing
    def find_balance(self, user_id: int) -> Mapping[str, Any] | None:
        pass

    def insert_usage(self, usage: Mapping[str, Any]) -> int:
        pass

    def find_usage(self, usage_id: int) -> Mapping[str, Any] | None:
        pass

    def count_usage(self, user_id: int) -> int:
        pass

    def list_usage(self, user_id: int, offset: int = 0, limit: int = 5) -> list[dict]:
        pass

    def list_usage_export(self, start_at: str = "", end_at: str = "", user_id: int | None = None, model: str = "", status: str = "", limit: int = 50000) -> list[dict]:
        pass

    def user_usage_totals(self, user_id: int) -> Mapping[str, Any]:
        pass

    def admin_totals(self) -> Mapping[str, Any]:
        pass

    def admin_model_usage(self, start_at: str) -> list[dict]:
        pass

    def update_balance_after_charge(self, user_id: int, cost: float, minimum_balance: float, minimum_usable: float) -> int:
        pass

    def credit_balance(self, user_id: int, amount: float) -> int:
        pass

    def sum_paid_orders(self) -> float:
        pass

    def count_active_recharge_codes(self) -> int:
        pass

    # admin analytics and observability
    def admin_daily_usage(self, start_at: str, limit: int = 30) -> list[dict]:
        pass

    def admin_daily_activity(self, start_at: str, limit: int = 30) -> list[dict]:
        pass

    def admin_user_usage(self, limit: int = 10) -> list[dict]:
        pass

    def admin_today_user_usage(self, start_at: str, limit: int = 10) -> list[dict]:
        pass

    def admin_status_usage(self) -> list[dict]:
        pass

    def admin_billing_usage(self) -> list[dict]:
        pass

    def admin_order_status(self) -> list[dict]:
        pass

    def insert_admin_event(self, event: Mapping[str, Any]) -> int:
        pass

    def count_admin_events(self, level: str | None = None) -> int:
        pass

    def list_admin_events(self, level: str | None = None, offset: int = 0, limit: int = 5) -> list[dict]:
        pass

    def clear_admin_events(self) -> int:
        """Remove transient warning/error records when the service starts."""
        pass

    # listener state
    def find_listener_status(self) -> Mapping[str, Any] | None:
        pass

    def upsert_listener_status(self, available: int, reason: str, updated_at: str) -> int:
        pass

    def mark_listener_alert(self, value: str | None) -> int:
        pass

    # conversation records
    def insert_conversation(self, conversation: Mapping[str, Any]) -> int:
        pass

    def insert_conversation_asset(self, asset: Mapping[str, Any]) -> int:
        pass

    def find_conversation(self, conversation_id: int, user_id: int | None = None) -> Mapping[str, Any] | None:
        pass

    def count_conversations(self, user_id: int | None = None) -> int:
        pass

    def list_conversations(self, user_id: int | None = None, offset: int = 0, limit: int = 5) -> list[dict]:
        pass

    def conversation_totals(self) -> Mapping[str, Any]:
        pass

    def conversation_model_totals(self) -> list[dict]:
        pass

    def clear_conversation_content(self) -> int:
        """Erase prompt/response columns retained for legacy schema compatibility."""
        pass

    def delete_conversation_assets(self) -> int:
        """Delete legacy multimodal asset metadata; new requests never insert it."""
        pass

    def update_conversation(self, conversation_id: int, changes: Mapping[str, Any]) -> int:
        pass

    def delete_conversations_before(self, created_before: str) -> int:
        pass

    def list_usage_for_conversation_backfill(self) -> list[dict]:
        pass


__all__ = ["StoreMapper"]
