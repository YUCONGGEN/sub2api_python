"""Grant limits constrain free coverage, never the user's overall usage."""
from types import SimpleNamespace

import pytest

from backend.service import store_service
from backend.service.store_service import StoreService


class FallbackMapper:
    def __init__(self, dimension='daily_tokens', free_used=100, paid_used=0, balance=0):
        self.balance = balance
        self.free = {'id': 11, dimension: 100}
        self.paid = {'id': 21, 'daily_tokens': 100}
        self.free_usage = {'free_tokens': free_used, 'free_cost': free_used}
        self.paid_usage = {'subscription_tokens': paid_used, 'subscription_cost': 0}
        self.allocations = []
        self.usage = None
        self.wallet_charges = []

    def ensure_billing_lock(self, *args):
        return 1

    def acquire_billing_lock(self, *args):
        return 1

    def find_balance(self, user_id):
        return {'id': user_id, 'enabled': 1, 'balance': self.balance}

    def find_active_quota_policies(self, *args):
        return [self.free] if self.free else []

    def find_active_subscriptions(self, *args):
        return [self.paid] if self.paid else []

    def quota_usage_totals(self, *args):
        return self.free_usage

    def subscription_usage_totals(self, *args):
        return self.paid_usage

    def update_balance_after_charge(self, user_id, cost, minimum_balance, minimum_usable):
        if self.balance < minimum_usable or self.balance - cost < minimum_balance:
            return 0
        self.balance -= cost
        self.wallet_charges.append(cost)
        return 1

    def insert_usage(self, usage):
        usage['id'] = 1
        self.usage = dict(usage)

    def insert_usage_allocation(self, allocation):
        self.allocations.append(dict(allocation))

    def find_usage(self, usage_id):
        return self.usage


@pytest.fixture(autouse=True)
def fixed_billing_config(monkeypatch):
    monkeypatch.setattr(store_service, 'get_config', lambda: {
        'rose': {'billing': {'min-usable-balance': .001, 'minimum-balance': -.1}}})


def service(mapper):
    return StoreService(SimpleNamespace(mapper=mapper))


@pytest.mark.parametrize('dimension', ['daily_amount', 'daily_tokens', 'hourly_tokens'])
def test_exhausted_free_limit_falls_through_to_paid_plan_even_with_empty_wallet(dimension):
    mapper = FallbackMapper(dimension=dimension)
    store = service(mapper)
    assert store.has_usable_balance(7)
    accepted, usage = store.charge(7, 'test-model', 60, 40, 1)
    assert accepted
    assert usage['billing_source'] == 'SUBSCRIPTION'
    assert usage['subscription_tokens'] == 100 and usage['subscription_cost'] == 1
    assert usage['free_tokens'] == usage['free_cost'] == usage['wallet_cost'] == 0
    assert mapper.wallet_charges == []


@pytest.mark.parametrize('dimension', ['daily_amount', 'daily_tokens', 'hourly_tokens'])
def test_exhausted_free_and_paid_limits_fall_through_to_wallet(dimension):
    mapper = FallbackMapper(dimension=dimension, paid_used=100, balance=10)
    store = service(mapper)
    assert store.has_usable_balance(7)
    accepted, usage = store.charge(7, 'test-model', 60, 40, 1)
    assert accepted and usage['billing_source'] == 'WALLET'
    assert usage['wallet_cost'] == 1 and usage['wallet_tokens'] == 100
    assert mapper.balance == 9
    assert mapper.allocations == []


@pytest.mark.parametrize('dimension', ['daily_amount', 'daily_tokens', 'hourly_tokens'])
def test_reject_only_when_no_free_paid_or_wallet_coverage_is_available(dimension):
    mapper = FallbackMapper(dimension=dimension, paid_used=100)
    store = service(mapper)
    assert not store.has_usable_balance(7)
    accepted, _ = store.charge(7, 'test-model', 60, 40, 1)
    assert not accepted
    assert mapper.usage is None and mapper.allocations == []


def test_single_request_spills_free_then_paid_then_wallet_without_double_charging():
    mapper = FallbackMapper(free_used=50, balance=10)
    accepted, usage = service(mapper).charge(7, 'test-model', 200, 50, 2.5)
    assert accepted
    assert usage['billing_source'] == 'FREE+SUBSCRIPTION+WALLET'
    assert (usage['free_tokens'], usage['subscription_tokens'], usage['wallet_tokens']) == (50, 100, 100)
    assert (usage['free_cost'], usage['subscription_cost'], usage['wallet_cost']) == (.5, 1, 1)
    assert [a['kind'] for a in mapper.allocations] == ['FREE', 'SUBSCRIPTION']
    assert mapper.wallet_charges == [1]


def test_free_coverage_is_used_before_paid_plan_or_wallet():
    mapper = FallbackMapper(free_used=0, balance=10)
    accepted, usage = service(mapper).charge(7, 'test-model', 60, 40, 1)
    assert accepted and usage['billing_source'] == 'FREE'
    assert mapper.balance == 10
    assert usage['subscription_tokens'] == usage['wallet_tokens'] == 0


def test_no_active_free_grant_still_allows_paid_plan_then_wallet():
    mapper = FallbackMapper()
    mapper.free = None  # Expired/disabled grants are absent from the active query.
    assert service(mapper).has_usable_balance(7)
    assert service(mapper).charge(7, 'test-model', 60, 40, 1)[1]['billing_source'] == 'SUBSCRIPTION'
    mapper.paid = None
    mapper.balance = 10
    assert service(mapper).has_usable_balance(7)
    assert service(mapper).charge(7, 'test-model', 60, 40, 1)[1]['billing_source'] == 'WALLET'
