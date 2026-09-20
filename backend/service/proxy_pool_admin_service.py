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
        )}
        result["checkable"] = bool(item.get("url_encrypted"))
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
        try:
            version = self._controller(str(monitor["controller_socket"]), "/version")
            group = self._controller(str(monitor["controller_socket"]), "/proxies/" + quote(str(monitor.get("group") or ""), safe=""))
            core.update(online=True, version=str(version.get("version") or "未知"))
            current = str(group.get("now") or "")
            available = [str(item) for item in group.get("all", [])]
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
        policy = {key: monitor.get(key) for key in self.POLICY_RULES}
        policy["standby_pool_size"] = int(policy.get("standby_pool_size") or max(1, len(nodes) - 1))
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
        node_count = len(monitor.get("nodes", []))
        if node_count > 1 and int(monitor.get("standby_pool_size", node_count - 1)) > node_count - 1:
            raise ValueError(f"备用池最多可设为 {node_count - 1} 个；请先增加可用节点")
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

    async def _download(self, url: str) -> bytes:
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
                return raw
        raise ValueError("订阅重定向次数过多")

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
        raw = await self._download(url)
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
            "status": "validated",
            "node_count": node_count,
            "url_encrypted": self.cipher.encrypt({"url": url}),
            "created_at": now,
            "updated_at": now,
            "last_checked_at": now,
            "last_error": "",
        }
        registry["subscriptions"].append(item)
        self._atomic_json(self.registry_path, registry)
        return self._public_subscription(item)

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
            raw = await self._download(url)
            item.update(status="validated", node_count=self._subscription_node_count(raw), last_error="")
        except Exception as exc:
            item.update(status="error", last_error=str(exc)[:300])
        item["last_checked_at"] = _utc_now()
        item["updated_at"] = item["last_checked_at"]
        self._atomic_json(self.registry_path, registry)
        return self._public_subscription(item)

    def delete_subscription(self, source_id: str) -> bool:
        self._ensure_enabled()
        registry = self._registry()
        before = len(registry["subscriptions"])
        registry["subscriptions"] = [item for item in registry["subscriptions"] if str(item.get("id")) != source_id]
        if len(registry["subscriptions"]) == before:
            return False
        self._atomic_json(self.registry_path, registry)
        return True


__all__ = ["ProxyPoolAdminDisabled", "ProxyPoolAdminService"]
