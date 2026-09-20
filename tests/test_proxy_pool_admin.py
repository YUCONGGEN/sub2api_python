import asyncio
import base64
import json

import pytest
import yaml

from backend.common.proxy_subscription import normalize_subscription
from backend.service.proxy_pool_admin_service import ProxyPoolAdminService


class FakeCipher:
    def encrypt(self, value):
        return "encrypted:" + json.dumps(value, ensure_ascii=False)[::-1]

    def decrypt(self, value):
        return json.loads(value.removeprefix("encrypted:")[::-1])


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
    result = asyncio.run(service.update_policy({"standby_pool_size": 1, "failure_confirmations": 4}))
    saved = json.loads(service.monitor_config_path.read_text(encoding="utf-8"))
    assert result["policy"]["standby_pool_size"] == 1
    assert saved["failure_confirmations"] == 4
    assert list(tmp_path.glob("monitor.json.bak-*"))
    with pytest.raises(ValueError, match="备用池最多可设为 2"):
        asyncio.run(service.update_policy({"standby_pool_size": 3}))
    with pytest.raises(ValueError, match="备用复测间隔必须在 1～60 秒之间"):
        asyncio.run(service.update_policy({"replacement_interval_seconds": 61}))


def test_subscription_url_is_encrypted_and_never_returned(tmp_path):
    service = configured_service(tmp_path)
    service._validate_remote_url = lambda url: ("example.com", url)

    async def download(url):
        return b"proxies:\n  - name: jp-1\n    type: ss\n", {
            "subscription-userinfo": "upload=100; download=200; total=1000; expire=2000000000"
        }

    service._download_details = download
    result = asyncio.run(service.add_subscription({
        "name": "演示订阅",
        "url": "https://example.com/private-token",
        "note": "备用地址 https://backup.example，账号 demo，密码 secret",
    }))
    persisted = service.registry_path.read_text(encoding="utf-8")
    assert result["host"] == "example.com"
    assert result["node_count"] == 1
    assert "private-token" not in persisted
    assert "密码 secret" not in persisted
    assert "url_encrypted" not in result
    assert "note_encrypted" not in result
    assert result["checkable"] is True
    assert result["note"] == "备用地址 https://backup.example，账号 demo，密码 secret"
    assert result["traffic_used_bytes"] == 300
    assert result["traffic_remaining_bytes"] == 700


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


def test_subscription_usage_falls_back_to_quota_nodes():
    dated = "proxies:\n  - name: '剩余流量：38.2 GB'\n  - name: '套餐到期：2026-12-23'\n"
    usage = ProxyPoolAdminService._subscription_content_usage(dated.encode("utf-8"))
    assert usage["traffic_remaining_bytes"] == int(38.2 * 1024 ** 3)
    assert usage["traffic_expire_label"] == "2026-12-23"
    assert usage["traffic_expire_at"] > 0

    permanent = """proxies:
  - {name: '剩余流量：988.75 GB', type: ss, server: info.example, port: 443}
  - {name: '套餐到期：长期有效', type: ss, server: info.example, port: 443}
  - {name: '日本可用节点', type: ss, server: jp.example, port: 443}
"""
    encoded = base64.b64encode(permanent.encode("utf-8"))
    usage = ProxyPoolAdminService._subscription_content_usage(encoded)
    assert usage["traffic_remaining_bytes"] == int(988.75 * 1024 ** 3)
    assert usage["traffic_expire_label"] == "长期有效"

    proxies = normalize_subscription(permanent.encode("utf-8"), "rose-demo")
    assert [item["name"] for item in proxies] == ["rose-demo｜日本可用节点"]

    vmess_lines = []
    for name in ("剩余流量：18.12 GB", "套餐到期：长期有效"):
        payload = base64.b64encode(json.dumps({"ps": name}, ensure_ascii=False).encode("utf-8")).decode()
        vmess_lines.append("vmess://" + payload)
    nested = base64.b64encode("\n".join(vmess_lines).encode("utf-8"))
    usage = ProxyPoolAdminService._subscription_content_usage(nested)
    assert usage["traffic_remaining_bytes"] == int(18.12 * 1024 ** 3)
    assert usage["traffic_expire_label"] == "长期有效"


