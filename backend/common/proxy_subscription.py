"""Local proxy-subscription normalization for the managed Mihomo pool.

No subscription URL or proxy credential leaves the server.  The normalized
provider files are written with owner-only permissions by the caller.
"""

from __future__ import annotations

import base64
import json
import re
from copy import deepcopy
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import yaml


SUPPORTED_SCHEMES = {"ss", "vmess", "vless", "trojan", "hysteria2", "hy2"}


def _b64decode(value: str) -> bytes:
    compact = "".join(str(value or "").split()).replace("-", "+").replace("_", "/")
    return base64.b64decode(compact + "=" * (-len(compact) % 4), validate=False)


def _first(query: dict[str, list[str]], *names: str, default: str = "") -> str:
    for name in names:
        values = query.get(name)
        if values:
            return str(values[0])
    return default


def _bool(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _port(parsed) -> int:
    try:
        value = int(parsed.port or 0)
    except ValueError as exc:
        raise ValueError("代理节点端口不正确") from exc
    if value < 1 or value > 65535:
        raise ValueError("代理节点缺少有效端口")
    return value


def _name(parsed, fallback: str) -> str:
    return unquote(parsed.fragment or "").strip() or fallback


def _transport(proxy: dict[str, Any], query: dict[str, list[str]]) -> None:
    network = _first(query, "type", "network", default="tcp").lower()
    if network and network != "tcp":
        proxy["network"] = network
    host = _first(query, "host")
    path = unquote(_first(query, "path", default="/"))
    if network == "ws":
        options: dict[str, Any] = {"path": path}
        if host:
            options["headers"] = {"Host": host}
        proxy["ws-opts"] = options
    elif network == "grpc":
        proxy["grpc-opts"] = {"grpc-service-name": _first(query, "serviceName", "service-name")}
    elif network == "http":
        options = {}
        if path:
            options["path"] = [path]
        if host:
            options["headers"] = {"Host": [host]}
        if options:
            proxy["http-opts"] = options


def _tls(proxy: dict[str, Any], query: dict[str, list[str]]) -> None:
    security = _first(query, "security").lower()
    if security not in {"tls", "reality"}:
        return
    proxy["tls"] = True
    servername = _first(query, "sni", "servername")
    if servername:
        proxy["servername"] = servername
    fingerprint = _first(query, "fp", "client-fingerprint")
    if fingerprint:
        proxy["client-fingerprint"] = fingerprint
    if _bool(_first(query, "allowInsecure", "insecure", "skip-cert-verify")):
        proxy["skip-cert-verify"] = True
    alpn = _first(query, "alpn")
    if alpn:
        proxy["alpn"] = [value for value in alpn.split(",") if value]
    if security == "reality":
        reality = {
            "public-key": _first(query, "pbk", "public-key"),
            "short-id": _first(query, "sid", "short-id"),
        }
        spider = unquote(_first(query, "spx", "spider-x"))
        if spider:
            reality["spider-x"] = spider
        proxy["reality-opts"] = {key: value for key, value in reality.items() if value}


def _vmess(value: str, fallback: str) -> dict[str, Any]:
    try:
        data = json.loads(_b64decode(value.split("://", 1)[1]).decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("VMess 节点格式不正确") from exc
    proxy: dict[str, Any] = {
        "name": str(data.get("ps") or fallback),
        "type": "vmess",
        "server": str(data.get("add") or ""),
        "port": int(data.get("port") or 0),
        "uuid": str(data.get("id") or ""),
        "alterId": int(data.get("aid") or 0),
        "cipher": str(data.get("scy") or "auto"),
        "udp": True,
    }
    network = str(data.get("net") or "tcp").lower()
    if network != "tcp":
        proxy["network"] = network
    if network == "ws":
        options: dict[str, Any] = {"path": str(data.get("path") or "/")}
        if data.get("host"):
            options["headers"] = {"Host": str(data["host"])}
        proxy["ws-opts"] = options
    elif network == "grpc":
        proxy["grpc-opts"] = {"grpc-service-name": str(data.get("path") or data.get("serviceName") or "")}
    if str(data.get("tls") or "").lower() in {"tls", "reality"}:
        proxy["tls"] = True
        if data.get("sni") or data.get("host"):
            proxy["servername"] = str(data.get("sni") or data.get("host"))
        if data.get("fp"):
            proxy["client-fingerprint"] = str(data["fp"])
    if not proxy["server"] or not proxy["uuid"] or proxy["port"] < 1:
        raise ValueError("VMess 节点缺少服务器、端口或 UUID")
    return proxy


def _ss(value: str, fallback: str) -> dict[str, Any]:
    body = value.split("://", 1)[1]
    fragment = ""
    if "#" in body:
        body, fragment = body.split("#", 1)
    query_text = ""
    if "?" in body:
        body, query_text = body.split("?", 1)
    if "@" not in body:
        try:
            body = _b64decode(body).decode("utf-8")
        except (ValueError, UnicodeError) as exc:
            raise ValueError("Shadowsocks 节点格式不正确") from exc
    user_info, separator, server_info = body.rpartition("@")
    if not separator:
        raise ValueError("Shadowsocks 节点缺少服务器")
    if ":" not in user_info:
        try:
            user_info = _b64decode(user_info).decode("utf-8")
        except (ValueError, UnicodeError) as exc:
            raise ValueError("Shadowsocks 凭据格式不正确") from exc
    method, separator, password = user_info.partition(":")
    if not separator:
        raise ValueError("Shadowsocks 凭据缺少加密方式")
    parsed = urlsplit("ss://user@" + server_info)
    query = parse_qs(query_text)
    proxy: dict[str, Any] = {
        "name": unquote(fragment).strip() or fallback,
        "type": "ss", "server": str(parsed.hostname or ""), "port": _port(parsed),
        "cipher": unquote(method), "password": unquote(password), "udp": True,
    }
    plugin = _first(query, "plugin")
    if plugin:
        name, *options = unquote(plugin).split(";")
        proxy["plugin"] = "obfs" if name in {"simple-obfs", "obfs-local"} else name
        parsed_options = {}
        for option in options:
            key, separator, option_value = option.partition("=")
            parsed_options[key] = option_value if separator else True
        if parsed_options:
            proxy["plugin-opts"] = parsed_options
    return proxy


def _url_proxy(value: str, fallback: str) -> dict[str, Any]:
    parsed = urlsplit(value)
    scheme = parsed.scheme.lower()
    query = parse_qs(parsed.query)
    if not parsed.hostname or not parsed.username:
        raise ValueError(f"{scheme} 节点缺少服务器或凭据")
    proxy: dict[str, Any] = {
        "name": _name(parsed, fallback), "type": "hysteria2" if scheme == "hy2" else scheme,
        "server": str(parsed.hostname), "port": _port(parsed), "udp": True,
    }
    credential = unquote(parsed.username)
    if scheme == "vless":
        proxy["uuid"] = credential
        flow = _first(query, "flow")
        if flow:
            proxy["flow"] = flow
        _transport(proxy, query)
        _tls(proxy, query)
    elif scheme == "trojan":
        proxy["password"] = credential
        _transport(proxy, query)
        _tls(proxy, query)
        if "tls" not in proxy:
            proxy["tls"] = True
            proxy["servername"] = _first(query, "sni", "peer", default=str(parsed.hostname))
    else:
        proxy["password"] = credential
        sni = _first(query, "sni", "peer")
        if sni:
            proxy["sni"] = sni
        if _bool(_first(query, "insecure", "allowInsecure")):
            proxy["skip-cert-verify"] = True
        obfs = _first(query, "obfs")
        if obfs:
            proxy["obfs"] = obfs
        obfs_password = _first(query, "obfs-password", "obfsParam")
        if obfs_password:
            proxy["obfs-password"] = obfs_password
    return proxy


def normalize_subscription(raw: bytes, prefix: str) -> list[dict[str, Any]]:
    """Return Mihomo proxy mappings with stable, source-scoped names."""
    text = raw.decode("utf-8-sig", errors="replace").strip()
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError:
        parsed = None
    proxies: list[dict[str, Any]] = []
    if isinstance(parsed, dict) and isinstance(parsed.get("proxies"), list):
        proxies = [deepcopy(item) for item in parsed["proxies"] if isinstance(item, dict)]
    else:
        compact = "".join(text.split())
        try:
            decoded = _b64decode(compact).decode("utf-8", errors="replace")
        except (ValueError, UnicodeError):
            decoded = text
        for index, line in enumerate(decoded.splitlines(), 1):
            value = line.strip()
            scheme = value.split("://", 1)[0].lower() if "://" in value else ""
            if scheme not in SUPPORTED_SCHEMES:
                continue
            fallback = f"{scheme}-{index}"
            try:
                proxy = _vmess(value, fallback) if scheme == "vmess" else _ss(value, fallback) if scheme == "ss" else _url_proxy(value, fallback)
            except ValueError:
                continue
            proxies.append(proxy)
    result = []
    names: set[str] = set()
    for index, proxy in enumerate(proxies, 1):
        reality = proxy.get("reality-opts") if isinstance(proxy.get("reality-opts"), dict) else {}
        short_id = str(reality.get("short-id") or "")
        if short_id and (len(short_id) > 16 or len(short_id) % 2 or not re.fullmatch(r"[0-9a-fA-F]+", short_id)):
            # Mihomo rejects the complete provider when one REALITY short ID is
            # malformed. Skip only that upstream node so the other nodes load.
            continue
        name = str(proxy.get("name") or f"node-{index}").strip()
        scoped = f"{prefix}｜{name}"[:180]
        if scoped in names:
            scoped = f"{scoped[:165]}-{index}"
        names.add(scoped)
        proxy["name"] = scoped
        if proxy.get("server") and proxy.get("type"):
            result.append(proxy)
    if not result:
        raise ValueError("订阅中没有可转换为 Mihomo 的代理节点")
    return result


def provider_yaml(proxies: list[dict[str, Any]]) -> str:
    return yaml.safe_dump({"proxies": proxies}, allow_unicode=True, sort_keys=False, width=4096)


__all__ = ["normalize_subscription", "provider_yaml"]
