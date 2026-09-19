from springbootai import get_config
from springbootai.annotations import Autowired, GetMapping, RequestHeader, RequestMapping, RestController, RequestParam

from backend.service.auth_service import AuthService
from backend.service.observability_service import ObservabilityService
from backend.common.response import forbidden, ok, unauthorized


@RestController
@RequestMapping("/api/dashboard")
class DashboardController:
    @Autowired
    def __init__(self, auth: AuthService, observability_service: ObservabilityService):
        self.auth = auth
        self.store = self.auth.store
        self.observability = observability_service

    @staticmethod
    def _visualization_visible(user: dict) -> bool:
        if str(user.get("role") or "").upper() == "ADMIN":
            return True
        cfg = get_config().get("rose", {}).get("data-visualization", {})
        value = cfg.get("visible-to-users", True) if isinstance(cfg, dict) else True
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @GetMapping("/summary")
    def summary(self, authorization: str = RequestHeader(name="Authorization", required=False), page: int = RequestParam(name="page", required=False, default=1), page_size: int = RequestParam(name="page_size", required=False, default=5)):
        user = self.auth.user_from_authorization(authorization)
        if not user:
            return unauthorized()
        return ok({"ok": True, "user": self.store.public_user(user), "usage": self.store.usage_summary(user["id"], page, page_size)})

    @GetMapping("/visualization")
    def visualization(self, authorization: str = RequestHeader(name="Authorization", required=False)):
        user = self.auth.user_from_authorization(authorization)
        if not user:
            return unauthorized()
        if not self._visualization_visible(user):
            return forbidden("数据可视化当前仅管理员可见")
        return ok({
            "ok": True,
            "analytics": self.observability.public_analytics(),
            "model_usage": self.store.model_usage_summary(),
        })


