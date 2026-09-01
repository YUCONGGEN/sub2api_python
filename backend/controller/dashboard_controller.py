from springbootai.annotations import Autowired, GetMapping, RequestHeader, RequestMapping, RestController, RequestParam

from backend.service.auth_service import AuthService
from backend.common.response import ok, unauthorized


@RestController
@RequestMapping("/api/dashboard")
class DashboardController:
    @Autowired
    def __init__(self, auth: AuthService):
        self.auth = auth
        self.store = self.auth.store

    @GetMapping("/summary")
    def summary(self, authorization: str = RequestHeader(name="Authorization", required=False), page: int = RequestParam(name="page", required=False, default=1), page_size: int = RequestParam(name="page_size", required=False, default=5)):
        user = self.auth.user_from_authorization(authorization)
        if not user:
            return unauthorized()
        return ok({"ok": True, "user": self.store.public_user(user), "usage": self.store.usage_summary(user["id"], page, page_size)})


