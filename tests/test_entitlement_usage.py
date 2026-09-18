from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

import pytest

from backend.common.time_utils import business_date_keys, business_day_start_utc, business_month_start_utc, business_week_start_utc
from backend.service.store_service import StoreService


class EntitlementMapper:
    def __init__(self):
        self.quota_queries = []
        self.entitlement_query = None

    @staticmethod
    def count_user_subscriptions(user_id):
        return 1

    @staticmethod
    def list_user_subscriptions(user_id, offset, limit):
        return [{
            "id": 41,
            "user_id": user_id,
            "plan_id": 2,
            "plan_name": "标准版",
            "status": "ACTIVE",
            "plan_enabled": 1,
            "starts_at": "2020-01-01T00:00:00+00:00",
            "ends_at": "2099-01-01T00:00:00+00:00",
            "daily_amount": 5,
            "daily_tokens": 1000,
        }]

    @staticmethod
    def subscription_usage_totals(subscription_id, start_at, end_at):
        assert subscription_id == 41
        assert datetime.fromisoformat(start_at).hour == 16
        return {"subscription_cost": 1.25, "subscription_tokens": 250}

    @staticmethod
    def count_user_quota_policies(user_id):
        return 1

    @staticmethod
    def list_user_quota_policies(user_id, offset, limit):
        return [{
            "id": 73,
            "user_id": user_id,
            "name": "注册赠送",
            "enabled": 1,
            "starts_at": "2020-01-01T00:00:00+00:00",
            "ends_at": "2099-01-01T00:00:00+00:00",
            "daily_amount": 2,
            "daily_tokens": 800,
            "hourly_tokens": 300,
            "hourly_window_hours": 2,
        }]

    def list_user_entitlements(self, user_id, now, offset, limit):
        self.entitlement_query = {"user_id": user_id, "now": now, "offset": offset, "limit": limit}
        quota = dict(self.list_user_quota_policies(user_id, 0, 1)[0], entitlement_type="FREE")
        subscription = dict(self.list_user_subscriptions(user_id, 0, 1)[0], entitlement_type="SUBSCRIPTION")
        return [quota, subscription][offset:offset + limit]

    def quota_usage_totals(self, user_id, quota_id, start_at, end_at):
        assert user_id == 7
        assert quota_id == 73
        self.quota_queries.append(start_at)
        if len(self.quota_queries) == 1:
            assert datetime.fromisoformat(start_at).hour == 16
            return {"free_cost": 0.5, "free_tokens": 200}
        return {"free_cost": 0.1, "free_tokens": 90}


def service_with(mapper):
    service = StoreService.__new__(StoreService)
    service.mapper = mapper
    return service


def test_subscription_list_includes_actual_used_and_remaining_amounts():
    result = service_with(EntitlementMapper()).list_user_subscriptions(7)
    usage = result["items"][0]["usage"]

    assert usage["active"] is True
    assert usage["period"] == "ASIA_SHANGHAI_DAY"
    assert usage["daily_amount"] == {
        "limit": 5.0, "used": 1.25, "remaining": 3.75, "unlimited": False,
    }
    assert usage["daily_tokens"] == {
        "limit": 1000, "used": 250, "remaining": 750, "unlimited": False,
    }


def test_unlisted_plan_keeps_existing_subscription_active_until_its_end():
    mapper = EntitlementMapper()
    original = mapper.list_user_subscriptions
    mapper.list_user_subscriptions = lambda *args: [dict(original(*args)[0], plan_enabled=0)]

    item = service_with(mapper).list_user_subscriptions(7)["items"][0]

    assert item["plan_enabled"] == 0
    assert item["usage"]["active"] is True


def test_runtime_subscription_queries_do_not_revoke_unlisted_plans():
    root = ElementTree.parse(Path(__file__).parents[1] / "backend/mappers/StoreMapper.xml").getroot()
    statements = {item.attrib.get("id"): "".join(item.itertext()) for item in root.findall("select")}

    for statement_id in (
        "list_active_subscription_group_candidates",
        "find_active_subscriptions",
        "find_active_subscription_for_plan",
        "has_active_entitlement",
    ):
        assert "p.enabled = 1" not in statements[statement_id]

    # Renewing at the end of the purchased period is a new purchase, so an
    # unlisted plan must still be excluded from automatic renewal.
    assert "p.enabled = 1" in statements["list_due_auto_renew_subscriptions"]


