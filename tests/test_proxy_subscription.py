import base64
import json

import yaml

from backend.common.proxy_subscription import normalize_subscription, provider_yaml


def encoded(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def test_normalizes_clash_yaml_and_prefixes_duplicate_names():
    raw = b"proxies:\n  - {name: JP, type: ss, server: one.example, port: 443, cipher: aes-128-gcm, password: x}\n  - {name: JP, type: ss, server: two.example, port: 443, cipher: aes-128-gcm, password: y}\n"
    proxies = normalize_subscription(raw, "rose:source")
    assert [item["name"] for item in proxies] == ["rose:source｜JP", "rose:source｜JP-2"]
    assert len(yaml.safe_load(provider_yaml(proxies))["proxies"]) == 2


def test_normalizes_base64_vmess_vless_and_hysteria2():
    vmess = "vmess://" + encoded(json.dumps({
        "v": "2", "ps": "日本 VMess", "add": "vm.example", "port": "443",
        "id": "11111111-1111-1111-1111-111111111111", "aid": "0", "net": "ws",
        "host": "cdn.example", "path": "/ws", "tls": "tls", "sni": "cdn.example",
    }))
    lines = "\n".join([
        vmess,
        "vless://22222222-2222-2222-2222-222222222222@vl.example:443?security=reality&type=grpc&sni=site.example&pbk=public&sid=abcd#日本VLESS",
        "hysteria2://password@hy.example:8443?sni=hy.example&insecure=1#日本HY2",
    ])
    raw = base64.b64encode(lines.encode())
    proxies = normalize_subscription(raw, "rose:test")
    assert [item["type"] for item in proxies] == ["vmess", "vless", "hysteria2"]
    assert proxies[0]["ws-opts"]["headers"]["Host"] == "cdn.example"
    assert proxies[1]["reality-opts"]["public-key"] == "public"
    assert proxies[2]["skip-cert-verify"] is True


def test_normalizes_both_shadowsocks_uri_encodings():
    user = encoded("aes-128-gcm:secret")
    full = encoded("aes-256-gcm:other@two.example:8443")
    raw = base64.b64encode((
        f"ss://{user}@one.example:443#one\nss://{full}#two"
    ).encode())
    proxies = normalize_subscription(raw, "rose:ss")
    assert [(item["cipher"], item["server"]) for item in proxies] == [
        ("aes-128-gcm", "one.example"), ("aes-256-gcm", "two.example")]


def test_skips_invalid_reality_short_id_without_dropping_provider():
    lines = "\n".join([
        "vless://22222222-2222-2222-2222-222222222222@bad.example:443?security=reality&pbk=public&sid=not-hex#bad",
        "vless://33333333-3333-3333-3333-333333333333@good.example:443?security=reality&pbk=public&sid=abcd#good",
    ])
    proxies = normalize_subscription(base64.b64encode(lines.encode()), "rose:test")
    assert [item["name"] for item in proxies] == ["rose:test｜good"]
