"""Configuration-only model removal must not create a phantom API model."""

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from backend.controller import config_controller
from backend.controller.model_controller import ModelController
from backend.service import ai_service


def init_gateway(monkeypatch, rose):
    config = {"rose": rose}
    monkeypatch.setattr(ai_service, "get_config", lambda: config)
    monkeypatch.setattr(config_controller, "get_config", lambda: config)
    # No database, background probes, or live upstream requests in these tests.
    monkeypatch.setattr(ai_service.threading, "Thread", lambda **kw: SimpleNamespace(start=lambda: None))
    gateway = ai_service.AiGatewayService(None)
    gateway.init()
    return gateway


@pytest.mark.parametrize("models", [[], {}])
def test_explicit_empty_models_disable_regular_api_catalog(monkeypatch, models):
    gateway = init_gateway(monkeypatch, {"models": models})
    assert gateway.models == {}
    assert gateway.clients == {}
    assert gateway.catalog() == []
    assert config_controller.ConfigController().public_config().data["models"] == []
    with pytest.raises(ValueError, match="模型未配置"):
        gateway.model_spec("deepseek-v4-flash")


def test_omitted_catalog_preserves_legacy_default(monkeypatch):
    gateway = init_gateway(monkeypatch, {"proxy": {"default-model": "legacy-model"}})
    assert list(gateway.models) == ["legacy-model"]
    assert gateway.model_spec(None)["provider"] == "OpenAI"


@pytest.mark.parametrize("models", [
    [{"id": "custom-model", "provider": "Custom"}],
    {"custom-model": {"provider": "Custom"}},
])
def test_nonempty_catalog_is_unchanged(monkeypatch, models):
    gateway = init_gateway(monkeypatch, {"models": models})
    assert list(gateway.models) == ["custom-model"]
    assert gateway.model_spec("custom-model")["provider"] == "Custom"


def test_per_model_use_proxy_is_friendly_and_legacy_compatible():
    gateway = ai_service.AiGatewayService(None)
    gateway._transport = {"trust-env": True}

    assert gateway._model_uses_proxy({"use-proxy": False}) is False
    assert gateway._model_uses_proxy({"use-proxy": "true"}) is True
    assert gateway._model_uses_proxy({"trust-env": False}) is False
    assert gateway._model_uses_proxy({}) is True


def test_empty_api_catalog_keeps_subscription_models(monkeypatch):
    gateway = init_gateway(monkeypatch, {"models": []})
    subscriptions = [
        {"id": "gpt-5.6-sol", "provider": "OpenAI Subscription", "status": "正常"},
        {"id": "claude-test", "provider": "Claude Subscription", "status": "正常"},
    ]
    controller = ModelController(
        gateway,
        SimpleNamespace(catalog=lambda: subscriptions),
        SimpleNamespace(user_from_authorization=lambda _: {"id": 1}),
        SimpleNamespace(filter_catalog=lambda user, models: models),
    )
    result = controller.models(
        page=1, page_size=12, query="", group="", provider="", availability="", authorization="Bearer test"
    ).data
    assert result["models"] == subscriptions
    assert result["pagination"]["total"] == 2
    assert result["filters"]["providers"] == ["Claude Subscription", "OpenAI Subscription"]


def test_example_config_has_no_regular_api_models():
    path = Path(__file__).resolve().parents[1] / "application.example.yml"
    text = path.read_text(encoding="utf-8")
    config = yaml.safe_load(text)
    assert config["rose"]["models"] == []
    for model in ("deepseek-v4-flash", "deepseek-v4-pro", "deepseek-v4-flash-vision-exp"):
        assert f"    # - id: {model}\n" in text


def test_public_config_exposes_user_group_overview_visibility(monkeypatch):
    monkeypatch.setattr(config_controller, "get_config", lambda: {"rose": {}})
    assert config_controller.ConfigController().public_config().data["user_group_overview_visible"] is True

    monkeypatch.setattr(config_controller, "get_config", lambda: {
        "rose": {"user-groups": {"overview-visible-to-users": False}}
    })
    assert config_controller.ConfigController().public_config().data["user_group_overview_visible"] is False


def test_public_config_exposes_data_visualization_visibility(monkeypatch):
    monkeypatch.setattr(config_controller, "get_config", lambda: {"rose": {}})
    assert config_controller.ConfigController().public_config().data["data_visualization_visible"] is True

    monkeypatch.setattr(config_controller, "get_config", lambda: {
        "rose": {"data-visualization": {"visible-to-users": False}}
    })
    assert config_controller.ConfigController().public_config().data["data_visualization_visible"] is False


def test_group_editor_uses_complete_monitoring_catalog_but_model_plaza_stays_filtered():
    root = Path(__file__).resolve().parents[1]
    admin = (root / "frontend/src/views/Admin.vue").read_text(encoding="utf-8")
    plaza = (root / "frontend/src/views/Models.vue").read_text(encoding="utf-8")
    assert "api.monitoring({ page, page_size: 12 })" in admin
    assert "api.models({ page, page_size: 12 })" not in admin
    assert "api.models({" in plaza
