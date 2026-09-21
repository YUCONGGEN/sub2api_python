"""Authenticated shared subscription-pool and contribution endpoints."""

from springbootai.annotations import (
    Autowired,
    DeleteMapping,
    GetMapping,
    PatchMapping,
    PathVariable,
    PostMapping,
    RequestBody,
    RequestHeader,
    RequestMapping,
    RequestParam,
    RestController,
)

from backend.common.response import bad, forbidden, not_found, ok, unauthorized
from backend.service.auth_service import AuthService
from backend.service.subscription_account_service import SubscriptionAccountService
from backend.service.subscription_gateway_service import SubscriptionGatewayService
from backend.service.subscription_oauth_service import SubscriptionOAuthService


@RestController
@RequestMapping("/api/upstream-subscriptions")
class SubscriptionController:
    @Autowired
    def __init__(self, auth: AuthService, accounts: SubscriptionAccountService, oauth: SubscriptionOAuthService, gateway: SubscriptionGatewayService):
        self.auth = auth
        self.accounts = accounts
        self.oauth = oauth
        self.gateway = gateway

    def _user(self, authorization: str | None):
        return self.auth.user_from_authorization(authorization)

    @staticmethod
    def _admin(user: dict | None) -> bool:
        return bool(user and str(user.get("role") or "").upper() == "ADMIN")

    def _available(self, user: dict | None):
        if not user:
            return unauthorized()
        if not self.accounts.contributions_available(user):
            return forbidden("用户贡献账号功能当前已关闭")
        return None

    def _managed(self, user: dict, account_id: int):
        public = self.accounts.find_public(account_id)
        if not public:
            return None, not_found("订阅账号不存在")
        row = self.accounts.manageable_row(user, account_id)
        if not row:
            return None, forbidden("只能管理自己添加的订阅账号")
        return row, None

    @staticmethod
    def _user_body(user: dict, body: dict) -> dict:
        if str(user.get("role") or "").upper() == "ADMIN":
            return dict(body)
        allowed = {
            "provider", "name", "models", "auth_type", "access_token", "refresh_token",
            "expires_at", "email", "account_ref", "credentials", "enabled", "compliance_confirmed",
            "session_id", "callback_value", "code", "state",
        }
        cleaned = {key: value for key, value in dict(body or {}).items() if key in allowed}
        cleaned.update({"priority": 0, "weight": 1, "input_price_cny": 0, "output_price_cny": 0, "price_multiplier": 1})
        return cleaned

    @GetMapping("")
    def list_accounts(
        self,
        authorization: str = RequestHeader(name="Authorization", required=False),
        provider: str = RequestParam(name="provider", required=False, default=""),
        page: int = RequestParam(name="page", required=False, default=1),
        page_size: int = RequestParam(name="page_size", required=False, default=20),
    ):
        user = self._user(authorization)
        error = self._available(user)
        if error:
            return error
        try:
            result = self.accounts.list_page_for_user(user, provider, page, page_size)
        except ValueError as exc:
            return bad(str(exc))
        metrics = self.gateway.metrics(include_users=True) if self._admin(user) else {}
        return ok({
            "ok": True,
            "accounts": result["items"],
            "pagination": result,
            "summary": result.get("summary", {}),
            "gateway_enabled": self.gateway.enabled,
            "gateway_metrics": metrics,
            "is_admin": self._admin(user),
            "contributions_enabled": True,
            "quota_visible": self.accounts.quota_visible(user),
        })

    @GetMapping("/gateway-metrics")
    def gateway_metrics(self, authorization: str = RequestHeader(name="Authorization", required=False)):
        user = self._user(authorization)
        if not self._admin(user):
            return forbidden()
        return ok({"ok": True, "gateway_metrics": self.gateway.metrics(include_users=True)})

    @PostMapping("")
    async def create_account(self, body: dict = RequestBody(), authorization: str = RequestHeader(name="Authorization", required=False)):
        user = self._user(authorization)
        error = self._available(user)
        if error:
            return error
        cleaned = self._user_body(user, body)
        try:
            validated = await self.gateway.validate_candidate(cleaned)
            account = self.accounts.create(cleaned, validated["credentials"], owner_user_id=int(user["id"]))
        except ValueError as exc:
            return bad(str(exc), 502 if "连通性" in str(exc) else 400)
        return ok({"ok": True, "account": account, "model_count": validated["model_count"]}, "连通性验证通过，订阅账号已加入共享池")

    @PatchMapping("/{account_id}")
    def update_account(self, account_id: int = PathVariable(name="account_id"), body: dict = RequestBody(), authorization: str = RequestHeader(name="Authorization", required=False)):
        user = self._user(authorization)
        error = self._available(user)
        if error:
            return error
        _, denied = self._managed(user, account_id)
        if denied:
            return denied
        try:
            account = self.accounts.update(account_id, self._user_body(user, body))
        except ValueError as exc:
            return bad(str(exc))
        return ok({"ok": True, "account": account}, "订阅账号已更新") if account else not_found("订阅账号不存在")

    @DeleteMapping("/{account_id}")
    def delete_account(self, account_id: int = PathVariable(name="account_id"), authorization: str = RequestHeader(name="Authorization", required=False)):
        user = self._user(authorization)
        error = self._available(user)
        if error:
            return error
        _, denied = self._managed(user, account_id)
        if denied:
            return denied
        return ok({"ok": True}, "订阅账号已删除") if self.accounts.delete(account_id) else not_found("订阅账号不存在")

    @PostMapping("/oauth/authorize")
    def oauth_authorize(self, body: dict = RequestBody(), authorization: str = RequestHeader(name="Authorization", required=False)):
        user = self._user(authorization)
        error = self._available(user)
        if error:
            return error
        if not body.get("compliance_confirmed"):
            return bad("请先确认你有权使用该订阅账号，并遵守上游服务条款")
        try:
            result = self.oauth.generate_authorization(str(body.get("provider") or ""), int(user["id"]))
        except ValueError as exc:
            return bad(str(exc))
        return ok({"ok": True, **result})

    @PostMapping("/oauth/exchange")
    async def oauth_exchange(self, body: dict = RequestBody(), authorization: str = RequestHeader(name="Authorization", required=False)):
        user = self._user(authorization)
        error = self._available(user)
        if error:
            return error
        cleaned = self._user_body(user, body)
        if not str(cleaned.get("name") or "").strip():
            return bad("账号名称不能为空")
        if not cleaned.get("compliance_confirmed"):
            return bad("请先确认你有权使用该订阅账号，并遵守上游服务条款")
        try:
            credentials = await self.oauth.exchange(
                str(cleaned.get("provider") or ""), session_id=str(cleaned.get("session_id") or ""),
                callback_value=str(cleaned.get("callback_value") or cleaned.get("code") or ""),
                state=str(cleaned.get("state") or ""), admin_id=int(user["id"]),
            )
            validated = await self.gateway.validate_candidate(cleaned, credentials)
            account = self.accounts.create(cleaned, validated["credentials"], owner_user_id=int(user["id"]))
        except ValueError as exc:
            return bad(str(exc), 502 if "连通性" in str(exc) or "OAuth" in str(exc) else 400)
        return ok({"ok": True, "account": account, "model_count": validated["model_count"]}, "OAuth 与连通性验证通过，账号已加入共享池")

    @PostMapping("/{account_id}/refresh")
    async def refresh_account(self, account_id: int = PathVariable(name="account_id"), authorization: str = RequestHeader(name="Authorization", required=False)):
        user = self._user(authorization)
        error = self._available(user)
        if error:
            return error
        _, denied = self._managed(user, account_id)
        if denied:
            return denied
        try:
            refreshed = await self.accounts.refresh_account(account_id, force=True)
        except ValueError as exc:
            return bad(str(exc), 502)
        return ok({"ok": True, "account": self.accounts.find_public(int(refreshed["account"]["id"]))}, "OAuth token 已刷新")

    @PostMapping("/{account_id}/test")
    async def test_account(self, account_id: int = PathVariable(name="account_id"), authorization: str = RequestHeader(name="Authorization", required=False)):
        user = self._user(authorization)
        error = self._available(user)
        if error:
            return error
        _, denied = self._managed(user, account_id)
        if denied:
            return denied
        try:
            result = await self.gateway.test_account(account_id)
        except ValueError as exc:
            return bad(str(exc), 502)
        return ok(result, result.get("message", "连接正常"))

    @GetMapping("/{account_id}/quota")
    async def account_quota(self, account_id: int = PathVariable(name="account_id"), refresh: str = RequestParam(name="refresh", required=False, default="false"), authorization: str = RequestHeader(name="Authorization", required=False)):
        user = self._user(authorization)
        error = self._available(user)
        if error:
            return error
        if not self.accounts.quota_visible(user):
            return forbidden("订阅剩余量当前仅管理员可见")
        row = self.accounts.find_public(account_id)
        if not row:
            return not_found("订阅账号不存在")
        if row.get("provider") != "openai":
            return bad("只有 OpenAI / Codex 订阅支持用量查询")
        try:
            # Regular users, including the account owner, always share the
            # configured quota cache. Only administrators may explicitly
            # bypass it, preventing browser reloads from querying upstream
            # more than once within the default five-minute window.
            force = str(refresh).lower() in {"1", "true", "yes", "on"} and self._admin(user)
            quota = await self.gateway.query_account_quota(account_id, force=force)
        except ValueError as exc:
            return bad(str(exc), 502)
        return ok({"ok": True, "quota": quota})

    @PostMapping("/{account_id}/quota/reset")
    async def reset_account_quota(self, account_id: int = PathVariable(name="account_id"), authorization: str = RequestHeader(name="Authorization", required=False)):
        user = self._user(authorization)
        error = self._available(user)
        if error:
            return error
        if not self.accounts.quota_visible(user):
            return forbidden("订阅剩余量当前仅管理员可见")
        row, denied = self._managed(user, account_id)
        if denied:
            return denied
        if str(row.get("provider") or "") != "openai":
            return bad("只有 OpenAI / Codex 订阅支持额度重置")
        try:
            result = await self.gateway.reset_account_quota(account_id)
        except ValueError as exc:
            return bad(str(exc), 502)
        return ok(result, "订阅额度已重置")

    @GetMapping("/config-requests")
    def list_config_requests(self, authorization: str = RequestHeader(name="Authorization", required=False), status: str = RequestParam(name="status", required=False, default=""), page: int = RequestParam(name="page", required=False, default=1), page_size: int = RequestParam(name="page_size", required=False, default=20)):
        user = self._user(authorization)
        error = self._available(user)
        if error:
            return error
        try:
            result = self.accounts.list_config_requests(user, status, page, page_size)
        except ValueError as exc:
            return bad(str(exc))
        return ok({"ok": True, "requests": result["items"], "pagination": result})

    @PostMapping("/config-requests")
    def create_config_request(self, body: dict = RequestBody(), authorization: str = RequestHeader(name="Authorization", required=False)):
        user = self._user(authorization)
        error = self._available(user)
        if error:
            return error
        try:
            request = self.accounts.create_config_request(int(user["id"]), body)
        except ValueError as exc:
            return bad(str(exc))
        return ok({"ok": True, "request": request}, "账号 Token 已加密发送给管理员")

    @GetMapping("/config-requests/{request_id}/secret")
    def config_request_secret(self, request_id: int = PathVariable(name="request_id"), authorization: str = RequestHeader(name="Authorization", required=False)):
        user = self._user(authorization)
        if not self._admin(user):
            return forbidden()
        result = self.accounts.config_request_secret(request_id)
        return ok({"ok": True, "request": result}) if result else not_found("Token 分享记录不存在")

    @PatchMapping("/config-requests/{request_id}")
    def update_config_request(self, request_id: int = PathVariable(name="request_id"), body: dict = RequestBody(), authorization: str = RequestHeader(name="Authorization", required=False)):
        user = self._user(authorization)
        if not self._admin(user):
            return forbidden()
        try:
            result = self.accounts.update_config_request(request_id, body)
        except ValueError as exc:
            return bad(str(exc))
        return ok({"ok": True, "request": result}, "Token 分享状态已更新") if result else not_found("Token 分享记录不存在")


__all__ = ["SubscriptionController"]
