"""Application service for user, billing, quota and usage business rules."""


import hashlib
import json
import math
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

import bcrypt
from springbootai import Autowired, Service, Scheduled, Transactional, get_config

try:
    from pymysql.err import IntegrityError as MySQLIntegrityError
except ImportError:  # SQLite-only installations do not need the optional import.
    MySQLIntegrityError = type("MySQLIntegrityError", (Exception,), {})

from backend.repository.store import StoreRepository
from backend.common.time_utils import business_date_keys, business_day_start_utc, business_month_start_utc, business_week_start_utc


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp used consistently by business records."""
    return datetime.now(timezone.utc).isoformat()


@Service("store_service")
class StoreService:
    """Orchestrate business rules and delegate SQL execution to the Mapper."""

    @Autowired
    def __init__(self, repository: StoreRepository):
        self.repository = repository
        self.mapper = repository.mapper
        # Group mappings change only through the admin service. Cache the
        # small rule lists so authenticated proxy requests do not add a SQL
        # query to the hot forwarding path.
        self._group_mapping_cache: dict[int, list[dict[str, Any]]] = {}
    @staticmethod
    def hash_password(password: str) -> str:
        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    @staticmethod
    def verify_password(password: str, password_hash: str) -> bool:
        try:
            return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
        except (ValueError, TypeError):
            return False

    @staticmethod
    def new_api_key() -> str:
        """Create a user-managed API key using the configured public prefix.

        API key prefixes are intentionally independent from recharge-code
        prefixes.  ``rose.billing.recharge-code-prefix`` controls vouchers,
        while this setting controls credentials created in the API key page.
        """
        security = get_config().get("rose", {}).get("security", {})
        configured = str(security.get("api-key-prefix", "sk-api-")).strip()
        # Keep the prefix safe for headers, URLs, and the database.  Permit
        # separators commonly used in key prefixes but reject whitespace or
        # punctuation that could make a credential ambiguous.
        prefix = re.sub(r"[^A-Za-z0-9_-]+", "", configured) or "sk-api-"
        if not prefix.endswith(("-", "_")):
            prefix += "-"
        return prefix + secrets.token_urlsafe(28)

    @staticmethod
    def revoked_api_key() -> str:
        return "revoked-" + secrets.token_urlsafe(28)
    @staticmethod
    def _row(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
        return dict(value) if value is not None else None

    @staticmethod
    def page_result(items: list[Mapping[str, Any]], total: int, page: int, page_size: int) -> dict[str, Any]:
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 5))
        total = max(0, int(total))
        pages = max(1, (total + page_size - 1) // page_size)
        return {
            "items": [dict(item) for item in items],
            "total": total,
            "page": min(page, pages),
            "page_size": page_size,
            "pages": pages,
        }

    @staticmethod
    def page_window(total: int, page: int = 1, page_size: int = 5) -> tuple[int, int, int, int]:
        """Clamp a page before querying so response metadata matches its rows."""
        total = max(0, int(total))
        page_size = max(1, min(int(page_size), 5))
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(max(1, int(page)), pages)
        return page, page_size, pages, (page - 1) * page_size

    @staticmethod
    def public_user(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        data = dict(row)
        data.pop("password_hash", None)
        data.pop("api_key", None)
        data.pop("session_version", None)
        data.pop("deleted_at", None)
        return data

    @staticmethod
    def _normalize_allowed_models(value: Any) -> list[str]:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError):
                value = [value]
        if not isinstance(value, (list, tuple, set)):
            value = []
        models: list[str] = []
        for item in value:
            model = str(item or "").strip()
            if model and model not in models:
                models.append(model[:160])
        return models

    def _public_group(self, row: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        data = dict(row)
        data["allowed_models"] = self._normalize_allowed_models(data.pop("allowed_models_json", "[]"))
        data["weight"] = max(0, min(10000, int(data.get("weight") or 0)))
        data["concurrency_limit"] = max(1, int(data.get("concurrency_limit") or 1))
        data["is_default"] = bool(data.get("is_default"))
        if "member_count" in data:
            data["member_count"] = int(data.get("member_count") or 0)
        if "plan_count" in data:
            data["plan_count"] = int(data.get("plan_count") or 0)
        group_id = int(data.get("id") or 0)
        mapping_loader = getattr(self.mapper, "list_group_model_mappings", None)
        cache = getattr(self, "_group_mapping_cache", {})
        if group_id and group_id not in cache:
            cache[group_id] = [dict(item) for item in (mapping_loader(group_id) if mapping_loader else [])]
            self._group_mapping_cache = cache
        mapping_rows = cache.get(group_id, [])
        data["model_mappings"] = [self._public_model_mapping(item) for item in (mapping_rows or [])]
        data["model_mapping_ids"] = [int(item["id"]) for item in data["model_mappings"]]
        return data

    @staticmethod
    def _public_model_mapping(row: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(row)
        data["id"] = int(data.get("id") or 0)
        data["enabled"] = bool(data.get("enabled"))
        return data

    def _with_group(self, row: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        data = dict(row)
        assigned_group = self._public_group(self.mapper.find_user_group(int(data.get("group_id") or 0)))
        if not assigned_group:
            assigned_group = self._public_group(self.mapper.find_default_user_group())
        effective_group = assigned_group
        source = "ASSIGNED"
        source_plan: dict[str, Any] | None = None
        candidate_loader = getattr(self.mapper, "list_active_subscription_group_candidates", None)
        candidate_rows = candidate_loader(int(data["id"]), utc_now()) if candidate_loader else []
        candidates = [
            self._public_group(value)
            for value in (candidate_rows or [])
        ]
        candidates = [value for value in candidates if value]
        if candidates and (not effective_group or int(candidates[0].get("weight") or 0) > int(effective_group.get("weight") or 0)):
            effective_group = candidates[0]
            source = "SUBSCRIPTION"
            source_plan = candidates[0]
        if effective_group:
            data.update({
                "assigned_group_id": assigned_group["id"] if assigned_group else None,
                "assigned_group_name": assigned_group["name"] if assigned_group else "未分组",
                "assigned_group_weight": assigned_group["weight"] if assigned_group else 0,
                "effective_group_id": effective_group["id"],
                "group_name": effective_group["name"],
                "group_weight": effective_group["weight"],
                "group_concurrency_limit": effective_group["concurrency_limit"],
                "group_allowed_models": effective_group["allowed_models"],
                "group_model_mappings": effective_group["model_mappings"],
                "group_source": source,
                "group_source_plan_id": source_plan.get("plan_id") if source_plan else None,
                "group_source_plan_name": source_plan.get("plan_name") if source_plan else None,
                "group_source_subscription_id": source_plan.get("subscription_id") if source_plan else None,
                "group_source_ends_at": source_plan.get("subscription_ends_at") if source_plan else None,
                "group_upgrade_candidates": [
                    {
                        "group_id": item.get("id"), "group_name": item.get("name"), "weight": item.get("weight"),
                        "plan_id": item.get("plan_id"), "plan_name": item.get("plan_name"),
                        "subscription_id": item.get("subscription_id"), "ends_at": item.get("subscription_ends_at"),
                    }
                    for item in candidates
                ],
            })
        else:
            data.update({"assigned_group_id": None, "assigned_group_name": "未分组", "effective_group_id": None, "group_name": "未分组", "group_weight": 0, "group_concurrency_limit": 1, "group_allowed_models": [], "group_model_mappings": [], "group_source": "ASSIGNED", "group_upgrade_candidates": []})
        return data

    # ------------------------------------------------------------------ users
    def find_user(self, user_id: int) -> dict[str, Any] | None:
        return self._with_group(self.mapper.find_user(int(user_id)))

    def find_by_username(self, username: str) -> dict[str, Any] | None:
        return self._with_group(self.mapper.find_user_by_username(str(username)))

    def find_by_api_key(self, api_key: str) -> dict[str, Any] | None:
        digest = hashlib.sha256(str(api_key).encode("utf-8")).hexdigest()
        row = self._with_group(self.mapper.find_user_by_api_key(digest))
        if row:
            now = datetime.now(timezone.utc)
            # last_used is informational. Coalesce it to one write per minute
            # instead of writing twice for every proxied request.
            self.mapper.touch_api_key(
                digest,
                now.isoformat(),
                (now - timedelta(minutes=1)).isoformat(),
            )
        return row

    def create_user(
        self,
        username: str,
        password: str,
        email: str = "",
        role: str = "USER",
        balance: float = 0,
        enabled: bool = True,
        group_id: int | None = None,
    ) -> dict[str, Any]:
        if group_id is None:
            group = self.mapper.find_named_user_group("管理员组") if str(role).upper() == "ADMIN" else self.mapper.find_default_user_group()
        else:
            group = self.mapper.find_user_group(int(group_id))
        if not group:
            raise ValueError("用户组不存在")
        user = {
            "username": str(username),
            "password_hash": self.hash_password(password),
            "email": str(email),
            "role": str(role),
            "balance": max(-0.1, float(balance)),
            "enabled": 1 if enabled else 0,
            "api_key": self.revoked_api_key(),
            "created_at": utc_now(),
            "group_id": int(group["id"]),
        }
        self.mapper.insert_user(user)
        return self.find_by_username(str(username)) or user

    @Transactional()
    def create_user_with_default_key(self, *args, **kwargs) -> tuple[dict[str, Any], dict[str, Any]]:
        """Create an account and reveal its first key exactly once to the caller."""
        user = self.create_user(*args, **kwargs)
        key = self.create_api_key(int(user["id"]), "默认密钥")
        return user, key

    def update_login(self, user_id: int) -> None:
        self.mapper.update_login(int(user_id), utc_now())

    def update_password(self, user_id: int, password_hash: str) -> None:
        self.mapper.update_password(int(user_id), str(password_hash))

    def usage_by_model_since(self, user_id: int, start_at: str) -> list[dict[str, Any]]:
        return [dict(row) for row in self.mapper.user_usage_by_model(int(user_id), str(start_at))]

    def list_users(self, keyword: str = "", page: int = 1, page_size: int = 5) -> dict[str, Any]:
        keyword = str(keyword or "")
        total = self.mapper.count_users(keyword)
        page, page_size, _, offset = self.page_window(total, page, page_size)
        rows = [self._with_group(row) for row in self.mapper.list_users(keyword, offset, page_size)]
        return self.page_result(rows, total, page, page_size)

    # ----------------------------------------------------------- user groups
    def find_user_group(self, group_id: int) -> dict[str, Any] | None:
        return self._public_group(self.mapper.find_user_group(int(group_id)))

    def list_user_groups(self, page: int = 1, page_size: int = 5) -> dict[str, Any]:
        total = int(self.mapper.count_user_groups() or 0)
        page, page_size, _, offset = self.page_window(total, page, page_size)
        rows = [self._public_group(row) for row in self.mapper.list_user_groups(offset, page_size)]
        return self.page_result(rows, total, page, page_size)

    def admin_group_detail(self, group_id: int, page: int = 1, page_size: int = 5) -> dict[str, Any] | None:
        group = self.find_user_group(group_id)
        if not group:
            return None
        # Current assigned members' lifetime usage, not historical group attribution.
        totals = dict(self.mapper.user_group_totals(int(group_id)))
        total = int(totals.get("member_count") or 0)
        page, page_size, _, offset = self.page_window(total, page, page_size)
        fields = ("id", "username", "email", "role", "enabled", "last_login", "requests", "total_cost", "total_tokens")
        members = [{key: row.get(key) for key in fields}
                   for row in self.mapper.user_group_members(int(group_id), offset, page_size)]
        return {"group": group, "totals": totals,
                "members": self.page_result(members, total, page, page_size)}

    @staticmethod
    def _normalize_mapping_ids(value: Any) -> list[int]:
        if not isinstance(value, (list, tuple, set)):
            return []
        result: list[int] = []
        for item in value:
            try:
                mapping_id = int(item)
            except (TypeError, ValueError):
                raise ValueError("模型映射编号必须是整数")
            if mapping_id > 0 and mapping_id not in result:
                result.append(mapping_id)
        return result

    @staticmethod
    def _group_values(values: Mapping[str, Any], *, partial: bool = False) -> dict[str, Any]:
        changes: dict[str, Any] = {}
        if not partial or "name" in values:
            name = str(values.get("name") or "").strip()
            if not 2 <= len(name) <= 120:
                raise ValueError("用户组名称需为 2-120 个字符")
            changes["name"] = name
        if not partial or "description" in values:
            changes["description"] = str(values.get("description") or "").strip()[:500]
        if not partial or "weight" in values:
            try:
                weight = int(values.get("weight", 0))
            except (TypeError, ValueError) as exc:
                raise ValueError("分组权重必须是整数") from exc
            if weight < 0 or weight > 10000:
                raise ValueError("分组权重必须在 0-10000 之间")
            changes["weight"] = weight
        if not partial or "concurrency_limit" in values:
            try:
                limit = int(values.get("concurrency_limit", 1))
            except (TypeError, ValueError) as exc:
                raise ValueError("并发数必须是整数") from exc
            if limit < 1 or limit > 100:
                raise ValueError("并发数必须在 1-100 之间")
            changes["concurrency_limit"] = limit
        if not partial or "allowed_models" in values:
            models = StoreService._normalize_allowed_models(values.get("allowed_models", ["*"]))
            changes["allowed_models_json"] = json.dumps(models, ensure_ascii=False, separators=(",", ":"))
        if not partial or "is_default" in values:
            changes["is_default"] = 1 if bool(values.get("is_default")) else 0
        if not partial or "model_mapping_ids" in values:
            changes["model_mapping_ids"] = StoreService._normalize_mapping_ids(values.get("model_mapping_ids", []))
        return changes

    def _validate_mapping_ids(self, mapping_ids: list[int]) -> None:
        for mapping_id in mapping_ids:
            if not self.mapper.find_model_mapping(int(mapping_id)):
                raise ValueError(f"模型映射 #{mapping_id} 不存在")

    def _replace_group_model_mappings(self, group_id: int, mapping_ids: list[int]) -> None:
        self.mapper.delete_group_model_mappings(int(group_id))
        for mapping_id in mapping_ids:
            self.mapper.insert_group_model_mapping(int(group_id), int(mapping_id))
        self._group_mapping_cache.pop(int(group_id), None)

    @Transactional()
    def create_user_group(self, values: Mapping[str, Any]) -> dict[str, Any]:
        group = self._group_values(values)
        mapping_ids = group.pop("model_mapping_ids", [])
        self._validate_mapping_ids(mapping_ids)
        if self.mapper.find_user_group_by_weight(int(group["weight"]), None):
            raise ValueError("分组权重已被使用，请设置不同的权重")
        now = utc_now()
        group.update({"created_at": now, "updated_at": now})
        self.mapper.insert_user_group(group)
        group_id = int(group.get("id") or 0)
        self._replace_group_model_mappings(group_id, mapping_ids)
        if group.get("is_default"):
            self.mapper.clear_default_user_groups(group_id, now)
        return self.find_user_group(group_id) or self._public_group(group)

    @Transactional()
    def update_user_group(self, group_id: int, values: Mapping[str, Any]) -> dict[str, Any] | None:
        existing = self.mapper.find_user_group(int(group_id))
        if not existing:
            return None
        changes = self._group_values(values, partial=True)
        if not changes:
            return self._public_group(existing)
        mapping_ids = changes.pop("model_mapping_ids", None)
        if mapping_ids is not None:
            self._validate_mapping_ids(mapping_ids)
        if existing.get("is_default") and changes.get("is_default") == 0:
            raise ValueError("默认用户组不能直接取消默认，请将其他组设为默认")
        if "weight" in changes and self.mapper.find_user_group_by_weight(int(changes["weight"]), int(group_id)):
            raise ValueError("分组权重已被使用，请设置不同的权重")
        changes["updated_at"] = utc_now()
        if changes.get("is_default"):
            self.mapper.clear_default_user_groups(int(group_id), changes["updated_at"])
        self.mapper.update_user_group(int(group_id), changes)
        if mapping_ids is not None:
            self._replace_group_model_mappings(int(group_id), mapping_ids)
        return self.find_user_group(int(group_id))

    @Transactional()
    def delete_user_group(self, group_id: int) -> bool:
        group = self.mapper.find_user_group(int(group_id))
        if not group:
            return False
        if group.get("is_default"):
            raise ValueError("默认用户组不能删除")
        default_group = self.mapper.find_default_user_group()
        if not default_group:
            raise ValueError("系统缺少默认用户组")
        self.mapper.assign_users_to_group(int(group_id), int(default_group["id"]))
        self.mapper.clear_subscription_plan_group(int(group_id), utc_now())
        self.mapper.delete_group_model_mappings(int(group_id))
        self._group_mapping_cache.pop(int(group_id), None)
        return bool(self.mapper.delete_user_group(int(group_id)))

    # ---------------------------------------------------------- model mappings
    @staticmethod
    def _model_mapping_values(values: Mapping[str, Any], *, partial: bool = False) -> dict[str, Any]:
        efforts = {"", "*", "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}
        changes: dict[str, Any] = {}
        if not partial or "name" in values:
            name = str(values.get("name") or "").strip()
            if not 2 <= len(name) <= 120:
                raise ValueError("映射名称需为 2-120 个字符")
            changes["name"] = name
        if not partial or "source_model" in values:
            model = str(values.get("source_model") or "").strip()
            if not model or len(model) > 160:
                raise ValueError("请求模型不能为空且不能超过 160 个字符")
            changes["source_model"] = model
        if not partial or "source_effort" in values:
            effort = str(values.get("source_effort") or "").strip().lower()
            if effort not in efforts:
                raise ValueError("请求推理强度无效")
            changes["source_effort"] = effort
        if not partial or "target_model" in values:
            model = str(values.get("target_model") or "").strip()
            if not model or len(model) > 160:
                raise ValueError("目标模型不能为空且不能超过 160 个字符")
            changes["target_model"] = model
        if not partial or "target_effort" in values:
            effort = str(values.get("target_effort") or "").strip().lower()
            if not effort or effort == "*" or effort not in efforts:
                raise ValueError("目标推理强度无效")
            changes["target_effort"] = effort
        if not partial or "enabled" in values:
            changes["enabled"] = 1 if bool(values.get("enabled", True)) else 0
        return changes

    def list_model_mappings(self) -> list[dict[str, Any]]:
        return [self._public_model_mapping(row) for row in self.mapper.list_model_mappings()]

    @Transactional()
    def create_model_mapping(self, values: Mapping[str, Any]) -> dict[str, Any]:
        mapping = self._model_mapping_values(values)
        if self.mapper.find_model_mapping_by_source(mapping["source_model"], mapping["source_effort"], None):
            raise ValueError("相同请求模型和推理强度的映射已存在")
        now = utc_now()
        mapping.update({"created_at": now, "updated_at": now})
        self.mapper.insert_model_mapping(mapping)
        self._group_mapping_cache.clear()
        return self._public_model_mapping(self.mapper.find_model_mapping(int(mapping.get("id") or 0)) or mapping)

    @Transactional()
    def update_model_mapping(self, mapping_id: int, values: Mapping[str, Any]) -> dict[str, Any] | None:
        existing = self.mapper.find_model_mapping(int(mapping_id))
        if not existing:
            return None
        changes = self._model_mapping_values(values, partial=True)
        source_model = changes.get("source_model", existing.get("source_model"))
        source_effort = changes.get("source_effort", existing.get("source_effort"))
        if self.mapper.find_model_mapping_by_source(source_model, source_effort, int(mapping_id)):
            raise ValueError("相同请求模型和推理强度的映射已存在")
        if not changes:
            return self._public_model_mapping(existing)
        changes["updated_at"] = utc_now()
        self.mapper.update_model_mapping(int(mapping_id), changes)
        self._group_mapping_cache.clear()
        return self._public_model_mapping(self.mapper.find_model_mapping(int(mapping_id)))

    @Transactional()
    def delete_model_mapping(self, mapping_id: int) -> bool:
        if not self.mapper.find_model_mapping(int(mapping_id)):
            return False
        self.mapper.delete_model_mapping_assignments(int(mapping_id))
        deleted = bool(self.mapper.delete_model_mapping(int(mapping_id)))
        self._group_mapping_cache.clear()
        return deleted

    def admin_user_detail(self, user_id: int, order_page: int = 1, page_size: int = 5) -> dict[str, Any] | None:
        user = self.find_user(user_id)
        if not user:
            return None
        now = datetime.now(timezone.utc)
        day_start = business_day_start_utc(now).isoformat()
        week_start = business_week_start_utc(now).isoformat()
        month_start = business_month_start_utc(now).isoformat()
        chart_start = business_day_start_utc(now - timedelta(days=13)).isoformat()
        chart_rows = {}
        for value in self.mapper.user_usage_daily(user_id, chart_start):
            row = dict(value)
            chart_rows[str(row.get("day"))] = row
        chart = []
        for day in business_date_keys(14, now):
            chart.append({"day": day, "tokens": 0, "cost": 0, "requests": 0, **chart_rows.get(day, {})})
        usage = {
            "today": dict(self.mapper.count_user_usage(user_id, day_start)),
            "week": dict(self.mapper.count_user_usage(user_id, week_start)),
            "month": dict(self.mapper.count_user_usage(user_id, month_start)),
            "total": dict(self.mapper.count_user_usage(user_id, None)),
            "chart": chart,
        }
        orders = self.list_orders(user_id, order_page, page_size)
        recent_usage = self.list_usage_page(user_id, 1, 5)
        # Four wider cards per page keep the admin entitlement view readable.
        subscriptions = self.list_user_subscriptions(user_id, 1, 4)
        quotas = self.list_user_quotas(user_id, 1, 4)
        return {
            "user": self.public_user(user),
            "usage": usage,
            "recent_usage": recent_usage["items"],
            "recent_usage_pagination": recent_usage,
            "orders": orders["items"],
            "orders_pagination": orders,
            "subscriptions": subscriptions["items"],
            "subscriptions_pagination": subscriptions,
            "quotas": quotas["items"],
            "quotas_pagination": quotas,
        }

    @Transactional()
    def delete_user(self, user_id: int) -> bool:
        user_id = int(user_id)
        if not self.mapper.find_user(user_id):
            return False
        deleted_at = utc_now()
        self.mapper.revoke_user_sessions(user_id, deleted_at)
        self.mapper.disable_user_entitlements(user_id, deleted_at)
        self.mapper.cancel_user_entitlements(user_id)
        for key in self.mapper.list_api_keys(user_id, 0, 10000) or []:
            self.mapper.revoke_api_key(user_id, int(key["id"]))
        # Keep usage, orders, subscriptions and voucher ownership for audit.
        return bool(self.mapper.soft_delete_user(
            user_id,
            f"deleted-{user_id}-{secrets.token_hex(6)}",
            self.hash_password(secrets.token_urlsafe(48)),
            self.revoked_api_key(),
            deleted_at,
        ))

    def update_user(self, user_id: int, **changes: Any) -> dict[str, Any] | None:
        allowed = {"email", "role", "balance", "enabled", "password", "group_id"}
        values = {key: value for key, value in changes.items() if key in allowed and value is not None}
        if "balance" in values:
            try:
                balance = float(values["balance"])
            except (TypeError, ValueError):
                raise ValueError("余额必须是数字")
            if not math.isfinite(balance) or balance < -0.1 or balance > 1_000_000_000:
                raise ValueError("余额必须在 -0.1 到 1000000000 元之间")
            values["balance"] = balance
        if "password" in values:
            values["password_hash"] = self.hash_password(str(values.pop("password")))
        if "group_id" in values:
            try:
                group_id = int(values["group_id"])
            except (TypeError, ValueError) as exc:
                raise ValueError("用户组不正确") from exc
            if not self.mapper.find_user_group(group_id):
                raise ValueError("用户组不存在")
            values["group_id"] = group_id
        if values:
            self.mapper.update_user(int(user_id), values)
        return self.public_user(self.find_user(int(user_id)))

    def update_api_key(self, user_id: int, api_key: str) -> dict[str, Any] | None:
        self.mapper.update_user_api_key(int(user_id), str(api_key))
        return self.find_user(int(user_id))

    # -------------------------------------------------------------- api keys
    def list_api_keys(self, user_id: int, page: int = 1, page_size: int = 5) -> dict[str, Any]:
        total = self.mapper.count_api_keys(int(user_id))
        page, page_size, _, offset = self.page_window(total, page, page_size)
        rows = []
        for value in self.mapper.list_api_keys(int(user_id), offset, page_size):
            row = dict(value)
            row["masked_key"] = f'{row.get("key_prefix") or "sk-"}{"•" * 12}{row.get("key_last4") or ""}'
            rows.append(row)
        return self.page_result(rows, total, page, page_size)

    def create_api_key(self, user_id: int, name: str, expires_at: str | None = None) -> dict[str, Any]:
        normalized_expiry = None
        if expires_at:
            try:
                parsed_expiry = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            except (TypeError, ValueError) as exc:
                raise ValueError("过期时间格式不正确") from exc
            if parsed_expiry.tzinfo is None:
                parsed_expiry = parsed_expiry.replace(tzinfo=timezone.utc)
            parsed_expiry = parsed_expiry.astimezone(timezone.utc)
            if parsed_expiry <= datetime.now(timezone.utc):
                raise ValueError("过期时间必须晚于当前时间")
            normalized_expiry = parsed_expiry.isoformat()
        plaintext = self.new_api_key()
        digest = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
        key = {
            "user_id": int(user_id),
            "name": str(name)[:64] or "未命名密钥",
            "api_key_hash": digest,
            "storage_key": "hashed-" + digest,
            "key_prefix": plaintext[:12],
            "key_last4": plaintext[-4:],
            "enabled": 1,
            "created_at": utc_now(),
            "last_used": None,
            "expires_at": normalized_expiry,
        }
        self.mapper.insert_api_key(key)
        public = self._row(self.mapper.find_api_key(int(user_id), int(key.get("id") or 0))) or key
        public["api_key"] = plaintext
        public["masked_key"] = f'{plaintext[:12]}{"•" * 12}{plaintext[-4:]}'
        public["reveal_once"] = True
        return public

    def revoke_api_key(self, user_id: int, key_id: int) -> dict[str, Any] | None:
        if not self.mapper.revoke_api_key(int(user_id), int(key_id)):
            return None
        return self._row(self.mapper.find_api_key(int(user_id), int(key_id)))

    def delete_api_key(self, user_id: int, key_id: int) -> bool:
        # Keep audit history: the legacy DELETE endpoint behaves as revocation.
        return bool(self.mapper.revoke_api_key(int(user_id), int(key_id)))

    # -------------------------------------------------------------- sessions
    def create_session(self, user_id: int, user_agent: str = "", ip_address: str = "") -> dict[str, Any]:
        now = utc_now()
        session = {
            "id": secrets.token_urlsafe(24),
            "user_id": int(user_id),
            "user_agent": str(user_agent or "")[:500],
            "ip_address": str(ip_address or "")[:128],
            "created_at": now,
            "last_seen_at": now,
        }
        self.mapper.insert_user_session(session)
        return session

    def find_session(self, user_id: int, session_id: str) -> dict[str, Any] | None:
        return self._row(self.mapper.find_user_session(int(user_id), str(session_id)))

    def list_sessions(self, user_id: int, current_session_id: str = "", page: int = 1, page_size: int = 5) -> dict[str, Any]:
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 20))
        total = int(self.mapper.count_active_user_sessions(int(user_id)) or 0)
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        rows = []
        for value in self.mapper.list_user_sessions(int(user_id), str(current_session_id), page_size, (page - 1) * page_size) or []:
            row = dict(value)
            row["current"] = bool(current_session_id and row.get("id") == current_session_id)
            rows.append(row)
        return {
            "sessions": rows,
            "pagination": {"page": page, "page_size": page_size, "total": total, "pages": pages},
        }

    def touch_session(self, user_id: int, session_id: str) -> None:
        self.mapper.touch_user_session(int(user_id), str(session_id), utc_now())

    def revoke_session(self, user_id: int, session_id: str) -> bool:
        return bool(self.mapper.revoke_user_session(int(user_id), str(session_id), utc_now()))

    def revoke_other_sessions(self, user_id: int, session_id: str) -> int:
        return int(self.mapper.revoke_other_user_sessions(int(user_id), str(session_id), utc_now()) or 0)

    # --------------------------------------------------------------- orders
    def create_order(
        self,
        user_id: int,
        provider: str,
        amount: float,
        payment_amount: float | None = None,
        qr_asset: str | None = None,
    ) -> dict[str, Any]:
        trade_no = "ROSE" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + secrets.token_hex(4).upper()
        order = {
            "user_id": int(user_id),
            "provider": str(provider),
            "trade_no": trade_no,
            "amount": round(float(amount), 2),
            "credits": round(float(amount), 2),
            "status": "PENDING",
            "qr_code": f"rose://pay/{str(provider).lower()}/{trade_no}",
            "created_at": utc_now(),
            "paid_at": None,
            "payment_amount": payment_amount,
            "qr_asset": qr_asset,
        }
        self.mapper.insert_order(order)
        return self._row(self.mapper.find_order_by_id(int(order.get("id") or 0))) or order

    def create_recharge_code(
        self,
        amount: float,
        created_by: int,
        code: str,
        expire_hours: float | int | None = None,
    ) -> dict[str, Any]:
        billing = get_config().get("rose", {}).get("billing", {})
        configured_expiry = expire_hours is None
        raw_expire_hours = billing.get("recharge-code-expire-hours", 48) if configured_expiry else expire_hours
        try:
            expire_hours = float(raw_expire_hours)
        except (TypeError, ValueError) as exc:
            if not configured_expiry:
                raise ValueError("兑换码有效期必须是数字") from exc
            expire_hours = 48
        if not math.isfinite(expire_hours):
            if not configured_expiry:
                raise ValueError("兑换码有效期必须是有限数字")
            expire_hours = 48
        if expire_hours < 1 or expire_hours > 24 * 365:
            raise ValueError("兑换码有效期必须在 1 小时到 365 天之间")
        created_at = datetime.now(timezone.utc)
        row = {
            "code_hash": self.hash_recharge_code(code),
            "code": str(code),
            "amount": round(float(amount), 2),
            "created_by": int(created_by),
            "created_at": created_at.isoformat(),
            "expires_at": (created_at + timedelta(hours=expire_hours)).isoformat(),
            "redeemed_by": None,
            "redeemed_at": None,
            "status": "ACTIVE",
        }
        self.mapper.insert_recharge_code(row)
        return self._row(self.mapper.find_recharge_code(row["code_hash"])) or row

    @staticmethod
    def hash_recharge_code(code: str) -> str:
        return hashlib.sha256(code.strip().upper().encode("utf-8")).hexdigest()

    def list_recharge_codes(self, page: int = 1, page_size: int = 5) -> dict[str, Any]:
        self.mapper.expire_recharge_codes(utc_now())
        total = self.mapper.count_recharge_codes()
        page, page_size, _, offset = self.page_window(total, page, page_size)
        rows = self.mapper.list_recharge_codes(offset, page_size)
        return self.page_result(rows, total, page, page_size)

    @Transactional()
    def redeem_recharge_code(self, code: str, user_id: int) -> dict[str, Any] | None:
        self.mapper.expire_recharge_codes(utc_now())
        row = self._row(self.mapper.find_recharge_code(self.hash_recharge_code(code)))
        if not row or row.get("status") != "ACTIVE":
            return None
        now = utc_now()
        if self.mapper.redeem_recharge_code(int(row["id"]), int(user_id), now) != 1:
            return None
        amount = float(row["amount"])
        self.mapper.credit_balance(int(user_id), amount)
        trade_no = "CODE" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + secrets.token_hex(4).upper()
        self.mapper.insert_order({
            "user_id": int(user_id),
            "provider": "CODE",
            "trade_no": trade_no,
            "amount": amount,
            "credits": amount,
            "status": "PAID",
            "qr_code": "recharge-code",
            "created_at": now,
            "paid_at": now,
            "payment_amount": None,
            "qr_asset": None,
        })
        return {"trade_no": trade_no, "amount": amount, "status": "PAID", "paid_at": now}

    def find_pending_personal_order(self, payment_amount: float, provider: str = "WECHAT") -> dict[str, Any] | None:
        normalized = "WECHAT_PERSONAL" if str(provider).upper() in {"WECHAT", "WECHAT_PERSONAL"} else str(provider).upper()
        return self._row(self.mapper.find_pending_order_by_amount(normalized, round(float(payment_amount), 2)))

    def count_pending_payment_amount(self, provider: str, payment_amount: float) -> int:
        normalized = "WECHAT_PERSONAL" if str(provider).upper() in {"WECHAT", "WECHAT_PERSONAL"} else str(provider).upper()
        return int(self.mapper.count_pending_amount(normalized, round(float(payment_amount), 2)))

    @Transactional()
    def create_personal_order(self, user_id: int, amount: float, candidates: list[tuple[float, str]]) -> dict[str, Any] | None:
        for payment_amount, qr_asset in candidates:
            value = round(float(payment_amount), 2)
            if self.mapper.count_pending_amount("WECHAT_PERSONAL", value):
                continue
            try:
                return self.create_order(user_id, "WECHAT_PERSONAL", amount, value, qr_asset)
            except (sqlite3.IntegrityError, MySQLIntegrityError):
                continue
        return None

    def find_personal_orders_by_amount(self, payment_amount: float) -> list[dict[str, Any]]:
        return [dict(row) for row in self.mapper.list_pending_orders_by_amount(round(float(payment_amount), 2))]

    def has_qr_asset(self, filename: str) -> bool:
        return bool(self.mapper.has_qr_asset(str(filename)))

    @Transactional()
    def settle_personal_callback(
        self,
        payment_amount: float,
        callback_hash: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        value = round(float(payment_amount), 2)
        duplicate = self._row(self.mapper.find_callback(str(callback_hash)))
        if duplicate:
            return {"status": "DUPLICATE", "trade_no": duplicate.get("trade_no")}
        rows = self.find_personal_orders_by_amount(value)
        if not rows:
            return {"status": "NOT_FOUND"}
        if len(rows) != 1:
            return {"status": "AMBIGUOUS", "count": len(rows)}
        order = rows[0]
        now = utc_now()
        if self.mapper.settle_order(int(order["id"]), now) != 1:
            return {"status": "NOT_FOUND"}
        self.mapper.credit_balance(int(order["user_id"]), float(order["credits"]))
        self.mapper.insert_callback({
            "callback_hash": str(callback_hash),
            "provider": "WECHAT_PERSONAL",
            "payment_amount": value,
            "trade_no": order["trade_no"],
            "received_at": now,
        })
        return {
            "status": "PAID",
            "trade_no": order["trade_no"],
            "amount": float(order["amount"]),
            "paid_at": now,
        }

    def revoke_recharge_code(self, code_id: int) -> bool:
        self.mapper.expire_recharge_codes(utc_now())
        return bool(self.mapper.revoke_recharge_code(int(code_id)))

    def list_orders(self, user_id: int, page: int = 1, page_size: int = 5) -> dict[str, Any]:
        total = self.mapper.count_orders(int(user_id))
        page, page_size, _, offset = self.page_window(total, page, page_size)
        rows = self.mapper.list_orders(int(user_id), offset, page_size)
        return self.page_result(rows, total, page, page_size)

    # --------------------------------------------------------- subscriptions
    @staticmethod
    def _optional_float(value: Any, default: float = 0.0) -> float:
        """Treat omitted/blank numeric form fields as their configured zero."""
        if value is None or (isinstance(value, str) and not value.strip()):
            return default
        return float(value)

    @staticmethod
    def _optional_int(value: Any, default: int = 0) -> int:
        if value is None or (isinstance(value, str) and not value.strip()):
            return default
        return int(value)

    def list_subscription_plans(self, page: int = 1, page_size: int = 5, enabled_only: bool = False) -> dict[str, Any]:
        visible = 1 if enabled_only else 0
        total = self.mapper.count_subscription_plans(visible)
        page, page_size, _, offset = self.page_window(total, page, page_size)
        rows = self.mapper.list_subscription_plans(visible, offset, page_size)
        return self.page_result(rows, total, page, page_size)

    def create_subscription_plan(self, values: Mapping[str, Any]) -> dict[str, Any]:
        now = utc_now()
        price = self._optional_float(values.get("price"), 0.0)
        daily_amount = self._optional_float(values.get("daily_amount"), 0.0)
        if not math.isfinite(price) or price < 0 or not math.isfinite(daily_amount) or daily_amount < 0:
            raise ValueError("套餐金额必须是非负数字")
        plan = {
            "name": str(values.get("name", "")).strip()[:80],
            "description": str(values.get("description", "")).strip()[:500],
            "price": round(price, 2),
            "duration_days": max(1, self._optional_int(values.get("duration_days"), 30)),
            "daily_amount": round(daily_amount, 4),
            "daily_tokens": max(0, self._optional_int(values.get("daily_tokens"), 0)),
            "group_id": None,
            "enabled": 1 if bool(values.get("enabled", True)) else 0,
            "created_at": now,
            "updated_at": now,
        }
        if not plan["name"]:
            raise ValueError("套餐名称不能为空")
        raw_group_id = values.get("group_id")
        if raw_group_id not in (None, ""):
            group = self.mapper.find_user_group(int(raw_group_id))
            if not group:
                raise ValueError("套餐指定的用户组不存在")
            plan["group_id"] = int(group["id"])
        self.mapper.insert_subscription_plan(plan)
        return self._row(self.mapper.find_subscription_plan(int(plan.get("id") or 0))) or plan

    @Transactional()
    def update_subscription_plan(self, plan_id: int, changes: Mapping[str, Any]) -> dict[str, Any] | None:
        if not self.mapper.find_subscription_plan(int(plan_id)):
            return None
        values = {key: value for key, value in changes.items() if key in {"name", "description", "price", "duration_days", "daily_amount", "daily_tokens", "enabled"}}
        if "group_id" in changes:
            raw_group_id = changes.get("group_id")
            if raw_group_id in (None, ""):
                values["clear_group_id"] = 1
            else:
                group = self.mapper.find_user_group(int(raw_group_id))
                if not group:
                    raise ValueError("套餐指定的用户组不存在")
                values["group_id"] = int(group["id"])
        if "name" in values:
            values["name"] = str(values["name"]).strip()[:80]
        for key in ("price", "daily_amount"):
            if key in values:
                value = self._optional_float(values[key], 0.0)
                if not math.isfinite(value) or value < 0:
                    raise ValueError("套餐金额必须是非负数字")
                values[key] = max(0.0, round(value, 4 if key == "daily_amount" else 2))
        for key in ("duration_days", "daily_tokens"):
            if key in values:
                values[key] = max(0, self._optional_int(values[key], 30 if key == "duration_days" else 0))
        if "duration_days" in values:
            values["duration_days"] = max(1, values["duration_days"])
        if "enabled" in values:
            values["enabled"] = 1 if bool(values["enabled"]) else 0
        values["updated_at"] = utc_now()
        if values:
            self.mapper.update_subscription_plan(int(plan_id), values)
            if values.get("enabled") == 0:
                # Taking a plan off sale only stops the next purchase/renewal.
                # Existing subscribers retain the current paid period.
                self.mapper.disable_plan_subscription_auto_renew(int(plan_id))
        return self._row(self.mapper.find_subscription_plan(int(plan_id)))

    @Transactional()
    def delete_subscription_plan(self, plan_id: int) -> bool:
        # Plans are immutable audit references once assigned. "Delete" means
        # disable, so historical subscriptions remain visible and billable
        # records never lose their plan name.
        changed = bool(self.mapper.delete_subscription_plan(int(plan_id), utc_now()))
        if changed:
            self.mapper.disable_plan_subscription_auto_renew(int(plan_id))
        return changed

    @Transactional()
    def permanently_delete_subscription_plan(self, plan_id: int) -> bool:
        plan_id = int(plan_id)
        if not self.mapper.find_subscription_plan(plan_id):
            return False
        subscription_count = int(self.mapper.count_subscriptions_for_plan(plan_id) or 0)
        if subscription_count:
            raise ValueError(
                f"该套餐已有 {subscription_count} 条用户订阅记录，不能永久删除；请改为停用套餐"
            )
        deleted = bool(self.mapper.permanently_delete_subscription_plan(plan_id))
        if not deleted and int(self.mapper.count_subscriptions_for_plan(plan_id) or 0):
            raise ValueError("该套餐刚产生用户订阅，不能永久删除；请改为停用套餐")
        return deleted

    @staticmethod
    def _entitlement_active(row: Mapping[str, Any], now: datetime, kind: str) -> bool:
        """Return whether an entitlement can actually pay for a request now."""
        if kind == "SUBSCRIPTION":
            # ``subscription_plans.enabled`` controls whether new purchases
            # and renewals are offered. It must not revoke a paid entitlement
            # that is still ACTIVE and inside its purchased time window.
            if str(row.get("status") or "").upper() != "ACTIVE":
                return False
        elif not bool(row.get("enabled")):
            return False
        try:
            starts_at = StoreService._subscription_datetime(row.get("starts_at"), "权益开始时间")
            ends_value = row.get("ends_at")
            ends_at = StoreService._subscription_datetime(ends_value, "权益结束时间") if ends_value else None
        except ValueError:
            return False
        return starts_at <= now and (ends_at is None or ends_at > now)

    @staticmethod
    def _usage_dimension(limit: float | int, used: float | int, active: bool, *, money: bool = False) -> dict[str, Any]:
        """Describe a limit without using Infinity, which is invalid JSON."""
        unlimited = float(limit or 0) <= 0
        if money:
            normalized_limit: float | int = round(max(0.0, float(limit or 0)), 4)
            normalized_used: float | int = round(max(0.0, float(used or 0)), 4)
            remaining = None if active and unlimited else round(
                max(0.0, float(normalized_limit) - float(normalized_used)) if active else 0.0, 4
            )
        else:
            normalized_limit = max(0, int(limit or 0))
            normalized_used = max(0, int(used or 0))
            remaining = None if active and unlimited else (
                max(0, int(normalized_limit) - int(normalized_used)) if active else 0
            )
        return {
            "limit": normalized_limit,
            "used": normalized_used,
            "remaining": remaining,
            "unlimited": unlimited,
        }

    def _subscription_with_usage(self, value: Mapping[str, Any], now: datetime) -> dict[str, Any]:
        row = dict(value)
        active = self._entitlement_active(row, now, "SUBSCRIPTION")
        day_start = business_day_start_utc(now).isoformat()
        totals = dict(self.mapper.subscription_usage_totals(int(row.get("id") or 0), day_start, None))
        row["usage"] = {
            "active": active,
            "as_of": now.isoformat(),
            "period": "ASIA_SHANGHAI_DAY",
            "daily_amount": self._usage_dimension(
                row.get("daily_amount") or 0, totals.get("subscription_cost") or 0, active, money=True
            ),
            "daily_tokens": self._usage_dimension(
                row.get("daily_tokens") or 0, totals.get("subscription_tokens") or 0, active
            ),
        }
        return row

    def _quota_with_usage(self, user_id: int, value: Mapping[str, Any], now: datetime) -> dict[str, Any]:
        row = dict(value)
        active = self._entitlement_active(row, now, "FREE")
        quota_id = int(row.get("id") or 0)
        day_start = business_day_start_utc(now).isoformat()
        daily = dict(self.mapper.quota_usage_totals(int(user_id), quota_id, day_start, None))
        try:
            window_hours = max(0.1, min(168.0, float(row.get("hourly_window_hours") or 1)))
        except (TypeError, ValueError):
            window_hours = 1.0
        window_start = (now - timedelta(hours=window_hours)).isoformat()
        window = dict(self.mapper.quota_usage_totals(int(user_id), quota_id, window_start, None))
        row["usage"] = {
            "active": active,
            "as_of": now.isoformat(),
            "period": "ASIA_SHANGHAI_DAY",
            "daily_amount": self._usage_dimension(
                row.get("daily_amount") or 0, daily.get("free_cost") or 0, active, money=True
            ),
            "daily_tokens": self._usage_dimension(
                row.get("daily_tokens") or 0, daily.get("free_tokens") or 0, active
            ),
            "window_tokens": {
                **self._usage_dimension(
                    row.get("hourly_tokens") or 0, window.get("free_tokens") or 0, active
                ),
                "window_hours": window_hours,
            },
        }
        return row

    def list_user_subscriptions(self, user_id: int, page: int = 1, page_size: int = 5) -> dict[str, Any]:
        total = self.mapper.count_user_subscriptions(int(user_id))
        page, page_size, _, offset = self.page_window(total, page, page_size)
        rows = self.mapper.list_user_subscriptions(int(user_id), offset, page_size)
        now = datetime.now(timezone.utc)
        enriched = [self._subscription_with_usage(row, now) for row in rows]
        return self.page_result(enriched, total, page, page_size)

    def list_user_entitlements(self, user_id: int, page: int = 1, page_size: int = 5) -> dict[str, Any]:
        """Return paid plans and free grants in one active-first page."""
        user_id = int(user_id)
        total = self.mapper.count_user_subscriptions(user_id) + self.mapper.count_user_quota_policies(user_id)
        page, page_size, _, offset = self.page_window(total, page, page_size)
        now = datetime.now(timezone.utc)
        rows = self.mapper.list_user_entitlements(
            user_id, now.isoformat(), offset, page_size
        )
        enriched = []
        for value in rows:
            row = dict(value)
            row.pop("sort_group", None)
            if row.get("entitlement_type") == "FREE":
                enriched.append(self._quota_with_usage(user_id, row, now))
            else:
                enriched.append(self._subscription_with_usage(row, now))
        return self.page_result(enriched, total, page, page_size)

    @staticmethod
    def _subscription_datetime(value: Any, field: str = "套餐时间") -> datetime:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field}格式不正确") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _subscription_duration(plan: Mapping[str, Any]) -> timedelta:
        return timedelta(days=max(1, int(plan.get("duration_days") or 30)))

    def _charge_subscription(self, user_id: int, price: float) -> None:
        billing = get_config().get("rose", {}).get("billing", {})
        minimum_balance = max(-0.1, float(billing.get("minimum-balance", -0.1)))
        if price and self.mapper.update_balance_after_charge(int(user_id), price, minimum_balance, 0.001) != 1:
            raise ValueError("余额不足，无法续订该套餐")

    def _lock_user_billing(self, user_id: int, now: datetime | None = None) -> None:
        """Serialize subscription and usage balance decisions for one user."""
        if not hasattr(self.mapper, "ensure_billing_lock"):
            return
        timestamp = (now or datetime.now(timezone.utc)).isoformat()
        self.mapper.ensure_billing_lock(int(user_id), timestamp)
        self.mapper.acquire_billing_lock(int(user_id), timestamp)

    @Transactional()
    def subscribe_plan(self, user_id: int, plan_id: int) -> dict[str, Any] | None:
        plan = self._row(self.mapper.find_subscription_plan(int(plan_id)))
        if not plan or not plan.get("enabled"):
            return None
        starts = datetime.now(timezone.utc)
        self._lock_user_billing(int(user_id), starts)
        current = self._row(self.mapper.find_active_subscription_for_plan(
            int(user_id), int(plan_id), starts.isoformat()
        ))
        if current:
            raise ValueError("该套餐已订阅，请在当前权益中点击“续订”")
        price = max(0.0, float(plan.get("price") or 0))
        self._charge_subscription(user_id, price)
        ends = starts + self._subscription_duration(plan)
        subscription = {
            "user_id": int(user_id), "plan_id": int(plan_id), "starts_at": starts.isoformat(),
            "ends_at": ends.isoformat(), "status": "ACTIVE", "auto_renew": 0, "created_at": utc_now(),
        }
        self.mapper.insert_user_subscription(subscription)
        return self._row(self.mapper.find_active_subscription_for_plan(
            int(user_id), int(plan_id), utc_now()
        ))

    @Transactional()
    def renew_subscription(self, user_id: int, subscription_id: int) -> dict[str, Any] | None:
        now = datetime.now(timezone.utc)
        self._lock_user_billing(int(user_id), now)
        current = self._row(self.mapper.find_user_subscription(int(subscription_id), int(user_id)))
        if not current or current.get("status") != "ACTIVE":
            raise ValueError("套餐不存在或已停用")
        current_end = self._subscription_datetime(current.get("ends_at"), "套餐结束时间")
        if current_end <= now:
            raise ValueError("套餐已到期，请重新订阅")
        if not current.get("plan_enabled", True):
            raise ValueError("该套餐已下架，暂不能续订")
        price = max(0.0, float(current.get("price") or 0))
        self._charge_subscription(user_id, price)
        ends = max(current_end, now) + self._subscription_duration(current)
        if self.mapper.extend_user_subscription(
            int(subscription_id), int(user_id), int(current["plan_id"]), ends.isoformat()
        ) != 1:
            raise RuntimeError("套餐续期失败，请稍后重试")
        return self._row(self.mapper.find_user_subscription(int(subscription_id), int(user_id)))

    def set_subscription_auto_renew(self, user_id: int, subscription_id: int, enabled: bool) -> dict[str, Any] | None:
        row = self._row(self.mapper.find_user_subscription(int(subscription_id), int(user_id)))
        now = datetime.now(timezone.utc)
        if not row or row.get("status") != "ACTIVE" or self._subscription_datetime(row.get("ends_at")) <= now:
            return None
        if enabled and not row.get("plan_enabled", True):
            raise ValueError("该套餐已下架，不能开启自动续订；当前周期仍可继续使用")
        if self.mapper.set_subscription_auto_renew(int(subscription_id), int(user_id), 1 if enabled else 0) != 1:
            return None
        return self._row(self.mapper.find_user_subscription(int(subscription_id), int(user_id)))

    def cancel_user_subscription(self, user_id: int, subscription_id: int) -> bool:
        return bool(self.mapper.cancel_user_subscription(int(subscription_id), int(user_id)))

    def update_user_subscription(self, user_id: int, subscription_id: int, changes: Mapping[str, Any]) -> dict[str, Any] | None:
        existing = self._row(self.mapper.find_user_subscription(int(subscription_id), int(user_id)))
        if not existing:
            return None
        allowed = {"starts_at", "ends_at", "status", "auto_renew"}
        values = {key: value for key, value in changes.items() if key in allowed}
        starts = self._subscription_datetime(values.get("starts_at", existing.get("starts_at")), "开始时间")
        ends = self._subscription_datetime(values.get("ends_at", existing.get("ends_at")), "结束时间")
        if ends <= starts:
            raise ValueError("结束时间必须晚于开始时间")
        values["starts_at"] = starts.isoformat()
        values["ends_at"] = ends.isoformat()
        if "status" in values:
            values["status"] = str(values["status"]).upper()
            if values["status"] not in {"ACTIVE", "DISABLED", "CANCELLED", "EXPIRED"}:
                raise ValueError("套餐状态不正确")
        if "auto_renew" in values:
            values["auto_renew"] = 1 if bool(values["auto_renew"]) else 0
            if values["auto_renew"] and not existing.get("plan_enabled", True):
                raise ValueError("该套餐已下架，不能开启自动续订；当前周期仍可继续使用")
        if values.get("status") != "ACTIVE":
            values["auto_renew"] = 0
        self.mapper.update_user_subscription(int(subscription_id), int(user_id), values)
        return self._row(self.mapper.find_user_subscription(int(subscription_id), int(user_id)))

    def delete_user_subscription(self, user_id: int, subscription_id: int) -> bool:
        return bool(self.mapper.delete_user_subscription(int(subscription_id), int(user_id)))

    @Transactional()
    def _renew_due_subscription(self, row: Mapping[str, Any], now: datetime) -> None:
        """Charge and extend one row atomically; failures must roll back."""
        user_id = int(row["user_id"])
        self._lock_user_billing(user_id, now)
        current = self._row(self.mapper.find_user_subscription(int(row["id"]), user_id))
        if not current or current.get("status") != "ACTIVE" or not current.get("auto_renew"):
            return
        if not current.get("plan_enabled", True):
            self.mapper.set_subscription_auto_renew(int(current["id"]), user_id, 0)
            return
        current_end = self._subscription_datetime(current.get("ends_at"), "套餐结束时间")
        if current_end > now:
            return
        price = max(0.0, float(current.get("price") or row.get("price") or 0))
        self._charge_subscription(user_id, price)
        ends = max(current_end, now) + self._subscription_duration(current)
        if self.mapper.extend_user_subscription(
            int(current["id"]), user_id, int(current["plan_id"]), ends.isoformat()
        ) != 1:
            raise RuntimeError("套餐续期写入失败")

    @Transactional()
    def _expire_subscription(self, subscription_id: int) -> None:
        self.mapper.expire_subscription(int(subscription_id))

    @Scheduled(fixed_rate=60000, initial_delay=15000)
    def process_subscription_renewals(self) -> None:
        """Renew each subscription in its own transaction."""
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        self.mapper.expire_subscriptions(now_iso)
        for row in self.mapper.list_due_auto_renew_subscriptions(now_iso) or []:
            try:
                self._renew_due_subscription(row, now)
            except (TypeError, ValueError):
                # Insufficient balance or malformed legacy data stops renewal;
                # the debit was rolled back by _renew_due_subscription().
                self._expire_subscription(int(row["id"]))
            except RuntimeError:
                # A transient write failure keeps the entitlement due so the
                # next scheduler pass can retry without charging twice.
                continue

    def list_user_quotas(self, user_id: int, page: int = 1, page_size: int = 5) -> dict[str, Any]:
        total = self.mapper.count_user_quota_policies(int(user_id))
        page, page_size, _, offset = self.page_window(total, page, page_size)
        rows = self.mapper.list_user_quota_policies(int(user_id), offset, page_size)
        now = datetime.now(timezone.utc)
        enriched = [self._quota_with_usage(int(user_id), row, now) for row in rows]
        return self.page_result(enriched, total, page, page_size)

    def create_user_quota(self, user_id: int, values: Mapping[str, Any]) -> dict[str, Any]:
        now = utc_now()
        starts_at = str(values.get("starts_at") or now).strip()
        ends_at = str(values.get("ends_at") or "").strip() or None
        try:
            starts_dt = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
            if starts_dt.tzinfo is None:
                starts_dt = starts_dt.replace(tzinfo=timezone.utc)
            if ends_at:
                ends_dt = datetime.fromisoformat(ends_at.replace("Z", "+00:00"))
                if ends_dt.tzinfo is None:
                    ends_dt = ends_dt.replace(tzinfo=timezone.utc)
                if ends_dt <= starts_dt:
                    raise ValueError("结束时间必须晚于开始时间")
        except (TypeError, ValueError) as exc:
            raise ValueError("额度开始/结束时间格式不正确") from exc
        daily_amount = float(values.get("daily_amount", 0))
        if not math.isfinite(daily_amount) or daily_amount < 0:
            raise ValueError("每日金额必须是非负数字")
        quota = {
            "user_id": int(user_id), "name": str(values.get("name", "免费额度")).strip()[:80] or "免费额度",
            "daily_amount": round(daily_amount, 4),
            "daily_tokens": max(0, int(values.get("daily_tokens", 0))),
            "hourly_tokens": max(0, int(values.get("hourly_tokens", 0))),
            "hourly_window_hours": max(0.1, min(168.0, float(values.get("hourly_window_hours", 1)))),
            "starts_at": starts_at, "ends_at": ends_at,
            "enabled": 1 if bool(values.get("enabled", True)) else 0, "created_at": now, "updated_at": now,
        }
        self.mapper.insert_user_quota_policy(quota)
        return self._row(self.mapper.list_user_quota_policies(int(user_id), 0, 1)[0])

    def update_user_quota(self, user_id: int, quota_id: int, changes: Mapping[str, Any]) -> dict[str, Any] | None:
        values = {key: value for key, value in changes.items() if key in {"name", "daily_amount", "daily_tokens", "hourly_tokens", "hourly_window_hours", "starts_at", "ends_at", "enabled"}}
        if "daily_amount" in values:
            value = float(values["daily_amount"])
            if not math.isfinite(value) or value < 0:
                raise ValueError("每日金额必须是非负数字")
            values["daily_amount"] = round(value, 4)
        for key in ("daily_tokens", "hourly_tokens"): 
            if key in values: values[key] = max(0, int(values[key]))
        if "hourly_window_hours" in values: values["hourly_window_hours"] = max(0.1, min(168.0, float(values["hourly_window_hours"])))
        if "enabled" in values: values["enabled"] = 1 if bool(values["enabled"]) else 0
        if "starts_at" in values or "ends_at" in values:
            existing = next((dict(row) for row in self.mapper.list_user_quota_policies(int(user_id), 0, 1000000) if int(row.get("id", 0)) == int(quota_id)), None)
            starts_at = str(values.get("starts_at", existing.get("starts_at") if existing else "")).strip()
            ends_at = str(values.get("ends_at", existing.get("ends_at") if existing else "") or "").strip() or None
            try:
                start_dt = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
                end_dt = datetime.fromisoformat(ends_at.replace("Z", "+00:00")) if ends_at else None
                if end_dt and end_dt <= start_dt:
                    raise ValueError
            except (TypeError, ValueError):
                raise ValueError("额度开始/结束时间格式不正确")
            values["starts_at"] = starts_at
            values["ends_at"] = ends_at
        values["updated_at"] = utc_now()
        self.mapper.update_user_quota_policy(int(quota_id), int(user_id), values)
        rows = self.mapper.list_user_quota_policies(int(user_id), 0, 1000000)
        return next((dict(row) for row in rows if int(row.get("id", 0)) == int(quota_id)), None)

    def delete_user_quota(self, user_id: int, quota_id: int) -> bool:
        return bool(self.mapper.delete_user_quota_policy(int(quota_id), int(user_id)))

    def update_order_qr(self, trade_no: str, qr_code: str) -> dict[str, Any] | None:
        self.mapper.update_order_qr(str(trade_no), str(qr_code))
        return self.find_order(str(trade_no))

    @Transactional()
    def pay_order(self, trade_no: str, user_id: int | None = None) -> dict[str, Any] | None:
        order = self._row(self.mapper.find_order(str(trade_no), user_id))
        if not order:
            return None
        changed = 0
        paid_at = None
        if order.get("status") == "PENDING":
            # Use the same id-based settlement statement as callback payments.
            # It avoids mapper parameter-name differences on legacy databases
            # and makes the affected-row check the single source of truth for
            # crediting the wallet exactly once.
            paid_at = utc_now()
            changed = int(self.mapper.settle_order(int(order["id"]), paid_at) or 0)
            if changed == 1:
                self.mapper.credit_balance(int(order["user_id"]), float(order["credits"]))
                # MyBatis keeps the transaction-bound select cache alive until
                # the method exits, so a read immediately after UPDATE can
                # still contain the old PENDING snapshot. Reflect the known
                # committed state in the response; the next request reads the
                # same state from the database as well.
                order["status"] = "PAID"
                order["paid_at"] = paid_at
        latest = self._row(self.mapper.find_order(str(trade_no), user_id))
        if latest and changed == 1:
            latest["status"] = "PAID"
            latest["paid_at"] = paid_at
        return latest or order

    def find_order(self, trade_no: str) -> dict[str, Any] | None:
        return self._row(self.mapper.find_order(str(trade_no), None))

    def update_listener_status(self, available: bool, reason: str = "") -> dict[str, Any] | None:
        previous = self._row(self.mapper.find_listener_status())
        self.mapper.upsert_listener_status(1 if available else 0, str(reason)[:500], utc_now())
        return previous

    def listener_status(self, stale_after_seconds: int = 15) -> dict[str, Any]:
        row = self._row(self.mapper.find_listener_status())
        if not row:
            return {"available": False, "reason": "微信监听器尚未上报状态", "updated_at": None, "stale": True}
        data = dict(row)
        try:
            updated = datetime.fromisoformat(str(data.get("updated_at")).replace("Z", "+00:00"))
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - updated).total_seconds()
        except (TypeError, ValueError):
            age = float("inf")
        data["stale"] = age > max(1, int(stale_after_seconds))
        if data["stale"]:
            data["available"] = 0
            data["reason"] = "微信监听器状态已过期"
        data["available"] = bool(data.get("available"))
        return data

    def mark_listener_alert(self) -> None:
        self.mapper.mark_listener_alert(utc_now())

    def clear_listener_alert(self) -> None:
        self.mapper.mark_listener_alert(None)

    @Transactional()
    def cancel_order(self, trade_no: str, user_id: int) -> dict[str, Any] | None:
        order = self._row(self.mapper.find_order(str(trade_no), int(user_id)))
        if not order:
            return None
        if order.get("status") == "PENDING":
            self.mapper.cancel_order(str(trade_no), int(user_id))
        return self._row(self.mapper.find_order(str(trade_no), int(user_id)))

    # --------------------------------------------------------------- billing
    def _allocate_entitlement(
        self,
        user_id: int,
        total_tokens: int,
        cost: float,
        entitlement: Mapping[str, Any],
        kind: str,
        now: datetime,
    ) -> tuple[float, int]:
        """Return the portion of a request covered by one active entitlement.

        A zero limit means that dimension is not constrained, which lets an
        administrator configure either money or tokens without inventing a
        second policy.  When both limits exist, the smaller remaining ratio
        wins so neither quota can be exceeded by a single request.
        """
        if kind == "FREE":
            identifier = int(entitlement.get("id") or 0)
            daily_start = business_day_start_utc(now).isoformat()
            daily = dict(self.mapper.quota_usage_totals(int(user_id), identifier, daily_start, None))
            daily_amount = max(0.0, float(entitlement.get("daily_amount") or 0))
            daily_tokens = max(0, int(entitlement.get("daily_tokens") or 0))
            hourly_tokens = max(0, int(entitlement.get("hourly_tokens") or 0))
            try:
                window_hours = max(0.1, min(168.0, float(entitlement.get("hourly_window_hours") or 1)))
            except (TypeError, ValueError):
                window_hours = 1.0
            hourly_start = (now - timedelta(hours=window_hours)).isoformat()
            hourly = dict(self.mapper.quota_usage_totals(int(user_id), identifier, hourly_start, None))
            used_amount = max(0.0, float(daily.get("free_cost") or 0))
            used_daily_tokens = max(0, int(daily.get("free_tokens") or 0))
            used_hourly_tokens = max(0, int(hourly.get("free_tokens") or 0))
        else:
            identifier = int(entitlement.get("id") or 0)
            daily_start = business_day_start_utc(now).isoformat()
            used = dict(self.mapper.subscription_usage_totals(identifier, daily_start, None))
            daily_amount = max(0.0, float(entitlement.get("daily_amount") or 0))
            daily_tokens = max(0, int(entitlement.get("daily_tokens") or 0))
            hourly_tokens = 0
            used_amount = max(0.0, float(used.get("subscription_cost") or 0))
            used_daily_tokens = max(0, int(used.get("subscription_tokens") or 0))
            used_hourly_tokens = 0

        # A zero value means unlimited for that dimension.  Therefore an
        # entitlement with all zero limits covers the whole request.
        remaining_amount = float("inf") if daily_amount <= 0 else max(0.0, daily_amount - used_amount)
        remaining_tokens = float("inf")
        if daily_tokens > 0:
            remaining_tokens = min(remaining_tokens, max(0, daily_tokens - used_daily_tokens))
        if hourly_tokens > 0:
            remaining_tokens = min(remaining_tokens, max(0, hourly_tokens - used_hourly_tokens))
        if total_tokens <= 0:
            token_ratio = 1.0
        elif math.isinf(remaining_tokens):
            token_ratio = 1.0
        else:
            token_ratio = min(1.0, remaining_tokens / total_tokens)
        amount_ratio = 1.0 if math.isinf(remaining_amount) or cost <= 0 else min(1.0, remaining_amount / cost)
        ratio = max(0.0, min(1.0, token_ratio, amount_ratio))
        covered_cost = min(cost, max(0.0, round(cost * ratio, 10)))
        covered_tokens = min(total_tokens, max(0, int(round(total_tokens * ratio))))
        return covered_cost, covered_tokens

    @Transactional()
    def charge(
        self,
        user_id: int,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost: float,
    ) -> tuple[bool, dict[str, Any] | None]:
        user_id = int(user_id)
        billing = get_config().get("rose", {}).get("billing", {})
        try:
            prompt_tokens = max(0, int(prompt_tokens))
            completion_tokens = max(0, int(completion_tokens))
            cost = float(cost)
            minimum_usable = max(0.0, float(billing.get("min-usable-balance", 0.001)))
            minimum_balance = max(-0.1, float(billing.get("minimum-balance", -0.1)))
        except (TypeError, ValueError):
            return False, None
        if not math.isfinite(cost) or cost < 0:
            return False, None
        now = datetime.now(timezone.utc)
        # The UPDATE obtains a per-user database write lock for the lifetime
        # of this transaction. It prevents concurrent requests from reading
        # the same remaining entitlement balance.
        self._lock_user_billing(user_id, now)
        user = self._row(self.mapper.find_balance(user_id))
        if not user or not user.get("enabled"):
            return False, user
        total_tokens = prompt_tokens + completion_tokens
        free_cost = free_tokens = subscription_cost = subscription_tokens = 0
        quota_id = subscription_id = None
        allocations: list[dict[str, Any]] = []

        # Entitlements are deliberately consumed in a deterministic order:
        # the policy/subscription expiring soonest is used first.
        active_quotas = self.mapper.find_active_quota_policies(user_id, now.isoformat()) or []
        remaining_cost = cost
        remaining_tokens = total_tokens
        for value in active_quotas:
            if remaining_cost <= 0 and remaining_tokens <= 0:
                break
            quota = dict(value)
            covered_cost, covered_tokens = self._allocate_entitlement(
                user_id, remaining_tokens, remaining_cost, quota, "FREE", now
            )
            if covered_cost <= 0 and covered_tokens <= 0:
                continue
            identifier = int(quota.get("id") or 0) or None
            quota_id = quota_id or identifier
            free_cost = round(free_cost + covered_cost, 10)
            free_tokens += covered_tokens
            remaining_cost = max(0.0, round(remaining_cost - covered_cost, 10))
            remaining_tokens = max(0, remaining_tokens - covered_tokens)
            allocations.append({"kind": "FREE", "entitlement_id": identifier, "cost": covered_cost, "tokens": covered_tokens})

        active_subscriptions = self.mapper.find_active_subscriptions(user_id, now.isoformat()) or []
        for value in active_subscriptions:
            if remaining_cost <= 0 and remaining_tokens <= 0:
                break
            subscription = dict(value)
            covered_cost, covered_tokens = self._allocate_entitlement(
                user_id, remaining_tokens, remaining_cost, subscription, "SUBSCRIPTION", now
            )
            if covered_cost <= 0 and covered_tokens <= 0:
                continue
            identifier = int(subscription.get("id") or 0) or None
            subscription_id = subscription_id or identifier
            subscription_cost = round(subscription_cost + covered_cost, 10)
            subscription_tokens += covered_tokens
            remaining_cost = max(0.0, round(remaining_cost - covered_cost, 10))
            remaining_tokens = max(0, remaining_tokens - covered_tokens)
            allocations.append({"kind": "SUBSCRIPTION", "entitlement_id": identifier, "cost": covered_cost, "tokens": covered_tokens})

        wallet_cost = remaining_cost
        wallet_tokens = remaining_tokens
        if wallet_cost > 0 and self.mapper.update_balance_after_charge(
            user_id, wallet_cost, minimum_balance, minimum_usable
        ) != 1:
            return False, user
        sources = []
        if free_cost > 0 or free_tokens > 0:
            sources.append("FREE")
        if subscription_cost > 0 or subscription_tokens > 0:
            sources.append("SUBSCRIPTION")
        if wallet_cost > 0 or wallet_tokens > 0:
            sources.append("WALLET")
        usage = {
            "user_id": user_id,
            "model": str(model),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "cost": round(cost, 10),
            "status": "SUCCEEDED",
            "created_at": now.isoformat(),
            "billing_source": "+".join(sources) or "WALLET",
            "free_cost": round(free_cost, 10),
            "free_tokens": free_tokens,
            "subscription_cost": round(subscription_cost, 10),
            "subscription_tokens": subscription_tokens,
            "wallet_cost": round(wallet_cost, 10),
            "wallet_tokens": wallet_tokens,
            "quota_id": quota_id,
            "subscription_id": subscription_id,
        }
        self.mapper.insert_usage(usage)
        usage_id = int(usage.get("id") or 0)
        for allocation in allocations:
            self.mapper.insert_usage_allocation({
                **allocation,
                "usage_id": usage_id,
                "user_id": user_id,
                "created_at": now.isoformat(),
            })
        return True, self._row(self.mapper.find_usage(int(usage.get("id") or 0))) or usage

    def has_usable_balance(self, user_id: int) -> bool:
        billing = get_config().get("rose", {}).get("billing", {})
        try:
            minimum = max(0.0, float(billing.get("min-usable-balance", 0.001)))
        except (TypeError, ValueError):
            minimum = 0.001
        row = self._row(self.mapper.find_balance(int(user_id)))
        if not row or not row.get("enabled"):
            return False
        if float(row.get("balance") or 0) >= minimum:
            return True
        # Do not treat an exhausted but unexpired entitlement as usable. This
        # lightweight one-token probe reuses the same limit calculations as
        # charge(), without adding a write to the forwarding hot path.
        now = datetime.now(timezone.utc)
        probe_cost = max(minimum, 0.00000001)
        for value in self.mapper.find_active_quota_policies(int(user_id), now.isoformat()) or []:
            covered_cost, covered_tokens = self._allocate_entitlement(
                int(user_id), 1, probe_cost, dict(value), "FREE", now
            )
            if covered_cost > 0 and covered_tokens > 0:
                return True
        for value in self.mapper.find_active_subscriptions(int(user_id), now.isoformat()) or []:
            covered_cost, covered_tokens = self._allocate_entitlement(
                int(user_id), 1, probe_cost, dict(value), "SUBSCRIPTION", now
            )
            if covered_cost > 0 and covered_tokens > 0:
                return True
        return False

    def list_usage_page(self, user_id: int, page: int = 1, page_size: int = 5) -> dict[str, Any]:
        total = self.mapper.count_usage(int(user_id))
        page, page_size, _, offset = self.page_window(total, page, page_size)
        recent = self.mapper.list_usage(int(user_id), offset, page_size)
        return self.page_result(recent, total, page, page_size)

    def usage_summary(self, user_id: int, page: int = 1, page_size: int = 5) -> dict[str, Any]:
        totals = dict(self.mapper.user_usage_totals(int(user_id)))
        recent = self.list_usage_page(user_id, page, page_size)
        return {
            **totals,
            "recent": recent["items"],
            "recent_pagination": recent,
        }

    def admin_summary(self) -> dict[str, Any]:
        self.mapper.expire_recharge_codes(utc_now())
        totals = dict(self.mapper.admin_totals())
        month_start = business_month_start_utc().isoformat()
        model_rows = [dict(row) for row in self.mapper.admin_model_usage(month_start)]
        configured_models = get_config().get("rose", {}).get("models", []) or []
        if isinstance(configured_models, dict):
            configured_models = [
                {"id": model_id, **(spec if isinstance(spec, dict) else {})}
                for model_id, spec in configured_models.items()
            ]
        model_usage = {}
        for spec in configured_models:
            if not isinstance(spec, dict):
                continue
            model_id = str(spec.get("id") or spec.get("model") or "").strip()
            if model_id:
                model_usage[model_id] = {
                    "model": model_id,
                    "month_tokens": 0,
                    "total_tokens": 0,
                    "month_requests": 0,
                    "total_requests": 0,
                    "month_cost": 0.0,
                    "total_cost": 0.0,
                }
        for row in model_rows:
            model_usage[str(row.get("model"))] = row
        return {
            "users": self.mapper.count_users(""),
            "active_users": self.mapper.count_active_users(),
            "total_tokens": totals.get("total_tokens", 0),
            "total_cost": totals.get("total_cost", 0),
            "total_requests": totals.get("total_requests", 0),
            "total_recharge": self.mapper.sum_paid_orders(),
            "active_recharge_codes": self.mapper.count_active_recharge_codes(),
            "model_usage": sorted(
                model_usage.values(),
                key=lambda item: (-int(item.get("total_tokens", 0)), item["model"]),
            ),
        }

    def export_usage(self, *, start_at: str = "", end_at: str = "", user_id: int | None = None, model: str = "", status: str = "", limit: int = 50000) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 50000), 100000))
        normalized_user = int(user_id) if str(user_id or "").isdigit() else None
        return [dict(row) for row in self.mapper.list_usage_export(
            str(start_at or ""), str(end_at or ""), normalized_user,
            str(model or "").strip(), str(status or "").strip().upper(), limit,
        )]


__all__ = ["StoreService"]

