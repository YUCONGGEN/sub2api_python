"""SpringBootAI-managed personal WeChat listener status endpoint.

The binary QR endpoint and provider notifications stay in the protocol adapter
because their response bodies are not SpringBootAI ``Result`` JSON.  Status is a
normal application API and therefore uses the framework controller contract.
"""

from springbootai.annotations import Autowired, GetMapping, RequestMapping, RestController, RequestParam

from backend.service.payment_service import PaymentService
from backend.common.response import ok


@RestController
@RequestMapping("/api/payment/personal-wechat")
class PersonalPaymentController:
    @Autowired
    def __init__(self, payment: PaymentService):
        self.payment = payment

    @GetMapping("/status")
    def status(self):
        return ok({"ok": True, "status": self.payment.personal_listener_status()})


