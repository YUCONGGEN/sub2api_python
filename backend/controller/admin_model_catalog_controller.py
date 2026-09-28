"""Administrator model catalog independent from the optional monitoring API."""

from springbootai.annotations import Autowired, GetMapping, RequestHeader, RequestMapping, RestController

from backend.common.response import forbidden, ok
from backend.common.model_capabilities import known_reasoning_efforts, model_reasoning_capabilities
from backend.service.ai_service import AiGatewayService
from backend.service.auth_service import AuthService
from backend.service.subscription_gateway_service import SubscriptionGatewayService


@RestController
@RequestMapping("/api/admin/model-catalog")
class AdminModelCatalogController:
    @Autowired
    def __init__(self, auth: AuthService, gateway: AiGatewayService, subscription_gateway: SubscriptionGatewayService):
        self.auth = auth
        self.gateway = gateway
        self.subscription_gateway = subscription_gateway

    @GetMapping("")
    def catalog(self, authorization: str = RequestHeader(name="Authorization", required=False)):
        user = self.auth.user_from_authorization(authorization)
        if not user or user.get("role") != "ADMIN":
            return forbidden()
        by_id = {}
        for item in [*self.gateway.catalog(), *self.subscription_gateway.catalog()]:
            model_id = str(item.get("id") or "").strip()
            if model_id:
                by_id.setdefault(model_id, {
                    "id": model_id,
                    "provider": str(item.get("provider") or ""),
                    "group": str(item.get("group") or ""),
                    "reasoning_efforts": list(known_reasoning_efforts(model_id) or item.get("reasoning_levels") or []),
                })
        return ok({"ok": True, "models": list(by_id.values()), "model_capabilities": model_reasoning_capabilities()})


__all__ = ["AdminModelCatalogController"]
