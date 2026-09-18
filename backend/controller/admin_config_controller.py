"""Administrator-only endpoints for application.yml maintenance."""

from springbootai.annotations import Autowired, GetMapping, PatchMapping, PostMapping, RequestBody, RequestHeader, RequestMapping, RestController

from backend.common.response import bad, forbidden, not_found, ok
from backend.service.application_config_service import (
    ApplicationConfigService,
    ConfigEditorDisabled,
    ConfigRevisionConflict,
    ConfigValidationError,
    RestartAlreadyScheduled,
    RestartUnavailable,
)
from backend.service.auth_service import AuthService


@RestController
@RequestMapping("/api/admin/application-config")
class AdminConfigController:
    @Autowired
    def __init__(self, auth: AuthService, config_service: ApplicationConfigService):
        self.auth = auth
        self.config_service = config_service

    def _is_admin(self, authorization: str | None) -> bool:
        user = self.auth.user_from_authorization(authorization)
        return bool(user and user.get("role") == "ADMIN")

    @GetMapping("")
    def read_config(self, authorization: str = RequestHeader(name="Authorization", required=False)):
        if not self._is_admin(authorization):
            return forbidden()
        try:
            return ok({"ok": True, **self.config_service.read()})
        except ConfigEditorDisabled as exc:
            return forbidden(str(exc))
        except FileNotFoundError as exc:
            return not_found(str(exc))
        except ConfigValidationError as exc:
            return bad(str(exc))

    @PatchMapping("")
    def save_config(self, body: dict = RequestBody(), authorization: str = RequestHeader(name="Authorization", required=False)):
        if not self._is_admin(authorization):
            return forbidden()
        if not isinstance(body, dict):
            return bad("请求内容格式不正确")
        try:
            saved = self.config_service.save(body.get("content"), body.get("revision", ""))
            return ok({"ok": True, **saved}, "application.yml 已保存，重启后端后生效")
        except ConfigEditorDisabled as exc:
            return forbidden(str(exc))
        except ConfigRevisionConflict as exc:
            return bad(str(exc), 409)
        except (ConfigValidationError, OSError) as exc:
            return bad(str(exc) or "保存 application.yml 失败")

    @PostMapping("/restart")
    def restart_backend(self, authorization: str = RequestHeader(name="Authorization", required=False)):
        if not self._is_admin(authorization):
            return forbidden()
        try:
            result = self.config_service.schedule_restart()
            return ok({"ok": True, **result}, "后端重启已安排")
        except ConfigEditorDisabled as exc:
            return forbidden(str(exc))
        except RestartAlreadyScheduled as exc:
            return bad(str(exc), 409)
        except RestartUnavailable as exc:
            return bad(str(exc), 503)


__all__ = ["AdminConfigController"]
