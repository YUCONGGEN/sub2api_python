from collections import deque
from datetime import datetime, timedelta, timezone

from springbootai.annotations import Autowired, GetMapping, RequestHeader, RequestMapping, RestController, RequestParam

from backend.service.ai_service import AiGatewayService
from backend.service.auth_service import AuthService
from backend.service.observability_service import ObservabilityService
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
        observability_service: ObservabilityService = None,
    ):
        self.auth = auth
        self.store = self.auth.store
        self.gateway = gateway
        self.subscription_gateway = subscription_gateway
        self.observability = observability_service
        self._health_history: dict[str, deque[dict]] = {}

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
        observed_at = datetime.now(timezone.utc).isoformat()
        for item in by_id.values():
            row = stats.get(item["id"], {})
            model_id = str(item["id"])
            history = self._health_history.setdefault(model_id, deque(maxlen=12))
            healthy = item.get("status") == "正常"
            history.append({"ok": healthy, "at": observed_at})
            models.append({
                **item,
                "requests_24h": row.get("requests", 0),
                "tokens_24h": row.get("tokens", 0),
                "cost_24h": row.get("cost", 0),
                "health_history": list(history),
            })
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 12))
        total = len(models)
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        start = (page - 1) * page_size
        gateway_metrics = self.subscription_gateway.metrics() if hasattr(self.subscription_gateway, "metrics") else {}
        runtime = self.observability.request_metrics() if self.observability else {}
        summary = {
            "total": total,
            "available": sum(1 for item in models if item["status"] == "正常"),
            "requests_24h": sum(int(item.get("requests_24h") or 0) for item in models),
            "tokens_24h": sum(int(item.get("tokens_24h") or 0) for item in models),
            "cost_24h": round(sum(float(item.get("cost_24h") or 0) for item in models), 8),
            "overall_status": "正常" if models and all(item["status"] == "正常" for item in models) else "部分异常",
            "p95_latency_ms": runtime.get("p95_latency_ms", 0),
            "rate_limited_24h": runtime.get("rate_limited_24h_runtime", 0),
            "gateway": gateway_metrics,
        }
        return ok({"ok": True, "updated_at": observed_at,
                "models": models[start:start + page_size],
                "pagination": {"page": page, "page_size": page_size, "total": total, "pages": pages},
                "summary": summary})


