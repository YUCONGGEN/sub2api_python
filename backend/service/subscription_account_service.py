"""SpringBootAI business service for subscription account lifecycle and scheduling."""

import asyncio
import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any

from springbootai import Autowired, PostConstruct, Service, Slf4j, Transactional, get_config

from backend.repository.subscription_repository import SubscriptionRepository
from backend.service.credential_cipher_service import CredentialCipherService
from backend.service.subscription_account_pool_service import SubscriptionAccountPoolService
from backend.service.subscription_oauth_service import SubscriptionOAuthService


DEFAULT_MODELS = {
    "openai": ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.3-codex-spark"],
    "claude": ["claude-opus-5", "claude-sonnet-5", "claude-fable-5", "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
}

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

    @PostConstruct
    def init(self) -> None:
        cfg = get_config().get("rose", {}).get("subscription-gateway", {})
        self.cooldown_enabled = as_bool(cfg.get("account-cooldown-enabled"), True)
        self.logger.info("订阅账号临时冷却 enabled=%s", self.cooldown_enabled)

    def _is_cooling(self, row: dict[str, Any], now: datetime | None = None) -> bool:
        if not getattr(self, "cooldown_enabled", True):
            return False
        until = parse_time(row.get("cooldown_until"))
        return bool(until and until > (now or datetime.now(timezone.utc)))

    @staticmethod
    def normalize_provider(value: Any) -> str:
        provider = str(value or "").strip().lower()
        if provider not in {"openai", "claude"}:
            raise ValueError("provider 只能是 openai 或 claude")
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

    @Transactional()
    def create(self, body: dict[str, Any], credentials: dict[str, Any] | None = None) -> dict[str, Any]:
        provider = self.normalize_provider(body.get("provider"))
        if not body.get("compliance_confirmed"):
            raise ValueError("请确认你有权使用该订阅账号，并遵守上游服务条款")
        name = str(body.get("name") or "").strip()
        if not name or len(name) > 120:
            raise ValueError("账号名称不能为空且不能超过 120 个字符")
        auth_type = str(body.get("auth_type") or "oauth").strip().lower().replace("-", "_")
        if auth_type not in {"oauth", "setup_token", "imported_token"}:
            raise ValueError("auth_type 只能是 oauth、setup_token 或 imported_token")
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
        account = {
            "provider": provider,
            "name": name,
            "auth_type": auth_type,
            "email": email,
            "account_ref": account_ref,
            "credentials_encrypted": self.cipher.encrypt(credentials),
            "models_json": json.dumps(self.normalize_models(provider, body.get("models")), ensure_ascii=False, separators=(",", ":")),
            "enabled": 1 if as_bool(body.get("enabled"), True) else 0,
            "priority": int(self._number(body.get("priority"), "优先级", -1000, 1000, 0)),
            "weight": int(self._number(body.get("weight"), "权重", 1, 100, 1)),
            "input_price_cny": self._number(body.get("input_price_cny"), "输入价格", 0, 1_000_000, 0),
            "output_price_cny": self._number(body.get("output_price_cny"), "输出价格", 0, 1_000_000, 0),
            "price_multiplier": self._number(body.get("price_multiplier"), "价格倍率", 0, 1000, 1),
            "status": "READY" if as_bool(body.get("enabled"), True) else "DISABLED",
            "error_count": 0,
            "last_error": "",
            "expires_at": expires_at,
            "cooldown_until": None,
            "last_used_at": None,
            "compliance_confirmed_at": now,
            "created_at": now,
            "updated_at": now,
        }
        return self._public(self.repository.create(account))

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
        if "models" in body:
            changes["models_json"] = json.dumps(self.normalize_models(str(existing["provider"]), body.get("models")), ensure_ascii=False, separators=(",", ":"))
        if "enabled" in body:
            enabled = as_bool(body.get("enabled"))
            changes["enabled"] = 1 if enabled else 0
            changes["status"] = "READY" if enabled else "DISABLED"
            changes["last_error"] = ""
            changes["error_count"] = 0
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
            changes["status"] = "READY"
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
        for provider in ("openai", "claude"):
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
            sample = accounts[0]
            result.append({
                        "id": str(model),
                        "provider": "OpenAI Subscription" if provider == "openai" else "Claude Subscription",
                        "endpoint": "Responses" if provider == "openai" else "Messages",
                        "group": "Subscription Gateway",
                        "pricing": {
                            "input-cny-per-million": float(sample.get("input_price_cny") or 0),
                            "output-cny-per-million": float(sample.get("output_price_cny") or 0),
                        },
                        "currency": "CNY",
                        "input": float(sample.get("input_price_cny") or 0),
                        "output": float(sample.get("output_price_cny") or 0),
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
            # An upstream 429 means this subscription can no longer serve
            # traffic safely. Disable it persistently instead of applying a
            # temporary cooldown; an administrator must explicitly re-enable
            # the account after checking its quota/status.
            now = utc_now()
            self.repository.disable_rate_limited(
                int(row["id"]), error_count=error_count,
                last_error=str(detail or "HTTP 429")[:1000],
                last_used_at=now, updated_at=now,
            )
            self.pool.forget(int(row["id"]))
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

    @staticmethod
    def cost(row: dict[str, Any], input_tokens: int, output_tokens: int) -> float:
        multiplier = max(0.0, float(row.get("price_multiplier") or 1))
        input_price = max(0.0, float(row.get("input_price_cny") or 0))
        output_price = max(0.0, float(row.get("output_price_cny") or 0))
        return round(((max(0, int(input_tokens)) * input_price + max(0, int(output_tokens)) * output_price) / 1_000_000) * multiplier, 8)


__all__ = ["SubscriptionAccountService", "DEFAULT_MODELS"]
