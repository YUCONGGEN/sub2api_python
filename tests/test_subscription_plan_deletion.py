from pathlib import Path
from xml.etree import ElementTree

import pytest

from backend.service.store_service import StoreService


class PlanDeletionMapper:
    def __init__(self, *, exists=True, subscriptions=0, delete_result=1):
        self.exists = exists
        self.subscriptions = subscriptions
        self.delete_result = delete_result
        self.deleted_plan_ids = []

    def find_subscription_plan(self, plan_id):
        return {"id": plan_id, "name": "测试套餐"} if self.exists else None

    def count_subscriptions_for_plan(self, plan_id):
        return self.subscriptions

    def permanently_delete_subscription_plan(self, plan_id):
        self.deleted_plan_ids.append(plan_id)
        return self.delete_result


def service_with(mapper):
    service = StoreService.__new__(StoreService)
    service.mapper = mapper
    return service


def test_unused_subscription_plan_can_be_permanently_deleted():
    mapper = PlanDeletionMapper()

    assert service_with(mapper).permanently_delete_subscription_plan(12) is True
    assert mapper.deleted_plan_ids == [12]


def test_plan_with_subscription_history_cannot_be_permanently_deleted():
    mapper = PlanDeletionMapper(subscriptions=3)

    with pytest.raises(ValueError, match="已有 3 条用户订阅记录"):
        service_with(mapper).permanently_delete_subscription_plan(12)
    assert mapper.deleted_plan_ids == []


def test_missing_subscription_plan_is_not_deleted():
    mapper = PlanDeletionMapper(exists=False)

    assert service_with(mapper).permanently_delete_subscription_plan(404) is False
    assert mapper.deleted_plan_ids == []


def test_mapper_hard_delete_is_guarded_against_subscription_races():
    root = ElementTree.parse(Path(__file__).parents[1] / "backend/mappers/StoreMapper.xml").getroot()
    statements = {item.attrib.get("id"): " ".join("".join(item.itertext()).split()) for item in root}

    hard_delete = statements["permanently_delete_subscription_plan"]
    assert "DELETE FROM subscription_plans" in hard_delete
    assert "NOT EXISTS" in hard_delete
    assert "user_subscriptions" in hard_delete