def test_subscription_policy_updates_enabled_state_and_source_limit(tmp_path):
    service = configured_service(tmp_path)
    service.registry_path.write_text(json.dumps({"subscriptions": [{
        "id": "source-a", "name": "A", "pool_source": "a", "enabled": True,
        "max_healthy_nodes": 2,
    }]}, ensure_ascii=False), encoding="utf-8")

    updated = asyncio.run(service.update_subscription("source-a", {"enabled": False, "max_healthy_nodes": 1}))
    monitor = json.loads(service.monitor_config_path.read_text(encoding="utf-8"))
    assert updated["enabled"] is False
    assert updated["max_healthy_nodes"] == 1
    assert monitor["disabled_sources"] == ["a"]
    assert monitor["source_node_limits"]["a"] == 1


def test_subscription_name_and_encrypted_note_can_be_updated(tmp_path):
    service = configured_service(tmp_path)
    service.registry_path.write_text(json.dumps({"subscriptions": [
        {"id": "source-a", "name": "旧名称", "pool_source": "a", "enabled": True,
         "max_healthy_nodes": 2},
        {"id": "source-b", "name": "其他订阅", "pool_source": "b", "enabled": True,
         "max_healthy_nodes": 2},
    ]}, ensure_ascii=False), encoding="utf-8")

    updated = asyncio.run(service.update_subscription("source-a", {
        "name": "新名称", "note": "账号 test，密码 private", "max_healthy_nodes": 2,
    }))
    persisted = service.registry_path.read_text(encoding="utf-8")
    assert updated["name"] == "新名称"
    assert updated["note"] == "账号 test，密码 private"
    assert "密码 private" not in persisted
    assert "note_encrypted" in persisted

    with pytest.raises(ValueError, match="订阅名称已存在"):
        asyncio.run(service.update_subscription("source-a", {"name": "其他订阅"}))
    with pytest.raises(ValueError, match="不能超过 100"):
        asyncio.run(service.update_subscription("source-a", {"note": "密" * 101}))


def test_country_policy_rejects_selection_without_matching_nodes(tmp_path):
    service = configured_service(tmp_path)
    with pytest.raises(ValueError, match="至少需要保留 2 个可用节点"):
        asyncio.run(service.update_policy({"allowed_countries": ["JP"]}))
    result = asyncio.run(service.update_policy({"allowed_countries": ["OTHER"]}))
    assert result["policy"]["allowed_countries"] == ["OTHER"]


def test_core_version_check_only_reports_update(tmp_path):
    service = configured_service(tmp_path)

    async def download(url):
        return json.dumps({"tag_name": "v1.3.0", "html_url": "https://example.com/release"}).encode()

    service._download = download
    result = asyncio.run(service.check_core_version())
    assert result["current"] == "1.2.3"
    assert result["latest"] == "1.3.0"
    assert result["update_available"] is True


def test_deleting_subscription_retires_its_existing_pool_nodes(tmp_path):
    service = configured_service(tmp_path)
    service.monitor_config_path.write_text(json.dumps({
        "controller_socket": "/tmp/test.sock", "group": "日本稳定池",
        "nodes": ["主节点", "备用一", "备用二"],
        "sources": {"主节点": "a", "备用一": "b", "备用二": "c"},
        "standby_pool_size": 1,
    }, ensure_ascii=False), encoding="utf-8")
    service.registry_path.write_text(json.dumps({"subscriptions": [
        {"id": "source-a", "name": "A", "pool_source": "a", "enabled": True},
        {"id": "source-b", "name": "B", "pool_source": "b", "enabled": True},
        {"id": "source-c", "name": "C", "pool_source": "c", "enabled": True},
    ]}, ensure_ascii=False), encoding="utf-8")

    assert asyncio.run(service.delete_subscription("source-a")) is True
    monitor = json.loads(service.monitor_config_path.read_text(encoding="utf-8"))
    assert monitor["retired_sources"] == ["a"]
    assert "a" in monitor["disabled_sources"]
    assert service._eligible_nodes(monitor) == ["备用一", "备用二"]


