import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree

import pytest

from backend.controller.admin_controller import AdminController
from backend.controller.auth_controller import AuthController
from backend.controller import auth_controller
from backend.service.store_service import StoreService


class SqlMapper:
    def __init__(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.xml = ElementTree.parse(Path(__file__).parents[1] / 'backend/mappers/StoreMapper.xml')
        self.db.executescript('''
            CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT, email TEXT, role TEXT,
              enabled INTEGER, last_login TEXT, group_id INTEGER, deleted_at TEXT);
            CREATE TABLE usage_records(id INTEGER PRIMARY KEY, user_id INTEGER, cost REAL, total_tokens INTEGER);
        ''')
        for uid in range(1, 10):
            self.db.execute('INSERT INTO users VALUES(?,?,?, ?,?,?,?,?)',
                            (uid, f'user{uid}', '', 'USER', int(uid != 2), None,
                             2 if uid == 8 else 1, 'deleted' if uid == 9 else None))
        self.db.executemany('INSERT INTO usage_records VALUES(?,?,?,?)',
                            [(1, 1, 1.25, 100), (2, 1, 2.5, 200), (3, 2, 5, 400),
                             (4, 7, 10, 800), (5, 8, 1000, 10000), (6, 9, 1000, 10000)])

    def find_user_group(self, group_id):
        return {'id': group_id, 'name': 'Test group', 'allowed_models_json': '["*"]'} if group_id in (1, 2, 3) else None

    def execute(self, name, **params):
        sql = ''.join(self.xml.find(f".//*[@id='{name}']").itertext())
        sql = re.sub(r'#\{(\w+)\}', r':\1', sql)
        return [dict(row) for row in self.db.execute(sql, params)]

    def user_group_totals(self, group_id):
        return self.execute('user_group_totals', group_id=group_id)[0]

    def user_group_members(self, group_id, offset, limit):
        return self.execute('user_group_members', group_id=group_id, offset=offset, limit=limit)


@pytest.fixture
def service():
    mapper = SqlMapper()
    yield StoreService(SimpleNamespace(mapper=mapper))
    mapper.db.close()


def test_group_totals_cover_all_pages_and_exclude_other_and_deleted_members(service):
    first = service.admin_group_detail(1, 1, 5)
    second = service.admin_group_detail(1, 2, 5)
    assert first['totals'] == second['totals'] == {
        'member_count': 7, 'requests': 4, 'total_cost': 18.75, 'total_tokens': 1500}
    assert len(first['members']['items']) == 5
    assert [row['id'] for row in second['members']['items']] == [6, 7]
    assert first['members']['pages'] == 2
    disabled = first['members']['items'][1]
    assert disabled['enabled'] == 0 and disabled['total_cost'] == 5
    unused = first['members']['items'][2]
    assert unused['requests'] == unused['total_cost'] == unused['total_tokens'] == 0


def test_empty_group_and_missing_group(service):
    result = service.admin_group_detail(3)
    assert result['totals'] == dict(member_count=0, requests=0, total_cost=0, total_tokens=0)
    assert result['members']['items'] == []
    assert service.admin_group_detail(999) is None


def test_user_group_overview_never_contains_member_records(service):
    result = service.user_group_overview(1)

    assert result['totals']['member_count'] == 7
    assert result['totals']['total_tokens'] == 1500
    assert 'members' not in result


def test_pagination_clamps_and_moving_member_changes_current_group_totals(service):
    assert service.admin_group_detail(1, 999, 999)['members']['page'] == 2
    assert service.admin_group_detail(1, -1, 0)['members']['page'] == 1
    service.mapper.db.execute('UPDATE users SET group_id=2 WHERE id=7')
    result = service.admin_group_detail(1)
    assert result['totals']['member_count'] == 6
    assert result['totals']['total_tokens'] == 700


def test_members_never_expose_authentication_fields(service):
    original = service.mapper.user_group_members
    service.mapper.user_group_members = lambda *args: [dict(row, password_hash='secret', api_key='secret') for row in original(*args)]
    for row in service.admin_group_detail(1)['members']['items']:
        assert 'password_hash' not in row and 'api_key' not in row


@pytest.mark.parametrize('role', [None, 'USER'])
def test_group_detail_requires_admin(service, role):
    auth = SimpleNamespace(store=service, user_from_authorization=lambda _: {'role': role} if role else None)
    controller = AdminController(auth, None, None)
    assert controller.user_group_detail(1, 'token', 1, 5).code == 403


def test_admin_group_detail_success_and_not_found(service):
    auth = SimpleNamespace(store=service, user_from_authorization=lambda _: {'role': 'ADMIN'})
    controller = AdminController(auth, None, None)
    assert controller.user_group_detail(1, 'token', 1, 5).data['totals']['total_tokens'] == 1500
    assert controller.user_group_detail(999, 'token', 1, 5).code == 404


def test_regular_user_sees_only_own_effective_group_overview(service, monkeypatch):
    user = {'id': 7, 'role': 'USER', 'group_id': 1, 'effective_group_id': 1, 'group_source': 'ASSIGNED'}
    auth = SimpleNamespace(user_from_authorization=lambda _: user)
    monkeypatch.setattr(auth_controller, 'get_config', lambda: {'rose': {'user-groups': {'overview-visible-to-users': True}}})

    result = AuthController(service, auth).group_overview('token')

    assert result.code == 200
    assert result.data['group']['id'] == 1
    assert result.data['totals']['member_count'] == 7
    assert 'members' not in result.data


def test_yaml_switch_blocks_regular_user_but_not_admin(service, monkeypatch):
    monkeypatch.setattr(auth_controller, 'get_config', lambda: {'rose': {'user-groups': {'overview-visible-to-users': False}}})
    regular = SimpleNamespace(user_from_authorization=lambda _: {'id': 7, 'role': 'USER', 'effective_group_id': 1})
    admin = SimpleNamespace(user_from_authorization=lambda _: {'id': 1, 'role': 'ADMIN', 'effective_group_id': 1})

    assert AuthController(service, regular).group_overview('token').code == 403
    admin_result = AuthController(service, admin).group_overview('token')
    assert admin_result.code == 200
    assert 'members' not in admin_result.data
