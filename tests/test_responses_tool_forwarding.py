from backend.service.ai_service import AiGatewayService


def test_responses_additional_tools_are_forwarded_to_chat_upstream():
    options = AiGatewayService._request_options({
        "input": [{
            "type": "additional_tools",
            "tools": [{
                "type": "function",
                "name": "functions.exec",
                "description": "run a command",
                "input_schema": {"type": "object", "properties": {"cmd": {"type": "string"}}},
            }],
        }],
        "model": "deepseek-v4-pro",
    }, model="deepseek-v4-pro")

    assert options["tools"] == [{
        "type": "function",
        "function": {
            "name": "functions.exec",
            "description": "run a command",
            "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}},
        },
    }]


def test_responses_top_level_and_additional_tools_are_merged():
    options = AiGatewayService._request_options({
        "tools": [{"type": "function", "name": "lookup", "parameters": {"type": "object"}}],
        "input": [{"type": "additional_tools", "tools": [{"type": "function", "name": "exec"}]}],
        "model": "deepseek-v4-pro",
    }, model="deepseek-v4-pro")

    assert [tool["function"]["name"] for tool in options["tools"]] == ["lookup", "exec"]
