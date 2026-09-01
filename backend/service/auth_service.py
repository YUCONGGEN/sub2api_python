from springbootai import Autowired, Service
from springbootai.security.jwt_utils import jwt_utils

from backend.service.store_service import StoreService


@Service
class AuthService:
    @Autowired
    def __init__(self, store: StoreService):
        self.store = store

    def issue_token(self, user: dict) -> str:
        payload = {
            "sub": str(user["id"]),
            "username": user["username"],
            "role": user["role"],
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
            if bearer_value.startswith("sk-"):
                return self.store.find_by_api_key(bearer_value)
            claims = self.decode_token(bearer_value)
            if claims and str(claims.get("sub", "")).isdigit():
                user = self.store.find_user(int(claims["sub"]))
                return user if user and user.get("enabled") else None
            return None
        if value.startswith("sk-"):
            return self.store.find_by_api_key(value)
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

