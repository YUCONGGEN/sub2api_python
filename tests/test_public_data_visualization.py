from types import SimpleNamespace

from backend.controller.dashboard_controller import DashboardController
from backend.controller import dashboard_controller


class PublicVisualizationStore:
    @staticmethod
    def model_usage_summary():
        return [{"model": "gpt-test", "total_tokens": 200, "month_tokens": 100}]


class PublicVisualizationObservability:
    @staticmethod
    def public_analytics():
        return {
            "daily": [{"day": "2026-09-19", "requests": 2, "total_tokens": 200, "total_cost": 0.5}],
            "activity": [{"day": "2026-09-19", "active_users": 1, "requests": 2}],
        }


def controller_for(role="USER"):
    store = PublicVisualizationStore()
    auth = SimpleNamespace(store=store, user_from_authorization=lambda _: {"id": 7, "role": role})
    return DashboardController(auth, PublicVisualizationObservability())


def test_regular_user_visualization_never_returns_rankings_billing_or_orders(monkeypatch):
    monkeypatch.setattr(dashboard_controller, "get_config", lambda: {
        "rose": {"data-visualization": {"visible-to-users": True}}
    })

    response = controller_for().visualization("Bearer user")

    assert response.code == 200
    assert set(response.data["analytics"]) == {"daily", "activity"}
    assert response.data["model_usage"][0]["model"] == "gpt-test"
    serialized = str(response.data)
    assert "today_users" not in serialized
    assert "billing_sources" not in serialized
    assert "orders" not in serialized


def test_yaml_switch_blocks_regular_user_but_admin_remains_allowed(monkeypatch):
    monkeypatch.setattr(dashboard_controller, "get_config", lambda: {
        "rose": {"data-visualization": {"visible-to-users": False}}
    })

    assert controller_for("USER").visualization("Bearer user").code == 403
    assert controller_for("ADMIN").visualization("Bearer admin").code == 200
