"""Encryption-at-rest for upstream OAuth credentials."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from springbootai import PostConstruct, Service, get_config


@Service("credential_cipher_service")
class CredentialCipherService:
    """Encrypt credential JSON with a deployment-specific application secret."""

    @PostConstruct
    def init(self) -> None:
        config = get_config()
        gateway = config.get("rose", {}).get("subscription-gateway", {})
        secret = str(gateway.get("credential-secret") or config.get("jwt", {}).get("secret_key") or "").strip()
        if len(secret) < 32:
            raise RuntimeError("rose.subscription-gateway.credential-secret 至少需要 32 个字符")
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
        self._fernet = Fernet(key)

    def encrypt(self, value: dict[str, Any]) -> str:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return "v1:" + self._fernet.encrypt(payload).decode("ascii")

    def decrypt(self, value: str) -> dict[str, Any]:
        raw = str(value or "")
        if not raw.startswith("v1:"):
            raise ValueError("不支持的凭据密文版本")
        try:
            decoded = self._fernet.decrypt(raw[3:].encode("ascii"))
            result = json.loads(decoded.decode("utf-8"))
        except (InvalidToken, UnicodeError, ValueError, TypeError) as exc:
            raise ValueError("上游凭据无法解密，请检查 ROSE_SUBSCRIPTION_CREDENTIAL_SECRET") from exc
        if not isinstance(result, dict):
            raise ValueError("上游凭据格式不正确")
        return result


__all__ = ["CredentialCipherService"]
