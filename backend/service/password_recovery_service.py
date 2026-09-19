"""Password recovery mail delivery and short-lived reset-link signing."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import smtplib
import threading
import time
from email.message import EmailMessage
from urllib.parse import urlencode

from springbootai import get_config

from backend.service.store_service import StoreService


logger = logging.getLogger(__name__)


def _as_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


class PasswordRecoveryService:
    """Keep recovery state stateless while making each link single-use.

    The signed token carries the user's current ``session_version``. A
    successful password change increments that version, so the same token can
    never be replayed and all existing login sessions are invalidated.
    """

    _attempts: dict[str, float] = {}
    _attempt_lock = threading.Lock()

    def __init__(self, store: StoreService):
        self.store = store

    @staticmethod
    def config() -> dict:
        cfg = get_config().get("rose", {}).get("password-recovery", {})
        return cfg if isinstance(cfg, dict) else {}

    def enabled(self) -> bool:
        return _as_bool(self.config().get("enabled", True), True)

    def delivery_hint_seconds(self) -> int:
        return max(1, min(600, int(self.config().get("mail-delivery-hint-seconds", 60) or 60)))

    def _token_secret(self) -> bytes:
        configured = str(self.config().get("token-secret", "")).strip()
        jwt_cfg = get_config().get("jwt", {})
        fallback = str(jwt_cfg.get("secret_key", "")).strip() if isinstance(jwt_cfg, dict) else ""
        secret = configured or fallback
        if len(secret) < 32:
            raise RuntimeError("密码找回签名密钥未配置或长度不足 32 位")
        return secret.encode("utf-8")

    @staticmethod
    def _b64encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    @staticmethod
    def _b64decode(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    def create_token(self, user: dict) -> str:
        minutes = max(5, min(120, int(self.config().get("reset-token-minutes", 15) or 15)))
        now = int(time.time())
        payload = {
            "purpose": "password-reset",
            "uid": int(user["id"]),
            "sv": int(user.get("session_version") or 0),
            "email": hashlib.sha256(str(user.get("email") or "").strip().lower().encode("utf-8")).hexdigest(),
            "iat": now,
            "exp": now + minutes * 60,
        }
        encoded = self._b64encode(json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8"))
        signature = hmac.new(self._token_secret(), encoded.encode("ascii"), hashlib.sha256).digest()
        return f"{encoded}.{self._b64encode(signature)}"

    def verify_token(self, token: str) -> dict:
        try:
            encoded, supplied = str(token or "").split(".", 1)
            expected = hmac.new(self._token_secret(), encoded.encode("ascii"), hashlib.sha256).digest()
            if not hmac.compare_digest(expected, self._b64decode(supplied)):
                raise ValueError
            payload = json.loads(self._b64decode(encoded).decode("utf-8"))
            if payload.get("purpose") != "password-reset" or int(payload.get("exp") or 0) < int(time.time()):
                raise ValueError
            user = self.store.find_user(int(payload.get("uid") or 0))
            email_hash = hashlib.sha256(str((user or {}).get("email") or "").strip().lower().encode("utf-8")).hexdigest()
            if not user or not user.get("enabled") or int(user.get("session_version") or 0) != int(payload.get("sv") or 0) or not hmac.compare_digest(email_hash, str(payload.get("email") or "")):
                raise ValueError
            return user
        except (KeyError, TypeError, ValueError, binascii.Error, json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("重置链接无效、已使用或已过期") from None

    @classmethod
    def _rate_limit(cls, key: str, seconds: int) -> None:
        now = time.monotonic()
        with cls._attempt_lock:
            last = cls._attempts.get(key, 0.0)
            if now - last < seconds:
                wait = max(1, int(seconds - (now - last)))
                raise ValueError(f"请求过于频繁，请 {wait} 秒后再试")
            cls._attempts[key] = now
            if len(cls._attempts) > 4096:
                cutoff = now - max(seconds, 3600)
                cls._attempts = {item: stamp for item, stamp in cls._attempts.items() if stamp >= cutoff}

    @staticmethod
    def mask_email(email: str) -> str:
        local, _, domain = str(email).partition("@")
        if not domain:
            return ""
        visible = local[:2] if len(local) > 2 else local[:1]
        return f"{visible}{'*' * max(3, len(local) - len(visible))}@{domain}"

    def request_reset(self, username: str) -> dict:
        if not self.enabled():
            raise PermissionError("密码找回功能当前未开启")
        user = self.store.find_by_username(username)
        email = str((user or {}).get("email") or "").strip()
        if not user or not user.get("enabled") or not email:
            return {"channel": "contact_admin", "message": "该账号未填写邮箱，请联系管理员找回密码。"}
        cooldown = max(30, min(3600, int(self.config().get("request-cooldown-seconds", 60) or 60)))
        self._rate_limit(f"reset:{int(user['id'])}", cooldown)
        token = self.create_token(user)
        frontend_url = str(self.config().get("frontend-base-url") or "http://www.yucg.cn:8240").rstrip("/")
        link = f"{frontend_url}/reset-password?{urlencode({'token': token})}"
        minutes = max(5, min(120, int(self.config().get("reset-token-minutes", 15) or 15)))
        self._send_mail(
            email,
            "重置登录密码",
            f"你好，{user['username']}：\n\n请在 {minutes} 分钟内打开下面的链接并设置新密码：\n{link}\n\n如果不是你本人操作，请忽略本邮件。该链接使用一次后立即失效。",
        )
        return {
            "channel": "email",
            "masked_email": self.mask_email(email),
            "delivery_hint_seconds": self.delivery_hint_seconds(),
            "message": "找回邮件已发送，通常会在 1 分钟左右到达，请耐心等待并检查垃圾邮件。",
        }

    def contact_admin(self, username: str, name: str, organization: str = "") -> dict:
        if not self.enabled():
            raise PermissionError("密码找回功能当前未开启")
        if not name.strip():
            raise ValueError("请填写姓名")
        cooldown = max(30, min(3600, int(self.config().get("request-cooldown-seconds", 60) or 60)))
        self._rate_limit(f"contact:{username.strip().lower()}", cooldown)
        cfg = self.config()
        recipient = str(cfg.get("admin-email") or "").strip()
        if not recipient:
            raise RuntimeError("管理员接收邮箱未配置")
        user = self.store.find_by_username(username)
        account_state = "账号存在但未绑定邮箱" if user and not str(user.get("email") or "").strip() else "请管理员核实账号"
        self._send_mail(
            recipient,
            f"用户申请找回密码：{username}",
            "登录页收到密码找回申请。\n\n"
            f"账户名：{username}\n"
            f"姓名：{name.strip()}\n"
            f"公司或学校：{organization.strip() or '未填写'}\n"
            f"系统核验：{account_state}\n\n"
            "请管理员核实身份后，在管理后台为用户处理密码。",
        )
        return {"message": "申请已发送给管理员，请等待管理员核实处理。"}

    def _send_mail(self, recipient: str, subject: str, content: str) -> None:
        cfg = self.config()
        smtp_cfg = cfg.get("smtp", {}) if isinstance(cfg.get("smtp"), dict) else {}
        host = str(smtp_cfg.get("host") or "smtp.qq.com").strip()
        port = int(smtp_cfg.get("port", 465) or 465)
        username = str(smtp_cfg.get("username") or "").strip()
        password = str(smtp_cfg.get("password") or "").strip()
        sender = str(smtp_cfg.get("sender") or username).strip()
        if not username or not password or not sender:
            raise RuntimeError("密码找回 SMTP 尚未完整配置")
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = sender
        message["To"] = recipient
        message.set_content(content)
        try:
            if _as_bool(smtp_cfg.get("ssl", True), True):
                with smtplib.SMTP_SSL(host, port, timeout=20) as server:
                    server.login(username, password)
                    server.send_message(message)
            else:
                with smtplib.SMTP(host, port, timeout=20) as server:
                    server.starttls()
                    server.login(username, password)
                    server.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            logger.warning("密码找回邮件发送失败：%s", type(exc).__name__)
            raise RuntimeError("邮件暂时无法发送，请稍后重试或联系管理员") from None
