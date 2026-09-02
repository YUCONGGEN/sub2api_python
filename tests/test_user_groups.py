import asyncio
import sqlite3
from pathlib import Path

from backend.repository.store import StoreRepository
from backend.service.store_service import StoreService
from backend.service.user_group_service import UserGroupService


def test_group_model_permissions_support_all_exact_and_empty():
    service = UserGroupService()
    assert service.model_allowed({"group_allowed_models": ["*"]}, "any-new-model")
    assert service.model_allowed({"group_allowed_models": ["gpt-5.6-sol"]}, "gpt-5.6-sol")
    assert not service.model_allowed({"group_allowed_models": ["gpt-5.6-sol"]}, "gpt-5.6-luna")
    assert not service.model_allowed({"group_allowed_models": []}, "gpt-5.6-sol")


def test_group_concurrency_waits_and_releases_fifo():
    async def scenario():
        service = UserGroupService()
        user = {
            "id": 7,
            "username": "queue-user",
            "group_name": "默认用户组",
            "group_concurrency_limit": 1,
        }
        first = await service.acquire(user)
        second_task = asyncio.create_task(service.acquire(user))
        await asyncio.sleep(0)
        metrics = service.queue_metrics(include_users=True)
        assert metrics["queue_waiting"] == 1
        assert metrics["queued_users"][0]["username"] == "queue-user"
        assert not second_task.done()

        first.release()
        second = await asyncio.wait_for(second_task, 1)
        assert service.active_for_user(7) == 1
        assert service.queue_metrics()["queue_waiting"] == 0
        second.release()
        assert service.active_for_user(7) == 0

    asyncio.run(scenario())


def test_legacy_users_are_assigned_to_seeded_groups():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        CREATE TABLE user_groups (
          id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE,
          description TEXT, weight INTEGER, concurrency_limit INTEGER,
          allowed_models_json TEXT, is_default INTEGER,
          created_at TEXT, updated_at TEXT
        );
        CREATE TABLE users (
          id INTEGER PRIMARY KEY, username TEXT, role TEXT, group_id INTEGER
        );
        INSERT INTO users(id,username,role,group_id) VALUES
          (1,'admin','ADMIN',NULL),(2,'member','USER',NULL);
    """)
    StoreRepository._ensure_sqlite_user_groups(connection)
    rows = connection.execute(
        "SELECT u.username,g.name,g.concurrency_limit FROM users u JOIN user_groups g ON g.id=u.group_id ORDER BY u.id"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("admin", "管理员组", 20),
        ("member", "默认用户组", 1),
    ]
    connection.close()


def test_subscription_group_uses_highest_weight_and_falls_back_to_assigned_group():
    groups = {
        1: {"id": 1, "name": "默认用户组", "weight": 0, "concurrency_limit": 1, "allowed_models_json": '["*"]', "is_default": 1},
        2: {"id": 2, "name": "标准组", "weight": 20, "concurrency_limit": 2, "allowed_models_json": '["gpt-5.6-sol"]', "is_default": 0},
        3: {"id": 3, "name": "高级组", "weight": 50, "concurrency_limit": 5, "allowed_models_json": '["*"]', "is_default": 0},
    }

    class Mapper:
        def __init__(self):
            self.active = [
                {**groups[3], "plan_id": 9, "plan_name": "高级套餐", "subscription_id": 99, "subscription_ends_at": "2099-01-01T00:00:00+00:00"},
                {**groups[2], "plan_id": 8, "plan_name": "标准套餐", "subscription_id": 88, "subscription_ends_at": "2099-01-01T00:00:00+00:00"},
            ]

        def find_user_group(self, group_id):
            return groups.get(group_id)

        def find_default_user_group(self):
            return groups[1]

        def list_active_subscription_group_candidates(self, user_id, now):
            return self.active

    service = object.__new__(StoreService)
    service.mapper = Mapper()
    upgraded = service._with_group({"id": 7, "username": "member", "group_id": 1})
    assert upgraded["group_id"] == 1
    assert upgraded["effective_group_id"] == 3
    assert upgraded["group_name"] == "高级组"
    assert upgraded["group_source"] == "SUBSCRIPTION"
    assert upgraded["group_source_plan_name"] == "高级套餐"

    service.mapper.active = []
    fallback = service._with_group({"id": 7, "username": "member", "group_id": 1})
    assert fallback["effective_group_id"] == 1
    assert fallback["group_name"] == "默认用户组"
    assert fallback["group_source"] == "ASSIGNED"


def test_full_schema_upgrades_users_table_without_group_column():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("""
        CREATE TABLE users (
          id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE,
          password_hash TEXT, email TEXT, role TEXT, balance REAL,
          enabled INTEGER, api_key TEXT UNIQUE, created_at TEXT, last_login TEXT
        )
    """)
    connection.execute(
        "INSERT INTO users(username,password_hash,email,role,balance,enabled,api_key,created_at) VALUES(?,?,?,?,?,?,?,?)",
        ("legacy", "x", "", "USER", 0, 1, "revoked-legacy", "now"),
    )
    schema = Path("backend/resources/schema.sql").read_text(encoding="utf-8")
    connection.executescript(schema)
    repository = object.__new__(StoreRepository)
    repository._ensure_sqlite_compatibility(connection)
    StoreRepository._ensure_sqlite_user_groups(connection)
    connection.execute("CREATE INDEX IF NOT EXISTS idx_users_group ON users(group_id, deleted_at)")
    row = connection.execute(
        "SELECT u.username,g.name,g.concurrency_limit FROM users u JOIN user_groups g ON g.id=u.group_id WHERE u.username='legacy'"
    ).fetchone()
    assert tuple(row) == ("legacy", "默认用户组", 1)
    connection.close()
