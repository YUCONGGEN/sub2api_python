from types import SimpleNamespace
import pytest
from backend.controller import auth_controller
from backend.service import store_service
from backend.controller.auth_controller import AuthController
from backend.service.store_service import StoreService
from backend.controller.model_controller import ModelController
from backend.service.user_group_service import UserGroupService


@pytest.mark.parametrize('billing', [{}, {'daily-login-bonus': 0}, {'daily-login-bonus': 'invalid'}])
def test_login_does_not_grant_balance_by_default(monkeypatch, billing):
    monkeypatch.setattr(store_service, 'get_config', lambda: {'rose': {'billing': billing}})
    calls = []
    service = StoreService.__new__(StoreService)
    service.mapper = SimpleNamespace(update_login=lambda *args: calls.append(args))
    service.update_login(7)
    assert calls[0][0] == 7
    assert calls[0][2] == 0


def test_registration_defaults_to_zero(monkeypatch):
    monkeypatch.setattr(auth_controller, 'get_config', lambda: {'rose': {'billing': {}}})
    values = []
    def create(*args, **kwargs):
        values.append(kwargs)
        return {'id': 1}, {'id': 1, 'api_key': 'test-only'}
    store = SimpleNamespace(find_by_username=lambda _: None, create_user_with_default_key=create, public_user=lambda u: u)
    auth = SimpleNamespace(issue_token=lambda *args: 'test-token')
    result = AuthController(store, auth).register({'username': 'tester', 'password': 'password123'}, '', '')
    assert result.code == 200
    assert values[0]['balance'] == 0


def test_regular_user_can_view_allowed_model_prices():
    models = [{'id': 'allowed', 'input': 2, 'output': 10, 'currency': 'CNY', 'pricing': {'multiplier': 1.5}}, {'id': 'restricted'}]
    user = {'id': 7, 'role': 'USER', 'group_allowed_models': ['allowed']}
    groups = UserGroupService.__new__(UserGroupService)
    controller = ModelController(SimpleNamespace(catalog=lambda: models, model_name='allowed'),
        SimpleNamespace(catalog=lambda: []), SimpleNamespace(user_from_authorization=lambda _: user), groups)
    result = controller.models(1, 12, '', '', '', '', 'test-token').data
    assert result['models'] == models[:1]
