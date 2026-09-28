import asyncio
from types import SimpleNamespace

from backend.controller.admin_controller import AdminController
from backend.service.user_group_service import UserGroupService
from backend.common.routing_trace import routing_scope, record_mapping, current_routing_trace


def test_preview_uses_same_rules_without_mutating_request():
    payload = {"model": "old", "reasoning": {"effort": "high"}}
    rule = {"id": 1, "enabled": True, "source_model": "old", "source_effort": "", "target_model": "new", "target_effort": "low"}
    user = {"group_allowed_models": ["*"], "effective_group_id": 42, "group_model_mappings": [rule]}
    preview = UserGroupService.preview_model_mapping(user, payload)
    assert preview["mapping_id"] is None
    assert preview["rules"][0]["reason"] == "只匹配未指定强度的请求"
    rule["source_effort"] = "*"
    preview = UserGroupService.preview_model_mapping(user, payload)
    assert preview["mapped_model"] == "new"
    assert preview["mapped_effort"] == "low"
    assert preview["group_id"] == 42
    assert payload == {"model": "old", "reasoning": {"effort": "high"}}
    user["group_allowed_models"] = []
    assert not UserGroupService.preview_model_mapping(user, payload)["allowed"]


def test_routing_trace_is_isolated_and_reset_on_failure():
    @routing_scope
    async def request(model, fail=False):
        record_mapping({}, {"model": model}, None)
        await asyncio.sleep(0)
        assert current_routing_trace()["requested_model"] == model
        if fail:
            raise ValueError("test")
        return current_routing_trace()

    async def run():
        results = await asyncio.gather(request("a"), request("b", True), return_exceptions=True)
        assert results[0]["requested_model"] == "a"
        assert isinstance(results[1], ValueError)
        assert current_routing_trace() == {}
    asyncio.run(run())


def test_preview_controller_requires_admin_and_validates_user():
    store = SimpleNamespace(find_user=lambda user_id: {"group_allowed_models": ["*"]} if user_id == 1 else None)
    auth = SimpleNamespace(store=store, user_from_authorization=lambda _: {"role": "USER"})
    controller = AdminController(auth, None, None)
    assert controller.preview_model_mapping({"user_id": 1, "model": "test"}, "token").code == 403
    auth.user_from_authorization = lambda _: {"role": "ADMIN"}
    assert controller.preview_model_mapping({"user_id": "bad"}, "token").code == 400
    assert controller.preview_model_mapping({"user_id": 2, "model": "test"}, "token").code == 404
    result = controller.preview_model_mapping({"user_id": 1, "model": "test"}, "token")
    assert result.data["preview"]["mapped_model"] == "test"