def test_subscription_sync_adds_new_nodes_and_removes_deleted_source(tmp_path):
    service = configured_service(tmp_path)
    service.profile_path = tmp_path / "mihomo.yml"
    service.provider_dir = tmp_path / "providers"
    service.profile_path.write_text(yaml.safe_dump({
        "mixed-port": 7890,
        "proxies": [],
        "proxy-groups": [{"name": "日本稳定池", "type": "select", "proxies": ["DIRECT"]}],
        "rules": ["MATCH,日本稳定池"],
    }, allow_unicode=True), encoding="utf-8")
    service.registry_path.write_text(json.dumps({"subscriptions": [
        {"id": "one", "name": "一号", "pool_source": "source-one", "enabled": True,
         "max_healthy_nodes": 2, "url_encrypted": service.cipher.encrypt({"url": "https://one.example/sub"})},
        {"id": "two", "name": "二号", "pool_source": "source-two", "enabled": True,
         "max_healthy_nodes": 2, "url_encrypted": service.cipher.encrypt({"url": "https://two.example/sub"})},
    ]}, ensure_ascii=False), encoding="utf-8")

    async def download(url):
        host = "one" if "one.example" in url else "two"
        proxies = [
            {"name": f"日本-{host}-{index}", "type": "ss", "server": f"{host}{index}.example",
             "port": 443, "cipher": "aes-128-gcm", "password": "secret"}
            for index in range(1, 4)
        ]
        return yaml.safe_dump({"proxies": proxies}, allow_unicode=True).encode(), {}

    service._download_details = download
    service._validate_profile = lambda path: None
    service._seed_candidate_health = lambda monitor, state: len(monitor["nodes"])
    service._ensure_allowed_selection = lambda monitor, state: monitor["nodes"][0]
    reloads = []
    service._controller_request = lambda socket, method, route, payload=None: reloads.append((method, route)) or {}

    result = asyncio.run(service.synchronize_pool())
    monitor = json.loads(service.monitor_config_path.read_text(encoding="utf-8"))
    profile = yaml.safe_load(service.profile_path.read_text(encoding="utf-8"))
    assert result["candidate_count"] == 6
    assert len(monitor["nodes"]) == 6
    assert set(profile["proxy-providers"]) == {"rose-one", "rose-two"}
    assert profile["proxy-groups"][0]["use"] == ["rose-one", "rose-two"]
    assert reloads[-1] == ("PUT", "/configs?force=false")

    assert asyncio.run(service.delete_subscription("one")) is True
    monitor = json.loads(service.monitor_config_path.read_text(encoding="utf-8"))
    profile = yaml.safe_load(service.profile_path.read_text(encoding="utf-8"))
    assert all("rose-one｜" not in name for name in monitor["nodes"])
    assert set(profile["proxy-providers"]) == {"rose-two"}


def test_incompatible_proxy_is_bisected_without_dropping_provider(tmp_path):
    service = configured_service(tmp_path)
    service.profile_path = tmp_path / "mihomo.yml"
    service._proxy_batch_is_compatible = lambda proxies: all("bad" not in item["name"] for item in proxies)
    proxies = [{"name": name, "type": "ss", "server": "example.com", "port": 443}
               for name in ("good-one", "bad-node", "good-two")]
    assert [item["name"] for item in service._filter_compatible_proxies(proxies)] == ["good-one", "good-two"]
