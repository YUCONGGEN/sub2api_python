"""Password recovery mail delivery, verification codes and legacy reset links."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import secrets
import socket
import smtplib
import threading
import time
from email.message import EmailMessage
from urllib.parse import urlencode

from springbootai import get_config

from backend.service.store_service import StoreService


logger = logging.getLogger(__name__)


def _direct_ipv4_socket(host: str, port: int, timeout: float | None, source_address=None) -> socket.socket:
    """Open a direct IPv4 socket without consulting any proxy settings."""
    error: OSError | None = None
    for family, socktype, proto, _, address in socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM):
        sock = socket.socket(family, socktype, proto)
        try:
            sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(address)
            return sock
        except OSError as exc:
            error = exc
            sock.close()
    raise error or OSError(f"无法连接 SMTP 主机 {host}:{port}")


class _DirectIPv4SMTP(smtplib.SMTP):
    def __init__(self, host="", port=0, *args, connect_host="", **kwargs):
        self._connect_host = str(connect_host or host)
        super().__init__(host, port, *args, **kwargs)

    def _get_socket(self, host, port, timeout):
        return _direct_ipv4_socket(self._connect_host, port, timeout, self.source_address)


class _DirectIPv4SMTPSSL(smtplib.SMTP_SSL):
    def __init__(self, host="", port=0, *args, connect_host="", **kwargs):
        self._connect_host = str(connect_host or host)
        super().__init__(host, port, *args, **kwargs)

    def _get_socket(self, host, port, timeout):
        raw = _direct_ipv4_socket(self._connect_host, port, timeout, self.source_address)
        return self.context.wrap_socket(raw, server_hostname=self._host)


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
    _codes: dict[int, dict] = {}
    _code_lock = threading.Lock()

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

    def code_minutes(self) -> int:
        return max(5, min(30, int(self.config().get("verification-code-minutes", 5) or 5)))

    def code_max_attempts(self) -> int:
        return max(1, min(10, int(self.config().get("verification-code-max-attempts", 5) or 5)))

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

    def lookup_account(self, username: str) -> dict:
        """Return only the recovery route and a masked address."""
        if not self.enabled():
            raise PermissionError("密码找回功能当前未开启")
        user = self.store.find_by_username(username)
        email = str((user or {}).get("email") or "").strip()
        if not user or not user.get("enabled") or not email:
            return {
                "email_available": False,
                "channel": "contact_admin",
                "message": "账户不存在或未填写邮箱，请填写身份信息联系管理员。",
            }
        return {
            "email_available": True,
            "channel": "email",
            "masked_email": self.mask_email(email),
            "message": "已找到绑定邮箱，请确认后发送验证码。",
        }

    def _code_digest(self, user_id: int, code: str) -> str:
        payload = f"password-recovery-code:{int(user_id)}:{str(code)}".encode("utf-8")
        return hmac.new(self._token_secret(), payload, hashlib.sha256).hexdigest()

    def request_code(self, username: str) -> dict:
        if not self.enabled():
            raise PermissionError("密码找回功能当前未开启")
        user = self.store.find_by_username(username)
        email = str((user or {}).get("email") or "").strip()
        if not user or not user.get("enabled") or not email:
            return {
                "channel": "contact_admin",
                "email_available": False,
                "message": "账户不存在或未填写邮箱，请填写身份信息联系管理员。",
            }
        cooldown = max(30, min(3600, int(self.config().get("request-cooldown-seconds", 60) or 60)))
        user_id = int(user["id"])
        with self._code_lock:
            active = type(self)._codes.get(user_id)
            if active and float(active.get("expires_at") or 0) >= time.time():
                raise ValueError(f"验证码已经发送，{self.code_minutes()} 分钟内无需重复发送")
            type(self)._codes.pop(user_id, None)
        self._rate_limit(f"code:{user_id}", cooldown)
        code = f"{secrets.randbelow(1_000_000):06d}"
        now = time.time()
        with self._code_lock:
            type(self)._codes[user_id] = {
                "digest": self._code_digest(user_id, code),
                "expires_at": now + self.code_minutes() * 60,
                "attempts_left": self.code_max_attempts(),
                "session_version": int(user.get("session_version") or 0),
                "email_digest": hashlib.sha256(email.lower().encode("utf-8")).hexdigest(),
            }
            if len(type(self)._codes) > 4096:
                type(self)._codes = {key: value for key, value in type(self)._codes.items() if float(value.get("expires_at") or 0) >= now}
        try:
            self._send_mail(
                email,
                "登录密码找回验证码",
                f"你好，{user['username']}：\n\n你的密码找回验证码是：{code}\n\n"
                f"验证码在 {self.code_minutes()} 分钟内有效，请勿转发给他人。"
                "如果不是你本人操作，请忽略本邮件。",
            )
        except Exception:
            with self._code_lock:
                type(self)._codes.pop(user_id, None)
            raise
        return {
            "channel": "email_code",
            "email_available": True,
            "masked_email": self.mask_email(email),
            "delivery_hint_seconds": self.delivery_hint_seconds(),
            "expires_in_seconds": self.code_minutes() * 60,
            "message": "验证码邮件已发送，通常会在 1 分钟左右到达，请耐心等待并检查垃圾邮件。",
        }

    def verify_code(self, username: str, code: str) -> dict:
        if not self.enabled():
            raise PermissionError("密码找回功能当前未开启")
        user = self.store.find_by_username(username)
        if not user or not user.get("enabled"):
            raise ValueError("验证码不正确或已过期")
        user_id = int(user["id"])
        now = time.time()
        with self._code_lock:
            challenge = type(self)._codes.get(user_id)
            if not challenge or float(challenge.get("expires_at") or 0) < now:
                type(self)._codes.pop(user_id, None)
                raise ValueError("验证码不正确或已过期")
            email_digest = hashlib.sha256(str(user.get("email") or "").strip().lower().encode("utf-8")).hexdigest()
            valid_context = (
                int(challenge.get("session_version") or 0) == int(user.get("session_version") or 0)
                and hmac.compare_digest(str(challenge.get("email_digest") or ""), email_digest)
            )
            valid_code = hmac.compare_digest(str(challenge.get("digest") or ""), self._code_digest(user_id, code))
            if not valid_context or not valid_code:
                challenge["attempts_left"] = int(challenge.get("attempts_left") or 1) - 1
                if challenge["attempts_left"] <= 0 or not valid_context:
                    type(self)._codes.pop(user_id, None)
                raise ValueError("验证码不正确或已过期")
            type(self)._codes.pop(user_id, None)
        return user

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
        if user and user.get("enabled") and str(user.get("email") or "").strip():
            raise ValueError("该账号已绑定邮箱，请使用邮箱验证码找回密码")
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

    def send_admin_notification(self, subject: str, content: str, recipient: str = "") -> None:
        """Send a server-side notification through the configured SMTP account.

        Subscription quota alerts reuse the same direct QQ SMTP path as
        password recovery.  When no dedicated recipient is configured, send
        to the SMTP sender/username (the mailbox that actually sends the
        message), then fall back to the configured recovery administrator.
        """
        cfg = self.config()
        smtp_cfg = cfg.get("smtp", {}) if isinstance(cfg.get("smtp"), dict) else {}
        candidates = [
            str(recipient or "").strip(),
            str(smtp_cfg.get("sender") or "").strip(),
            str(smtp_cfg.get("username") or "").strip(),
            str(cfg.get("admin-email") or "").strip(),
        ]
        target = next(
            (
                value for value in candidates
                if value and value.lower() not in {"admin@example.com", "example@example.com"}
            ),
            "",
        )
        if not target:
            raise RuntimeError("管理员通知邮箱未配置")
        self._send_mail(target, str(subject).strip()[:200], str(content))

    def _send_mail(self, recipient: str, subject: str, content: str) -> None:
        cfg = self.config()
        smtp_cfg = cfg.get("smtp", {}) if isinstance(cfg.get("smtp"), dict) else {}
        # smtplib opens a direct TCP socket and never consumes HTTP(S) proxy
        # environment variables. ``use-proxy`` is kept in YAML as an explicit,
        # self-documenting guarantee for operators.
        if _as_bool(smtp_cfg.get("use-proxy", False), False):
            raise RuntimeError("密码找回邮件不允许通过代理发送")
        host = str(smtp_cfg.get("host") or "smtp.qq.com").strip()
        connect_host = str(smtp_cfg.get("connect-host") or host).strip()
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
                with _DirectIPv4SMTPSSL(host, port, timeout=20, connect_host=connect_host) as server:
                    server.login(username, password)
                    server.send_message(message)
            else:
                with _DirectIPv4SMTP(host, port, timeout=20, connect_host=connect_host) as server:
                    server.starttls()
                    server.login(username, password)
                    server.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            # Keep the provider's SMTP code/reason for diagnosis, but never
            # log credentials or the verification-code body.
            raw_detail = getattr(exc, "smtp_error", b"")
            if isinstance(raw_detail, bytes):
                detail = raw_detail.decode("utf-8", errors="replace")
            else:
                detail = str(raw_detail or exc)
            detail = " ".join(detail.split())[:240]
            logger.warning(
                "密码找回邮件发送失败：%s code=%s detail=%s",
                type(exc).__name__, getattr(exc, "smtp_code", ""), detail,
            )
            raise RuntimeError("邮件暂时无法发送，请稍后重试或联系管理员") from None
