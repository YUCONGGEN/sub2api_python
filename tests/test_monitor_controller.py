from backend.controller.monitor_controller import MonitorController
from backend.common.time_utils import business_date_keys
from backend.service.observability_service import ObservabilityService


class FakeStore:
    @staticmethod
    def usage_by_model_since(user_id, since):
        return [
            {"model": "gpt-subscription", "requests": 2, "tokens": 300, "cost": 0.02},
        ]


class FakeAuth:
    store = FakeStore()

    @staticmethod
    def user_from_authorization(authorization):
        return {"id": 7} if authorization else None


class FakeGateway:
    @staticmethod
    def catalog():
        return [{"id": "yaml-model", "provider": "Configured", "group": "YAML", "status": "正常"}]


class FakeSubscriptionGateway:
    @staticmethod
    def catalog():
        return [
            {
                "id": "gpt-subscription",
                "provider": "OpenAI Subscription",
                "group": "Subscription Gateway",
                "status": "正常",
            },
        ]


def test_monitoring_includes_subscription_gateway_models_and_usage():
    controller = MonitorController(FakeAuth(), FakeGateway(), FakeSubscriptionGateway())

    response = controller.overview("Bearer test", page=1, page_size=12)
    payload = response.data

    assert payload["summary"]["total"] == 2
    assert payload["summary"]["available"] == 2
    assert payload["summary"]["requests_24h"] == 2
    assert payload["summary"]["tokens_24h"] == 300
    assert payload["summary"]["overall_status"] == "正常"
    by_id = {item["id"]: item for item in payload["models"]}
    assert set(by_id) == {"yaml-model", "gpt-subscription"}
    assert by_id["gpt-subscription"]["requests_24h"] == 2
    assert by_id["gpt-subscription"]["tokens_24h"] == 300
    assert by_id["gpt-subscription"]["cost_24h"] == 0.02
    assert by_id["gpt-subscription"]["health_history"][-1]["ok"] is True


class TrackingSubscriptionGateway:
    def __init__(self):
        self.include_users = None

    @staticmethod
    def catalog():
        return []

    def metrics(self, include_users=False):
        self.include_users = include_users
        result = {"active_requests": 1, "queue_waiting": 0}
        if include_users:
            result.update({"active_users": [{"user_id": 7, "username": "admin"}], "queued_users": []})
        return result


class FakeAdminAuth(FakeAuth):
    @staticmethod
    def user_from_authorization(authorization):
        return {"id": 7, "role": "ADMIN"} if authorization else None


def test_monitoring_only_exposes_current_gateway_usernames_to_admins():
    regular_gateway = TrackingSubscriptionGateway()
    regular = MonitorController(FakeAuth(), FakeGateway(), regular_gateway).overview("Bearer user", page=1, page_size=12).data
    assert regular_gateway.include_users is False
    assert "active_users" not in regular["summary"]["gateway"]

    admin_gateway = TrackingSubscriptionGateway()
    admin = MonitorController(FakeAdminAuth(), FakeGateway(), admin_gateway).overview("Bearer admin", page=1, page_size=12).data
    assert admin_gateway.include_users is True
    assert admin["summary"]["gateway"]["active_users"][0]["username"] == "admin"


class EmptyAnalyticsMapper:
    @staticmethod
    def admin_daily_usage(start, limit):
        return []

    @staticmethod
    def admin_daily_activity(start, limit):
        return []

    @staticmethod
    def admin_user_usage(limit):
        return []

    @staticmethod
    def admin_status_usage():
        return []

    @staticmethod
    def admin_billing_usage():
        return []

    @staticmethod
    def admin_order_status():
        return []


def test_admin_daily_charts_always_include_the_current_beijing_day():
    service = ObservabilityService.__new__(ObservabilityService)
    service.mapper = EmptyAnalyticsMapper()

    analytics = service.analytics()

    assert len(analytics["daily"]) == 14
    assert len(analytics["activity"]) == 14
    assert analytics["daily"][-1]["day"] == business_date_keys(1)[0]
    assert analytics["daily"][-1]["requests"] == 0
    assert analytics["activity"][-1]["active_users"] == 0
