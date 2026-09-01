"""SpringBootAI annotated administration API for upstream subscription accounts."""

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

from backend.common.response import bad, forbidden, not_found, ok
from backend.service.auth_service import AuthService
from backend.service.subscription_account_service import SubscriptionAccountService
from backend.service.subscription_gateway_service import SubscriptionGatewayService
from backend.service.subscription_oauth_service import SubscriptionOAuthService


@RestController
@RequestMapping("/api/admin/upstream-subscriptions")
class SubscriptionAdminController:
    @Autowired
    def __init__(
        self,
        auth: AuthService,
        accounts: SubscriptionAccountService,
        oauth: SubscriptionOAuthService,
        gateway: SubscriptionGatewayService,
    ):
        self.auth = auth
        self.accounts = accounts
        self.oauth = oauth
        self.gateway = gateway

    def _admin(self, authorization: str | None):
        user = self.auth.user_from_authorization(authorization)
        return user if user and user.get("role") == "ADMIN" else None

    @GetMapping("")
    def list_accounts(
        self,
        authorization: str = RequestHeader(name="Authorization", required=False),
        provider: str = RequestParam(name="provider", required=False, default=""),
        page: int = RequestParam(name="page", required=False, default=1),
        page_size: int = RequestParam(name="page_size", required=False, default=20),
    ):
        if not self._admin(authorization):
            return forbidden()
        try:
            result = self.accounts.list_page(provider, page, page_size)
        except ValueError as exc:
            return bad(str(exc))
        return ok({"ok": True, "accounts": result["items"], "pagination": result, "summary": result.get("summary", {}), "gateway_enabled": self.gateway.enabled, "gateway_metrics": self.gateway.metrics()})

    @PostMapping("")
    def create_account(self, body: dict = RequestBody(), authorization: str = RequestHeader(name="Authorization", required=False)):
        if not self._admin(authorization):
            return forbidden()
        try:
            account = self.accounts.create(body)
        except ValueError as exc:
            return bad(str(exc))
        return ok({"ok": True, "account": account}, "订阅账号已加密保存")

    @PatchMapping("/{account_id}")
    def update_account(
        self,
        account_id: int = PathVariable(name="account_id"),
        body: dict = RequestBody(),
        authorization: str = RequestHeader(name="Authorization", required=False),
    ):
        if not self._admin(authorization):
            return forbidden()
        try:
            account = self.accounts.update(account_id, body)
        except ValueError as exc:
            return bad(str(exc))
        return ok({"ok": True, "account": account}, "订阅账号已更新") if account else not_found("订阅账号不存在")

    @DeleteMapping("/{account_id}")
    def delete_account(self, account_id: int = PathVariable(name="account_id"), authorization: str = RequestHeader(name="Authorization", required=False)):
        if not self._admin(authorization):
            return forbidden()
        return ok({"ok": True}, "订阅账号已删除") if self.accounts.delete(account_id) else not_found("订阅账号不存在")

    @PostMapping("/oauth/authorize")
    def oauth_authorize(self, body: dict = RequestBody(), authorization: str = RequestHeader(name="Authorization", required=False)):
        admin = self._admin(authorization)
        if not admin:
            return forbidden()
        if not body.get("compliance_confirmed"):
            return bad("请先确认你有权使用该订阅账号，并遵守上游服务条款")
        try:
            result = self.oauth.generate_authorization(str(body.get("provider") or ""), int(admin["id"]))
        except ValueError as exc:
            return bad(str(exc))
        return ok({"ok": True, **result})

    @PostMapping("/oauth/exchange")
    async def oauth_exchange(self, body: dict = RequestBody(), authorization: str = RequestHeader(name="Authorization", required=False)):
        admin = self._admin(authorization)
        if not admin:
            return forbidden()
        if not str(body.get("name") or "").strip():
            return bad("账号名称不能为空")
        if not body.get("compliance_confirmed"):
            return bad("请先确认你有权使用该订阅账号，并遵守上游服务条款")
        try:
            credentials = await self.oauth.exchange(
                str(body.get("provider") or ""),
                session_id=str(body.get("session_id") or ""),
                callback_value=str(body.get("callback_value") or body.get("code") or ""),
                state=str(body.get("state") or ""),
                admin_id=int(admin["id"]),
            )
            account = self.accounts.create({**body, "auth_type": "oauth"}, credentials)
        except ValueError as exc:
            return bad(str(exc))
        except Exception as exc:
            return bad(f"OAuth 请求失败：{exc}", 502)
        return ok({"ok": True, "account": account}, "OAuth 授权完成，凭据已加密保存")

    @PostMapping("/{account_id}/refresh")
    async def refresh_account(self, account_id: int = PathVariable(name="account_id"), authorization: str = RequestHeader(name="Authorization", required=False)):
        if not self._admin(authorization):
            return forbidden()
        try:
            refreshed = await self.accounts.refresh_account(account_id, force=True)
        except ValueError as exc:
            return bad(str(exc))
        except Exception as exc:
            return bad(f"刷新失败：{exc}", 502)
        return ok({"ok": True, "account": self.accounts.find_public(int(refreshed["account"]["id"]))}, "OAuth token 已刷新")

    @PostMapping("/{account_id}/test")
    async def test_account(self, account_id: int = PathVariable(name="account_id"), authorization: str = RequestHeader(name="Authorization", required=False)):
        if not self._admin(authorization):
            return forbidden()
        if not self.accounts.find_public(account_id):
            return not_found("订阅账号不存在")
        try:
            result = await self.gateway.test_account(account_id)
        except ValueError as exc:
            return bad(str(exc), 502)
        return ok(result, result.get("message", "连接正常"))


__all__ = ["SubscriptionAdminController"]
