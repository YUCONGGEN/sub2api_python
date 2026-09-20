from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

import pytest

from backend.service.store_service import StoreService


class AnnouncementMapper:
    def __init__(self):
        self.rows = []
        self.reads = set()

    def find_current_announcement(self, now):
        matches = [
            row for row in self.rows
            if row["enabled"] and (not row["expires_at"] or row["expires_at"] > now)
        ]
        return dict(max(matches, key=lambda row: row["id"])) if matches else None

    def find_announcement(self, announcement_id):
        return next((dict(row) for row in self.rows if row["id"] == announcement_id), None)

    def disable_announcements(self, updated_at):
        changed = 0
        for row in self.rows:
            if row["enabled"]:
                row["enabled"] = 0
                row["updated_at"] = updated_at
                changed += 1
        return changed

    def insert_announcement(self, announcement):
        announcement["id"] = len(self.rows) + 1
        self.rows.append({**announcement, "enabled": 1})
        return 1

    def find_announcement_read(self, announcement_id, user_id):
        return {"announcement_id": announcement_id, "user_id": user_id} if (announcement_id, user_id) in self.reads else None

    def insert_announcement_read(self, announcement_id, user_id, read_at):
        self.reads.add((announcement_id, user_id))
        return 1


@pytest.fixture
def announcement_service():
    service = StoreService.__new__(StoreService)
    service.mapper = AnnouncementMapper()
    return service


def test_publish_replaces_current_and_tracks_user_acknowledgement(announcement_service):
    first = announcement_service.publish_announcement("维护通知", "今晚进行维护", 1)
    assert announcement_service.current_announcement(7) == {**first, "acknowledged": False}
    assert announcement_service.acknowledge_announcement(first["id"], 7) is True
    assert announcement_service.current_announcement(7)["acknowledged"] is True

    second = announcement_service.publish_announcement("维护完成", "服务已经恢复", 1)
    assert second["id"] != first["id"]
    assert announcement_service.current_announcement(7)["id"] == second["id"]
    assert announcement_service.current_announcement(7)["acknowledged"] is False
    assert announcement_service.acknowledge_announcement(first["id"], 7) is False


def test_expired_announcement_is_not_current(announcement_service):
    expiry = datetime.now(timezone.utc) + timedelta(minutes=5)
    announcement = announcement_service.publish_announcement("短期通知", "五分钟后到期", 1, expiry.isoformat())
    assert announcement_service.current_announcement()["id"] == announcement["id"]

    announcement_service.mapper.rows[0]["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    assert announcement_service.current_announcement() is None
    assert announcement_service.acknowledge_announcement(announcement["id"], 7) is False


@pytest.mark.parametrize("expiry", ["not-a-date", "2020-01-01T00:00:00+00:00"])
def test_publish_rejects_invalid_or_past_expiry(announcement_service, expiry):
    with pytest.raises(ValueError, match="到期时间"):
        announcement_service.publish_announcement("到期测试", "内容", 1, expiry)


def test_schemas_include_announcement_expiry_and_read_tracking():
    root = Path(__file__).resolve().parents[1]
    sqlite_schema = (root / "backend/resources/schema.sql").read_text(encoding="utf-8")
    mysql_schema = (root / "backend/resources/schema_mysql.sql").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS announcements" in sqlite_schema
    assert "expires_at TEXT" in sqlite_schema
    assert "CREATE TABLE IF NOT EXISTS announcement_reads" in sqlite_schema
    assert "expires_at VARCHAR(40)" in mysql_schema

    connection = sqlite3.connect(":memory:")
    connection.executescript(sqlite_schema)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(announcements)")}
    assert {"title", "content", "enabled", "expires_at"} <= columns
