import pytest

from backend.service.store_service import StoreService


def test_model_mapping_validation_allows_empty_source_and_target_models():
    values = StoreService._model_mapping_values({
        "name": "全部模型统一强度",
        "source_model": "",
        "source_effort": "*",
        "target_model": "",
        "target_effort": "medium",
        "enabled": True,
    })

    assert values["source_model"] == ""
    assert values["target_model"] == ""


@pytest.mark.parametrize("field", ["source_model", "target_model"])
def test_model_mapping_validation_rejects_overlong_models(field):
    payload = {
        "name": "模型映射",
        "source_model": "",
        "source_effort": "*",
        "target_model": "",
        "target_effort": "medium",
        "enabled": True,
    }
    payload[field] = "m" * 161

    with pytest.raises(ValueError, match="不能超过 160"):
        StoreService._model_mapping_values(payload)
