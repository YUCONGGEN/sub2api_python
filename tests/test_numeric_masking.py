from decimal import Decimal

import pytest
from springbootai.orm.pymybatis.configuration import Configuration
from springbootai.orm.pymybatis.core.sql_session import SqlSession
from springbootai.orm.pymybatis.security.sensitive_data_masker import SensitiveDataMasker

from backend.common.numeric_masking import install_numeric_masking_guard


@pytest.fixture
def numeric_guard(monkeypatch):
    monkeypatch.setattr(SensitiveDataMasker, "detect_type", SensitiveDataMasker.detect_type)
    install_numeric_masking_guard()


def test_large_numeric_totals_stay_numeric_in_sql_results(numeric_guard, tmp_path):
    configuration = Configuration()
    configuration.dialect = "sqlite"
    configuration.datasources = {"default": {"database": str(tmp_path / "stats.db")}}
    configuration.pool_min_size = 1
    configuration.pool_max_size = 1
    configuration.cache_enabled = False
    with SqlSession(configuration) as session:
        row = session.select_one(
            "SELECT 16374444681 AS total_tokens, 1234567890123456 AS tokens, "
            "'13812345678' AS phone, 'alice@example.com' AS email"
        )
    assert row["total_tokens"] == 16374444681
    assert isinstance(row["total_tokens"], int)
    assert row["tokens"] == 1234567890123456
    assert row["phone"] == "138****5678"
    assert row["email"] == "ali***@example.com"


def test_explicit_sensitive_fields_remain_masked(numeric_guard):
    row = SensitiveDataMasker().mask_dict({
        "total_cost": Decimal("16374444681"),
        "phone": 13812345678,
        "bank_card": 1234567890123456,
        "password": "private-value",
        "contact": "13812345678",
    })
    assert row["total_cost"] == Decimal("16374444681")
    assert row["phone"] == "138****5678"
    assert row["bank_card"] == "1234********3456"
    assert row["password"] == "******"
    assert row["contact"] == "138****5678"


def test_install_is_idempotent(numeric_guard):
    original = SensitiveDataMasker.detect_type
    install_numeric_masking_guard()
    assert SensitiveDataMasker.detect_type is original
