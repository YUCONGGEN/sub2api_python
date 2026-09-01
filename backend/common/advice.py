"""Global exception translation for annotated SpringBootAI controllers.

The protocol adapters in ``backend.controller.proxy_controller`` and
``payment_controller`` intentionally stay outside this advice because they
must return OpenAI/payment wire responses.  All regular controllers return a
framework ``Result`` and are covered here.
"""

from __future__ import annotations

from springbootai.annotations import ControllerAdvice, ExceptionHandler, ResponseStatus, Slf4j
from springbootai.web import Result


@ControllerAdvice()
@Slf4j
class GlobalExceptionHandler:
    """Map predictable Python errors to stable HTTP status codes."""

    @ExceptionHandler(ValueError)
    @ResponseStatus(400)
    def handle_value_error(self, exc: ValueError):
        self.logger.warning("请求参数错误: %s", exc)
        return Result.error(code=400, message=str(exc) or "请求参数不正确")

    @ExceptionHandler(TypeError)
    @ResponseStatus(400)
    def handle_type_error(self, exc: TypeError):
        self.logger.warning("请求类型错误: %s", exc)
        return Result.error(code=400, message="请求参数类型不正确")

    @ExceptionHandler(KeyError)
    @ResponseStatus(400)
    def handle_key_error(self, exc: KeyError):
        self.logger.warning("请求缺少字段: %s", exc)
        return Result.error(code=400, message=f"缺少必要字段: {exc.args[0] if exc.args else 'unknown'}")

    @ExceptionHandler(PermissionError)
    @ResponseStatus(403)
    def handle_permission_error(self, exc: PermissionError):
        self.logger.warning("权限拒绝: %s", exc)
        return Result.forbidden(message=str(exc) or "没有操作权限")

    @ExceptionHandler(RuntimeError)
    @ResponseStatus(500)
    def handle_runtime_error(self, exc: RuntimeError):
        self.logger.error("运行时错误: %s", exc)
        return Result.error(code=500, message="服务暂时不可用")

    @ExceptionHandler(Exception)
    @ResponseStatus(500)
    def handle_unexpected_error(self, exc: Exception):
        # 不把数据库、密钥或上游异常细节返回给客户端。
        self.logger.exception("未处理的控制器异常: %s", exc)
        return Result.error(code=500, message="服务器内部错误")


__all__ = ["GlobalExceptionHandler"]
