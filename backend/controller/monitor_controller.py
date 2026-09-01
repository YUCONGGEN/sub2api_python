from datetime import datetime, timedelta, timezone

from springbootai.annotations import Autowired, GetMapping, RequestHeader, RequestMapping, RestController, RequestParam

from backend.service.ai_service import AiGatewayService
from backend.service.auth_service import AuthService
from backend.service.subscription_gateway_service import SubscriptionGatewayService
from backend.common.response import ok, unauthorized


@RestController
@RequestMapping("/api/monitoring")
class MonitorController:
    @Autowired
    def __init__(
        self,
        auth: AuthService,
        gateway: AiGatewayService,
        subscription_gateway: SubscriptionGatewayService,
    ):
        self.auth = auth
        self.store = self.auth.store
        self.gateway = gateway
        self.subscription_gateway = subscription_gateway

    @GetMapping("")
    def overview(self, authorization: str = RequestHeader(name="Authorization", required=False), page: int = RequestParam(name="page", required=False, default=1), page_size: int = RequestParam(name="page_size", required=False, default=12)):
        user = self.auth.user_from_authorization(authorization)
        if not user:
            return unauthorized()
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        rows = self.store.usage_by_model_since(user["id"], since)
        stats = {row["model"]: row for row in rows}
        configured_models = self.gateway.catalog()
        subscription_models = self.subscription_gateway.catalog()
        by_id = {str(item.get("id")): item for item in configured_models}
        for item in subscription_models:
            by_id.setdefault(str(item.get("id")), item)
        models = []
        for item in by_id.values():
            row = stats.get(item["id"], {})
            models.append({
                **item,
                "requests_24h": row.get("requests", 0),
                "tokens_24h": row.get("tokens", 0),
                "cost_24h": row.get("cost", 0),
            })
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 12))
        total = len(models)
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        start = (page - 1) * page_size
        return ok({"ok": True, "updated_at": datetime.now(timezone.utc).isoformat(),
                "models": models[start:start + page_size],
                "pagination": {"page": page, "page_size": page_size, "total": total, "pages": pages},
                "summary": {"total": total, "available": sum(1 for item in models if item["status"] == "正常")}})


