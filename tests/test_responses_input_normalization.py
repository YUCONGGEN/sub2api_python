from backend.common.multimodal import normalize_chat_messages, normalize_content
from backend.protocol.openai_adapter import _responses_input_to_messages


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