def test_unlisted_plan_stops_future_renewal_without_revoking_current_period():
    root = ElementTree.parse(Path(__file__).parents[1] / "backend/mappers/StoreMapper.xml").getroot()
    updates = {item.attrib.get("id"): "".join(item.itertext()) for item in root.findall("update")}

    stop_renewal = updates["disable_plan_subscription_auto_renew"]
    assert "SET auto_renew = 0" in stop_renewal
    assert "plan_id = #{plan_id}" in stop_renewal
    assert "status = 'ACTIVE'" in stop_renewal

    # This also cleans up plans that were already disabled before this fix, so
    # their subscriptions cannot remain ACTIVE forever after the end time.
    expiration = updates["expire_subscriptions"]
    assert "subscription_plans WHERE enabled = 0" in expiration


def test_unlisted_plan_cannot_turn_auto_renew_back_on():
    class Mapper:
        @staticmethod
        def find_user_subscription(subscription_id, user_id):
            return {
                "id": subscription_id,
                "user_id": user_id,
                "status": "ACTIVE",
                "auto_renew": 0,
                "plan_enabled": 0,
                "ends_at": "2099-01-01T00:00:00+00:00",
            }

        @staticmethod
        def set_subscription_auto_renew(*args):
            raise AssertionError("下架套餐不应写入自动续订")

    with pytest.raises(ValueError, match="当前周期仍可继续使用"):
        service_with(Mapper()).set_subscription_auto_renew(7, 41, True)


def test_free_grant_list_includes_daily_and_rolling_window_usage():
    mapper = EntitlementMapper()
    result = service_with(mapper).list_user_quotas(7)
    usage = result["items"][0]["usage"]

    assert usage["active"] is True
    assert usage["daily_amount"]["remaining"] == 1.5
    assert usage["daily_tokens"]["used"] == 200
    assert usage["daily_tokens"]["remaining"] == 600
    assert usage["window_tokens"]["used"] == 90
    assert usage["window_tokens"]["remaining"] == 210
    assert usage["window_tokens"]["window_hours"] == 2
    assert len(mapper.quota_queries) == 2


def test_unlimited_dimension_uses_null_remaining_only_while_active():
    active = StoreService._usage_dimension(0, 42, True)
    inactive = StoreService._usage_dimension(0, 42, False)

    assert active == {"limit": 0, "used": 42, "remaining": None, "unlimited": True}
    assert inactive == {"limit": 0, "used": 42, "remaining": 0, "unlimited": True}


def test_combined_entitlements_use_one_five_item_page():
    mapper = EntitlementMapper()
    result = service_with(mapper).list_user_entitlements(7, page=1, page_size=5)

    assert result["total"] == 2
    assert result["page_size"] == 5
    assert [item["entitlement_type"] for item in result["items"]] == ["FREE", "SUBSCRIPTION"]
    assert result["items"][0]["usage"]["window_tokens"]["remaining"] == 210
    assert result["items"][1]["usage"]["daily_tokens"]["remaining"] == 750
    assert mapper.entitlement_query["offset"] == 0
    assert mapper.entitlement_query["limit"] == 5


def test_business_calendar_uses_beijing_midnight_while_storing_utc():
    now = datetime(2026, 9, 2, 1, 30, tzinfo=timezone.utc)

    assert business_day_start_utc(now).isoformat() == "2026-09-01T16:00:00+00:00"
    assert business_week_start_utc(now).isoformat() == "2026-08-30T16:00:00+00:00"
    assert business_month_start_utc(now).isoformat() == "2026-08-31T16:00:00+00:00"
    assert business_date_keys(3, now) == ["2026-08-31", "2026-09-01", "2026-09-02"]
