from backend.service.user_group_service import UserGroupService


def _user(*mappings):
    return {"group_model_mappings": list(mappings)}


def test_group_mapping_rewrites_model_and_effort_without_mutating_request():
    payload = {"model": "gpt-5.6-sol", "reasoning": {"effort": "xhigh", "summary": "auto"}}
    outgoing, matched = UserGroupService.apply_model_mapping(
        _user({
            "id": 1,
            "enabled": True,
            "source_model": "gpt-5.6-sol",
            "source_effort": "xhigh",
            "target_model": "gpt-5.6-sol",
            "target_effort": "high",
        }),
        payload,
    )

    assert matched["id"] == 1
    assert outgoing["model"] == "gpt-5.6-sol"
    assert outgoing["reasoning"] == {"effort": "high", "summary": "auto"}
    assert payload["reasoning"]["effort"] == "xhigh"


def test_exact_effort_mapping_wins_over_wildcard_and_can_change_model():
    outgoing, matched = UserGroupService.apply_model_mapping(
        _user(
            {"id": 1, "enabled": True, "source_model": "gpt-5.6-sol", "source_effort": "*", "target_model": "gpt-5.6-luna", "target_effort": "low"},
            {"id": 2, "enabled": True, "source_model": "gpt-5.6-sol", "source_effort": "xhigh", "target_model": "gpt-5.6-terra", "target_effort": "high"},
        ),
        {"model": "gpt-5.6-sol", "reasoning_effort": "xhigh"},
    )

    assert matched["id"] == 2
    assert outgoing == {"model": "gpt-5.6-terra", "reasoning": {"effort": "high"}}


def test_mapping_only_applies_when_selected_and_enabled():
    payload = {"model": "gpt-5.6-sol", "reasoning": {"effort": "xhigh"}}
    outgoing, matched = UserGroupService.apply_model_mapping(
        _user({"id": 1, "enabled": False, "source_model": "gpt-5.6-sol", "source_effort": "xhigh", "target_model": "gpt-5.6-terra", "target_effort": "high"}),
        payload,
    )

    assert matched is None
    assert outgoing == payload
    assert outgoing is not payload


def test_unspecified_and_wildcard_efforts_are_distinct():
    mappings = _user(
        {"id": 1, "enabled": True, "source_model": "gpt-5.6-sol", "source_effort": "", "target_model": "gpt-5.6-sol", "target_effort": "medium"},
        {"id": 2, "enabled": True, "source_model": "gpt-5.6-sol", "source_effort": "*", "target_model": "gpt-5.6-sol", "target_effort": "low"},
    )
    unspecified, first = UserGroupService.apply_model_mapping(mappings, {"model": "gpt-5.6-sol"})
    explicit, second = UserGroupService.apply_model_mapping(mappings, {"model": "gpt-5.6-sol", "reasoning": {"effort": "high"}})

    assert first["id"] == 1
    assert unspecified["reasoning"]["effort"] == "medium"
    assert second["id"] == 2
    assert explicit["reasoning"]["effort"] == "low"
