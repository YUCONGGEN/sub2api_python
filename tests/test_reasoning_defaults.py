import asyncio
import logging
from copy import deepcopy
from types import SimpleNamespace

import pytest

from backend.common.reasoning import configured_gpt_reasoning_effort, default_gpt_reasoning_effort, with_responses_reasoning
from backend.service import ai_service, subscription_gateway_service
from backend.service.ai_service import AiGatewayService, ReliableOpenAIChatModel
from backend.service.subscription_gateway_service import SubscriptionGatewayService


@pytest.mark.parametrize("value, expected", [(None, "high"), ("", "high"), ("  ", "high"), ("medium", "medium"), ("LOW", "low")])
def test_reasoning_default_is_configurable(value, expected):
    assert configured_gpt_reasoning_effort({}) == "high"
    assert configured_gpt_reasoning_effort({"rose": {"proxy": {"default-gpt-reasoning-effort": value}}}) == expected
    for invalid in ("none", "ultra", "typo"):
        with pytest.raises(ValueError, match="default-gpt-reasoning-effort"):
            configured_gpt_reasoning_effort({"rose": {"proxy": {"default-gpt-reasoning-effort": invalid}}})


def test_both_services_load_same_yaml_default_at_startup(monkeypatch):
    config = {"rose": {
        "proxy": {"default-gpt-reasoning-effort": "medium"},
        "models": [{"id": "gpt-5.6-sol", "api-key": ""}],
    }}
    monkeypatch.setattr(ai_service, "get_config", lambda: config)
    monkeypatch.setattr(subscription_gateway_service, "get_config", lambda: config)
    monkeypatch.setattr(ai_service.threading, "Thread", lambda *a, **kw: SimpleNamespace(start=lambda: None))
    ordinary = AiGatewayService(None)
    ordinary.init()
    subscription = SubscriptionGatewayService(None, None)
    subscription.logger = logging.getLogger("test.reasoning.config")
    subscription.init()
    assert ordinary.gpt_default_reasoning_effort == "medium"
    assert subscription.gpt_default_reasoning_effort == "medium"
    # No per-request YAML lookup or background reread.
    config["rose"]["proxy"]["default-gpt-reasoning-effort"] = "low"
    assert ordinary.gpt_default_reasoning_effort == "medium"
    assert subscription.gpt_default_reasoning_effort == "medium"


@pytest.mark.parametrize("model", [
    "gpt-5", "gpt-5-mini", "gpt-5-nano", "gpt-5-pro", "gpt-5.1",
    "gpt-5.1-codex-mini", "gpt-5.1-codex-max", "gpt-5.3-codex-spark",
    "gpt-5.4", "gpt-5.5", "gpt-5.6", "gpt-5.6-sol", "gpt-5.6-terra",
    "gpt-5.6-luna", "gpt-6-astra", "gpt-5-2025-08-07",
])
def test_supported_gpt_defaults_to_high(model):
    assert default_gpt_reasoning_effort(model) == "high"


@pytest.mark.parametrize("model", [
    None, "", "gpt-test", "gpt-4", "gpt-4.1", "gpt-4o", "gpt-4o-mini",
    "gpt-5-chat-latest", "gpt-5.6-chat-latest", "gpt-image-1", "gpt-audio",
    "gpt-realtime", "gpt-5.6-sol-audio", "o3", "deepseek-v4-flash", "claude-sonnet-4-5",
])
def test_other_models_are_not_given_a_gpt_default(model):
    payload = {"model": model, "input": "hello"}
    assert default_gpt_reasoning_effort(model) is None
    assert with_responses_reasoning(payload) == payload


