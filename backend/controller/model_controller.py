from springbootai.annotations import Autowired, GetMapping, RequestHeader, RequestMapping, RestController, RequestParam

from backend.service.ai_service import AiGatewayService
from backend.service.subscription_gateway_service import SubscriptionGatewayService
from backend.service.auth_service import AuthService
from backend.service.user_group_service import UserGroupService
from backend.common.response import ok, unauthorized


@RestController
@RequestMapping("/api/models")
class ModelController:
    @Autowired
    def __init__(self, gateway: AiGatewayService, subscription_gateway: SubscriptionGatewayService, auth: AuthService, user_group_service: UserGroupService):
        self.gateway = gateway
        self.subscription_gateway = subscription_gateway
        self.auth = auth
        self.groups = user_group_service

    @GetMapping("")
    def models(self, page: int = RequestParam(name="page", required=False, default=1), page_size: int = RequestParam(name="page_size", required=False, default=12), query: str = RequestParam(name="query", required=False, default=""), group: str = RequestParam(name="group", required=False, default=""), provider: str = RequestParam(name="provider", required=False, default=""), availability: str = RequestParam(name="availability", required=False, default=""), authorization: str = RequestHeader(name="Authorization", required=False)):
        user = self.auth.user_from_authorization(authorization)
        if not user:
            return unauthorized()
        configured_models = self.gateway.catalog()
        subscription_models = self.subscription_gateway.catalog()
        by_id = {str(item.get("id")): item for item in configured_models}
        for item in subscription_models:
            by_id.setdefault(str(item.get("id")), item)
        all_models = self.groups.filter_catalog(user, list(by_id.values()))
        # Filter against the complete configured catalog before pagination.
        # The UI still receives at most five records per page.
        query_text = str(query or "").strip().lower()
        group_text = str(group or "").strip()
        provider_text = str(provider or "").strip()
        availability_text = str(availability or "").strip().lower()
        filtered_models = []
        for item in all_models:
            haystack = " ".join(str(item.get(key) or "") for key in ("id", "provider", "group", "description")).lower()
            if query_text and query_text not in haystack:
                continue
            if group_text and str(item.get("group") or "") != group_text:
                continue
            if provider_text and str(item.get("provider") or "") != provider_text:
                continue
            if availability_text == "enabled" and item.get("status") != "正常":
                continue
            if availability_text in {"disabled", "unavailable"} and item.get("status") == "正常":
                continue
            filtered_models.append(item)
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 12))
        total = len(filtered_models)
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        start = (page - 1) * page_size
        return ok({
            "ok": True,
            "default_model": self.gateway.model_name,
            "models": filtered_models[start:start + page_size],
            "filters": {
                "groups": sorted({str(item.get("group")) for item in all_models if item.get("group")}),
                "providers": sorted({str(item.get("provider")) for item in all_models if item.get("provider")}),
            },
            "pagination": {"page": page, "page_size": page_size, "total": total, "pages": pages},
        })


