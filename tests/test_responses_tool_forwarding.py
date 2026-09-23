from backend.service.ai_service import AiGatewayService
from backend.protocol.openai_adapter import _responses_namespace_map, _responses_tool_output


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


def test_namespace_tools_round_trip_between_responses_and_chat_names():
    payload = {"input": [{
        "type": "additional_tools",
        "tools": [{"type": "namespace", "name": "functions", "tools": [{"type": "function", "name": "exec"}]}],
    }]}
    namespaces = _responses_namespace_map(payload)
    output = _responses_tool_output({
        "id": "call_1", "function": {"name": "functions__exec", "arguments": '{"cmd":"pwd"}'},
    }, namespaces)

    assert output["name"] == "exec"
    assert output["namespace"] == "functions"
    options = AiGatewayService._request_options(payload, model="deepseek-v4-pro")
    assert options["tools"][0]["function"]["name"] == "functions__exec"
