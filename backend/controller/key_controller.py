from datetime import datetime

from springbootai.annotations import Autowired, DeleteMapping, GetMapping, PatchMapping, PostMapping, RequestBody, RequestHeader, RequestMapping, RestController, RequestParam, PathVariable

from backend.service.auth_service import AuthService
from backend.common.response import bad, ok, unauthorized


@RestController
@RequestMapping("/api/keys")
class KeyController:
    @Autowired
    def __init__(self, auth: AuthService):
        self.auth = auth
        self.store = self.auth.store

    def user(self, authorization: str | None):
        return self.auth.user_from_authorization(authorization)

    @GetMapping("")
    def keys(self, authorization: str = RequestHeader(name="Authorization", required=False), page: int = RequestParam(name="page", required=False, default=1), page_size: int = RequestParam(name="page_size", required=False, default=5)):
        user = self.user(authorization)
        if not user:
            return unauthorized()
        result = self.store.list_api_keys(user["id"], page, page_size)
        return ok({"ok": True, "keys": result["items"], "pagination": result})

    @PostMapping("")
    def create(self, body: dict = RequestBody(), authorization: str = RequestHeader(name="Authorization", required=False)):
        user = self.user(authorization)
        if not user:
            return unauthorized()
        name = str(body.get("name", "")).strip()
        if not name:
            return bad("请输入密钥名称")
        expires_at = body.get("expires_at")
        if expires_at:
            try:
                datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            except ValueError:
                return bad("过期时间格式不正确")
        try:
            key = self.store.create_api_key(user["id"], name, str(expires_at) if expires_at else None)
        except ValueError as exc:
            return bad(str(exc))
        except Exception:
            return bad("创建密钥失败，请稍后重试")
        return ok({"ok": True, "key": key})

    @PatchMapping("/{key_id}/revoke")
    def revoke(self, key_id: int = PathVariable(name="key_id"), authorization: str = RequestHeader(name="Authorization", required=False)):
        user = self.user(authorization)
        if not user:
            return unauthorized()
        key = self.store.revoke_api_key(user["id"], key_id)
        return ok({"ok": True, "key": key}, "密钥已撤销") if key else bad("密钥不存在", 404)

    @DeleteMapping("/{key_id}")
    def delete(self, key_id: int = PathVariable(name="key_id"), authorization: str = RequestHeader(name="Authorization", required=False)):
        user = self.user(authorization)
        if not user:
            return unauthorized()
        deleted = self.store.delete_api_key(user["id"], key_id)
        return ok({"ok": True}, "密钥已撤销，历史记录已保留") if deleted else bad("密钥不存在", 404)



