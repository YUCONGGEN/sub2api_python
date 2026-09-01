import asyncio
import json

from backend.service.claude_chat_compatibility_service import ClaudeChatCompatibilityService


def test_chat_request_converts_to_anthropic_messages_tools_and_session():
    service = ClaudeChatCompatibilityService()
    converted = service.to_anthropic({
        "model": "claude-sonnet-4-6",
        "messages": [
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": [
                {"type": "text", "text": "Inspect"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            ]},
            {"role": "assistant", "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "lookup", "arguments": "{\"q\":1}"},
            }]},
            {"role": "tool", "tool_call_id": "call_1", "content": "found"},
        ],
        "tools": [{"type": "function", "function": {
            "name": "lookup",
            "description": "Lookup",
            "parameters": {"type": "object"},
        }}],
        "tool_choice": {"type": "function", "function": {"name": "lookup"}},
        "max_completion_tokens": 256,
        "prompt_cache_key": "chat-session-1",
    })

    assert converted["system"] == "Be concise."
    assert converted["max_tokens"] == 256
    assert converted["metadata"] == {"user_id": "chat-session-1"}
    assert converted["messages"][0]["content"][1] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"},
    }
    assert converted["messages"][1]["content"][0] == {
        "type": "tool_use",
        "id": "call_1",
        "name": "lookup",
        "input": {"q": 1},
    }
    assert converted["messages"][2]["content"][0]["tool_use_id"] == "call_1"
    assert converted["tools"][0]["input_schema"] == {"type": "object"}
    assert converted["tool_choice"] == {"type": "tool", "name": "lookup"}


def test_anthropic_response_converts_to_chat_completion():
    service = ClaudeChatCompatibilityService()
    converted = service.from_anthropic({
        "id": "msg_abc",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-4-6",
        "content": [
            {"type": "thinking", "thinking": "short reason"},
            {"type": "text", "text": "hello"},
            {"type": "tool_use", "id": "tool_1", "name": "lookup", "input": {"q": 1}},
        ],
        "stop_reason": "tool_use",
        "usage": {
            "input_tokens": 7,
            "cache_read_input_tokens": 2,
            "cache_creation_input_tokens": 1,
            "output_tokens": 4,
        },
    }, "requested-model")

    assert converted["id"] == "chatcmpl-abc"
    assert converted["model"] == "claude-sonnet-4-6"
    assert converted["choices"][0]["finish_reason"] == "tool_calls"
    assert converted["choices"][0]["message"]["content"] == "hello"
    assert converted["choices"][0]["message"]["reasoning_content"] == "short reason"
    assert converted["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] == '{"q":1}'
    assert converted["usage"]["prompt_tokens"] == 10
    assert converted["usage"]["completion_tokens"] == 4


def test_anthropic_stream_converts_to_chat_sse():
    service = ClaudeChatCompatibilityService()
    source = "".join([
        'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_stream","model":"claude-sonnet-4-6","usage":{"input_tokens":5}}}\n\n',
        'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n',
        'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"hello"}}\n\n',
        'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":2}}\n\n',
        'event: message_stop\ndata: {"type":"message_stop"}\n\n',
    ]).encode("utf-8")

    async def raw_stream():
        yield source[:19]
        yield source[19:173]
        yield source[173:]

    async def collect():
        return b"".join([
            chunk async for chunk in service.stream_from_anthropic(
                raw_stream(), "claude-sonnet-4-6", include_usage=True,
            )
        ])

    raw_output = asyncio.run(collect()).decode("utf-8")
    frames = [
        json.loads(block.removeprefix("data: "))
        for block in raw_output.strip().split("\n\n")
        if block.removeprefix("data: ") != "[DONE]"
    ]

    assert frames[0]["id"] == "chatcmpl-stream"
    assert frames[0]["choices"][0]["delta"] == {"role": "assistant"}
    assert frames[1]["choices"][0]["delta"] == {"content": "hello"}
    assert frames[-2]["choices"][0]["finish_reason"] == "stop"
    assert frames[-1]["choices"] == []
    assert frames[-1]["usage"] == {
        "prompt_tokens": 5,
        "completion_tokens": 2,
        "total_tokens": 7,
    }
    assert raw_output.endswith("data: [DONE]\n\n")
