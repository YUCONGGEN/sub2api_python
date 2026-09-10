import asyncio
import json
from copy import deepcopy

import pytest

from backend.service.openai_chat_compatibility_service import OpenAIChatCompatibilityService


def test_chat_request_converts_messages_images_tools_and_reasoning():
    service = OpenAIChatCompatibilityService()
    payload = {
        "model": "gpt-5.4",
        "messages": [
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": [
                {"type": "text", "text": "Inspect this"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA", "detail": "low"}},
            ]},
            {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "lookup", "arguments": "{\"q\":\"x\"}"},
            }]},
            {"role": "tool", "tool_call_id": "call_1", "content": "found"},
        ],
        "tools": [{
            "type": "function",
            "function": {
                "name": "lookup",
                "description": "Look something up",
                "parameters": {"type": "object"},
            },
        }],
        "tool_choice": {"type": "function", "function": {"name": "lookup"}},
        "reasoning_effort": "medium",
        "max_completion_tokens": 32,
        "temperature": 1,
        "prompt_cache_key": "conversation-7",
    }

    converted = service.to_responses(payload)

    assert converted["model"] == "gpt-5.4"
    assert converted["stream"] is True
    assert converted["store"] is False
    assert not {"max_tokens", "max_completion_tokens", "max_output_tokens"} & converted.keys()
    assert converted["reasoning"] == {"effort": "medium"}
    assert converted["prompt_cache_key"] == "conversation-7"
    assert "temperature" not in converted
    assert converted["input"][0] == {"role": "system", "content": "Be concise."}
    assert converted["input"][1]["content"][1] == {
        "type": "input_image",
        "image_url": "data:image/png;base64,AAAA",
        "detail": "low",
    }
    assert converted["input"][2] == {
        "type": "function_call",
        "call_id": "call_1",
        "name": "lookup",
        "arguments": "{\"q\":\"x\"}",
    }
    assert converted["input"][3] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "found",
    }
    assert converted["tools"][0]["strict"] is False
    assert converted["tool_choice"] == {"type": "function", "name": "lookup"}


@pytest.mark.parametrize("field", ["max_tokens", "max_completion_tokens", "max_output_tokens"])
@pytest.mark.parametrize("limit", [1, 256, "256", 256.0, None])
def test_subscription_chat_accepts_but_does_not_forward_output_limits(field, limit):
    payload = {
        "model": "gpt-6-astra",
        "messages": [{"role": "user", "content": "hello"}],
        field: limit,
    }
    original = deepcopy(payload)

    converted = OpenAIChatCompatibilityService().to_responses(payload)

    assert not {"max_tokens", "max_completion_tokens", "max_output_tokens"} & converted.keys()
    assert payload == original


@pytest.mark.parametrize("field", ["max_tokens", "max_completion_tokens", "max_output_tokens"])
@pytest.mark.parametrize("limit", [True, False, 0, -1, 1.5, "1.5", "invalid", [], {}, float("inf")])
def test_subscription_chat_rejects_invalid_output_limits(field, limit):
    with pytest.raises(ValueError, match=field + " must be a positive integer"):
        OpenAIChatCompatibilityService().to_responses({
            "model": "gpt-6-astra",
            "messages": [{"role": "user", "content": "hello"}],
            field: limit,
        })


@pytest.mark.parametrize("model", ["gpt-6-astra", "gpt-5.6-sol", "o3", "custom-model-alias"])
def test_subscription_chat_filters_unsupported_defaults_without_changing_supported_fields(model):
    unsupported = {
        "max_tokens": 32,
        "max_completion_tokens": 64,
        "max_output_tokens": 128,
        "temperature": 1,
        "top_p": 1,
        "frequency_penalty": 0,
        "presence_penalty": 0,
        "metadata": {"client": "trae"},
        "safety_identifier": "client-user",
        "truncation": "auto",
        "stream_options": {"include_usage": True},
    }
    supported = {
        "model": model,
        "instructions": "Be concise.",
        "parallel_tool_calls": True,
        "prompt_cache_key": "conversation-7",
        "service_tier": "priority",
        "reasoning": {"effort": "high", "summary": "auto"},
    }
    payload = {**unsupported, **supported, "messages": [{"role": "user", "content": "hello"}]}
    original = deepcopy(payload)

    converted = OpenAIChatCompatibilityService().to_responses(payload)

    assert not unsupported.keys() & converted.keys()
    assert all(converted[key] == value for key, value in supported.items())
    assert payload == original


@pytest.mark.parametrize("fields, expected", [
    ({"reasoning_effort": "low", "reasoning": {"summary": "auto"}}, {"effort": "low", "summary": "auto"}),
    ({"reasoning_effort": "none", "reasoning": {"effort": None}}, {"effort": "none"}),
    ({"reasoning-effort": "medium"}, {"effort": "medium"}),
    ({"reasoning_effort": "high", "reasoning": {"effort": "max"}}, {"effort": "max"}),
])
def test_chat_bridge_keeps_explicit_effort_before_gateway_default(fields, expected):
    payload = {"model": "gpt-5.6-sol", "messages": [{"role": "user", "content": "hello"}], **fields}
    original = deepcopy(payload)
    assert OpenAIChatCompatibilityService().to_responses(payload)["reasoning"] == expected
    assert payload == original


