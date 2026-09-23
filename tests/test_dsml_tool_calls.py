import json

from backend.common.multimodal import (
    has_dsml_tool_call_marker,
    is_dsml_tool_call_prefix,
    parse_dsml_tool_calls,
)


def test_dsml_calls_alias_with_parameter_attributes_is_converted():
    raw = (
        '<｜｜DSML｜｜calls>'
        '<｜｜DSML｜｜invoke name="exec">'
        '<｜｜DSML｜｜parameter name="command" string="true">cmd /c "dir /b"'
        '</｜｜DSML｜｜parameter>'
        '<｜｜DSML｜｜parameter name="workdir" string="true">D:\\openai\\codex\\live'
        '</｜｜DSML｜｜parameter>'
        '</｜｜DSML｜｜invoke>'
        '</｜｜DSML｜｜calls>'
    )

    visible, calls = parse_dsml_tool_calls(raw)

    assert visible == ""
    assert len(calls) == 1
    assert calls[0]["type"] == "function"
    assert calls[0]["function"]["name"] == "exec"
    assert json.loads(calls[0]["function"]["arguments"]) == {
        "command": 'cmd /c "dir /b"',
        "workdir": r"D:\openai\codex\live",
    }
    assert "DSML" not in visible


def test_standard_dsml_tool_calls_wrapper_remains_supported():
    raw = (
        '<｜｜DSML｜｜tool_calls>'
        '<｜｜DSML｜｜invoke name="lookup">'
        '<｜｜DSML｜｜parameter name="q">hello &amp; goodbye'
        '</｜｜DSML｜｜parameter>'
        '</｜｜DSML｜｜invoke>'
        '</｜｜DSML｜｜tool_calls>'
    )

    visible, calls = parse_dsml_tool_calls(raw)

    assert visible == ""
    assert json.loads(calls[0]["function"]["arguments"]) == {"q": "hello & goodbye"}


def test_dsml_tags_with_pretty_printed_separator_spaces_are_converted():
    raw = (
        '<| | DSML | | calls>'
        '<| | DSML | | invoke name="exec">'
        '<| | DSML | | parameter name="cmd" string="true">Get-ChildItem'
        '</| | DSML | | parameter>'
        '</| | DSML | | invoke>'
        '</| | DSML | | calls>'
    )

    visible, calls = parse_dsml_tool_calls(raw)

    assert visible == ""
    assert calls[0]["function"]["name"] == "exec"
    assert json.loads(calls[0]["function"]["arguments"]) == {"cmd": "Get-ChildItem"}


def test_dsml_stream_helpers_recognize_calls_alias_and_partial_prefix():
    marker = "<｜｜DSML｜｜calls>"
    assert has_dsml_tool_call_marker(marker)
    assert is_dsml_tool_call_prefix(marker[:8])
    assert is_dsml_tool_call_prefix("<| | DSML")
    assert not is_dsml_tool_call_prefix("ordinary answer")


def test_plain_text_is_unchanged():
    assert parse_dsml_tool_calls("ordinary answer") == ("ordinary answer", [])
