"""Raw OpenAI wire adapters kept outside annotated Result controllers."""

from backend.protocol.openai_adapter import openai_chat, openai_models, openai_responses
from backend.protocol.subscription_adapter import anthropic_count_tokens, anthropic_messages


def register_proxy_protocol_routes(app):
    """Register exact OpenAI-compatible paths without changing payloads."""
    app.add_api_route("/v1/chat/completions", openai_chat, methods=["POST"], tags=["OpenAI Compatible"])
    app.add_api_route("/v1/responses", openai_responses, methods=["POST"], tags=["OpenAI Responses Compatible"])
    app.add_api_route("/v1/models", openai_models, methods=["GET"], tags=["OpenAI Compatible"])
    app.add_api_route("/chat/completions", openai_chat, methods=["POST"], tags=["OpenAI Compatible"])
    app.add_api_route("/responses", openai_responses, methods=["POST"], tags=["OpenAI Responses Compatible"])
    app.add_api_route("/models", openai_models, methods=["GET"], tags=["OpenAI Compatible"])
    app.add_api_route("/v1/messages", anthropic_messages, methods=["POST"], tags=["Anthropic Compatible"])
    app.add_api_route("/messages", anthropic_messages, methods=["POST"], tags=["Anthropic Compatible"])
    app.add_api_route("/v1/messages/count_tokens", anthropic_count_tokens, methods=["POST"], tags=["Anthropic Compatible"])


__all__ = ["register_proxy_protocol_routes"]
