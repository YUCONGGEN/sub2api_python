import re

from springbootai.annotations import Autowired, GetMapping, PatchMapping, PostMapping, RequestBody, RequestHeader, RequestMapping, RestController, RequestParam, PathVariable

from backend.service.store_service import StoreService
from backend.service.auth_service import AuthService
from backend.common.response import bad, ok, unauthorized


@RestController
@RequestMapping("/api/auth")
class AuthController:
    @Autowired
    def __init__(self, store: StoreService, auth: AuthService):
        self.store = store
        self.auth = auth

    @PostMapping("/login")
    def login(self, body: dict = RequestBody()):
        username = str(body.get("username", "")).strip()
        password = str(body.get("password", ""))
        user = self.store.find_by_username(username)
        if not user or not user.get("enabled") or not self.store.verify_password(password, user["password_hash"]):
            return unauthorized("账户或密码错误")
        self.store.update_login(user["id"])
        return ok({"ok": True, "token": self.auth.issue_token(user), "user": self.store.public_user(user)})

    @PostMapping("/register")
    def register(self, body: dict = RequestBody()):
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
            user = self.store.create_user(username, password, email)
        except Exception:
            return bad("注册失败，请更换账户名", 409)
        return ok({"ok": True, "token": self.auth.issue_token(user), "user": self.store.public_user(user)})

    @GetMapping("/me")
    def me(self, authorization: str = RequestHeader(name="Authorization", required=False)):
        user = self.auth.user_from_authorization(authorization)
        if not user:
            return unauthorized()
        return ok({"ok": True, "user": self.store.public_user(user)})

    @PostMapping("/rotate-key")
    def rotate_key(self, authorization: str = RequestHeader(name="Authorization", required=False)):
        return bad("系统不再提供默认密钥，请到 API 密钥页面创建命名密钥", 410)

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





