"""Safe control-plane access for the external Japanese proxy pool.

The service never restarts or reloads the proxy core.  It edits only the
configured monitor policy and keeps subscription URLs encrypted at rest.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import tempfile
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
import yaml
from springbootai import Autowired, PostConstruct, Service, get_config

from backend.service.credential_cipher_service import CredentialCipherService


class ProxyPoolAdminDisabled(RuntimeError):
    pass


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float = 2.0):
        super().__init__("localhost", timeout=timeout)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


@Service("proxy_pool_admin_service")
class ProxyPoolAdminService:
    COUNTRY_OPTIONS = (
        {"code": "JP", "label": "日本"},
        {"code": "HK", "label": "香港"},
        {"code": "TW", "label": "台湾"},
        {"code": "SG", "label": "新加坡"},
        {"code": "KR", "label": "韩国"},
        {"code": "US", "label": "美国"},
        {"code": "GB", "label": "英国"},
        {"code": "DE", "label": "德国"},
        {"code": "CA", "label": "加拿大"},
        {"code": "AU", "label": "澳大利亚"},
        {"code": "OTHER", "label": "其他国家"},
    )
    COUNTRY_ALIASES = {
        "JP": ("日本", "东京", "大阪", "埼玉", "jp", "japan", "tokyo", "osaka"),
        "HK": ("香港", "港", "hk", "hong kong"),
        "TW": ("台湾", "台北", "新北", "tw", "taiwan", "taipei"),
        "SG": ("新加坡", "狮城", "sg", "singapore"),
        "KR": ("韩国", "首尔", "韩", "kr", "korea", "seoul"),
        "US": ("美国", "洛杉矶", "西雅图", "硅谷", "纽约", "达拉斯", "us", "usa", "united states"),
        "GB": ("英国", "伦敦", "uk", "gb", "united kingdom", "london"),
        "DE": ("德国", "法兰克福", "de", "germany", "frankfurt"),
        "CA": ("加拿大", "多伦多", "温哥华", "ca", "canada", "toronto", "vancouver"),
        "AU": ("澳大利亚", "澳洲", "悉尼", "au", "australia", "sydney"),
    }
    POLICY_LABELS = {
        "standby_pool_size": "备用池大小",
        "interval_seconds": "主节点检查间隔",
        "failure_confirmations": "失败确认次数",
        "failure_interval_seconds": "失败复测间隔",
        "replacement_confirmations": "备用成功确认次数",
        "replacement_interval_seconds": "备用复测间隔",
        "quarantine_seconds": "故障隔离时间",
        "quarantine_recheck_seconds": "隔离重试间隔",
        "standby_probes_per_cycle": "每轮备用检查数",
        "standby_interval_seconds": "备用复查间隔",
        "recovery_successes": "恢复确认次数",
        "recovery_interval_seconds": "恢复检查间隔",
        "recovery_max_interval_seconds": "恢复检查最大间隔",
    }
    POLICY_RULES = {
        "standby_pool_size": (1, 20),
        "interval_seconds": (10, 3600),
        "failure_confirmations": (2, 10),
        "failure_interval_seconds": (1, 300),
        "replacement_confirmations": (2, 10),
        "replacement_interval_seconds": (1, 60),
        "quarantine_seconds": (30, 86400),
        "quarantine_recheck_seconds": (10, 3600),
        "standby_probes_per_cycle": (0, 20),
        "standby_interval_seconds": (30, 86400),
        "recovery_successes": (2, 10),
        "recovery_interval_seconds": (10, 3600),
        "recovery_max_interval_seconds": (60, 86400),
    }

    @Autowired
    def __init__(self, credential_cipher_service: CredentialCipherService):
        # SpringBootAI resolves constructor dependencies by bean name when a
        # postponed annotation has not yet been evaluated.
        self.cipher = credential_cipher_service
        self.enabled = False
        self.monitor_config_path = Path("monitor.json")
        self.monitor_state_path = Path("state.json")
        self.registry_path = Path("proxy-subscriptions.json")
        self.max_subscription_bytes = 2 * 1024 * 1024
        self.request_timeout_seconds = 15.0
        self.core_version_release_url = "https://api.github.com/repos/MetaCubeX/mihomo/releases/latest"

    @PostConstruct
    def init(self) -> None:
        options = get_config().get("rose", {}).get("proxy-pool-admin", {}) or {}
        self.enabled = _as_bool(options.get("enabled"), False)
        configured = str(options.get("monitor-config-path") or "").strip()
        if configured:
            self.monitor_config_path = Path(configured).expanduser().resolve()
        state = str(options.get("monitor-state-path") or "").strip()
        self.monitor_state_path = Path(state).expanduser().resolve() if state else self.monitor_config_path.with_name("state.json")
        registry = str(options.get("subscription-registry-path") or "").strip()
        self.registry_path = Path(registry).expanduser().resolve() if registry else self.monitor_config_path.with_name("proxy-subscriptions.json")
        self.max_subscription_bytes = max(4096, min(int(options.get("max-subscription-bytes", self.max_subscription_bytes)), 8 * 1024 * 1024))
        self.request_timeout_seconds = max(3.0, min(float(options.get("request-timeout-seconds", 15)), 60.0))
        self.core_version_release_url = str(options.get("core-version-release-url") or self.core_version_release_url).strip()

    def _ensure_enabled(self) -> None:
        if not self.enabled:
            raise ProxyPoolAdminDisabled("代理订阅管理未在 YAML 中启用")

    @staticmethod
    def _read_json(path: Path, default: Any) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return default
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"无法读取 {path.name}：{exc}") from exc

    @staticmethod
    def _atomic_json(path: Path, value: Any, *, backup: bool = False) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if backup and path.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            shutil.copy2(path, path.with_name(f"{path.name}.bak-{stamp}"))
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.chmod(temporary_name, 0o600)
            except OSError:
                pass
            os.replace(temporary_name, path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)

    def _controller(self, socket_path: str, route: str) -> dict[str, Any]:
        connection = _UnixConnection(socket_path)
        try:
            connection.request("GET", route)
            response = connection.getresponse()
            body = response.read()
            if not 200 <= response.status < 300:
                raise RuntimeError(f"HTTP {response.status}")
            decoded = json.loads(body) if body else {}
            return decoded if isinstance(decoded, dict) else {}
        finally:
            connection.close()

    def _registry(self) -> dict[str, Any]:
        value = self._read_json(self.registry_path, {"subscriptions": []})
        if not isinstance(value, dict) or not isinstance(value.get("subscriptions", []), list):
            raise ValueError("代理订阅注册表格式不正确")
        value.setdefault("subscriptions", [])
        return value

    @staticmethod
    def _public_subscription(item: dict[str, Any]) -> dict[str, Any]:
        result = {key: item.get(key) for key in (
            "id", "name", "host", "enabled", "status", "node_count",
            "created_at", "updated_at", "last_checked_at", "last_error",
            "pool_source", "pool_node_count", "max_healthy_nodes",
            "traffic_upload_bytes", "traffic_download_bytes", "traffic_used_bytes",
            "traffic_total_bytes", "traffic_remaining_bytes", "traffic_expire_at",
        )}
        result["checkable"] = bool(item.get("url_encrypted"))
        return result

    @classmethod
    def _country_for_node(cls, name: str) -> str:
        lowered = str(name or "").casefold()
        for code, aliases in cls.COUNTRY_ALIASES.items():
            for alias in aliases:
                candidate = alias.casefold()
                if candidate.isascii() and len(candidate) <= 2:
                    if re.search(rf"(^|[^a-z]){re.escape(candidate)}([^a-z]|$)", lowered):
                        return code
                elif candidate in lowered:
                    return code
        return "OTHER"

    @staticmethod
    def _subscription_by_source(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {
            str(item.get("pool_source")): item
            for item in registry.get("subscriptions", [])
            if str(item.get("pool_source") or "").strip()
        }

    def _sync_subscription_policy(self, monitor: dict[str, Any], registry: dict[str, Any]) -> None:
        subscriptions = self._subscription_by_source(registry)
        retired = {str(source) for source in monitor.get("retired_sources", []) if str(source).strip()}
        monitor["disabled_sources"] = sorted(retired | {
            source for source, item in subscriptions.items() if not _as_bool(item.get("enabled"), True)
        })
        monitor["source_node_limits"] = {
            source: max(1, min(int(item.get("max_healthy_nodes") or 2), 20))
            for source, item in subscriptions.items()
        }
        countries = monitor.setdefault("node_countries", {})
        for node in monitor.get("nodes", []):
            countries.setdefault(node, self._country_for_node(node))

    def _eligible_nodes(self, monitor: dict[str, Any]) -> list[str]:
        default_countries = [item["code"] for item in self.COUNTRY_OPTIONS]
        allowed = {str(code).upper() for code in monitor.get("allowed_countries", default_countries)}
        disabled = {str(source) for source in monitor.get("disabled_sources", [])}
        limits = monitor.get("source_node_limits", {}) if isinstance(monitor.get("source_node_limits"), dict) else {}
        countries = monitor.get("node_countries", {}) if isinstance(monitor.get("node_countries"), dict) else {}
        sources = monitor.get("sources", {}) if isinstance(monitor.get("sources"), dict) else {}
        counts: dict[str, int] = {}
        result = []
        for node in monitor.get("nodes", []):
            source = str(sources.get(node) or "")
            country = str(countries.get(node) or self._country_for_node(node)).upper()
            if source in disabled or country not in allowed:
                continue
            limit = max(1, min(int(limits.get(source) or 20), 20))
            if counts.get(source, 0) >= limit:
                continue
            counts[source] = counts.get(source, 0) + 1
            result.append(node)
        return result

    def snapshot(self) -> dict[str, Any]:
        self._ensure_enabled()
        monitor = self._read_json(self.monitor_config_path, {})
        state = self._read_json(self.monitor_state_path, {})
        if not isinstance(monitor, dict) or not monitor.get("controller_socket"):
            raise ValueError("代理池 monitor.json 缺少 controller_socket")
        core = {"online": False, "version": "", "error": ""}
        current = ""
        available: list[str] = []
        connection_traffic: dict[str, dict[str, int]] = {}
        try:
            version = self._controller(str(monitor["controller_socket"]), "/version")
            group = self._controller(str(monitor["controller_socket"]), "/proxies/" + quote(str(monitor.get("group") or ""), safe=""))
            core.update(online=True, version=str(version.get("version") or "未知"))
            current = str(group.get("now") or "")
            available = [str(item) for item in group.get("all", [])]
            try:
                connections = self._controller(str(monitor["controller_socket"]), "/connections")
                approved = set(str(node) for node in monitor.get("nodes", []))
                for connection in connections.get("connections", []):
                    matched = approved.intersection(str(node) for node in connection.get("chains", []))
                    for node in matched:
                        traffic = connection_traffic.setdefault(node, {"connections": 0, "bytes": 0})
                        traffic["connections"] += 1
                        traffic["bytes"] += int(connection.get("upload", 0) or 0) + int(connection.get("download", 0) or 0)
            except (OSError, RuntimeError, ValueError, http.client.HTTPException):
                connection_traffic = {}
        except (OSError, RuntimeError, ValueError, http.client.HTTPException) as exc:
            core["error"] = str(exc)
        nodes_state = state.get("nodes", {}) if isinstance(state.get("nodes"), dict) else {}
        nodes = []
        for name in monitor.get("nodes", []):
            record = nodes_state.get(name, {}) if isinstance(nodes_state.get(name), dict) else {}
            nodes.append({
                "name": name,
                "source": (monitor.get("sources") or {}).get(name, ""),
                "status": "active" if name == current else str(record.get("status") or "unknown"),
                "delay_ms": record.get("delay_ms"),
                "last_probe_at": record.get("last_probe_at"),
                "available_in_core": name in available,
            })
        registry = self._registry()
        self._sync_subscription_policy(monitor, registry)
        source_counts: dict[str, int] = {}
        for source in (monitor.get("sources") or {}).values():
            source_counts[str(source)] = source_counts.get(str(source), 0) + 1
        for item in registry["subscriptions"]:
            item["pool_node_count"] = source_counts.get(str(item.get("pool_source") or ""), 0)
            item.setdefault("max_healthy_nodes", 2)
        subscriptions_by_source = self._subscription_by_source(registry)
        eligible = set(self._eligible_nodes(monitor))
        countries = monitor.get("node_countries", {}) or {}
        for node in nodes:
            source = str(node.get("source") or "")
            subscription = subscriptions_by_source.get(source, {})
            traffic = connection_traffic.get(str(node.get("name")), {})
            node.update({
                "country": str(countries.get(node["name"]) or self._country_for_node(node["name"])),
                "eligible": node["name"] in eligible,
                "active_connections": int(traffic.get("connections") or 0),
                "active_traffic_bytes": int(traffic.get("bytes") or 0),
                "subscription_name": subscription.get("name") or source,
                "subscription_used_bytes": subscription.get("traffic_used_bytes"),
                "subscription_remaining_bytes": subscription.get("traffic_remaining_bytes"),
                "subscription_total_bytes": subscription.get("traffic_total_bytes"),
            })
        policy = {key: monitor.get(key) for key in self.POLICY_RULES}
        policy["standby_pool_size"] = int(policy.get("standby_pool_size") or max(1, len(nodes) - 1))
        policy["allowed_countries"] = [str(code).upper() for code in monitor.get("allowed_countries", [item["code"] for item in self.COUNTRY_OPTIONS])]
        last_check = state.get("last_check") if isinstance(state.get("last_check"), dict) else {}
        interval = max(10, int(monitor.get("interval_seconds", 60)))
        checked_at = float(last_check.get("time") or 0)
        monitor_online = bool(checked_at and datetime.now(timezone.utc).timestamp() - checked_at <= interval * 3 + 30)
        return {
            "enabled": True,
            "core": core,
            "monitor": {"online": monitor_online, "last_check": last_check, "group": monitor.get("group", "")},
            "policy": policy,
            "policy_limits": {key: {"min": limits[0], "max": limits[1]} for key, limits in self.POLICY_RULES.items()},
            "country_options": list(self.COUNTRY_OPTIONS),
            "eligible_node_count": len(eligible),
            "nodes": nodes,
            "subscriptions": [self._public_subscription(item) for item in registry["subscriptions"]],
        }

    def update_policy(self, body: dict[str, Any]) -> dict[str, Any]:
        self._ensure_enabled()
        monitor = self._read_json(self.monitor_config_path, {})
        if not isinstance(body, dict):
            raise ValueError("规则格式不正确")
        for key, (minimum, maximum) in self.POLICY_RULES.items():
            if key not in body:
                continue
            label = self.POLICY_LABELS.get(key, "该配置项")
            try:
                value = int(body[key])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{label}必须填写整数") from exc
            if value < minimum or value > maximum:
                unit = "个" if key == "standby_pool_size" or key.endswith("confirmations") or key == "standby_probes_per_cycle" or key == "recovery_successes" else "秒"
                raise ValueError(f"{label}必须在 {minimum}～{maximum} {unit}之间")
            monitor[key] = value
        if "allowed_countries" in body:
            values = body.get("allowed_countries")
            if not isinstance(values, list):
                raise ValueError("可用国家必须是列表")
            supported = {item["code"] for item in self.COUNTRY_OPTIONS}
            countries = list(dict.fromkeys(str(value).upper() for value in values if str(value).strip()))
            if not countries:
                raise ValueError("至少选择一个可用国家")
            invalid = [value for value in countries if value not in supported]
            if invalid:
                raise ValueError("包含不支持的国家选项")
            monitor["allowed_countries"] = countries
        registry = self._registry()
        self._sync_subscription_policy(monitor, registry)
        if not self._eligible_nodes(monitor):
            raise ValueError("当前国家和订阅设置下没有可用节点，请至少保留一个来源")
        eligible_count = len(self._eligible_nodes(monitor))
        if eligible_count < 2:
            raise ValueError("筛选后至少需要保留 2 个可用节点，才能维持主节点和备用节点")
        if int(monitor.get("standby_pool_size", eligible_count - 1)) > eligible_count - 1:
            raise ValueError(f"当前筛选后备用池最多可设为 {eligible_count - 1} 个；请增加可用节点或调小备用池")
        self._atomic_json(self.monitor_config_path, monitor, backup=True)
        return self.snapshot()

    @staticmethod
    def _validate_remote_url(url: str) -> tuple[str, str]:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("订阅地址必须是有效的 HTTP/HTTPS URL，且不能包含 URL 用户名或密码")
        try:
            addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
        except OSError as exc:
            raise ValueError("订阅域名无法解析") from exc
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
                raise ValueError("订阅地址不能指向本机或内网地址")
        return parsed.hostname, url

    async def _download_details(self, url: str) -> tuple[bytes, dict[str, str]]:
        current = url
        async with httpx.AsyncClient(timeout=self.request_timeout_seconds, trust_env=False) as client:
            for _ in range(4):
                await asyncio.to_thread(self._validate_remote_url, current)
                try:
                    response = await client.get(current, follow_redirects=False, headers={"User-Agent": "rose-proxy-pool-admin/1.0"})
                except httpx.HTTPError as exc:
                    raise ValueError(f"订阅请求失败：{type(exc).__name__}") from exc
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("订阅重定向缺少目标地址")
                    current = str(response.url.join(location))
                    continue
                if not 200 <= response.status_code < 300:
                    raise ValueError(f"订阅上游返回 HTTP {response.status_code}")
                raw = response.content
                if not raw or len(raw) > self.max_subscription_bytes:
                    raise ValueError("订阅内容为空或超过大小限制")
                return raw, {str(key).lower(): str(value) for key, value in response.headers.items()}
        raise ValueError("订阅重定向次数过多")

    async def _download(self, url: str) -> bytes:
        raw, _ = await self._download_details(url)
        return raw

    @staticmethod
    def _subscription_usage(headers: dict[str, str]) -> dict[str, Any]:
        value = str(headers.get("subscription-userinfo") or "").strip()
        if not value:
            return {}
        parsed: dict[str, int] = {}
        for part in value.split(";"):
            key, separator, raw_value = part.strip().partition("=")
            if not separator:
                continue
            try:
                parsed[key.strip().lower()] = max(0, int(raw_value.strip()))
            except ValueError:
                continue
        upload = parsed.get("upload", 0)
        download = parsed.get("download", 0)
        total = parsed.get("total", 0)
        used = upload + download
        if total <= 0:
            return {}
        return {
            "traffic_upload_bytes": upload,
            "traffic_download_bytes": download,
            "traffic_used_bytes": used,
            "traffic_total_bytes": total,
            "traffic_remaining_bytes": max(0, total - used),
            "traffic_expire_at": parsed.get("expire") or None,
        }

    @staticmethod
    def _subscription_node_count(raw: bytes) -> int:
        text = raw.decode("utf-8-sig", errors="replace").strip()
        try:
            parsed = yaml.safe_load(text)
        except yaml.YAMLError:
            parsed = None
        if isinstance(parsed, dict):
            proxies = parsed.get("proxies")
            if isinstance(proxies, list) and proxies:
                return len([item for item in proxies if isinstance(item, dict) and item.get("name")])
            providers = parsed.get("proxy-providers")
            if isinstance(providers, dict) and providers:
                return len(providers)
        compact = "".join(text.split())
        try:
            decoded = base64.b64decode(compact + "=" * (-len(compact) % 4), validate=False).decode("utf-8", errors="replace")
        except (ValueError, UnicodeError):
            decoded = text
        prefixes = ("ss://", "ssr://", "vmess://", "vless://", "trojan://", "hysteria2://", "hy2://", "tuic://")
        count = sum(1 for line in decoded.splitlines() if line.strip().lower().startswith(prefixes))
        if not count:
            raise ValueError("未识别到 Clash 或通用代理订阅节点")
        return count

    async def add_subscription(self, body: dict[str, Any]) -> dict[str, Any]:
        self._ensure_enabled()
        name = str(body.get("name") or "").strip()[:80]
        url = str(body.get("url") or "").strip()
        if not name:
            raise ValueError("订阅名称不能为空")
        host, _ = await asyncio.to_thread(self._validate_remote_url, url)
        raw, headers = await self._download_details(url)
        node_count = self._subscription_node_count(raw)
        registry = self._registry()
        if any(str(item.get("name") or "").casefold() == name.casefold() for item in registry["subscriptions"]):
            raise ValueError("订阅名称已存在")
        source_id = hashlib.sha256(f"{name}\0{url}\0{_utc_now()}".encode()).hexdigest()[:16]
        now = _utc_now()
        item = {
            "id": source_id,
            "name": name,
            "host": host,
            "enabled": True,
            "pool_source": f"subscription-{source_id}",
            "max_healthy_nodes": 2,
            "status": "validated",
            "node_count": node_count,
            "url_encrypted": self.cipher.encrypt({"url": url}),
            "created_at": now,
            "updated_at": now,
            "last_checked_at": now,
            "last_error": "",
            **self._subscription_usage(headers),
        }
        registry["subscriptions"].append(item)
        self._atomic_json(self.registry_path, registry)
        return self._public_subscription(item)

    def update_subscription(self, source_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self._ensure_enabled()
        registry = self._registry()
        item = next((entry for entry in registry["subscriptions"] if str(entry.get("id")) == source_id), None)
        if not item:
            raise KeyError(source_id)
        previous = dict(item)
        if "enabled" in body:
            item["enabled"] = _as_bool(body.get("enabled"), True)
        if "max_healthy_nodes" in body:
            try:
                maximum = int(body.get("max_healthy_nodes"))
            except (TypeError, ValueError) as exc:
                raise ValueError("每个订阅的健康节点上限必须填写整数") from exc
            if maximum < 1 or maximum > 20:
                raise ValueError("每个订阅的健康节点上限必须在 1～20 个之间")
            item["max_healthy_nodes"] = maximum
        item["updated_at"] = _utc_now()
        monitor = self._read_json(self.monitor_config_path, {})
        source = str(item.get("pool_source") or "")
        if item.get("enabled") and source:
            monitor["retired_sources"] = [
                value for value in monitor.get("retired_sources", []) if str(value) != source
            ]
        self._sync_subscription_policy(monitor, registry)
        eligible_count = len(self._eligible_nodes(monitor))
        if eligible_count < 2:
            item.clear()
            item.update(previous)
            raise ValueError("该设置会使可用节点少于 2 个，无法维持主节点和备用节点")
        standby_size = int(monitor.get("standby_pool_size", eligible_count - 1))
        if standby_size > eligible_count - 1:
            item.clear()
            item.update(previous)
            raise ValueError(f"该设置下备用池最多可设为 {eligible_count - 1} 个，请先调小备用池")
        self._atomic_json(self.registry_path, registry, backup=True)
        self._atomic_json(self.monitor_config_path, monitor, backup=True)
        item["pool_node_count"] = sum(1 for value in (monitor.get("sources") or {}).values() if str(value) == source)
        return self._public_subscription(item)

    async def check_core_version(self) -> dict[str, Any]:
        self._ensure_enabled()
        monitor = self._read_json(self.monitor_config_path, {})
        if not monitor.get("controller_socket"):
            raise ValueError("代理核心控制地址未配置")
        current_data = self._controller(str(monitor["controller_socket"]), "/version")
        current = str(current_data.get("version") or "").lstrip("v")
        raw = await self._download(self.core_version_release_url)
        try:
            release = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("无法解析代理核心版本信息") from exc
        latest = str(release.get("tag_name") or release.get("name") or "").lstrip("v")
        if not latest:
            raise ValueError("版本服务没有返回有效版本号")

        def version_tuple(value: str) -> tuple[int, ...]:
            parts = []
            for part in value.split("."):
                digits = "".join(char for char in part if char.isdigit())
                if not digits:
                    break
                parts.append(int(digits))
            return tuple(parts)

        return {
            "current": current or "未知",
            "latest": latest,
            "update_available": bool(current and version_tuple(latest) > version_tuple(current)),
            "release_url": str(release.get("html_url") or ""),
            "checked_at": _utc_now(),
        }

    async def check_subscription(self, source_id: str) -> dict[str, Any]:
        self._ensure_enabled()
        registry = self._registry()
        item = next((entry for entry in registry["subscriptions"] if str(entry.get("id")) == source_id), None)
        if not item:
            raise KeyError(source_id)
        if not item.get("url_encrypted"):
            raise ValueError("历史导入来源未保留订阅地址，无法在线检查；可删除后用原订阅 URL 重新添加")
        try:
            url = str(self.cipher.decrypt(str(item.get("url_encrypted") or "")).get("url") or "")
            raw, headers = await self._download_details(url)
            item.update(status="validated", node_count=self._subscription_node_count(raw), last_error="")
            usage = self._subscription_usage(headers)
            for key in (
                "traffic_upload_bytes", "traffic_download_bytes", "traffic_used_bytes",
                "traffic_total_bytes", "traffic_remaining_bytes", "traffic_expire_at",
            ):
                if key in usage:
                    item[key] = usage[key]
        except Exception as exc:
            item.update(status="error", last_error=str(exc)[:300])
        item["last_checked_at"] = _utc_now()
        item["updated_at"] = item["last_checked_at"]
        self._atomic_json(self.registry_path, registry)
        return self._public_subscription(item)

    def delete_subscription(self, source_id: str) -> bool:
        self._ensure_enabled()
        registry = self._registry()
        removed = next((item for item in registry["subscriptions"] if str(item.get("id")) == source_id), None)
        if not removed:
            return False
        registry["subscriptions"] = [item for item in registry["subscriptions"] if str(item.get("id")) != source_id]
        monitor = self._read_json(self.monitor_config_path, {})
        source = str(removed.get("pool_source") or "")
        if source and source in {str(value) for value in (monitor.get("sources") or {}).values()}:
            retired = {str(value) for value in monitor.get("retired_sources", []) if str(value).strip()}
            retired.add(source)
            monitor["retired_sources"] = sorted(retired)
        self._sync_subscription_policy(monitor, registry)
        eligible_count = len(self._eligible_nodes(monitor))
        if eligible_count < 2:
            raise ValueError("不能删除：删除后可用节点少于 2 个，无法维持主节点和备用节点")
        standby_size = int(monitor.get("standby_pool_size", eligible_count - 1))
        if standby_size > eligible_count - 1:
            raise ValueError(f"不能删除：删除后备用池最多可设为 {eligible_count - 1} 个，请先调小备用池")
        self._atomic_json(self.registry_path, registry, backup=True)
        self._atomic_json(self.monitor_config_path, monitor, backup=True)
        return True


__all__ = ["ProxyPoolAdminDisabled", "ProxyPoolAdminService"]
