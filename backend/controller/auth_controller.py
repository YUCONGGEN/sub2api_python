import re

from springbootai import get_config
from springbootai.annotations import Autowired, DeleteMapping, GetMapping, PatchMapping, PostMapping, RequestBody, RequestHeader, RequestMapping, RestController, RequestParam, PathVariable

from backend.service.store_service import StoreService
from backend.service.auth_service import AuthService
from backend.service.password_recovery_service import PasswordRecoveryService
from backend.common.response import bad, forbidden, not_found, ok, unauthorized


@RestController
@RequestMapping("/api/auth")
class AuthController:
    @Autowired
    def __init__(self, store: StoreService, auth: AuthService):
        self.store = store
        self.auth = auth
        self.password_recovery = PasswordRecoveryService(store)

    @PostMapping("/login")
    def login(self, body: dict = RequestBody(), user_agent: str = RequestHeader(name="User-Agent", required=False), forwarded_for: str = RequestHeader(name="X-Forwarded-For", required=False)):
        username = str(body.get("username", "")).strip()
        password = str(body.get("password", ""))
        user = self.store.find_by_username(username)
        if not user or not user.get("enabled") or not self.store.verify_password(password, user["password_hash"]):
            return unauthorized("账户或密码错误")
        self.store.update_login(user["id"])
        return ok({"ok": True, "token": self.auth.issue_token(user, user_agent, str(forwarded_for or "").split(",")[0].strip()), "user": self.store.public_user(user)})

    @PostMapping("/password-recovery/request")
    def request_password_recovery(self, body: dict = RequestBody()):
        username = str(body.get("username", "")).strip()
        if not username:
            return bad("请填写账户名")
        try:
            # Backward-compatible route: new requests use the same guarded
            # verification-code flow, so callers cannot bypass duplicate-send
            # protection through the legacy endpoint.
            return ok({"ok": True, **self.password_recovery.request_code(username)})
        except PermissionError as exc:
            return forbidden(str(exc))
        except ValueError as exc:
            return bad(str(exc), 429)
        except RuntimeError as exc:
            return bad(str(exc), 503)

    @PostMapping("/password-recovery/lookup")
    def lookup_password_recovery(self, body: dict = RequestBody()):
        username = str(body.get("username", "")).strip()
        if not username:
            return bad("请填写账户名")
        try:
            return ok({"ok": True, **self.password_recovery.lookup_account(username)})
        except PermissionError as exc:
            return forbidden(str(exc))

    @PostMapping("/password-recovery/request-code")
    def request_password_recovery_code(self, body: dict = RequestBody()):
        username = str(body.get("username", "")).strip()
        if not username:
            return bad("请填写账户名")
        try:
            return ok({"ok": True, **self.password_recovery.request_code(username)})
        except PermissionError as exc:
            return forbidden(str(exc))
        except ValueError as exc:
            return bad(str(exc), 429)
        except RuntimeError as exc:
            return bad(str(exc), 503)

    @PostMapping("/password-recovery/verify-code")
    def verify_password_recovery_code(self, body: dict = RequestBody()):
        username = str(body.get("username", "")).strip()
        code = str(body.get("code", "")).strip()
        new_password = str(body.get("new_password", ""))
        if not username:
            return bad("请填写账户名")
        if not re.fullmatch(r"\d{6}", code):
            return bad("请输入 6 位数字验证码")
        if len(new_password) < 6 or len(new_password) > 128:
            return bad("新密码长度需为 6-128 位")
        try:
            user = self.password_recovery.verify_code(username, code)
        except PermissionError as exc:
            return forbidden(str(exc))
        except (ValueError, RuntimeError) as exc:
            return bad(str(exc), 400)
        self.store.update_password(user["id"], self.store.hash_password(new_password))
        return ok({"ok": True}, "密码已重置，请使用新密码登录")

    @PostMapping("/password-recovery/contact")
    def contact_password_recovery(self, body: dict = RequestBody()):
        username = str(body.get("username", "")).strip()
        name = str(body.get("name", "")).strip()
        organization = str(body.get("organization", "")).strip()
        if not username:
            return bad("请填写账户名")
        if len(name) < 2 or len(name) > 64:
            return bad("请填写 2-64 个字符的姓名")
        if len(organization) > 128:
            return bad("公司或学校不能超过 128 个字符")
        try:
            result = self.password_recovery.contact_admin(username, name, organization)
            return ok({"ok": True, **result})
        except PermissionError as exc:
            return forbidden(str(exc))
        except ValueError as exc:
            return bad(str(exc), 429)
        except RuntimeError as exc:
            return bad(str(exc), 503)

    @PostMapping("/password-recovery/reset")
    def reset_password(self, body: dict = RequestBody()):
        token = str(body.get("token", "")).strip()
        new_password = str(body.get("new_password", ""))
        if len(new_password) < 6 or len(new_password) > 128:
            return bad("新密码长度需为 6-128 位")
        if not self.password_recovery.enabled():
            return forbidden("密码找回功能当前未开启")
        try:
            user = self.password_recovery.verify_token(token)
        except (ValueError, RuntimeError) as exc:
            return bad(str(exc), 400)
        self.store.update_password(user["id"], self.store.hash_password(new_password))
        return ok({"ok": True}, "密码已重置，请使用新密码登录")

    @PostMapping("/register")
    def register(self, body: dict = RequestBody(), user_agent: str = RequestHeader(name="User-Agent", required=False), forwarded_for: str = RequestHeader(name="X-Forwarded-For", required=False)):
        username = str(body.get("username", "")).strip()
        password = str(body.get("password", ""))
        email = str(body.get("email", "")).strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
            return bad("账户名需为 3-32 位字母、数字、下划线、中划线或点")
        if len(password) < 6 or len(password) > 128:
            return bad("密码长度需为 6-128 位")
        if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            return bad("邮箱格式不正确")
        if self.store.find_by_username(username):
            return bad("账户已存在", 409)
        try:
            user, default_key = self.store.create_user_with_default_key(username, password, email)
        except Exception:
            return bad("注册失败，请更换账户名", 409)
        return ok({
            "ok": True,
            "token": self.auth.issue_token(user, user_agent, str(forwarded_for or "").split(",")[0].strip()),
            "user": self.store.public_user(user),
            # Plaintext is returned once and is never stored server-side.
            "api_key": default_key.get("api_key"),
            "api_key_id": default_key.get("id"),
        })

    @GetMapping("/me")
    def me(self, authorization: str = RequestHeader(name="Authorization", required=False)):
        user = self.auth.user_from_authorization(authorization)
        if not user:
            return unauthorized()
        return ok({"ok": True, "user": self.store.public_user(user)})

    @GetMapping("/announcement")
    def announcement(self, authorization: str = RequestHeader(name="Authorization", required=False)):
        user = self.auth.user_from_authorization(authorization)
        if not user:
            return unauthorized()
        return ok({
            "ok": True,
            "announcement": self.store.current_announcement(int(user["id"])),
        })

    @PostMapping("/announcement/{announcement_id}/acknowledge")
    def acknowledge_announcement(self, announcement_id: int = PathVariable(name="announcement_id"), authorization: str = RequestHeader(name="Authorization", required=False)):
        user = self.auth.user_from_authorization(authorization)
        if not user:
            return unauthorized()
        if not self.store.acknowledge_announcement(int(announcement_id), int(user["id"])):
            return not_found("公示已撤下或已被新公示替换")
        return ok({"ok": True}, "已确认公示")

    @staticmethod
    def _group_overview_visible(user: dict) -> bool:
        if str(user.get("role") or "").upper() == "ADMIN":
            return True
        cfg = get_config().get("rose", {}).get("user-groups", {})
        value = cfg.get("overview-visible-to-users", True) if isinstance(cfg, dict) else True
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @staticmethod
    def _group_members_visible(user: dict) -> bool:
        if str(user.get("role") or "").upper() == "ADMIN":
            return True
        cfg = get_config().get("rose", {}).get("user-groups", {})
        value = cfg.get("member-usage-visible-to-users", True) if isinstance(cfg, dict) else True
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @GetMapping("/group-overview")
    def group_overview(self, authorization: str = RequestHeader(name="Authorization", required=False), page: int = RequestParam(name="page", required=False, default=1), page_size: int = RequestParam(name="page_size", required=False, default=5)):
        user = self.auth.user_from_authorization(authorization)
        if not user:
            return unauthorized()
        if not self._group_overview_visible(user):
            return forbidden("所在分组概况当前仅管理员可见")
        group_id = user.get("effective_group_id") or user.get("group_id")
        if not group_id:
            return not_found("当前账号未加入用户分组")
        page = page if isinstance(page, (int, str)) else 1
        page_size = page_size if isinstance(page_size, (int, str)) else 5
        detail = self.store.user_group_overview(
            int(group_id), page, page_size,
            include_members=self._group_members_visible(user),
        )
        if detail is None:
            return not_found("所在分组不存在或已删除")
        is_admin = str(user.get("role") or "").upper() == "ADMIN"
        if not is_admin:
            group = detail.get("group") or {}
            detail["group"] = {key: group.get(key) for key in ("id", "name")}
            return ok({"ok": True, **detail})
        return ok({"ok": True, **detail,
                   "group_source": user.get("group_source") or "ASSIGNED",
                   "group_source_plan_name": user.get("group_source_plan_name")})

    @PostMapping("/rotate-key")
    def rotate_key(self, authorization: str = RequestHeader(name="Authorization", required=False)):
        return bad("首枚默认密钥只在注册时生成一次；如需更换，请到 API 密钥页面创建命名密钥", 410)

    @PatchMapping("/profile")
    def profile(self, body: dict = RequestBody(), authorization: str = RequestHeader(name="Authorization", required=False)):
        user = self.auth.user_from_authorization(authorization)
        if not user:
            return unauthorized()
        email = str(body.get("email", "")).strip()
        if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            return bad("邮箱格式不正确")
        updated = self.store.update_user(user["id"], email=email)
        return ok({"ok": True, "user": updated})

    @PostMapping("/password")
    def password(self, body: dict = RequestBody(), authorization: str = RequestHeader(name="Authorization", required=False)):
        user = self.auth.user_from_authorization(authorization)
        if not user:
            return unauthorized()
        current = str(body.get("current_password", ""))
        new = str(body.get("new_password", ""))
        if not self.store.verify_password(current, user["password_hash"]):
            return bad("当前密码不正确", 403)
        if len(new) < 6 or len(new) > 128:
            return bad("新密码长度需为 6-128 位")
        self.store.update_password(user["id"], self.store.hash_password(new))
        return ok({"ok": True}, "密码已更新，请重新登录")

    @GetMapping("/sessions")
    def sessions(self, authorization: str = RequestHeader(name="Authorization", required=False), page: int = RequestParam(name="page", required=False, default=1), page_size: int = RequestParam(name="page_size", required=False, default=5)):
        user = self.auth.user_from_authorization(authorization)
        claims = self.auth.claims_from_authorization(authorization)
        if not user:
            return unauthorized()
        # Tokens issued before session tracking was introduced remain valid
        # until their normal expiry. Do not turn a harmless device-list visit
        # into a forced logout during a rolling deployment.
        if not claims or not claims.get("sid"):
            return ok({"ok": True, "sessions": [], "legacy_session": True, "pagination": {"page": 1, "page_size": 5, "total": 0, "pages": 1}})
        result = self.store.list_sessions(user["id"], str(claims["sid"]), int(page), int(page_size))
        return ok({"ok": True, **result})

    @DeleteMapping("/sessions/{session_id}")
    def revoke_session(self, session_id: str = PathVariable(name="session_id"), authorization: str = RequestHeader(name="Authorization", required=False)):
        user = self.auth.user_from_authorization(authorization)
        if not user:
            return unauthorized()
        return ok({"ok": True}, "会话已退出") if self.store.revoke_session(user["id"], session_id) else bad("会话不存在", 404)

    @PostMapping("/sessions/revoke-others")
    def revoke_other_sessions(self, authorization: str = RequestHeader(name="Authorization", required=False)):
        user = self.auth.user_from_authorization(authorization)
        claims = self.auth.claims_from_authorization(authorization)
        if not user:
            return unauthorized()
        if not claims or not claims.get("sid"):
            return bad("当前登录创建于设备管理上线前，请重新登录后再使用此功能", 409)
        count = self.store.revoke_other_sessions(user["id"], str(claims["sid"]))
        return ok({"ok": True, "revoked": count}, "其他设备已退出")

    @PostMapping("/logout")
    def logout(self, authorization: str = RequestHeader(name="Authorization", required=False)):
        user = self.auth.user_from_authorization(authorization)
        claims = self.auth.claims_from_authorization(authorization)
        if user and claims and claims.get("sid"):
            self.store.revoke_session(user["id"], str(claims["sid"]))
        return ok({"ok": True}, "已退出登录")





