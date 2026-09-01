from backend.controller.monitor_controller import MonitorController


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

    assert payload["summary"] == {"total": 2, "available": 2}
    by_id = {item["id"]: item for item in payload["models"]}
    assert set(by_id) == {"yaml-model", "gpt-subscription"}
    assert by_id["gpt-subscription"]["requests_24h"] == 2
    assert by_id["gpt-subscription"]["tokens_24h"] == 300
    assert by_id["gpt-subscription"]["cost_24h"] == 0.02