@pytest.mark.parametrize("reasoning", ["high", [], False, 0])
def test_chat_bridge_does_not_default_malformed_reasoning(reasoning):
    with pytest.raises(ValueError, match="reasoning must be a JSON object"):
        OpenAIChatCompatibilityService().to_responses({
            "model": "gpt-5.6-sol", "messages": [{"role": "user", "content": "hello"}],
            "reasoning": reasoning,
        })


def test_non_stream_responses_converts_text_tools_and_usage():
    service = OpenAIChatCompatibilityService()
    converted = service.from_responses({
        "id": "resp_abc",
        "object": "response",
        "created_at": 123,
        "model": "gpt-5.4",
        "status": "completed",
        "output": [
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "short reason"}]},
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "hello"}]},
            {"type": "function_call", "call_id": "call_9", "name": "lookup", "arguments": "{\"q\":1}"},
        ],
        "usage": {
            "input_tokens": 10,
            "output_tokens": 4,
            "total_tokens": 14,
            "input_tokens_details": {"cached_tokens": 3},
            "output_tokens_details": {"reasoning_tokens": 2},
        },
    }, "requested-model")

    assert converted["id"] == "chatcmpl-abc"
    assert converted["object"] == "chat.completion"
    assert converted["model"] == "gpt-5.4"
    assert converted["choices"][0]["finish_reason"] == "tool_calls"
    assert converted["choices"][0]["message"]["content"] == "hello"
    assert converted["choices"][0]["message"]["reasoning_content"] == "short reason"
    assert converted["choices"][0]["message"]["tool_calls"][0]["id"] == "call_9"
    assert converted["usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 4,
        "total_tokens": 14,
        "prompt_tokens_details": {"cached_tokens": 3},
        "completion_tokens_details": {"reasoning_tokens": 2},
    }


def test_responses_stream_converts_split_sse_tool_calls_and_usage():
    service = OpenAIChatCompatibilityService()
    source = "".join([
        'event: response.created\ndata: {"type":"response.created","response":{"id":"resp_stream","created_at":456,"model":"gpt-5.4","status":"in_progress"}}\n\n',
        'event: response.output_item.added\ndata: {"type":"response.output_item.added","output_index":0,"item":{"type":"function_call","call_id":"call_stream","name":"lookup"}}\n\n',
        'event: response.function_call_arguments.delta\ndata: {"type":"response.function_call_arguments.delta","output_index":0,"delta":"{\\"q\\":"}\n\n',
        'event: response.function_call_arguments.delta\ndata: {"type":"response.function_call_arguments.delta","output_index":0,"delta":"1}"}\n\n',
        'event: response.completed\ndata: {"type":"response.completed","response":{"id":"resp_stream","created_at":456,"model":"gpt-5.4","status":"completed","output":[],"usage":{"input_tokens":8,"output_tokens":3,"total_tokens":11}}}\n\n',
    ]).encode("utf-8")

    async def raw_stream():
        cuts = [1, 8, 31, 77, 143, 211, 319, 470, len(source)]
        start = 0
        for end in cuts:
            yield source[start:end]
            start = end

    async def collect():
        return b"".join([
            chunk
            async for chunk in service.stream_from_responses(
                raw_stream(), "gpt-5.4", include_usage=True,
            )
        ])

    raw_output = asyncio.run(collect()).decode("utf-8")
    frames = []
    for block in raw_output.strip().split("\n\n"):
        data = block.removeprefix("data: ")
        if data != "[DONE]":
            frames.append(json.loads(data))

    assert frames[0]["id"] == "chatcmpl-stream"
    assert frames[0]["choices"][0]["delta"] == {"role": "assistant"}
    tool_start = frames[1]["choices"][0]["delta"]["tool_calls"][0]
    assert tool_start["index"] == 0
    assert tool_start["id"] == "call_stream"
    assert tool_start["function"]["name"] == "lookup"
    argument_deltas = [
        frame["choices"][0]["delta"]["tool_calls"][0]["function"]["arguments"]
        for frame in frames
        if frame.get("choices")
        and frame["choices"][0]["delta"].get("tool_calls")
        and "arguments" in frame["choices"][0]["delta"]["tool_calls"][0]["function"]
    ]
    assert "".join(argument_deltas) == '{"q":1}'
    assert frames[-2]["choices"][0]["finish_reason"] == "tool_calls"
    assert frames[-1]["choices"] == []
    assert frames[-1]["usage"]["total_tokens"] == 11
    assert raw_output.endswith("data: [DONE]\n\n")


def test_non_stream_chat_buffers_streaming_subscription_response():
    service = OpenAIChatCompatibilityService()
    source = "".join([
        'data: {"type":"response.created","response":{"id":"resp_buffered","created_at":789,"model":"gpt-5.4"}}\n\n',
        'data: {"type":"response.output_text.delta","output_index":0,"delta":"hel"}\n\n',
        'data: {"type":"response.output_text.delta","output_index":0,"delta":"lo"}\n\n',
        'data: {"type":"response.completed","response":{"id":"resp_buffered","created_at":789,"model":"gpt-5.4","status":"completed","output":[],"usage":{"input_tokens":2,"output_tokens":1}}}\n\n',
    ]).encode("utf-8")

    async def raw_stream():
        yield source[:37]
        yield source[37:181]
        yield source[181:]

    converted = asyncio.run(service.from_responses_stream(raw_stream(), "gpt-5.4"))

    assert converted["id"] == "chatcmpl-buffered"
    assert converted["choices"][0]["message"]["content"] == "hello"
    assert converted["choices"][0]["finish_reason"] == "stop"
    assert converted["usage"]["total_tokens"] == 3