@pytest.mark.parametrize("fields", [
    {}, {"reasoning_effort": None}, {"reasoning_effort": ""}, {"reasoning_effort": " \t"},
    {"reasoning": None}, {"reasoning": {}}, {"reasoning": {"effort": None}},
    {"reasoning": {"effort": "", "summary": "auto"}}, {"reasoning-effort": ""},
])
def test_missing_effort_normalizes_without_mutating_input(fields):
    payload = {"model": "gpt-5.6-sol", "input": [{"role": "user", "content": "hello"}], **fields}
    original = deepcopy(payload)
    outgoing = with_responses_reasoning(payload)
    assert outgoing["reasoning"]["effort"] == "high"
    assert "reasoning_effort" not in outgoing
    assert "reasoning-effort" not in outgoing
    assert outgoing["input"] is payload["input"]  # no full prompt copy on the forwarding path
    if isinstance(payload.get("reasoning"), dict) and "summary" in payload["reasoning"]:
        assert outgoing["reasoning"]["summary"] == "auto"
    assert payload == original
    assert with_responses_reasoning(outgoing) == outgoing


@pytest.mark.parametrize("effort", ["minimal", "low", "medium", "high", "xhigh", "max"])
def test_explicit_effort_is_not_replaced(effort):
    for fields in (
        {"reasoning_effort": effort},
        {"reasoning-effort": effort},
        {"reasoning": {"effort": effort, "summary": "auto"}},
        {"reasoning": {"summary": "auto"}, "reasoning_effort": effort},
        {"reasoning": {"effort": ""}, "reasoning_effort": effort},
    ):
        payload = {"model": "gpt-5.6-sol", **fields}
        original = deepcopy(payload)
        assert with_responses_reasoning(payload)["reasoning"]["effort"] == effort
        assert payload == original


def test_nested_effort_wins_over_conflicting_aliases():
    outgoing = with_responses_reasoning({
        "model": "gpt-6-astra", "reasoning": {"effort": "low", "summary": "auto"},
        "reasoning_effort": "medium", "reasoning-effort": "max",
    })
    assert outgoing == {"model": "gpt-6-astra", "reasoning": {"effort": "low", "summary": "auto"}}


@pytest.mark.parametrize("fields", [
    {"reasoning_effort": None}, {"reasoning_effort": "none"}, {"reasoning_effort": "ultra"},
    {"reasoning_effort": False}, {"reasoning_effort": 0}, {"reasoning_effort": "invalid"},
    {"reasoning": {"effort": "none", "summary": "auto"}},
])
def test_missing_none_and_invalid_efforts_fall_back_to_high(fields):
    payload = {"model": "gpt-6-astra", **fields}
    outgoing = with_responses_reasoning(payload)
    assert outgoing["reasoning"]["effort"] == "high"
    if isinstance(fields.get("reasoning"), dict) and "summary" in fields["reasoning"]:
        assert outgoing["reasoning"]["summary"] == "auto"


@pytest.mark.parametrize("reasoning", ["invalid", [], False])
def test_malformed_reasoning_container_remains_for_boundary_validation(reasoning):
    payload = {"model": "gpt-6-astra", "reasoning": reasoning}
    assert with_responses_reasoning(payload) == payload


def test_api_options_preserve_explicit_effort_and_use_resolved_upstream_name():
    assert AiGatewayService._request_options({"model": "gpt-5.6-sol"}) == {"reasoning_effort": "high"}
    assert AiGatewayService._request_options({"model": "gpt-5.6-sol", "reasoning_effort": "none"}) == {"reasoning_effort": "high"}
    assert AiGatewayService._request_options({"model": "gpt-5.6-sol", "reasoning_effort": "turbo"}) == {"reasoning_effort": "high"}
    assert AiGatewayService._request_options({"model": "gpt-5.6-sol", "reasoning_effort": " XHIGH "}) == {"reasoning_effort": "xhigh"}
    assert AiGatewayService._request_options({"model": "gpt-6-astra", "reasoning": {"effort": "low"}}) == {"reasoning_effort": "low"}
    assert AiGatewayService._request_options({"model": "gpt-6-astra"}, model="deepseek-v4-flash") == {}
    assert AiGatewayService._request_options({"model": "custom-alias"}, model="gpt-5.6-sol") == {"reasoning_effort": "high"}
    assert AiGatewayService._request_options({"model": "deepseek-v4-flash"}, reasoning_effort="medium") == {"reasoning_effort": "medium"}


