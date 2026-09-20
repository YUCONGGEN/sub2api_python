"""Administrator-only proxy subscription and failover policy API."""

import httpx

from springbootai.annotations import Autowired, DeleteMapping, GetMapping, PatchMapping, PathVariable, PostMapping, RequestBody, RequestHeader, RequestMapping, RestController

from backend.common.response import bad, forbidden, not_found, ok
from backend.service.auth_service import AuthService
from backend.service.proxy_pool_admin_service import ProxyPoolAdminDisabled, ProxyPoolAdminService


@RestController
@RequestMapping("/api/admin/proxy-pool")
class ProxyPoolAdminController:
    @Autowired
    def __init__(self, auth: AuthService, proxy_pool: ProxyPoolAdminService):
        self.auth = auth
        self.proxy_pool = proxy_pool

    def _admin(self, authorization: str | None) -> bool:
        user = self.auth.user_from_authorization(authorization)
        return bool(user and user.get("role") == "ADMIN")

    @GetMapping("")
    def snapshot(self, authorization: str = RequestHeader(name="Authorization", required=False)):
        if not self._admin(authorization):
            return forbidden()
        try:
            return ok({"ok": True, **self.proxy_pool.snapshot()})
        except ProxyPoolAdminDisabled as exc:
            return forbidden(str(exc))
        except (ValueError, OSError) as exc:
            return bad(str(exc), 503)

    @PatchMapping("/policy")
    def update_policy(self, body: dict = RequestBody(), authorization: str = RequestHeader(name="Authorization", required=False)):
        if not self._admin(authorization):
            return forbidden()
        try:
            return ok({"ok": True, **self.proxy_pool.update_policy(body)}, "代理池规则已保存，将在下一轮检查时生效")
        except ProxyPoolAdminDisabled as exc:
            return forbidden(str(exc))
        except (ValueError, OSError) as exc:
            return bad(str(exc))

    @PostMapping("/subscriptions")
    async def add_subscription(self, body: dict = RequestBody(), authorization: str = RequestHeader(name="Authorization", required=False)):
        if not self._admin(authorization):
            return forbidden()
        try:
            source = await self.proxy_pool.add_subscription(body if isinstance(body, dict) else {})
            return ok({"ok": True, "subscription": source}, "订阅连通性验证通过并已加密保存")
        except ProxyPoolAdminDisabled as exc:
            return forbidden(str(exc))
        except (ValueError, httpx.HTTPError, OSError) as exc:
            return bad(str(exc), 502)

    @PostMapping("/subscriptions/{source_id}/check")
    async def check_subscription(self, source_id: str = PathVariable(name="source_id"), authorization: str = RequestHeader(name="Authorization", required=False)):
        if not self._admin(authorization):
            return forbidden()
        try:
            source = await self.proxy_pool.check_subscription(source_id)
            return ok({"ok": True, "subscription": source}, "订阅检查完成")
        except ProxyPoolAdminDisabled as exc:
            return forbidden(str(exc))
        except KeyError:
            return not_found("代理订阅不存在")
        except (ValueError, OSError) as exc:
            return bad(str(exc), 502)

    @DeleteMapping("/subscriptions/{source_id}")
    def delete_subscription(self, source_id: str = PathVariable(name="source_id"), authorization: str = RequestHeader(name="Authorization", required=False)):
        if not self._admin(authorization):
            return forbidden()
        try:
            deleted = self.proxy_pool.delete_subscription(source_id)
        except ProxyPoolAdminDisabled as exc:
            return forbidden(str(exc))
        except (ValueError, OSError) as exc:
            return bad(str(exc))
        return ok({"ok": True}, "代理订阅已删除") if deleted else not_found("代理订阅不存在")


__all__ = ["ProxyPoolAdminController"]
