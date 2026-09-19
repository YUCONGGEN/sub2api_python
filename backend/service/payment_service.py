"""Real WeChat Pay v3 Native and Alipay RSA2 integrations.

The service deliberately uses the official HTTP protocols instead of a vendor
SDK so deployments only need the credentials and certificates configured in
application.yml. Demo mode never contacts a payment provider.
"""

import base64
import json
import mimetypes
import secrets
import smtplib
import time
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.x509 import load_pem_x509_certificate
from springbootai import Autowired, Service, get_config

from backend.service.store_service import StoreService


class PaymentConfigurationError(RuntimeError):
    """Raised when production payment credentials are incomplete."""


class PaymentProviderError(RuntimeError):
    """Raised when a provider rejects an order request."""


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value) if value is not None else default


def _inline_secret(value: Any) -> str:
    return str(value or "").replace("\\n", "\n").strip()


@Service
class PaymentService:
    @Autowired
    def __init__(self, store: StoreService):
        self.store = store

    @staticmethod
    def config() -> dict[str, Any]:
        return get_config().get("rose", {}).get("payment", {})

    @classmethod
    def public_payment_methods(cls) -> list[dict[str, str]]:
        """Return payment channels explicitly exposed to normal users."""
        cfg = cls.config()
        definitions = (
            ("WECHAT_PERSONAL", "personal-wechat", "微信支付"),
            ("WECHAT", "wechat", "微信商户支付"),
            ("ALIPAY", "alipay", "支付宝"),
        )
        methods: list[dict[str, str]] = []
        for provider, key, label in definitions:
            item = cfg.get(key, {}) if isinstance(cfg.get(key), dict) else {}
            if _as_bool(item.get("enabled", False)) and _as_bool(item.get("visible-to-users", False)):
                methods.append({"provider": provider, "label": label})
        return methods

    @classmethod
    def payment_method_visible(cls, provider: str) -> bool:
        value = str(provider or "").upper()
        return any(item["provider"] == value for item in cls.public_payment_methods())

    @staticmethod
    def application_name() -> str:
        cfg = get_config().get("rose", {}).get("application", {})
        value = cfg.get("name") if isinstance(cfg, dict) else None
        return str(value or "大模型接口管理").strip() or "大模型接口管理"

    @classmethod
    def demo_mode(cls) -> bool:
        return _as_bool(cls.config().get("demo-mode"), True)

    @classmethod
    def notify_url(cls, provider: str) -> str:
        base = str(cls.config().get("notify-base-url", "http://localhost:8241")).rstrip("/")
        return f"{base}/api/payment/{provider.lower()}/notify"

    @staticmethod
    def _read_secret(section: dict[str, Any], inline_name: str, path_name: str) -> str:
        inline = _inline_secret(section.get(inline_name))
        if inline:
            return inline
        path = str(section.get(path_name) or "").strip()
        if path:
            try:
                return Path(path).expanduser().read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise PaymentConfigurationError(f"无法读取支付证书文件: {path}") from exc
        return ""

    @classmethod
    def _private_key(cls, section: dict[str, Any], inline_name: str = "private-key", path_name: str = "private-key-path"):
        value = cls._read_secret(section, inline_name, path_name)
        if not value:
            raise PaymentConfigurationError(f"缺少 {inline_name} 或 {path_name}")
        try:
            return serialization.load_pem_private_key(value.encode("utf-8"), password=None)
        except (ValueError, TypeError) as exc:
            raise PaymentConfigurationError("支付私钥不是有效的 PEM RSA 私钥") from exc

    @classmethod
    def create_checkout(cls, order: dict[str, Any]) -> dict[str, Any]:
        provider = str(order["provider"]).upper()
        if provider == "WECHAT_PERSONAL":
            return cls._personal_wechat_checkout(order)
        if cls.demo_mode():
            return {"qr_code": order.get("qr_code"), "mode": "demo", "provider": provider}
        if provider == "WECHAT":
            return cls._wechat_native(order)
        if provider == "ALIPAY":
            return cls._alipay_precreate(order)
        raise PaymentConfigurationError(f"不支持的支付渠道: {provider}")

    @classmethod
    def personal_wechat_config(cls) -> dict[str, Any]:
        return cls.config().get("personal-wechat", {})

    @classmethod
    def personal_listener_stale_seconds(cls) -> int:
        try:
            return max(3, int(cls.personal_wechat_config().get("listener-stale-seconds", 15)))
        except (TypeError, ValueError):
            return 15

    def personal_listener_status(self) -> dict[str, Any]:
        status = self.store.listener_status(self.personal_listener_stale_seconds())
        missing_heartbeat = status.get("updated_at") is None
        # A fresh database has no heartbeat row until the standalone listener
        # sends its first callback. Persist the unavailable state before
        # sending the alert so polling cannot trigger the same email forever.
        if missing_heartbeat:
            self.store.update_listener_status(False, status.get("reason") or "微信监听器尚未上报状态")
            status = self.store.listener_status(self.personal_listener_stale_seconds())
        # A stopped listener cannot send its final heartbeat. Convert a stale
        # heartbeat into the same one-shot alert used by explicit failure
        # heartbeats, so the operator is notified even after a crash/exit.
        if not status.get("available") and (missing_heartbeat or status.get("stale")) and not status.get("last_alert_at"):
            self.store.mark_listener_alert()
            self._send_listener_alert(status.get("reason") or "微信收款窗口检测失效")
            status = self.store.listener_status(self.personal_listener_stale_seconds())
        return status

    def record_personal_listener_status(self, available: bool, reason: str = "") -> dict[str, Any]:
        previous = self.store.update_listener_status(available, reason)
        if not available and (not previous or not bool(previous.get("available"))):
            last_alert = str(previous.get("last_alert_at") or "") if previous else ""
            if not last_alert:
                self._send_listener_alert(reason or "微信收款窗口检测失效")
                self.store.mark_listener_alert()
        elif available and previous and not bool(previous.get("available")):
            self.store.clear_listener_alert()
        return self.personal_listener_status()

    @classmethod
    def _send_listener_alert(cls, reason: str) -> None:
        cfg = cls.personal_wechat_config()
        alert = cfg.get("alert-email", {}) if isinstance(cfg.get("alert-email"), dict) else {}
        recipient = str(alert.get("recipient", "1516933915@qq.com")).strip() or "1516933915@qq.com"
        smtp_cfg = alert.get("smtp", {}) if isinstance(alert.get("smtp"), dict) else {}
        host = str(smtp_cfg.get("host", "smtp.qq.com")).strip()
        username = str(smtp_cfg.get("username", "")).strip()
        password = str(smtp_cfg.get("password", "")).strip()
        if not username or not password:
            import logging
            logging.getLogger(__name__).warning("微信收款窗口失效，未发送邮件：请配置 personal-wechat.alert-email.smtp.username/password")
            return
        message = EmailMessage()
        message["Subject"] = "微信支付窗口检测失效"
        message["From"] = str(smtp_cfg.get("sender", username)).strip() or username
        message["To"] = recipient
        message.set_content(f"微信支付窗口检测失效。\n原因：{reason}\n请登录微信并打开微信收款助手窗口。")
        port = int(smtp_cfg.get("port", 465))
        try:
            if _as_bool(smtp_cfg.get("ssl", True), True):
                with smtplib.SMTP_SSL(host, port, timeout=15) as server:
                    server.login(username, password)
                    server.send_message(message)
            else:
                with smtplib.SMTP(host, port, timeout=15) as server:
                    server.starttls()
                    server.login(username, password)
                    server.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            import logging
            logging.getLogger(__name__).warning("微信支付窗口失效邮件发送失败：%s", exc)

    @classmethod
    def personal_wechat_assets(cls) -> list[tuple[float, str]]:
        """Return configured QR assets as (amount, relative filename)."""
        cfg = cls.personal_wechat_config()
        roots = [cfg.get("asset-dir", "./wexin_pay"), cfg.get("fallback-asset-dir", "./weixin_pay")]
        result: list[tuple[float, str]] = []
        seen: set[str] = set()
        for raw_root in roots:
            root = Path(str(raw_root)).expanduser()
            if not root.is_absolute():
                root = Path.cwd() / root
            if not root.is_dir():
                continue
            for path in sorted(root.iterdir()):
                if not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                    continue
                try:
                    value = round(float(path.stem), 2)
                except ValueError:
                    continue
                key = path.name.lower()
                if key in seen:
                    continue
                seen.add(key)
                result.append((value, path.name))
        return sorted(result, key=lambda item: item[0])

    @classmethod
    def personal_wechat_candidates(cls, amount: float) -> list[tuple[float, str]]:
        cfg = cls.personal_wechat_config()
        if not _as_bool(cfg.get("enabled"), True):
            return []
        target = round(float(amount), 2)
        if abs(target - round(target)) > 0.0001:
            return []
        tail_min = float(cfg.get("tail-min", 0.01))
        tail_max = float(cfg.get("tail-max", 0.05))
        candidates = [(value, filename) for value, filename in cls.personal_wechat_assets()
                      if target - tail_max - 0.0001 <= value <= target - tail_min + 0.0001]
        return candidates

    @classmethod
    def _personal_wechat_checkout(cls, order: dict[str, Any]) -> dict[str, Any]:
        asset = str(order.get("qr_asset") or "").strip()
        if not asset:
            raise PaymentConfigurationError("个人微信收款码不存在，请检查 wexin_pay 目录和金额配置")
        path = next((root / asset for root in cls._personal_wechat_roots() if (root / asset).is_file()), None)
        if path is None:
            raise PaymentConfigurationError(f"个人微信收款码文件不存在: {path}")
        # The frontend receives a same-origin static URL, never a filesystem path.
        public_path = "/api/payment/personal-wechat/qr/" + quote(path.name)
        return {
            "qr_image": public_path,
            "mode": "personal-callback",
            "provider": "WECHAT_PERSONAL",
            "payment_amount": f"{float(order.get('payment_amount', order['amount'])):.2f}",
            "contact": {
                "qq": str(cls.personal_wechat_config().get("contact-qq", "1516933915")),
                "wechat": str(cls.personal_wechat_config().get("contact-wechat", "17739798184")),
            },
        }

    @classmethod
    def _personal_wechat_roots(cls) -> list[Path]:
        cfg = cls.personal_wechat_config()
        roots = []
        for raw in (cfg.get("asset-dir", "./wexin_pay"), cfg.get("fallback-asset-dir", "./weixin_pay")):
            root = Path(str(raw)).expanduser()
            if not root.is_absolute():
                root = Path.cwd() / root
            roots.append(root.resolve())
        return roots

    @classmethod
    def _wechat_native(cls, order: dict[str, Any]) -> dict[str, Any]:
        cfg = cls.config().get("wechat", {})
        if not _as_bool(cfg.get("enabled"), False):
            raise PaymentConfigurationError("微信支付未启用，请设置 rose.payment.wechat.enabled=true")
        required = {"app-id": cfg.get("app-id"), "mch-id": cfg.get("mch-id"), "api-v3-key": cfg.get("api-v3-key"), "cert-serial-no": cfg.get("cert-serial-no")}
        missing = [key for key, value in required.items() if not str(value or "").strip()]
        if missing:
            raise PaymentConfigurationError("微信支付缺少配置: " + ", ".join(missing))
        api_v3_key = _inline_secret(cfg.get("api-v3-key"))
        if len(api_v3_key.encode("utf-8")) != 32:
            raise PaymentConfigurationError("微信 api-v3-key 必须是 32 字节")
        private_key = cls._private_key(cfg)
        body = {
            "appid": str(cfg["app-id"]),
            "mchid": str(cfg["mch-id"]),
            "description": cls.application_name() + "余额充值",
            "out_trade_no": order["trade_no"],
            "notify_url": cls.notify_url("wechat"),
            "amount": {"total": int(round(float(order["amount"]) * 100)), "currency": "CNY"},
        }
        body_text = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        timestamp = str(int(time.time()))
        nonce = secrets.token_urlsafe(24)
        message = f"{timestamp}\n{nonce}\n{body_text}\n".encode("utf-8")
        signature = base64.b64encode(private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())).decode("ascii")
        authorization = (
            "WECHATPAY2-SHA256-RSA2048 "
            f'mchid="{cfg["mch-id"]}",nonce_str="{nonce}",timestamp="{timestamp}",'
            f'serial_no="{cfg["cert-serial-no"]}",signature="{signature}"'
        )
        url = str(cfg.get("gateway", "https://api.mch.weixin.qq.com")).rstrip("/") + "/v3/pay/transactions/native"
        try:
            response = requests.post(url, data=body_text.encode("utf-8"), headers={"Authorization": authorization, "Accept": "application/json", "Content-Type": "application/json"}, timeout=20)
            response.raise_for_status()
            result = response.json()
        except requests.RequestException as exc:
            detail = getattr(getattr(exc, "response", None), "text", "")[:500]
            raise PaymentProviderError(f"微信下单失败: {detail or exc}") from exc
        if not result.get("code_url"):
            raise PaymentProviderError(f"微信下单响应缺少 code_url: {result}")
        return {"qr_code": result["code_url"], "mode": "live", "provider": "WECHAT"}

    @classmethod
    def _alipay_precreate(cls, order: dict[str, Any]) -> dict[str, Any]:
        cfg = cls.config().get("alipay", {})
        if not _as_bool(cfg.get("enabled"), False):
            raise PaymentConfigurationError("支付宝未启用，请设置 rose.payment.alipay.enabled=true")
        app_id = str(cfg.get("app-id") or "").strip()
        if not app_id:
            raise PaymentConfigurationError("支付宝缺少 app-id")
        private_key = cls._private_key(cfg)
        biz_content = {
            "out_trade_no": order["trade_no"],
            "total_amount": f"{float(order['amount']):.2f}",
            "subject": cls.application_name() + "余额充值",
            "product_code": "FACE_TO_FACE_PAYMENT",
        }
        params = {
            "app_id": app_id,
            "method": "alipay.trade.precreate",
            "format": "JSON",
            "charset": "utf-8",
            "sign_type": "RSA2",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "version": "1.0",
            "notify_url": cls.notify_url("alipay"),
            "biz_content": json.dumps(biz_content, ensure_ascii=False, separators=(",", ":")),
        }
        params["sign"] = cls._alipay_sign(params, private_key)
        try:
            response = requests.post(str(cfg.get("gateway", "https://openapi.alipay.com/gateway.do")), data=params, timeout=20)
            response.raise_for_status()
            result = response.json().get("alipay_trade_precreate_response", {})
        except (requests.RequestException, ValueError) as exc:
            detail = getattr(getattr(exc, "response", None), "text", "")[:500]
            raise PaymentProviderError(f"支付宝下单失败: {detail or exc}") from exc
        if result.get("code") != "10000" or not result.get("qr_code"):
            raise PaymentProviderError(f"支付宝下单失败: {result}")
        return {"qr_code": result["qr_code"], "mode": "live", "provider": "ALIPAY"}

    @staticmethod
    def _alipay_sign(params: dict[str, Any], private_key) -> str:
        content = "&".join(f"{key}={params[key]}" for key in sorted(params) if key not in {"sign", "sign_type"} and params[key] is not None and params[key] != "")
        signature = private_key.sign(content.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
        return base64.b64encode(signature).decode("ascii")

    @classmethod
    def verify_alipay_notify(cls, params: dict[str, str]) -> bool:
        cfg = cls.config().get("alipay", {})
        public_value = cls._read_secret(cfg, "public-key", "public-key-path")
        if not public_value:
            raise PaymentConfigurationError("支付宝缺少 public-key")
        if "BEGIN PUBLIC KEY" not in public_value:
            public_value = "-----BEGIN PUBLIC KEY-----\n" + public_value + "\n-----END PUBLIC KEY-----"
        public_key = serialization.load_pem_public_key(public_value.encode("utf-8"))
        sign = params.get("sign")
        if not sign:
            return False
        content = "&".join(f"{key}={params[key]}" for key in sorted(params) if key not in {"sign", "sign_type"} and params[key] is not None and params[key] != "")
        try:
            public_key.verify(base64.b64decode(sign), content.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
            return True
        except (ValueError, TypeError):
            return False

    @classmethod
    def _wechat_platform_key(cls):
        path = str(cls.config().get("wechat", {}).get("platform-cert-path") or "").strip()
        if not path:
            raise PaymentConfigurationError("微信回调验签需要配置 platform-cert-path")
        try:
            certificate = load_pem_x509_certificate(Path(path).expanduser().read_bytes())
            return certificate.public_key()
        except OSError as exc:
            raise PaymentConfigurationError(f"无法读取微信平台证书: {path}") from exc

    @classmethod
    def verify_wechat_notify(cls, headers: dict[str, str], body: bytes) -> bool:
        signature = headers.get("Wechatpay-Signature") or headers.get("wechatpay-signature")
        timestamp = headers.get("Wechatpay-Timestamp") or headers.get("wechatpay-timestamp")
        nonce = headers.get("Wechatpay-Nonce") or headers.get("wechatpay-nonce")
        serial = headers.get("Wechatpay-Serial") or headers.get("wechatpay-serial")
        configured_serial = str(cls.config().get("wechat", {}).get("platform-cert-serial-no") or "").strip()
        if configured_serial and serial != configured_serial:
            return False
        if not signature or not timestamp or not nonce:
            return False
        message = f"{timestamp}\n{nonce}\n{body.decode('utf-8')}\n".encode("utf-8")
        try:
            cls._wechat_platform_key().verify(base64.b64decode(signature), message, padding.PKCS1v15(), hashes.SHA256())
            return True
        except (ValueError, TypeError):
            return False

    @classmethod
    def decrypt_wechat_resource(cls, resource: dict[str, Any]) -> dict[str, Any]:
        key = _inline_secret(cls.config().get("wechat", {}).get("api-v3-key"))
        if len(key.encode("utf-8")) != 32:
            raise PaymentConfigurationError("微信 api-v3-key 必须是 32 字节")
        try:
            encrypted = base64.b64decode(resource["ciphertext"])
            plaintext = AESGCM(key.encode("utf-8")).decrypt(resource["nonce"].encode("utf-8"), encrypted, resource.get("associated_data", "").encode("utf-8"))
            return json.loads(plaintext.decode("utf-8"))
        except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise PaymentProviderError("微信回调资源解密失败") from exc

