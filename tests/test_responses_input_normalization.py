from backend.common.multimodal import normalize_chat_messages, normalize_content
from backend.protocol.openai_adapter import (
    _normalize_responses_function_call_ids,
    _responses_input_to_messages,
    _responses_tool_output,
)


def test_responses_only_parts_are_safe_for_chat_upstreams():
    content = normalize_content([
        {"type": "output_text", "text": "answer"},
        {"type": "reasoning", "summary": [{"type": "summary_text", "text": "private"}]},
        {"type": "compaction", "id": "ctx_1"},
        {"type": "input_text", "text": "follow-up"},
    ], for_chat=True)

    assert content == [
        {"type": "text", "text": "answer"},
        {"type": "text", "text": "follow-up"},
    ]


def test_responses_input_converts_history_and_keeps_complete_tool_pair():
    messages = _responses_input_to_messages([
        {
            "role": "assistant",
            "content": [
                {"type": "output_text", "text": "I will look that up."},
                {"type": "reasoning", "summary": []},
            ],
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "lookup", "arguments": "{}"},
            }],
        },
        {"type": "function_call_output", "call_id": "call_1", "output": "result"},
    ])

    assert messages == [
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "I will look that up."}],
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "lookup", "arguments": "{}"},
            }],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "result"},
    ]


def test_orphaned_tool_calls_and_tool_messages_are_removed():
    messages = normalize_chat_messages([
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "partial"}],
            "tool_calls": [{"id": "missing", "type": "function", "function": {"name": "x", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "stray", "content": "stale"},
    ])

    assert messages == [{"role": "assistant", "content": [{"type": "text", "text": "partial"}]}]


def test_compaction_only_input_does_not_create_empty_user_message():
    assert _responses_input_to_messages([
        {"type": "compaction", "id": "ctx_1"},
        {"type": "reasoning", "summary": []},
    ]) == []


def test_legacy_function_call_item_ids_are_repaired_without_changing_call_ids():
    payload = {
        "model": "gpt-6-sol",
        "input": [
            {"type": "function_call", "id": "call_00_old", "call_id": "call_00_old", "name": "lookup", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call_00_old", "output": "ok"},
            {"type": "compaction", "encrypted_content": "opaque"},
        ],
    }

    normalized = _normalize_responses_function_call_ids(payload)

    assert normalized["input"][0]["id"] == "fc_00_old"
    assert normalized["input"][0]["call_id"] == "call_00_old"
    assert normalized["input"][1] == payload["input"][1]
    assert normalized["input"][2] == payload["input"][2]
    assert payload["input"][0]["id"] == "call_00_old"


def test_custom_tool_call_ids_keep_the_ctc_prefix():
    payload = {
        "input": [{"type": "custom_tool_call", "id": "ctc_00_custom", "call_id": "call_00_custom"}],
    }

    normalized = _normalize_responses_function_call_ids(payload)

    assert normalized["input"][0]["id"] == "ctc_00_custom"


def test_fallback_responses_tool_output_separates_item_and_call_ids():
    item = _responses_tool_output({
        "id": "call_00_tool",
        "function": {"name": "lookup", "arguments": "{}"},
    }, {})

    assert item["id"] == "fc_00_tool"
    assert item["call_id"] == "call_00_tool"
