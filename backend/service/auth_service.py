from springbootai import Autowired, Service
from springbootai.security.jwt_utils import jwt_utils

from backend.service.store_service import StoreService


@Service
class AuthService:
    @Autowired
    def __init__(self, store: StoreService):
        self.store = store

    def issue_token(self, user: dict, user_agent: str = "", ip_address: str = "") -> str:
        session = self.store.create_session(user["id"], user_agent, ip_address)
        payload = {
            "sub": str(user["id"]),
            "username": user["username"],
            "role": user["role"],
            "sv": int(user.get("session_version") or 0),
            "sid": session["id"],
        }
        return jwt_utils.generate_token(payload)

    def decode_token(self, token: str) -> dict | None:
        try:
            return jwt_utils.verify_token(token)
        except Exception:
            return None

    def user_from_authorization(self, authorization: str | None) -> dict | None:
        if not authorization:
            return None
        value = authorization.strip()
        if value.lower().startswith("bearer "):
            bearer_value = value[7:].strip()
            claims = self.decode_token(bearer_value)
            if claims and str(claims.get("sub", "")).isdigit():
                user = self.store.find_user(int(claims["sub"]))
                if not user or not user.get("enabled"):
                    return None
                if int(claims.get("sv") or 0) != int(user.get("session_version") or 0):
                    return None
                session_id = str(claims.get("sid") or "")
                if session_id:
                    session = self.store.find_session(user["id"], session_id)
                    if not session or session.get("revoked_at"):
                        return None
                    self.store.touch_session(user["id"], session_id)
                return user
            # API key prefixes are administrator-configurable, so do not
            # hard-code a sk- prefix here. A non-JWT bearer is an API key.
            return self.store.find_by_api_key(bearer_value)
        return self.store.find_by_api_key(value)

    def claims_from_authorization(self, authorization: str | None) -> dict | None:
        value = str(authorization or "").strip()
        if value.lower().startswith("bearer "):
            return self.decode_token(value[7:].strip())
        return None

    def require_user(self, authorization: str | None) -> dict:
        user = self.user_from_authorization(authorization)
        if not user:
            raise PermissionError("请先登录或提供有效的 API Key")
        return user

    def require_admin(self, authorization: str | None) -> dict:
        user = self.require_user(authorization)
        if user.get("role") != "ADMIN":
            raise PermissionError("仅管理员可以执行此操作")
        return user

