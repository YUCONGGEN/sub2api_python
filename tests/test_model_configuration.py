"""Configuration-only model removal must not create a phantom API model."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from backend.controller import config_controller
from backend.controller.model_controller import ModelController
from backend.service import ai_service
from backend.service.ai_service import ReliableOpenAIChatModel
from backend.service.user_group_service import UserGroupService


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


def test_openai_subscription_fallback_rewrites_only_unconfigured_gpt_models(monkeypatch):
    gateway = init_gateway(monkeypatch, {
        "subscription-gateway": {"openai-fallback-model": "openai"},
        "models": [
            {"id": "openai", "provider": "OpenAI", "enabled": True},
            {"id": "configured-gpt", "provider": "OpenAI", "enabled": True},
        ],
    })

    original = {"model": "gpt-5.6-sol", "messages": [{"role": "user", "content": "hi"}]}
    routed = gateway.apply_openai_subscription_fallback(original)
    assert routed["model"] == "gpt-5.6-sol"
    assert routed["_rose_model_id"] == "openai"
    assert routed["_rose_upstream_model"] == "gpt-5.6-sol"
    assert original["model"] == "gpt-5.6-sol"
    assert gateway.apply_openai_subscription_fallback({"model": "configured-gpt"})["model"] == "configured-gpt"
    assert gateway.apply_openai_subscription_fallback({"model": "claude-opus-5"})["model"] == "claude-opus-5"
    assert gateway.apply_openai_subscription_fallback({"model": "gpt-image-2"})["_rose_model_id"] == "openai"
    assert gateway.apply_openai_subscription_fallback({"model": "o4-mini"})["_rose_model_id"] == "openai"


@pytest.mark.parametrize("fallback", ["", "missing", "disabled", "not-openai"])
def test_openai_subscription_fallback_requires_an_enabled_openai_target(monkeypatch, fallback):
    gateway = init_gateway(monkeypatch, {
        "subscription-gateway": {"openai-fallback-model": fallback},
        "models": [
            {"id": "disabled", "provider": "OpenAI", "enabled": False},
            {"id": "not-openai", "provider": "Custom", "enabled": True},
        ],
    })
    payload = {"model": "gpt-5.6-sol"}
    assert gateway.apply_openai_subscription_fallback(payload) is payload


def test_responses_fallback_uses_the_model_requested_by_codex():
    model = ReliableOpenAIChatModel(
        api_key="test-key",
        base_url="https://api.example.com/v1",
        model="openai",
        endpoint="Responses",
    )
    payload = model._http_payload(
        [{"role": "user", "content": "hello"}],
        {
            "_rose_upstream_model": "gpt-5.6-sol",
            "reasoning_effort": "high",
            "max_tokens": 8,
        },
        stream=False,
    )
    assert model._request_url() == "https://api.example.com/v1/responses"
    assert payload["model"] == "gpt-5.6-sol"
    assert payload["input"] == [{"role": "user", "content": "hello"}]
    assert payload["reasoning"] == {"effort": "high"}
    assert payload["max_output_tokens"] == 8
    assert payload["stream"] is False
    assert "messages" not in payload
    assert "_rose_upstream_model" not in payload


def test_fallback_invocation_uses_openai_client_but_returns_requested_model():
    calls = []

    class Model:
        async def acall(self, messages, options=None):
            calls.append((messages, options))
            return SimpleNamespace(
                content=lambda: "ok",
                metadata={"usage": {"input_tokens": 2, "output_tokens": 1}},
            )

    gateway = ai_service.AiGatewayService(None)
    gateway.openai_subscription_fallback_model = "openai"
    gateway.gpt_default_reasoning_effort = "high"
    gateway.demo_mode = False
    gateway.models = {
        "openai": {
            "id": "openai",
            "enabled": True,
            "provider": "OpenAI",
            "endpoint": "Responses",
            "upstream-model": "openai",
            "demo-mode-when-key-missing": False,
        }
    }
    gateway.clients = {"openai": SimpleNamespace(chat_model=Model())}
    payload = gateway.apply_openai_subscription_fallback({
        "model": "gpt-5.6-sol",
        "messages": [{"role": "user", "content": "hello"}],
    })
    answer, usage, model, _ = asyncio.run(gateway.ainvoke_with_trace(payload))

    assert answer == "ok"
    assert usage == {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}
    assert model == "gpt-5.6-sol"
    assert calls[0][1]["_rose_upstream_model"] == "gpt-5.6-sol"


def test_group_model_mapping_is_applied_before_openai_fallback():
    gateway = ai_service.AiGatewayService(None)
    gateway.openai_subscription_fallback_model = "openai"
    gateway.models = {"openai": {"id": "openai", "enabled": True, "provider": "OpenAI"}}
    user = {"group_model_mappings": [{
        "enabled": True,
        "source_model": "gpt-6-astra",
        "source_effort": "max",
        "target_model": "gpt-5.6-sol",
        "target_effort": "high",
    }]}
    mapped, matched = UserGroupService.apply_model_mapping(
        user,
        {"model": "gpt-6-astra", "reasoning": {"effort": "max"}},
    )
    routed = gateway.apply_openai_subscription_fallback(mapped)

    assert matched is not None
    assert routed["model"] == "gpt-5.6-sol"
    assert routed["reasoning"] == {"effort": "high"}
    assert routed["_rose_model_id"] == "openai"
    assert routed["_rose_upstream_model"] == "gpt-5.6-sol"


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


def test_public_config_exposes_group_member_usage_visibility(monkeypatch):
    monkeypatch.setattr(config_controller, "get_config", lambda: {"rose": {}})
    assert config_controller.ConfigController().public_config().data["user_group_members_visible"] is True

    monkeypatch.setattr(config_controller, "get_config", lambda: {
        "rose": {"user-groups": {"member-usage-visible-to-users": False}}
    })
    assert config_controller.ConfigController().public_config().data["user_group_members_visible"] is False


def test_public_config_exposes_data_visualization_visibility(monkeypatch):
    monkeypatch.setattr(config_controller, "get_config", lambda: {"rose": {}})
    assert config_controller.ConfigController().public_config().data["data_visualization_visible"] is True

    monkeypatch.setattr(config_controller, "get_config", lambda: {
        "rose": {"data-visualization": {"visible-to-users": False}}
    })
    assert config_controller.ConfigController().public_config().data["data_visualization_visible"] is False


def test_public_config_expands_one_fallback_connection_into_real_model_ids(monkeypatch):
    monkeypatch.setattr(config_controller, "get_config", lambda: {"rose": {
        "models": [{
            "id": "openai",
            "enabled": True,
            "catalog-models": ["codex-auto-review", "gpt-5.6-sol", "gpt-6-astra"],
        }],
    }})
    models = config_controller.ConfigController().public_config().data["models"]
    assert [item["id"] for item in models] == ["codex-auto-review", "gpt-5.6-sol", "gpt-6-astra"]


def test_gateway_catalog_expands_fallback_connection_for_plaza_and_monitoring():
    gateway = ai_service.AiGatewayService(None)
    gateway.models = {"openai": {
        "id": "openai",
        "enabled": True,
        "provider": "OpenAI",
        "endpoint": "Responses",
        "group": "OpenAI API Fallback",
        "upstream-model": "openai",
        "catalog-models": ["codex-auto-review", "gpt-5.6-sol", "gpt-6-astra"],
        "api-key": "test-key",
        "currency": "CNY",
        "pricing": {
            "input-cny-per-million": 1,
            "output-cny-per-million": 2,
            "cache-cny-per-million": 0,
        },
    }}
    gateway.health = {"openai": gateway._health_record(gateway.models["openai"], "ok", "正常")}
    gateway.refresh_health = lambda force=False: gateway.health

    models = gateway.catalog()
    assert [item["id"] for item in models] == ["codex-auto-review", "gpt-5.6-sol", "gpt-6-astra"]
    assert all(item["status"] == "正常" for item in models)
    assert all(item["endpoint"] == "Responses" for item in models)
    assert [item["upstream_model"] for item in models] == ["codex-auto-review", "gpt-5.6-sol", "gpt-6-astra"]


def test_group_editor_uses_public_config_catalog_when_monitoring_is_disabled():
    root = Path(__file__).resolve().parents[1]
    admin = (root / "frontend/src/views/Admin.vue").read_text(encoding="utf-8")
    plaza = (root / "frontend/src/views/Models.vue").read_text(encoding="utf-8")
    assert "modelOptions: { type: Array" in admin
    assert "api.monitoring({ page, page_size: 12 })" not in admin
    assert "const publicModels = (this.modelOptions || [])" in admin
    assert "api.adminModelCatalog()" in admin
    assert "api.models({ page, page_size: 12 })" not in admin
    assert "api.models({" in plaza


def test_admin_model_catalog_combines_configured_and_subscription_models():
    from backend.controller.admin_model_catalog_controller import AdminModelCatalogController

    class Auth:
        def user_from_authorization(self, value):
            return {"role": "ADMIN"}

    class Gateway:
        def catalog(self):
            return [{"id": "gpt-local", "provider": "OpenAI"}]

    class Subscriptions:
        def catalog(self):
            return [{"id": "gpt-pool", "provider": "OpenAI"}, {"id": "gpt-local"}]

    result = AdminModelCatalogController(Auth(), Gateway(), Subscriptions()).catalog("Bearer test")
    assert [item["id"] for item in result.data["models"]] == ["gpt-local", "gpt-pool"]