def test_responses_tools_are_normalized_for_chat_upstreams():
    options = AiGatewayService._request_options({
        "model": "deepseek-v4-pro",
        "tools": [
            {"type": "function", "name": "apply_patch", "description": "edit", "parameters": {"type": "object"}},
            {"type": "custom", "name": "lookup", "input_schema": {"type": "object"}},
            {"type": "web_search_preview"},
        ],
    })
    assert options["tools"] == [
        {"type": "function", "function": {"name": "apply_patch", "description": "edit", "parameters": {"type": "object"}}},
        {"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}},
    ]


@pytest.mark.parametrize("choice_type", ["function", "custom"])
def test_responses_tool_choice_is_normalized_for_chat_upstreams(choice_type):
    options = AiGatewayService._request_options({
        "model": "deepseek-v4-pro",
        "tools": [{"type": "function", "name": "lookup", "parameters": {"type": "object"}}],
        "tool_choice": {"type": choice_type, "name": "lookup"},
    })
    assert options["tool_choice"] == {"type": "function", "function": {"name": "lookup"}}


def test_unsupported_responses_builtin_tool_and_choice_are_omitted():
    options = AiGatewayService._request_options({
        "model": "deepseek-v4-pro",
        "tools": [{"type": "apply_patch"}],
        "tool_choice": {"type": "apply_patch"},
        "parallel_tool_calls": True,
    })
    assert "tools" not in options
    assert "tool_choice" not in options
    assert "parallel_tool_calls" not in options


def test_developer_role_is_normalized_only_for_chat_wire_payloads():
    model = ReliableOpenAIChatModel.__new__(ReliableOpenAIChatModel)
    model.model = "deepseek-v4-pro"
    model.temperature = 0.7
    model.endpoint = "chat"
    payload = model._http_payload([
        {"role": "developer", "content": "instructions"},
        {"role": "user", "content": "hello"},
    ])
    assert [item["role"] for item in payload["messages"]] == ["system", "user"]

    model.endpoint = "responses"
    payload = model._http_payload([{"role": "developer", "content": "instructions"}])
    assert payload["input"][0]["role"] == "developer"


@pytest.mark.parametrize("mode", ["sync", "async", "stream", "async_stream"])
@pytest.mark.parametrize("configured_effort", ["high", "medium"])
def test_all_api_invocation_paths_send_configured_effort_for_resolved_default_model(mode, configured_effort):
    requests = []
    response = SimpleNamespace(content=lambda: "hello", metadata={"usage": {"prompt_tokens": 4, "completion_tokens": 2}})

    class RecordingModel(ReliableOpenAIChatModel):
        def __init__(self):
            # Exercise the real wire-payload builder without clients, network,
            # API keys, the framework's startup hooks, or production config.
            self.model = "gpt-5.6-sol"
            self.temperature = 1

        def call(self, messages, options=None):
            requests.append(self._http_payload(messages, options))
            return response

        async def acall(self, messages, options=None):
            return self.call(messages, options)

        def stream(self, messages, options=None):
            requests.append(self._http_payload(messages, options, stream=True))
            yield response

        async def astream(self, messages, options=None):
            for item in self.stream(messages, options):
                yield item

    service = AiGatewayService(None)
    service.gpt_default_reasoning_effort = configured_effort
    service.model_name = "custom-alias"
    service.models = {"custom-alias": {"id": "custom-alias", "upstream-model": "gpt-5.6-sol", "reasoning-effort": "medium"}}
    service.clients = {"custom-alias": SimpleNamespace(chat_model=RecordingModel())}
    payload = {"messages": [{"role": "user", "content": "hello"}]}
    original = deepcopy(payload)
    if mode == "sync":
        service.invoke_with_trace(payload)
    elif mode == "async":
        asyncio.run(service.ainvoke_with_trace(payload))
    elif mode == "stream":
        list(service.stream_with_trace(payload))
    else:
        async def collect():
            return [item async for item in service.astream_with_trace(payload)]
        asyncio.run(collect())

    assert len(requests) == 1
    assert requests[0]["model"] == "gpt-5.6-sol"
    assert requests[0]["reasoning_effort"] == configured_effort
    assert "reasoning" not in requests[0]
    assert requests[0]["stream"] == (mode in {"stream", "async_stream"})
    assert requests[0]["messages"] == original["messages"]
    assert payload == original
