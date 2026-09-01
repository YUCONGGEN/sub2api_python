"""SpringBootAI controller for the authenticated proxy operation.

The public OpenAI wire adapter is registered separately because exact Chat
Completions/Responses payloads and SSE streams must not be wrapped in the
framework's standard ``Result`` envelope.
"""

from springbootai.annotations import Autowired, PostMapping, RequestBody, RequestHeader, RequestMapping, RestController

from backend.common.response import bad, forbidden, ok, unauthorized
from backend.service.ai_service import AiGatewayService
from backend.service.auth_service import AuthService
from backend.service.conversation_service import ConversationService


@RestController
@RequestMapping("/internal/proxy")
class ProxyController:
    """Framework-managed proxy facade used by adapters and integration tests."""

    @Autowired
    def __init__(self, ai: AiGatewayService, auth: AuthService, conversations: ConversationService):
        self.ai = ai
        self.auth = auth
        self.conversations = conversations

    @PostMapping("/invoke")
    def invoke(self, body: dict = RequestBody(), authorization: str = RequestHeader(name="Authorization", required=False)):
        """Invoke, charge, and record a request using application services."""
        user = self.auth.user_from_authorization(authorization)
        if not user:
            return unauthorized("Invalid API key")
        if not user.get("enabled"):
            return forbidden("Account disabled")
        if not self.ai.store.has_usable_balance(user["id"]):
            return bad("Insufficient balance", code=402)
        conversation = self.conversations.begin(user["id"], "internal", body, body.get("messages"))
        try:
            answer, usage, model = self.ai.invoke(body)
            charged, cost, record = self.ai.charge_and_record(user["id"], model, usage)
        except Exception as exc:
            self.conversations.fail(conversation, exc)
            raise
        if not charged:
            self.conversations.complete(conversation, {"error": {"message": "Insufficient balance"}}, "", usage, 0, "BILLING_FAILED", "Insufficient balance")
            return bad("Insufficient balance", code=402)
        self.conversations.complete(conversation, {"answer": answer, "usage": usage, "model": model}, answer, usage, cost)
        return ok({
            "answer": answer,
            "usage": usage,
            "model": model,
            "rose": {"cost_cny": cost, "usage_id": record.get("id") if record else None},
        })


def register_proxy_route(app):
    """Register raw OpenAI protocol routes outside Result wrapping."""
    from .protocol_routes import register_proxy_protocol_routes
    register_proxy_protocol_routes(app)


__all__ = ["ProxyController", "register_proxy_route"]
