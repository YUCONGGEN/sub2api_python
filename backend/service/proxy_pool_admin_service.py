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
import logging
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, unquote, urlencode, urlsplit

import httpx
import yaml
from springbootai import Autowired, PostConstruct, Scheduled, Service, get_config

from backend.common.proxy_subscription import normalize_subscription, provider_yaml
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
    SUBSCRIPTION_USAGE_FIELDS = (
        "traffic_upload_bytes", "traffic_download_bytes", "traffic_used_bytes",
        "traffic_total_bytes", "traffic_remaining_bytes", "traffic_expire_at",
        "traffic_expire_label",
    )
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
        self.profile_path: Path | None = None
        self.provider_dir = Path("providers")
        self.mihomo_home: Path | None = None
        self.mihomo_binary = "mihomo"
        self.sync_interval_seconds = 300
        self._last_sync_attempt = 0.0
        self._sync_lock = threading.Lock()
        self.logger = logging.getLogger("proxy_pool_admin")

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
        profile = str(options.get("profile-path") or "").strip()
        self.profile_path = Path(profile).expanduser().resolve() if profile else None
        provider_dir = str(options.get("provider-dir") or "").strip()
        self.provider_dir = Path(provider_dir).expanduser().resolve() if provider_dir else (
            self.profile_path.parent / ".rose-providers" if self.profile_path else self.monitor_config_path.parent / "providers"
        )
        mihomo_home = str(options.get("mihomo-home") or "").strip()
        if mihomo_home:
            self.mihomo_home = Path(mihomo_home).expanduser().resolve()
        elif self.profile_path:
            self.mihomo_home = self.profile_path.parent.parent if self.profile_path.parent.name == "profiles" else self.profile_path.parent
        self.mihomo_binary = str(options.get("mihomo-binary") or "mihomo").strip()
        self.sync_interval_seconds = max(60, min(int(options.get("sync-interval-seconds", 300) or 300), 86400))

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

    def _controller_request(self, socket_path: str, method: str, route: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        connection = _UnixConnection(socket_path, timeout=10)
        try:
            body = json.dumps(payload).encode("utf-8") if payload is not None else None
            headers = {"Content-Type": "application/json"} if body is not None else {}
            connection.request(method, route, body=body, headers=headers)
            response = connection.getresponse()
            body = response.read()
            if not 200 <= response.status < 300:
                message = body.decode("utf-8", errors="replace")[:300]
                raise RuntimeError(f"HTTP {response.status}: {message}")
            decoded = json.loads(body) if body else {}
            return decoded if isinstance(decoded, dict) else {}
        finally:
            connection.close()

    def _controller(self, socket_path: str, route: str) -> dict[str, Any]:
        return self._controller_request(socket_path, "GET", route)

    def _registry(self) -> dict[str, Any]:
        value = self._read_json(self.registry_path, {"subscriptions": []})
        if not isinstance(value, dict) or not isinstance(value.get("subscriptions", []), list):
            raise ValueError("代理订阅注册表格式不正确")
        value.setdefault("subscriptions", [])
        return value

    def _public_subscription(self, item: dict[str, Any]) -> dict[str, Any]:
        result = {key: item.get(key) for key in (
            "id", "name", "host", "enabled", "status", "node_count",
            "created_at", "updated_at", "last_checked_at", "last_error",
            "pool_source", "pool_node_count", "max_healthy_nodes",
            "traffic_upload_bytes", "traffic_download_bytes", "traffic_used_bytes",
            "traffic_total_bytes", "traffic_remaining_bytes", "traffic_expire_at",
            "traffic_expire_label",
        )}
        result["note"] = ""
        if item.get("note_encrypted"):
            try:
                result["note"] = str(self.cipher.decrypt(str(item["note_encrypted"])).get("note") or "")[:100]
            except Exception:
                result["note"] = ""
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

    def _eligible_nodes(self, monitor: dict[str, Any], state: dict[str, Any] | None = None, current: str = "") -> list[str]:
        default_countries = [item["code"] for item in self.COUNTRY_OPTIONS]
        allowed = {str(code).upper() for code in monitor.get("allowed_countries", default_countries)}
        disabled = {str(source) for source in monitor.get("disabled_sources", [])}
        limits = monitor.get("source_node_limits", {}) if isinstance(monitor.get("source_node_limits"), dict) else {}
        countries = monitor.get("node_countries", {}) if isinstance(monitor.get("node_countries"), dict) else {}
        sources = monitor.get("sources", {}) if isinstance(monitor.get("sources"), dict) else {}
        records = (state or {}).get("nodes", {}) if isinstance((state or {}).get("nodes"), dict) else {}
        grouped: dict[str, list[tuple[int, int, str]]] = {}
        rank = {"active": 0, "ready": 1, "probation": 2, "unknown": 3, "quarantined": 4}
        for index, node in enumerate(monitor.get("nodes", [])):
            source = str(sources.get(node) or "")
            country = str(countries.get(node) or self._country_for_node(node)).upper()
            if source in disabled or country not in allowed:
                continue
            recorded = str((records.get(node) or {}).get("status") or "unknown")
            status = "active" if node == current and recorded != "quarantined" else recorded
            grouped.setdefault(source, []).append((rank.get(status, 3), index, node))
        result: list[str] = []
        for source, candidates in grouped.items():
            limit = max(1, min(int(limits.get(source) or 20), 20))
            result.extend(node for _, _, node in sorted(candidates)[:limit])
        return result

    def _validate_pool_capacity(self, monitor: dict[str, Any], state: dict[str, Any] | None = None) -> int:
        eligible_count = len(self._eligible_nodes(monitor, state or {}))
        if eligible_count < 2:
            raise ValueError("至少需要保留 2 个可用节点，才能维持主节点和备用节点")
        standby_size = int(monitor.get("standby_pool_size", eligible_count - 1))
        if standby_size > eligible_count - 1:
            raise ValueError(f"当前备用池最多可设为 {eligible_count - 1} 个，请先调小备用池")
        return eligible_count

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
        registry = self._registry()
        self._sync_subscription_policy(monitor, registry)
        nodes_state = state.get("nodes", {}) if isinstance(state.get("nodes"), dict) else {}
        selected_nodes = self._eligible_nodes(monitor, state, current)
        nodes = []
        for name in selected_nodes:
            record = nodes_state.get(name, {}) if isinstance(nodes_state.get(name), dict) else {}
            nodes.append({
                "name": name,
                "source": (monitor.get("sources") or {}).get(name, ""),
                "status": "active" if name == current else str(record.get("status") or "unknown"),
                "delay_ms": record.get("delay_ms"),
                "last_probe_at": record.get("last_probe_at"),
                "available_in_core": name in available,
            })
        source_counts: dict[str, int] = {}
        for name in selected_nodes:
            source = str((monitor.get("sources") or {}).get(name) or "")
            source_counts[source] = source_counts.get(source, 0) + 1
        for item in registry["subscriptions"]:
            item["pool_node_count"] = source_counts.get(str(item.get("pool_source") or ""), 0)
            item.setdefault("max_healthy_nodes", 2)
        subscriptions_by_source = self._subscription_by_source(registry)
        eligible = set(selected_nodes)
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
            "monitor": {"online": monitor_online, "last_check": last_check, "group": monitor.get("group", ""), "sync": monitor.get("pool_sync", {})},
            "policy": policy,
            "policy_limits": {key: {"min": limits[0], "max": limits[1]} for key, limits in self.POLICY_RULES.items()},
            "country_options": list(self.COUNTRY_OPTIONS),
            "eligible_node_count": len(eligible),
            "nodes": nodes,
            "subscriptions": [self._public_subscription(item) for item in registry["subscriptions"]],
        }

    async def update_policy(self, body: dict[str, Any]) -> dict[str, Any]:
        self._ensure_enabled()
        monitor = self._read_json(self.monitor_config_path, {})
        previous_monitor = json.loads(json.dumps(monitor))
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
        try:
            self._atomic_json(self.monitor_config_path, monitor, backup=True)
            if self.profile_path:
                await self.synchronize_pool()
                monitor = self._read_json(self.monitor_config_path, {})
            state = self._read_json(self.monitor_state_path, {})
            self._validate_pool_capacity(monitor, state)
        except Exception:
            self._atomic_json(self.monitor_config_path, previous_monitor)
            raise
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
        result = {
            "traffic_upload_bytes": upload,
            "traffic_download_bytes": download,
            "traffic_used_bytes": used,
            "traffic_total_bytes": total,
            "traffic_remaining_bytes": max(0, total - used),
        }
        if parsed.get("expire"):
            result["traffic_expire_at"] = parsed["expire"]
        return result

    @staticmethod
    def _subscription_content_usage(raw: bytes) -> dict[str, Any]:
        """Read quota hints embedded as pseudo-node names by some providers."""
        text = raw.decode("utf-8-sig", errors="replace")
        candidates = [text]
        compact = "".join(text.split())
        try:
            decoded = base64.b64decode(compact + "=" * (-len(compact) % 4), validate=False).decode("utf-8", errors="replace")
            if decoded and decoded != text:
                candidates.append(decoded)
        except (ValueError, UnicodeError):
            pass
        embedded_names: list[str] = []
        for candidate in candidates:
            for line in candidate.splitlines():
                value = line.strip()
                if value.lower().startswith("vmess://"):
                    try:
                        payload = value.split("://", 1)[1]
                        details = json.loads(base64.b64decode(payload + "=" * (-len(payload) % 4), validate=False).decode("utf-8"))
                        embedded_names.append(str(details.get("ps") or ""))
                    except (ValueError, UnicodeError, json.JSONDecodeError):
                        pass
                elif "://" in value and "#" in value:
                    embedded_names.append(unquote(value.rsplit("#", 1)[1]).strip())
        content = "\n".join([*candidates, *embedded_names])
        result: dict[str, Any] = {}
        remaining = re.search(
            r"(?:剩余流量|流量剩余|剩余)\s*[:：]\s*([0-9]+(?:\.[0-9]+)?)\s*(B|KB|MB|GB|TB)",
            content,
            flags=re.IGNORECASE,
        )
        if remaining:
            units = {"B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}
            result["traffic_remaining_bytes"] = int(float(remaining.group(1)) * units[remaining.group(2).upper()])
        expiry = re.search(
            r"(?:套餐到期|到期时间|有效期)\s*[:：]\s*(长期有效|永不过期|永久|[0-9]{4}[-/.年][0-9]{1,2}[-/.月][0-9]{1,2}日?)",
            content,
            flags=re.IGNORECASE,
        )
        if expiry:
            label = expiry.group(1).strip()
            if label in {"长期有效", "永不过期", "永久"}:
                result["traffic_expire_label"] = "长期有效"
            else:
                normalized = re.sub(r"[/.年月]", "-", label).rstrip("日-")
                try:
                    expires = datetime.strptime(normalized, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    result["traffic_expire_at"] = int(expires.timestamp())
                    result["traffic_expire_label"] = normalized
                except ValueError:
                    pass
        return result

    @classmethod
    def _refresh_subscription_usage(cls, item: dict[str, Any], raw: bytes, headers: dict[str, str]) -> None:
        usage = cls._subscription_content_usage(raw)
        usage.update(cls._subscription_usage(headers))
        for key in cls.SUBSCRIPTION_USAGE_FIELDS:
            item.pop(key, None)
        item.update(usage)

    @staticmethod
    def _atomic_text(path: Path, text: str, mode: int = 0o600) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_name, mode)
            os.replace(temporary_name, path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)

    @staticmethod
    def _managed_provider_name(item: dict[str, Any]) -> str:
        return f"rose-{str(item.get('id') or '')[:16]}"

    def _validate_profile(self, path: Path) -> None:
        try:
            command = [self.mihomo_binary]
            if self.mihomo_home:
                command.extend(["-d", str(self.mihomo_home)])
            command.extend(["-t", "-f", str(path)])
            result = subprocess.run(
                command,
                capture_output=True, text=True, timeout=45, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError("无法调用 Mihomo 校验生成的节点配置") from exc
        if result.returncode != 0:
            raise ValueError("Mihomo 拒绝了生成的节点配置，已保留原配置")

    def _render_profile(self, provider_files: dict[str, str]) -> str:
        if not self.profile_path or not self.profile_path.is_file():
            raise ValueError("尚未配置有效的 Mihomo 主配置文件路径")
        try:
            profile = yaml.safe_load(self.profile_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ValueError("无法读取 Mihomo 主配置文件") from exc
        if not isinstance(profile, dict):
            raise ValueError("Mihomo 主配置文件格式不正确")
        existing = profile.get("proxy-providers") if isinstance(profile.get("proxy-providers"), dict) else {}
        providers = {key: value for key, value in existing.items() if not str(key).startswith("rose-")}
        for name, filename in provider_files.items():
            providers[name] = {
                "type": "file",
                "path": str((self.provider_dir / filename).resolve()),
                "health-check": {
                    "enable": True,
                    "url": "https://cp.cloudflare.com/generate_204",
                    "interval": 300,
                    "timeout": 5000,
                    "lazy": True,
                },
            }
        profile["proxy-providers"] = providers
        group_name = str(self._read_json(self.monitor_config_path, {}).get("group") or "")
        managed = next((group for group in profile.get("proxy-groups", []) if str(group.get("name")) == group_name), None)
        if not isinstance(managed, dict):
            raise ValueError("Mihomo 主配置中找不到受控代理组")
        managed["type"] = "select"
        managed["proxies"] = []
        managed["use"] = list(provider_files)
        return yaml.safe_dump(profile, allow_unicode=True, sort_keys=False, width=4096)

    def _proxy_batch_is_compatible(self, proxies: list[dict[str, Any]]) -> bool:
        if not self.profile_path or not proxies:
            return bool(proxies)
        try:
            profile = yaml.safe_load(self.profile_path.read_text(encoding="utf-8"))
            if not isinstance(profile, dict):
                return False
            existing = profile.get("proxy-providers") if isinstance(profile.get("proxy-providers"), dict) else {}
            profile["proxy-providers"] = {key: value for key, value in existing.items() if not str(key).startswith("rose-")}
            profile["proxies"] = proxies
            group_name = str(self._read_json(self.monitor_config_path, {}).get("group") or "")
            managed = next((group for group in profile.get("proxy-groups", []) if str(group.get("name")) == group_name), None)
            if not isinstance(managed, dict):
                return False
            managed.pop("use", None)
            managed["type"] = "select"
            managed["proxies"] = [str(proxy["name"]) for proxy in proxies]
            descriptor, filename = tempfile.mkstemp(prefix=".rose-proxy-check-", suffix=".yaml", dir=str(self.profile_path.parent))
            os.close(descriptor)
            candidate = Path(filename)
            try:
                candidate.write_text(yaml.safe_dump(profile, allow_unicode=True, sort_keys=False, width=4096), encoding="utf-8")
                self._validate_profile(candidate)
                return True
            finally:
                candidate.unlink(missing_ok=True)
        except (OSError, ValueError, yaml.YAMLError):
            return False

    def _filter_compatible_proxies(self, proxies: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Bisect only rejected batches so one bad upstream node cannot drop a provider."""
        if not self.profile_path or self._proxy_batch_is_compatible(proxies):
            return proxies
        if len(proxies) == 1:
            return []
        middle = len(proxies) // 2
        return self._filter_compatible_proxies(proxies[:middle]) + self._filter_compatible_proxies(proxies[middle:])

    def _ensure_allowed_selection(self, monitor: dict[str, Any], state: dict[str, Any]) -> str:
        socket_path = str(monitor["controller_socket"])
        group_name = str(monitor.get("group") or "")
        group = self._controller(socket_path, "/proxies/" + quote(group_name, safe=""))
        current = str(group.get("now") or "")
        eligible = self._eligible_nodes(monitor, state, current)
        if current in eligible:
            return current
        query = urlencode({
            "url": str(monitor.get("test_url") or "https://cp.cloudflare.com/generate_204"),
            "timeout": int(monitor.get("probe_timeout_ms") or 5000),
        })
        for candidate in eligible:
            try:
                result = self._controller(socket_path, f"/proxies/{quote(candidate, safe='')}/delay?{query}")
                if int(result.get("delay") or 0) <= 0:
                    continue
                self._controller_request(
                    socket_path, "PUT", "/proxies/" + quote(group_name, safe=""), {"name": candidate}
                )
                record = state.setdefault("nodes", {}).setdefault(candidate, {})
                record.update(status="active", delay_ms=int(result["delay"]), last_probe_at=time.time())
                return candidate
            except (OSError, RuntimeError, ValueError, http.client.HTTPException):
                continue
        raise ValueError("同步后的国家范围内没有通过连通性检查的节点，已保留原节点池")

    def _seed_candidate_health(self, monitor: dict[str, Any], state: dict[str, Any]) -> int:
        """Probe enough candidates to fill each source's configured healthy slots."""
        allowed = {str(value).upper() for value in monitor.get("allowed_countries", [])}
        disabled = {str(value) for value in monitor.get("disabled_sources", [])}
        sources = monitor.get("sources", {}) or {}
        countries = monitor.get("node_countries", {}) or {}
        limits = monitor.get("source_node_limits", {}) or {}
        grouped: dict[str, list[str]] = {}
        for node in monitor.get("nodes", []):
            source = str(sources.get(node) or "")
            country = str(countries.get(node) or self._country_for_node(node)).upper()
            if source in disabled or (allowed and country not in allowed):
                continue
            grouped.setdefault(source, []).append(node)
        socket_path = str(monitor["controller_socket"])
        query = urlencode({
            "url": str(monitor.get("test_url") or "https://cp.cloudflare.com/generate_204"),
            "timeout": int(monitor.get("probe_timeout_ms") or 5000),
        })
        now = time.time()
        healthy_total = 0
        records = state.setdefault("nodes", {})
        for source, candidates in grouped.items():
            wanted = max(1, min(int(limits.get(source) or 20), 20))
            healthy = 0
            for candidate in candidates:
                record = records.setdefault(candidate, {})
                if record.get("status") in {"active", "ready"} and record.get("last_success_at"):
                    healthy += 1
                else:
                    try:
                        result = self._controller(socket_path, f"/proxies/{quote(candidate, safe='')}/delay?{query}")
                        delay = int(result.get("delay") or 0)
                    except (OSError, RuntimeError, ValueError, http.client.HTTPException):
                        delay = 0
                    record["last_probe_at"] = now
                    record["checks"] = int(record.get("checks") or 0) + 1
                    if delay > 0:
                        record.update(status="ready", delay_ms=delay, last_success_at=now, consecutive_successes=1)
                        healthy += 1
                    else:
                        record.update(status="quarantined", consecutive_successes=0)
                        record["failures"] = int(record.get("failures") or 0) + 1
                        state.setdefault("quarantine_until", {})[candidate] = now + float(monitor.get("quarantine_seconds") or 600)
                if healthy >= wanted:
                    break
            healthy_total += healthy
        return healthy_total

    async def synchronize_pool(self, *, force: bool = False) -> dict[str, Any]:
        """Download, normalize, validate and hot-load enabled subscriptions."""
        self._ensure_enabled()
        if not self.profile_path:
            return {"configured": False, "changed": False, "message": "未配置自动节点同步"}
        if not self._sync_lock.acquire(blocking=False):
            raise ValueError("节点同步正在执行，请稍后再试")
        self._last_sync_attempt = time.monotonic()
        try:
            registry = self._registry()
            monitor = self._read_json(self.monitor_config_path, {})
            self._sync_subscription_policy(monitor, registry)
            provider_texts: dict[str, str] = {}
            provider_files: dict[str, str] = {}
            sources: dict[str, str] = {}
            countries: dict[str, str] = {}
            failures: list[str] = []
            now = _utc_now()
            for item in registry.get("subscriptions", []):
                if not _as_bool(item.get("enabled"), True):
                    continue
                provider_name = self._managed_provider_name(item)
                filename = f"{provider_name}.yaml"
                proxies: list[dict[str, Any]] = []
                encrypted = str(item.get("url_encrypted") or "")
                try:
                    if not encrypted:
                        raise ValueError("订阅地址未保留，请删除后重新添加")
                    url = str(self.cipher.decrypt(encrypted).get("url") or "")
                    raw, headers = await self._download_details(url)
                    raw_hash = hashlib.sha256(raw).hexdigest()
                    existing = self.provider_dir / filename
                    if not force and str(item.get("subscription_content_hash") or "") == raw_hash and existing.is_file():
                        cached = yaml.safe_load(existing.read_text(encoding="utf-8"))
                        proxies = [entry for entry in (cached.get("proxies") or []) if isinstance(entry, dict)]
                    else:
                        proxies = normalize_subscription(raw, provider_name)
                        proxies = self._filter_compatible_proxies(proxies)
                    if not proxies:
                        raise ValueError("订阅中的节点均未通过 Mihomo 配置校验")
                    item.update(
                        status="validated", node_count=len(proxies), last_error="",
                        last_checked_at=now, updated_at=now,
                        subscription_content_hash=raw_hash,
                    )
                    self._refresh_subscription_usage(item, raw, headers)
                except Exception as exc:
                    item.update(status="error", last_error=str(exc)[:300], last_checked_at=now, updated_at=now)
                    existing = self.provider_dir / filename
                    if existing.is_file():
                        try:
                            value = yaml.safe_load(existing.read_text(encoding="utf-8"))
                            proxies = [entry for entry in (value.get("proxies") or []) if isinstance(entry, dict)]
                        except (OSError, yaml.YAMLError, AttributeError):
                            proxies = []
                    failures.append(str(item.get("name") or item.get("id")))
                if not proxies:
                    continue
                provider_texts[filename] = provider_yaml(proxies)
                provider_files[provider_name] = filename
                source = str(item.get("pool_source") or f"subscription-{item['id']}")
                item["pool_source"] = source
                for proxy in proxies:
                    node = str(proxy.get("name") or "")
                    if node:
                        sources[node] = source
                        countries[node] = self._country_for_node(node)
            if len(sources) < 2:
                raise ValueError("启用的订阅合计不足 2 个可用节点，已保留原节点池")

            content_hash = hashlib.sha256(json.dumps({
                "providers": provider_texts,
                "sources": sources,
                "allowed_countries": monitor.get("allowed_countries", []),
                "source_node_limits": monitor.get("source_node_limits", {}),
            }, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
            previous_hash = str((monitor.get("pool_sync") or {}).get("content_hash") or "")
            changed = force or content_hash != previous_hash
            monitor["nodes"] = list(sources)
            monitor["sources"] = sources
            monitor["node_countries"] = countries
            self._sync_subscription_policy(monitor, registry)
            state = self._read_json(self.monitor_state_path, {})
            if isinstance(state.get("nodes"), dict):
                state["nodes"] = {name: value for name, value in state["nodes"].items() if name in sources}
            if isinstance(state.get("quarantine_until"), dict):
                state["quarantine_until"] = {name: value for name, value in state["quarantine_until"].items() if name in sources}
            if changed:
                profile_text = self._render_profile(provider_files)
                backup_dir = self.monitor_config_path.parent / "backups" / datetime.now().strftime("pool-sync-%Y%m%d-%H%M%S-%f")
                backup_dir.mkdir(parents=True, exist_ok=False)
                if self.profile_path.exists():
                    shutil.copy2(self.profile_path, backup_dir / "profile.yml")
                if self.provider_dir.exists():
                    shutil.copytree(self.provider_dir, backup_dir / "providers")
                self.provider_dir.mkdir(parents=True, exist_ok=True)
                try:
                    for filename, text in provider_texts.items():
                        self._atomic_text(self.provider_dir / filename, text)
                    for stale in self.provider_dir.glob("rose-*.yaml"):
                        if stale.name not in provider_texts:
                            stale.unlink()
                    descriptor, candidate_name = tempfile.mkstemp(prefix=".rose-profile-", suffix=".yaml", dir=str(self.profile_path.parent))
                    os.close(descriptor)
                    candidate = Path(candidate_name)
                    try:
                        candidate.write_text(profile_text, encoding="utf-8")
                        self._validate_profile(candidate)
                        os.replace(candidate, self.profile_path)
                    finally:
                        candidate.unlink(missing_ok=True)
                    self._controller_request(
                        str(monitor["controller_socket"]), "PUT", "/configs?force=false",
                        {"path": str(self.profile_path)},
                    )
                    self._seed_candidate_health(monitor, state)
                    self._ensure_allowed_selection(monitor, state)
                    self._validate_pool_capacity(monitor, state)
                except Exception:
                    old_profile = backup_dir / "profile.yml"
                    if old_profile.exists():
                        shutil.copy2(old_profile, self.profile_path)
                    old_providers = backup_dir / "providers"
                    if self.provider_dir.exists():
                        shutil.rmtree(self.provider_dir)
                    if old_providers.exists():
                        shutil.copytree(old_providers, self.provider_dir)
                    try:
                        self._controller_request(
                            str(monitor["controller_socket"]), "PUT", "/configs?force=false",
                            {"path": str(self.profile_path)},
                        )
                    except Exception:
                        pass
                    raise
            else:
                self._ensure_allowed_selection(monitor, state)
            monitor["pool_sync"] = {
                "configured": True,
                "changed": changed,
                "content_hash": content_hash,
                "synced_at": now,
                "candidate_count": len(sources),
                "provider_count": len(provider_files),
                "failed_subscriptions": failures,
            }
            self._atomic_json(self.registry_path, registry, backup=changed)
            self._atomic_json(self.monitor_config_path, monitor, backup=changed)
            self._atomic_json(self.monitor_state_path, state)
            return dict(monitor["pool_sync"])
        finally:
            self._sync_lock.release()

    @Scheduled(fixed_rate=60000, initial_delay=30000)
    async def scheduled_pool_sync(self) -> None:
        if not self.enabled or not self.profile_path:
            return
        if time.monotonic() - self._last_sync_attempt < self.sync_interval_seconds:
            return
        try:
            await self.synchronize_pool()
        except Exception as exc:
            self.logger.warning("代理订阅自动同步失败，保留原节点池: %s", exc)

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
        note = str(body.get("note") or "").strip()
        url = str(body.get("url") or "").strip()
        if not name:
            raise ValueError("订阅名称不能为空")
        if len(note) > 100:
            raise ValueError("订阅备注不能超过 100 个字符")
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
        }
        self._refresh_subscription_usage(item, raw, headers)
        if note:
            item["note_encrypted"] = self.cipher.encrypt({"note": note})
        registry["subscriptions"].append(item)
        self._atomic_json(self.registry_path, registry, backup=True)
        try:
            if not self.profile_path:
                monitor = self._read_json(self.monitor_config_path, {})
                source = str(item.get("pool_source") or "")
                if item.get("enabled") and source:
                    monitor["retired_sources"] = [value for value in monitor.get("retired_sources", []) if str(value) != source]
                self._sync_subscription_policy(monitor, registry)
                self._atomic_json(self.monitor_config_path, monitor, backup=True)
            await self.synchronize_pool()
        except Exception:
            registry["subscriptions"] = [entry for entry in registry["subscriptions"] if str(entry.get("id")) != source_id]
            self._atomic_json(self.registry_path, registry)
            raise
        return self._public_subscription(item)

    async def update_subscription(self, source_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self._ensure_enabled()
        registry = self._registry()
        item = next((entry for entry in registry["subscriptions"] if str(entry.get("id")) == source_id), None)
        if not item:
            raise KeyError(source_id)
        previous = dict(item)
        if "name" in body:
            name = str(body.get("name") or "").strip()
            if not name:
                raise ValueError("订阅名称不能为空")
            if len(name) > 80:
                raise ValueError("订阅名称不能超过 80 个字符")
            if any(
                str(entry.get("id")) != source_id
                and str(entry.get("name") or "").casefold() == name.casefold()
                for entry in registry["subscriptions"]
            ):
                raise ValueError("订阅名称已存在")
            item["name"] = name
        if "note" in body:
            note = str(body.get("note") or "").strip()
            if len(note) > 100:
                raise ValueError("订阅备注不能超过 100 个字符")
            if note:
                item["note_encrypted"] = self.cipher.encrypt({"note": note})
            else:
                item.pop("note_encrypted", None)
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
        self._atomic_json(self.registry_path, registry, backup=True)
        try:
            if not self.profile_path:
                monitor = self._read_json(self.monitor_config_path, {})
                source = str(item.get("pool_source") or "")
                if item.get("enabled") and source:
                    monitor["retired_sources"] = [value for value in monitor.get("retired_sources", []) if str(value) != source]
                self._sync_subscription_policy(monitor, registry)
                self._atomic_json(self.monitor_config_path, monitor, backup=True)
            await self.synchronize_pool()
            monitor = self._read_json(self.monitor_config_path, {})
            self._validate_pool_capacity(monitor, self._read_json(self.monitor_state_path, {}))
        except Exception:
            item.clear()
            item.update(previous)
            self._atomic_json(self.registry_path, registry)
            if self.profile_path:
                try:
                    await self.synchronize_pool(force=True)
                except Exception:
                    pass
            raise
        monitor = self._read_json(self.monitor_config_path, {})
        source = str(item.get("pool_source") or "")
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
            self._refresh_subscription_usage(item, raw, headers)
        except Exception as exc:
            item.update(status="error", last_error=str(exc)[:300])
        item["last_checked_at"] = _utc_now()
        item["updated_at"] = item["last_checked_at"]
        self._atomic_json(self.registry_path, registry)
        if item.get("status") == "validated":
            await self.synchronize_pool()
        return self._public_subscription(item)

    async def delete_subscription(self, source_id: str) -> bool:
        self._ensure_enabled()
        registry = self._registry()
        removed = next((item for item in registry["subscriptions"] if str(item.get("id")) == source_id), None)
        if not removed:
            return False
        registry["subscriptions"] = [item for item in registry["subscriptions"] if str(item.get("id")) != source_id]
        self._atomic_json(self.registry_path, registry, backup=True)
        try:
            if not self.profile_path:
                monitor = self._read_json(self.monitor_config_path, {})
                source = str(removed.get("pool_source") or "")
                if source and source in {str(value) for value in (monitor.get("sources") or {}).values()}:
                    retired = {str(value) for value in monitor.get("retired_sources", []) if str(value).strip()}
                    retired.add(source)
                    monitor["retired_sources"] = sorted(retired)
                self._sync_subscription_policy(monitor, registry)
                self._atomic_json(self.monitor_config_path, monitor, backup=True)
            await self.synchronize_pool(force=True)
            monitor = self._read_json(self.monitor_config_path, {})
            self._validate_pool_capacity(monitor, self._read_json(self.monitor_state_path, {}))
        except Exception:
            registry["subscriptions"].append(removed)
            self._atomic_json(self.registry_path, registry)
            if self.profile_path:
                try:
                    await self.synchronize_pool(force=True)
                except Exception:
                    pass
            raise
        return True


__all__ = ["ProxyPoolAdminDisabled", "ProxyPoolAdminService"]
