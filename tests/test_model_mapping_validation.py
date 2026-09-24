import sqlite3

import pytest

from backend.repository.store import StoreRepository
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


def test_model_mapping_source_is_unique_only_within_one_group():
    rows = [
        {"source_model": "gpt-6-astra", "source_effort": "xhigh"},
        {"source_model": "gpt-6-astra", "source_effort": "xhigh"},
    ]
    with pytest.raises(ValueError, match="同一用户组"):
        StoreService._validate_mapping_rows_for_group(rows)

    # Separate groups validate their own selected mapping list independently.
    StoreService._validate_mapping_rows_for_group(rows[:1])
    StoreService._validate_mapping_rows_for_group(rows[1:])


def test_sqlite_migrates_legacy_global_mapping_unique_constraint():
    conn = sqlite3.connect(":memory:")
    # Keep this test independent of the application working directory.
    from pathlib import Path
    schema_text = (Path(__file__).resolve().parents[1] / "backend/resources/schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema_text)
    conn.execute("DROP TABLE model_mappings")
    conn.execute(
        """
        CREATE TABLE model_mappings (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          source_model TEXT NOT NULL,
          source_effort TEXT NOT NULL DEFAULT '',
          target_model TEXT NOT NULL,
          target_effort TEXT NOT NULL,
          enabled INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          UNIQUE(source_model, source_effort)
        )
        """
    )
    StoreRepository._ensure_sqlite_model_mapping_scope(conn)
    conn.execute(
        "INSERT INTO model_mappings(name,source_model,source_effort,target_model,target_effort,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
        ("one", "gpt-6-astra", "xhigh", "gpt-6-astra", "medium", 1, "now", "now"),
    )
    conn.execute(
        "INSERT INTO model_mappings(name,source_model,source_effort,target_model,target_effort,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
        ("two", "gpt-6-astra", "xhigh", "gpt-6-astra", "high", 1, "now", "now"),
    )
    assert conn.execute("SELECT COUNT(*) FROM model_mappings").fetchone()[0] == 2
