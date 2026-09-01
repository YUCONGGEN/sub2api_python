"""Small response helpers shared by SpringBootAI controllers.

SpringBootAI wraps every ``@RestController`` return value in ``Result``.  Keeping
the application payload in ``data`` makes the HTTP status and the JSON contract
consistent while the Vue client can continue to consume the unwrapped payload.
"""

from springbootai import Result


def as_bool(value, default: bool = False) -> bool:
    """Parse JSON booleans without treating the string ``"false"`` as true."""
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def ok(data=None, message: str = "success"):
    return Result.success(data=data, message=message)


def bad(message: str, code: int = 400):
    return Result.error(code=code, message=message)


def unauthorized(message: str = "请先登录"):
    return Result.unauthorized(message=message)


def forbidden(message: str = "需要管理员权限"):
    return Result.forbidden(message=message)


def not_found(message: str = "资源不存在"):
    return Result.not_found(message=message)
