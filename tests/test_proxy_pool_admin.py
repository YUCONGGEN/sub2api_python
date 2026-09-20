import asyncio
import json

import pytest

from backend.service.proxy_pool_admin_service import ProxyPoolAdminService


class FakeCipher:
    def encrypt(self, value):
        return "encrypted:" + value["url"][::-1]

    def decrypt(self, value):
        return {"url": value.removeprefix("encrypted:")[::-1]}


def configured_service(tmp_path):
    monitor = tmp_path / "monitor.json"
    state = tmp_path / "state.json"
    registry = tmp_path / "subscriptions.json"
    monitor.write_text(json.dumps({
        "controller_socket": "/tmp/test.sock",
        "group": "日本稳定池",
        "nodes": ["主节点", "备用一", "备用二"],
        "sources": {"主节点": "a", "备用一": "b", "备用二": "c"},
        "interval_seconds": 60,
        "failure_confirmations": 3,
        "replacement_confirmations": 2,
    }, ensure_ascii=False), encoding="utf-8")
    state.write_text(json.dumps({
        "last_check": {"time": 99999999999, "status": "healthy", "node": "主节点"},
        "nodes": {"备用一": {"status": "ready", "delay_ms": 120}},
    }, ensure_ascii=False), encoding="utf-8")
    service = ProxyPoolAdminService(FakeCipher())
    service.enabled = True
    service.monitor_config_path = monitor
    service.monitor_state_path = state
    service.registry_path = registry
    service._controller = lambda path, route: ({"version": "v1.2.3"} if route == "/version" else {"now": "主节点", "all": ["主节点", "备用一", "备用二"]})
    return service


def test_snapshot_reports_core_policy_and_nodes_without_secrets(tmp_path):
    service = configured_service(tmp_path)
    result = service.snapshot()
    assert result["core"] == {"online": True, "version": "v1.2.3", "error": ""}
    assert result["policy"]["standby_pool_size"] == 2
    assert result["nodes"][0]["status"] == "active"
    assert result["nodes"][1]["status"] == "ready"
    assert result["subscriptions"] == []


def test_policy_update_is_bounded_and_backed_up(tmp_path):
    service = configured_service(tmp_path)
    result = service.update_policy({"standby_pool_size": 1, "failure_confirmations": 4})
    saved = json.loads(service.monitor_config_path.read_text(encoding="utf-8"))
    assert result["policy"]["standby_pool_size"] == 1
    assert saved["failure_confirmations"] == 4
    assert list(tmp_path.glob("monitor.json.bak-*"))
    with pytest.raises(ValueError, match="最多可设为 2"):
        service.update_policy({"standby_pool_size": 3})


def test_subscription_url_is_encrypted_and_never_returned(tmp_path):
    service = configured_service(tmp_path)
    service._validate_remote_url = lambda url: ("example.com", url)

    async def download(url):
        return b"proxies:\n  - name: jp-1\n    type: ss\n"

    service._download = download
    result = asyncio.run(service.add_subscription({"name": "演示订阅", "url": "https://example.com/private-token"}))
    persisted = service.registry_path.read_text(encoding="utf-8")
    assert result["host"] == "example.com"
    assert result["node_count"] == 1
    assert "private-token" not in persisted
    assert "url_encrypted" not in result
    assert result["checkable"] is True


def test_imported_subscription_is_visible_but_not_checkable(tmp_path):
    service = configured_service(tmp_path)
    service.registry_path.write_text(json.dumps({"subscriptions": [{
        "id": "legacy-hy2",
        "name": "旧 HY2 订阅",
        "host": "47.112.97.173",
        "status": "imported",
        "node_count": 2,
    }]}, ensure_ascii=False), encoding="utf-8")

    source = service.snapshot()["subscriptions"][0]
    assert source["status"] == "imported"
    assert source["checkable"] is False
    assert "url_encrypted" not in source
    with pytest.raises(ValueError, match="历史导入来源未保留订阅地址"):
        asyncio.run(service.check_subscription("legacy-hy2"))


def test_subscription_parser_accepts_base64_and_rejects_unknown_content():
    encoded = "c3M6Ly9vbmUKdm1lc3M6Ly90d28="
    assert ProxyPoolAdminService._subscription_node_count(encoded.encode()) == 2
    with pytest.raises(ValueError, match="未识别"):
        ProxyPoolAdminService._subscription_node_count(b"not a subscription")
