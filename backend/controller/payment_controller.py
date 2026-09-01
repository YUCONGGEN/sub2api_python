"""SpringBootAI payment controller and raw provider route registration.

Provider callbacks need signed raw bytes, form-urlencoded input, and binary QR
responses. Those protocol adapters live separately; normal payment state is a
framework-managed controller with constructor injection.
"""

from springbootai.annotations import Autowired, GetMapping, RequestMapping, RestController

from backend.common.response import ok
from backend.service.payment_service import PaymentService


@RestController
@RequestMapping("/internal/payment")
class PaymentController:
    """Framework controller for payment service health."""

    @Autowired
    def __init__(self, payment: PaymentService):
        self.payment = payment

    @GetMapping("/personal-wechat/status")
    def personal_wechat_status(self):
        return ok({"ok": True, "status": self.payment.personal_listener_status()})


def register_payment_routes(app):
    """Register provider protocol adapters outside the Result JSON contract."""
    from .payment_protocol_routes import register_payment_protocol_routes

    register_payment_protocol_routes(app)


__all__ = ["PaymentController", "register_payment_routes"]
