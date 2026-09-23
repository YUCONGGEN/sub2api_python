"""SpringBootAI business service for subscription account lifecycle and scheduling."""

import asyncio
import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from springbootai import Autowired, PostConstruct, Scheduled, Service, Slf4j, Transactional, get_config

from backend.common.subscription_providers import DEFAULT_MODELS, OAUTH_PROVIDERS, SUBSCRIPTION_PROVIDERS, provider_label
from backend.repository.subscription_repository import SubscriptionRepository
from backend.service.credential_cipher_service import CredentialCipherService
from backend.service.subscription_account_pool_service import SubscriptionAccountPoolService
from backend.service.subscription_oauth_service import SubscriptionOAuthService


NO_COOLDOWN_ERROR_MARKERS = (
    "currently overloaded",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


@Service("subscription_account_service")
@Slf4j
class SubscriptionAccountService:
    # The automatic guard performs one recovery check after the upstream
    # quota window has reset.  Keep the old cadence constants as compatibility
    # metadata for callers that may still import them; the gateway no longer
    # uses that staged 3h/2h/1h schedule.
    LOW_QUOTA_RECOVERY_MAX_CHECKS = 1
    LOW_QUOTA_RECOVERY_MAX_ATTEMPTS = 44
    LOW_QUOTA_RECOVERY_INTERVALS = (
        (8, 3 * 60 * 60),
        (20, 2 * 60 * 60),
        (44, 1 * 60 * 60),
    )

    @Autowired
    def __init__(
        self,
        repository: SubscriptionRepository,
        cipher: CredentialCipherService,
        oauth: SubscriptionOAuthService,
        pool: SubscriptionAccountPoolService,
    ):
        self.repository = repository
        self.cipher = cipher
        self.oauth = oauth
        self.pool = pool
        self._refresh_locks: dict[int, asyncio.Lock] = {}
        # Keep the historical behavior for directly constructed instances
        # (including tests); application configuration is applied at startup.
        self.cooldown_enabled = True
        self.user_contributions_enabled = True
        self.quota_visible_to_users = True
        self.auto_token_refresh_enabled = True
        self.token_refresh_ahead_seconds = 86400

    @PostConstruct
    def init(self) -> None:
        cfg = get_config().get("rose", {}).get("subscription-gateway", {})
        self.cooldown_enabled = as_bool(cfg.get("account-cooldown-enabled"), True)
        self.user_contributions_enabled = as_bool(cfg.get("user-contributions-enabled"), True)
        self.quota_visible_to_users = as_bool(cfg.get("quota-visible-to-users"), True)
        self.auto_token_refresh_enabled = as_bool(cfg.get("token-refresh-enabled"), True)
        self.token_refresh_ahead_seconds = max(
            300,
            min(604800, int(cfg.get("token-refresh-ahead-seconds", 86400) or 86400)),
        )
        self.logger.info("订阅账号临时冷却 enabled=%s", self.cooldown_enabled)

    def _token_refresh_due(
        self,
        row: dict[str, Any],
        credentials: dict[str, Any],
        now: datetime | None = None,
    ) -> bool:
        expires = parse_time(credentials.get("expires_at") or row.get("expires_at"))
        if not expires:
            return False
        refresh_at = (now or datetime.now(timezone.utc)) + timedelta(
            seconds=int(getattr(self, "token_refresh_ahead_seconds", 86400) or 86400),
        )
        return expires <= refresh_at

    @Scheduled(fixed_rate=3600000, initial_delay=60000)
    async def refresh_expiring_tokens(self) -> None:
        """Refresh active OAuth tokens before their final 24-hour window."""
        if not getattr(self, "auto_token_refresh_enabled", True):
            return
        now = datetime.now(timezone.utc)
        refreshed = 0
        for provider in OAUTH_PROVIDERS:
            for row in self.repository.list_provider(provider):
                try:
                    credentials = self._credentials(row)
                    if (
                        not str(credentials.get("refresh_token") or "").strip()
                        or not self._token_refresh_due(row, credentials, now)
                    ):
                        continue
                    # The scheduler has already applied the configurable
                    # one-day threshold. Force one refresh here; ordinary
                    # request-time acquisition keeps its short five-minute
                    # fallback so it cannot refresh on every API request.
                    await self.refresh_account(int(row["id"]), force=True)
                    refreshed += 1
                except Exception as exc:
                    self.logger.warning(
                        "订阅账号令牌提前刷新失败 account_id=%s provider=%s error=%s",
                        row.get("id"), provider, str(exc)[:300],
                    )
        if refreshed:
            self.logger.info("订阅账号令牌提前刷新完成 count=%s", refreshed)

    def _is_cooling(self, row: dict[str, Any], now: datetime | None = None) -> bool:
        if not getattr(self, "cooldown_enabled", True):
            return False
        until = parse_time(row.get("cooldown_until"))
        return bool(until and until > (now or datetime.now(timezone.utc)))

    @staticmethod
    def normalize_provider(value: Any) -> str:
        provider = str(value or "").strip().lower()
        if provider not in SUBSCRIPTION_PROVIDERS:
            raise ValueError("provider 不受支持")
        return provider

    @staticmethod
    def normalize_models(provider: str, value: Any) -> list[str]:
        if isinstance(value, str):
            raw = value.replace("\n", ",").split(",")
        elif isinstance(value, list):
            raw = value
        else:
            raw = []
        result: list[str] = []
        seen: set[str] = set()
        for item in raw:
            model = str(item or "").strip()
            if not model or len(model) > 160 or model in seen:
                continue
            seen.add(model)
            result.append(model)
        return result or list(DEFAULT_MODELS[provider])

    @classmethod
    def normalize_model_pricing(
        cls,
        models: list[str],
        value: Any,
        *,
        default_input: float = 0,
        default_output: float = 0,
        default_multiplier: float = 1,
    ) -> dict[str, dict[str, float]]:
        if value in (None, ""):
            return {}
        if not isinstance(value, dict):
            raise ValueError("模型定价必须是对象")
        allowed = {str(model) for model in models if str(model) != "*"}
        result: dict[str, dict[str, float]] = {}
        for raw_model, raw_pricing in value.items():
            model = str(raw_model or "").strip()
            if not model or len(model) > 160 or model not in allowed:
                raise ValueError(f"模型定价包含未配置的模型：{model or '空模型'}")
            if not isinstance(raw_pricing, dict):
                raise ValueError(f"模型 {model} 的定价必须是对象")
            result[model] = {
                "input_price_cny": cls._number(raw_pricing.get("input_price_cny"), f"{model} 输入价格", 0, 1_000_000, default_input),
                "output_price_cny": cls._number(raw_pricing.get("output_price_cny"), f"{model} 输出价格", 0, 1_000_000, default_output),
                "price_multiplier": cls._number(raw_pricing.get("price_multiplier"), f"{model} 价格倍率", 0, 1000, default_multiplier),
            }
        return result

    @staticmethod
    def _model_pricing_map(row: dict[str, Any]) -> dict[str, dict[str, Any]]:
        value = row.get("model_pricing")
        if isinstance(value, dict):
            return value
        try:
            parsed = json.loads(str(row.get("model_pricing_json") or "{}"))
        except (TypeError, ValueError):
            parsed = {}
        return parsed if isinstance(parsed, dict) else {}

    @classmethod
    def pricing_for_model(cls, row: dict[str, Any], model_id: str | None = None) -> dict[str, float]:
        pricing = cls._model_pricing_map(row).get(str(model_id or ""), {})
        if not isinstance(pricing, dict):
            pricing = {}
        base_multiplier = row.get("price_multiplier")
        if base_multiplier in (None, ""):
            base_multiplier = 1
        return {
            "input_price_cny": max(0.0, float(pricing.get("input_price_cny", row.get("input_price_cny") or 0) or 0)),
            "output_price_cny": max(0.0, float(pricing.get("output_price_cny", row.get("output_price_cny") or 0) or 0)),
            "price_multiplier": max(0.0, float(pricing.get("price_multiplier", base_multiplier))),
        }

    @staticmethod
    def _number(value: Any, name: str, minimum: float, maximum: float, default: float) -> float:
        if value in (None, ""):
            return default
        try:
            result = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} 必须是数字") from exc
        if not math.isfinite(result) or result < minimum or result > maximum:
            raise ValueError(f"{name} 必须在 {minimum} 到 {maximum} 之间")
        return result

    def _credentials(self, row: dict[str, Any]) -> dict[str, Any]:
        return self.cipher.decrypt(str(row.get("credentials_encrypted") or ""))

    @staticmethod
    def _credential_value(source: dict[str, Any], *paths: tuple[str, ...]) -> str:
        for path in paths:
            current: Any = source
            for key in path:
                if not isinstance(current, dict):
                    current = None
                    break
                current = current.get(key)
            value = str(current or "").strip()
            if value:
                return value
        return ""

    @classmethod
    def normalize_imported_credentials(
        cls,
        provider: str,
        body: dict[str, Any],
        supplied: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Accept either a raw access token or a Codex auth.json object."""
        credentials = dict(supplied or {})
        raw_access = str(credentials.get("access_token") or body.get("access_token") or "").strip()
        if raw_access.startswith(("{", "[")):
            try:
                imported = json.loads(raw_access)
            except ValueError as exc:
                raise ValueError("auth.json 不是有效 JSON") from exc
            if not isinstance(imported, dict):
                raise ValueError("auth.json 顶层必须是 JSON 对象")
            access_token = cls._credential_value(
                imported,
                ("tokens", "access_token"), ("tokens", "accessToken"),
                ("access_token",), ("accessToken",), ("token",),
            )
            if not access_token:
                raise ValueError("auth.json 缺少 tokens.access_token")
            credentials["access_token"] = access_token
            imported_fields = {
                "refresh_token": cls._credential_value(
                    imported, ("tokens", "refresh_token"), ("tokens", "refreshToken"),
                    ("refresh_token",), ("refreshToken",),
                ),
                "id_token": cls._credential_value(
                    imported, ("tokens", "id_token"), ("tokens", "idToken"),
                    ("id_token",), ("idToken",),
                ),
                "account_id": cls._credential_value(
                    imported, ("chatgpt_account_id",), ("chatgptAccountId",),
                    ("account_id",), ("accountId",), ("account", "id"),
                    ("account", "account_id"), ("account", "chatgpt_account_id"),
                ),
                "email": cls._credential_value(imported, ("email",), ("user", "email")),
                "expires_at": cls._credential_value(
                    imported, ("tokens", "expires_at"), ("tokens", "expiresAt"),
                    ("expires_at",), ("expiresAt",),
                ),
            }
            for key, value in imported_fields.items():
                if value and not str(credentials.get(key) or "").strip():
                    credentials[key] = value
        elif raw_access:
            credentials["access_token"] = raw_access

        # Explicit companion fields take precedence over auth.json fields.
        for key in ("refresh_token", "id_token", "account_id", "email", "expires_at"):
            value = str(body.get(key) or "").strip()
            if value:
                credentials[key] = value

        access_token = str(credentials.get("access_token") or "").strip()
        if provider == "openai" and len(access_token.split(".")) == 5:
            raise ValueError(
                "检测到浏览器会话 JWE，不能作为 Codex OAuth access_token；"
                "请粘贴 Codex auth.json（系统会读取 tokens.access_token），或使用 OAuth 授权"
            )
        return credentials

    def _public(self, row: dict[str, Any]) -> dict[str, Any]:
        data = dict(row)
        encrypted = data.pop("credentials_encrypted", "")
        try:
            credentials = self.cipher.decrypt(str(encrypted or ""))
            data["has_access_token"] = bool(str(credentials.get("access_token") or "").strip())
            data["has_refresh_token"] = bool(str(credentials.get("refresh_token") or "").strip())
            data["plan_type"] = str(credentials.get("plan_type") or "")
        except ValueError:
            data["has_access_token"] = False
            data["has_refresh_token"] = False
            data["credential_error"] = True
        try:
            models = json.loads(str(data.pop("models_json", "[]") or "[]"))
        except ValueError:
            models = []
        data["models"] = models if isinstance(models, list) else []
        try:
            model_pricing = json.loads(str(data.pop("model_pricing_json", "{}") or "{}"))
        except ValueError:
            model_pricing = {}
        data["model_pricing"] = model_pricing if isinstance(model_pricing, dict) else {}
        data["enabled"] = bool(data.get("enabled"))
        if not getattr(self, "cooldown_enabled", True) and data.get("status") == "COOLDOWN":
            data["status"] = "READY" if data["enabled"] else "DISABLED"
            data["cooldown_until"] = None
        data["credential_mask"] = "••••••••" if data["has_access_token"] else "未配置"
        return data

    def list_page(self, provider: str = "", page: int = 1, page_size: int = 20) -> dict[str, Any]:
        provider = self.normalize_provider(provider) if str(provider or "").strip() else ""
        result = self.repository.list_page(provider, page, page_size)
        result["items"] = [self._public(row) for row in result["items"]]
        return result

    def find_public(self, account_id: int) -> dict[str, Any] | None:
        row = self.repository.find(account_id)
        return self._public(row) if row else None

    @staticmethod
    def _is_admin(user: dict[str, Any] | None) -> bool:
        return bool(user and str(user.get("role") or "").upper() == "ADMIN")

    def contributions_available(self, user: dict[str, Any] | None) -> bool:
        return self._is_admin(user) or bool(getattr(self, "user_contributions_enabled", True))

    def quota_visible(self, user: dict[str, Any] | None) -> bool:
        """Admins always see quota; regular users follow the YAML switch."""
        return self._is_admin(user) or bool(getattr(self, "quota_visible_to_users", True))

    def can_manage(self, user: dict[str, Any] | None, row: dict[str, Any] | None) -> bool:
        if not user or not row:
            return False
        if self._is_admin(user):
            return True
        return int(row.get("owner_user_id") or 0) == int(user.get("id") or 0)

    def manageable_row(self, user: dict[str, Any], account_id: int) -> dict[str, Any] | None:
        row = self.repository.find(account_id)
        return row if self.can_manage(user, row) else None

    def _visible_to(self, row: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
        data = self._public(row)
        owner_id = int(data.get("owner_user_id") or 0)
        own = owner_id > 0 and owner_id == int(user.get("id") or 0)
        can_manage = self.can_manage(user, row)
        data["can_manage"] = can_manage
        data["is_own"] = own
        data["owner_label"] = "我贡献的" if own else (f"用户 #{owner_id}" if owner_id and self._is_admin(user) else ("用户贡献" if owner_id else "管理员账号"))
        if not can_manage:
            for key in ("email", "account_ref", "credential_error", "has_access_token", "has_refresh_token", "plan_type", "expires_at", "last_error"):
                data.pop(key, None)
            data["credential_mask"] = "凭据已加密"
        return data

    def list_page_for_user(self, user: dict[str, Any], provider: str = "", page: int = 1, page_size: int = 20) -> dict[str, Any]:
        provider = self.normalize_provider(provider) if str(provider or "").strip() else ""
        result = self.repository.list_page(provider, page, page_size)
        result["items"] = [self._visible_to(row, user) for row in result["items"]]
        return result

    @Transactional()
    def create(self, body: dict[str, Any], credentials: dict[str, Any] | None = None, owner_user_id: int | None = None) -> dict[str, Any]:
        provider = self.normalize_provider(body.get("provider"))
        if not body.get("compliance_confirmed"):
            raise ValueError("请确认你有权使用该订阅账号，并遵守上游服务条款")
        name = str(body.get("name") or "").strip()
        if not name or len(name) > 120:
            raise ValueError("账号名称不能为空且不能超过 120 个字符")
        auth_type = str(body.get("auth_type") or "oauth").strip().lower().replace("-", "_")
        if auth_type not in {"oauth", "setup_token", "imported_token", "api_key"}:
            raise ValueError("auth_type 只能是 oauth、setup_token、imported_token 或 api_key")
        credentials = self.normalize_imported_credentials(
            provider, body, dict(credentials or body.get("credentials") or {}),
        )
        access_token = str(credentials.get("access_token") or "").strip()
        refresh_token = str(credentials.get("refresh_token") or body.get("refresh_token") or "").strip()
        if not access_token:
            raise ValueError("access_token 不能为空")
        credentials["access_token"] = access_token
        if refresh_token:
            credentials["refresh_token"] = refresh_token
        expires_at = str(credentials.get("expires_at") or body.get("expires_at") or "").strip() or None
        if expires_at and not parse_time(expires_at):
            raise ValueError("expires_at 必须是 ISO-8601 时间")
        email = str(credentials.get("email") or body.get("email") or "").strip()[:255]
        account_ref = str(credentials.get("account_id") or credentials.get("chatgpt_account_id") or credentials.get("organization_id") or body.get("account_ref") or "").strip()[:255]
        now = utc_now()
        models = self.normalize_models(provider, body.get("models"))
        input_price = self._number(body.get("input_price_cny"), "输入价格", 0, 1_000_000, 0)
        output_price = self._number(body.get("output_price_cny"), "输出价格", 0, 1_000_000, 0)
        price_multiplier = self._number(body.get("price_multiplier"), "价格倍率", 0, 1000, 1)
        model_pricing = self.normalize_model_pricing(
            models, body.get("model_pricing"),
            default_input=input_price, default_output=output_price, default_multiplier=price_multiplier,
        )
        account = {
            "owner_user_id": int(owner_user_id) if owner_user_id else None,
            "provider": provider,
            "name": name,
            "auth_type": auth_type,
            "email": email,
            "account_ref": account_ref,
            "credentials_encrypted": self.cipher.encrypt(credentials),
            "models_json": json.dumps(models, ensure_ascii=False, separators=(",", ":")),
            "model_pricing_json": json.dumps(model_pricing, ensure_ascii=False, separators=(",", ":")),
            "enabled": 1 if as_bool(body.get("enabled"), True) else 0,
            "priority": int(self._number(body.get("priority"), "优先级", -1000, 1000, 0)),
            "weight": int(self._number(body.get("weight"), "权重", 1, 100, 1)),
            "input_price_cny": input_price,
            "output_price_cny": output_price,
            "price_multiplier": price_multiplier,
            "status": "READY" if as_bool(body.get("enabled"), True) else "DISABLED",
            "error_count": 0,
            "last_error": "",
            "disable_reason": "" if as_bool(body.get("enabled"), True) else "MANUAL",
            "quota_recovery_attempts": 0,
            "quota_recovery_next_at": None,
            "expires_at": expires_at,
            "cooldown_until": None,
            "last_used_at": None,
            "compliance_confirmed_at": now,
            "created_at": now,
            "updated_at": now,
        }
        return self._public(self.repository.create(account))

    @staticmethod
    def _normalize_request_url(value: Any) -> str:
        raw = str(value or "").strip()
        if not raw or len(raw) > 1000:
            raise ValueError("URL 不能为空且不能超过 1000 个字符")
        parsed = urlsplit(raw)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("URL 必须是有效的 HTTP/HTTPS 地址，且不能包含账号密码")
        return urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path.rstrip("/"), parsed.query, ""))

    def _public_config_request(self, row: dict[str, Any]) -> dict[str, Any]:
        data = dict(row)
        encrypted = str(data.pop("api_key_encrypted", "") or "")
        try:
            secret = str(self.cipher.decrypt(encrypted).get("api_key") or "")
        except (ValueError, AttributeError):
            secret = ""
        data["api_key_mask"] = (secret[:7] + "••••" + secret[-4:]) if len(secret) > 12 else ("••••••••" if secret else "未提供")
        return data

    @Transactional()
    def create_config_request(self, user_id: int, body: dict[str, Any]) -> dict[str, Any]:
        if not bool(getattr(self, "user_contributions_enabled", True)):
            raise ValueError("用户贡献功能当前已关闭")
        base_url = self._normalize_request_url(body.get("url") or body.get("base_url"))
        # ``token`` is the public name used by the sharing UI. Keep
        # ``api_key`` as a backwards-compatible alias for existing clients
        # and rows created by the former "upstream config request" form.
        api_key = str(body.get("token") or body.get("api_key") or "").strip()
        model_id = str(body.get("model_id") or "").strip()
        if not api_key or len(api_key) > 8192:
            raise ValueError("账号 Token/API Key 不能为空且不能超过 8192 个字符")
        if not model_id or len(model_id) > 200:
            raise ValueError("Model ID 不能为空且不能超过 200 个字符")
        now = utc_now()
        row = self.repository.create_config_request({
            "user_id": int(user_id),
            "base_url": base_url,
            "api_key_encrypted": self.cipher.encrypt({"api_key": api_key}),
            "model_id": model_id,
            # Proxy routing belongs to this exact URL/API Key/Model ID tuple.
            # It is intentionally not inherited from a global request default.
            "use_proxy": 1 if as_bool(body.get("use_proxy"), False) else 0,
            "status": "PENDING",
            "admin_note": "",
            "created_at": now,
            "updated_at": now,
        })
        return self._public_config_request(row)

    def list_config_requests(self, user: dict[str, Any], status: str = "", page: int = 1, page_size: int = 20) -> dict[str, Any]:
        normalized = str(status or "").strip().upper()
        if normalized and normalized not in {"PENDING", "ACCEPTED", "REJECTED"}:
            raise ValueError("Token 分享状态不正确")
        owner_filter = 0 if self._is_admin(user) else int(user["id"])
        result = self.repository.list_config_requests(owner_filter, normalized, page, page_size)
        result["items"] = [self._public_config_request(row) for row in result["items"]]
        return result

    def config_request_secret(self, request_id: int) -> dict[str, Any] | None:
        row = self.repository.find_config_request(request_id)
        if not row:
            return None
        credentials = self.cipher.decrypt(str(row.get("api_key_encrypted") or ""))
        return {"id": int(row["id"]), "url": row["base_url"], "api_key": str(credentials.get("api_key") or ""), "model_id": row["model_id"], "use_proxy": bool(row.get("use_proxy"))}

    @Transactional()
    def update_config_request(self, request_id: int, body: dict[str, Any]) -> dict[str, Any] | None:
        status = str(body.get("status") or "").strip().upper()
        if status not in {"PENDING", "ACCEPTED", "REJECTED"}:
            raise ValueError("status 只能是 PENDING、ACCEPTED 或 REJECTED")
        note = str(body.get("admin_note") or "").strip()
        if len(note) > 1000:
            raise ValueError("管理员备注不能超过 1000 个字符")
        row = self.repository.update_config_request(request_id, status, note, utc_now())
        return self._public_config_request(row) if row else None

    @Transactional()
    def update(self, account_id: int, body: dict[str, Any]) -> dict[str, Any] | None:
        existing = self.repository.find(account_id)
        if not existing:
            return None
        changes: dict[str, Any] = {"updated_at": utc_now()}
        if "name" in body:
            name = str(body.get("name") or "").strip()
            if not name or len(name) > 120:
                raise ValueError("账号名称不能为空且不能超过 120 个字符")
            changes["name"] = name
        try:
            existing_models = json.loads(str(existing.get("models_json") or "[]"))
        except ValueError:
            existing_models = []
        models = self.normalize_models(str(existing["provider"]), body.get("models")) if "models" in body else existing_models
        if "models" in body:
            changes["models_json"] = json.dumps(models, ensure_ascii=False, separators=(",", ":"))
        if "enabled" in body:
            enabled = as_bool(body.get("enabled"))
            changes["enabled"] = 1 if enabled else 0
            changes["status"] = "READY" if enabled else "DISABLED"
            changes["last_error"] = ""
            changes["error_count"] = 0
            changes["disable_reason"] = "" if enabled else "MANUAL"
            changes["quota_recovery_attempts"] = 0
            # MyBatis 的动态更新需要区分“不更新”和“清空”；空字符串在读取时按无计划处理。
            changes["quota_recovery_next_at"] = ""
        for key, label, minimum, maximum, default in (
            ("priority", "优先级", -1000, 1000, int(existing.get("priority") or 0)),
            ("weight", "权重", 1, 100, int(existing.get("weight") or 1)),
            ("input_price_cny", "输入价格", 0, 1_000_000, float(existing.get("input_price_cny") or 0)),
            ("output_price_cny", "输出价格", 0, 1_000_000, float(existing.get("output_price_cny") or 0)),
            ("price_multiplier", "价格倍率", 0, 1000, float(existing.get("price_multiplier") or 1)),
        ):
            if key in body:
                value = self._number(body.get(key), label, minimum, maximum, default)
                changes[key] = int(value) if key in {"priority", "weight"} else value
        if "model_pricing" in body or "models" in body:
            raw_model_pricing = body.get("model_pricing") if "model_pricing" in body else {
                model: pricing
                for model, pricing in self._model_pricing_map(existing).items()
                if model in models
            }
            model_pricing = self.normalize_model_pricing(
                models,
                raw_model_pricing,
                default_input=float(changes.get("input_price_cny", existing.get("input_price_cny") or 0)),
                default_output=float(changes.get("output_price_cny", existing.get("output_price_cny") or 0)),
                default_multiplier=float(changes.get("price_multiplier", existing.get("price_multiplier") or 1)),
            )
            changes["model_pricing_json"] = json.dumps(model_pricing, ensure_ascii=False, separators=(",", ":"))
        supplied_credentials = body.get("credentials") if isinstance(body.get("credentials"), dict) else {}
        access_input = str(supplied_credentials.get("access_token") or body.get("access_token") or "").strip()
        refresh_input = str(supplied_credentials.get("refresh_token") or body.get("refresh_token") or "").strip()
        access_token = ""
        refresh_token = ""
        normalized_credentials: dict[str, Any] = {}
        if access_input or refresh_input:
            normalized_credentials = self.normalize_imported_credentials(
                str(existing["provider"]), body, supplied_credentials,
            )
            access_token = str(normalized_credentials.get("access_token") or "").strip()
            refresh_token = str(normalized_credentials.get("refresh_token") or "").strip()
        if access_token or refresh_token:
            credentials = self._credentials(existing)
            for key, value in normalized_credentials.items():
                if value not in (None, ""):
                    credentials[key] = value
            credential_expires_at = str(credentials.get("expires_at") or "").strip()
            if credential_expires_at:
                if not parse_time(credential_expires_at):
                    raise ValueError("expires_at 必须是 ISO-8601 时间")
                changes["expires_at"] = credential_expires_at
            credential_email = str(credentials.get("email") or "").strip()[:255]
            if credential_email:
                changes["email"] = credential_email
            account_ref = str(
                credentials.get("account_id") or credentials.get("chatgpt_account_id")
                or credentials.get("organization_id") or ""
            ).strip()[:255]
            if account_ref:
                changes["account_ref"] = account_ref
            changes["credentials_encrypted"] = self.cipher.encrypt(credentials)
            changes["status"] = "READY" if as_bool(existing.get("enabled"), True) else "DISABLED"
            changes["last_error"] = ""
            changes["error_count"] = 0
        updated = self.repository.update(account_id, changes)
        return self._public(updated) if updated else None

    def delete(self, account_id: int) -> bool:
        deleted = self.repository.delete(account_id)
        if deleted:
            self.pool.forget(account_id)
        return deleted

    async def refresh_account(self, account_id: int, force: bool = True) -> dict[str, Any]:
        lock = self._refresh_locks.setdefault(int(account_id), asyncio.Lock())
        async with lock:
            row = self.repository.find(account_id)
            if not row:
                raise ValueError("上游订阅账号不存在")
            credentials = self._credentials(row)
            expires = parse_time(credentials.get("expires_at") or row.get("expires_at"))
            if not force and (not expires or expires > datetime.now(timezone.utc) + timedelta(minutes=5)):
                return {"account": row, "credentials": credentials}
            refresh_token = str(credentials.get("refresh_token") or "").strip()
            if not refresh_token:
                if expires and expires <= datetime.now(timezone.utc):
                    self.record_failure(row, 401, "OAuth token 已过期且没有 refresh_token")
                    raise ValueError("OAuth token 已过期，需要重新授权")
                return {"account": row, "credentials": credentials}
            try:
                refreshed = await self.oauth.refresh(str(row["provider"]), refresh_token)
            except Exception as exc:
                self.record_failure(row, 401, str(exc))
                raise
            credentials.update({key: value for key, value in refreshed.items() if value not in (None, "")})
            encrypted = self.cipher.encrypt(credentials)
            email = str(credentials.get("email") or row.get("email") or "")[:255]
            account_ref = str(credentials.get("account_id") or credentials.get("chatgpt_account_id") or credentials.get("organization_id") or row.get("account_ref") or "")[:255]
            updated = self.repository.update_credentials(
                int(row["id"]), credentials_encrypted=encrypted, email=email,
                account_ref=account_ref, expires_at=credentials.get("expires_at"), updated_at=utc_now(),
            )
            return {"account": updated or row, "credentials": credentials}

    @staticmethod
    def _supports(row: dict[str, Any], model: str) -> bool:
        try:
            models = json.loads(str(row.get("models_json") or "[]"))
        except ValueError:
            return False
        return isinstance(models, list) and ("*" in models or str(model) in models)

    async def acquire(
        self,
        provider: str,
        model: str,
        excluded: set[int] | None = None,
        preferred_account_id: int | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        provider = self.normalize_provider(provider)
        excluded = excluded or set()
        now = datetime.now(timezone.utc)
        rows = [
            row for row in self.repository.list_provider(provider)
            if int(row.get("id") or 0) not in excluded
            and self._supports(row, model)
            and not self._is_cooling(row, now)
        ]
        if not rows:
            raise LookupError(f"没有可用于模型 {model} 的 {provider} 订阅账号")
        preferred = next(
            (row for row in rows if int(row.get("id") or 0) == int(preferred_account_id or 0)),
            None,
        )
        last_error: Exception | None = None

        # An explicit healthy session affinity wins even when it points to a
        # lower-priority account. This keeps one conversation on one account.
        if preferred:
            try:
                fresh = await self.refresh_account(int(preferred["id"]), force=False)
                return fresh["account"], fresh["credentials"]
            except Exception as exc:
                last_error = exc

        remaining = [row for row in rows if not preferred or int(row["id"]) != int(preferred["id"])]
        priorities = sorted({int(row.get("priority") or 0) for row in remaining}, reverse=True)
        for priority in priorities:
            tier = [row for row in remaining if int(row.get("priority") or 0) == priority]
            # Each account is attempted at most once. Smooth weighted round
            # robin chooses the normal candidate; refresh failures fall
            # through the rest of the tier and then lower-priority tiers.
            while tier:
                row = self.pool.select(tier, provider, model, priority)
                tier = [candidate for candidate in tier if int(candidate["id"]) != int(row["id"])]
                try:
                    fresh = await self.refresh_account(int(row["id"]), force=False)
                    return fresh["account"], fresh["credentials"]
                except Exception as exc:
                    last_error = exc
        if last_error:
            raise last_error
        raise LookupError(f"没有可用于模型 {model} 的 {provider} 订阅账号")

    def has_route(self, provider: str, model: str) -> bool:
        try:
            provider = self.normalize_provider(provider)
        except ValueError:
            return False
        now = datetime.now(timezone.utc)
        return any(
            self._supports(row, model)
            and not self._is_cooling(row, now)
            for row in self.repository.list_provider(provider)
        )

    def catalog(self) -> list[dict[str, Any]]:
        routes: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for provider in SUBSCRIPTION_PROVIDERS:
            for row in self.repository.list_provider(provider):
                public = self._public(row)
                for model in public.get("models") or []:
                    if model == "*":
                        continue
                    key = (provider, str(model))
                    routes.setdefault(key, []).append(public)
        now = datetime.now(timezone.utc)
        result = []
        for (provider, model), accounts in routes.items():
            available = [
                account for account in accounts
                if not self._is_cooling(account, now)
            ]
            cooling = len(accounts) - len(available)
            is_available = bool(available)
            detail = f"{len(available)}/{len(accounts)} 个订阅账号可调度"
            if cooling:
                detail += f"，{cooling} 个冷却中"
            if not is_available:
                last_error = next((str(item.get("last_error") or "").strip() for item in accounts if item.get("last_error")), "")
                if last_error:
                    detail += f"：{last_error[:180]}"
            sample = available[0] if available else accounts[0]
            model_pricing = self.pricing_for_model(sample, model)
            result.append({
                        "id": str(model),
                        "provider": provider_label(provider) + " Subscription",
                        "endpoint": {
                            "responses": "Responses",
                            "anthropic": "Messages",
                            "chat": "Chat Completions",
                        }.get(str(SUBSCRIPTION_PROVIDERS[provider]["protocol"]), "Chat Completions"),
                        "group": "Subscription Gateway",
                        "pricing": {
                            "input-cny-per-million": model_pricing["input_price_cny"],
                            "output-cny-per-million": model_pricing["output_price_cny"],
                            "multiplier": model_pricing["price_multiplier"],
                        },
                        "currency": "CNY",
                        "input": model_pricing["input_price_cny"],
                        "output": model_pricing["output_price_cny"],
                        "description": "由已授权订阅账号池提供的标准兼容 API。",
                        "enabled": is_available,
                        "upstream_model": str(model),
                        "supports_image": True,
                        "status": "正常" if is_available else "冷却中",
                        "health": {
                            "state": "ok" if is_available else "cooldown",
                            "label": "正常" if is_available else "冷却中",
                            "detail": detail,
                            "latency_ms": None,
                            "checked_at": now.isoformat(),
                        },
                    })
        return result

    def record_success(self, row: dict[str, Any]) -> None:
        self.repository.mark_result(
            int(row["id"]), status="READY", error_count=0, last_error="", cooldown_until=None,
            last_used_at=utc_now(), updated_at=utc_now(),
        )

    def record_failure(self, row: dict[str, Any], status_code: int | None, detail: str, retry_after: float | None = None) -> None:
        code = int(status_code or 0)
        error_count = int(row.get("error_count") or 0) + 1
        if code == 429:
            # A 429 may only describe the short (for example five-hour)
            # window. It must not persistently disable or cool an account.
            # A real upstream 429 is handled asynchronously by the gateway,
            # which checks the weekly quota before deciding whether to disable.
            return
        normalized_detail = str(detail or "").strip().lower()
        if any(marker in normalized_detail for marker in NO_COOLDOWN_ERROR_MARKERS):
            return
        # Request validation failures are caused by the caller's payload and
        # say nothing about account health.  Only authentication, timeout,
        # rate-limit and server-side failures are eligible to penalize an
        # upstream account.
        if 400 <= code < 500 and code not in {401, 403, 408, 429}:
            return
        if code in {401, 403}:
            status = "INVALID"
            cooldown = None
        elif not getattr(self, "cooldown_enabled", True):
            # Preserve diagnostics without removing this account from the pool.
            # The current gateway attempt still excludes it and can fail over
            # to another account, but later requests may try it again.
            status = "READY" if as_bool(row.get("enabled"), True) else "DISABLED"
            cooldown = None
        else:
            status = "COOLDOWN"
            seconds = retry_after if retry_after and retry_after > 0 else (60 if code == 429 else min(300, 10 * error_count))
            cooldown = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()
        self.repository.mark_result(
            int(row["id"]), status=status, error_count=error_count,
            last_error=str(detail or f"HTTP {code}")[:1000], cooldown_until=cooldown,
            last_used_at=utc_now(), updated_at=utc_now(),
        )

    def disable_for_weekly_quota(
        self,
        row: dict[str, Any],
        remaining_percent: float,
        threshold: float = 1.0,
        reset_at: str | None = None,
    ) -> bool:
        """System-disable only an enabled account with a confirmed low quota."""
        try:
            remaining = float(remaining_percent)
            limit = float(threshold)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(remaining) or not math.isfinite(limit) or remaining < 0 or remaining >= limit:
            return False
        if not as_bool(row.get("enabled"), True) and str(row.get("disable_reason") or "").upper() == "MANUAL":
            return False
        now = utc_now()
        detail = f"每周订阅剩余量 {remaining:.2f}% 低于 {limit:.2f}%，已停用并进入自动复查"
        reset = parse_time(reset_at)
        recovery_time = reset + timedelta(minutes=1) if reset and reset > datetime.now(timezone.utc) else datetime.now(timezone.utc) + timedelta(minutes=1)
        recovery_next_at = recovery_time.isoformat()
        changed = self.repository.disable_rate_limited(
            int(row["id"]), error_count=int(row.get("error_count") or 0),
            last_error=detail[:1000], recovery_next_at=recovery_next_at,
            last_used_at=now, updated_at=now,
        )
        # Older in-memory/test repositories did not return the mapper row
        # count.  Treat None as success while respecting an explicit 0/False
        # from the production mapper, which prevents duplicate notifications.
        if changed is False:
            return False
        self.pool.forget(int(row["id"]))
        return True

    def list_system_disabled_accounts(self) -> list[dict[str, Any]]:
        """Return only accounts disabled by the automatic low-quota guard."""
        return self.repository.list_system_disabled_accounts()

    def enable_after_quota_recovery(self, account_id: int) -> bool:
        """Re-enable an account only when its disable reason is LOW_QUOTA."""
        return self.repository.enable_system_recovered(int(account_id), updated_at=utc_now())

    @classmethod
    def low_quota_recovery_delay(cls, attempts: int) -> int | None:
        """Return seconds until the next low-quota check after ``attempts``."""
        completed = max(0, int(attempts))
        for limit, seconds in cls.LOW_QUOTA_RECOVERY_INTERVALS:
            if completed < limit:
                return seconds
        return None

    def record_low_quota_recovery_attempt(self, account_id: int, attempts: int, now: datetime | None = None) -> bool:
        """Persist a failed recovery check and its next scheduled time."""
        completed = max(0, min(self.LOW_QUOTA_RECOVERY_MAX_ATTEMPTS, int(attempts)))
        # New automatic recovery is deliberately one-shot.  A failed check
        # must not recreate the historical 3h/2h/1h polling cadence.
        if completed >= self.LOW_QUOTA_RECOVERY_MAX_CHECKS:
            delay = None
        else:
            delay = 60
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        next_at = (current.astimezone(timezone.utc) + timedelta(seconds=delay)).isoformat() if delay else None
        return self.repository.update_low_quota_recovery(
            int(account_id), attempts=completed, next_at=next_at, updated_at=utc_now(),
        )

    @classmethod
    def cost(cls, row: dict[str, Any], input_tokens: int, output_tokens: int, model_id: str | None = None) -> float:
        pricing = cls.pricing_for_model(row, model_id)
        multiplier = pricing["price_multiplier"]
        input_price = pricing["input_price_cny"]
        output_price = pricing["output_price_cny"]
        return round(((max(0, int(input_tokens)) * input_price + max(0, int(output_tokens)) * output_price) / 1_000_000) * multiplier, 8)


__all__ = ["SubscriptionAccountService", "DEFAULT_MODELS"]
