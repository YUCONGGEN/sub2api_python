"""Thin ASGI protocol bridge to SpringBootAI-managed subscription services."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import Request
from fastapi.responses import Response, StreamingResponse

from backend.service.auth_service import AuthService
from backend.service.claude_chat_compatibility_service import ClaudeChatCompatibilityService
from backend.service.openai_chat_compatibility_service import OpenAIChatCompatibilityService
from backend.service.subscription_gateway_service import SubscriptionGatewayResponse, SubscriptionGatewayService


def _beans(request: Request) -> tuple[SubscriptionGatewayService, AuthService]:
    context = request.app.state.spring_application.application_context
    return context.get_bean("subscription_gateway_service"), context.get_bean("auth_service")


def _chat_compatibility(request: Request) -> OpenAIChatCompatibilityService:
    context = request.app.state.spring_application.application_context
    return context.get_bean("openai_chat_compatibility_service")


def _claude_chat_compatibility(request: Request) -> ClaudeChatCompatibilityService:
    context = request.app.state.spring_application.application_context
    return context.get_bean("claude_chat_compatibility_service")


def _local_authorization(request: Request) -> str:
    authorization = str(request.headers.get("Authorization") or "").strip()
    if authorization:
        return authorization
    api_key = str(request.headers.get("x-api-key") or "").strip()
    return f"Bearer {api_key}" if api_key else ""


def _error(status: int, message: str, error_type: str = "invalid_request_error", source: str = "") -> Response:
    return Response(
        content=json.dumps({"error": {"message": message, "type": error_type}}, ensure_ascii=False, separators=(",", ":")),
        status_code=status,
        headers={"X-Rose-Error-Source": source} if source else None,
        media_type="application/json",
    )


def as_response(result: SubscriptionGatewayResponse):
    if result.stream is not None:
        return StreamingResponse(
            result.stream,
            status_code=result.status_code,
            headers=result.headers,
            media_type=result.headers.get("content-type", "text/event-stream"),
        )
    return Response(
        content=result.body or b"",
        status_code=result.status_code,
        headers=result.headers,
        media_type=result.headers.get("content-type", "application/json"),
    )


async def maybe_proxy_openai_subscription(request: Request, payload: dict[str, Any], user: dict[str, Any]):
    gateway, _ = _beans(request)
    model = str(payload.get("model") or "").strip()
    if not gateway.should_route("openai", model):
        return None
    return as_response(await gateway.proxy_openai(payload, int(user["id"])))


async def maybe_proxy_openai_chat_subscription(request: Request, payload: dict[str, Any], user: dict[str, Any]):
    """Route matching Chat Completions models through the Responses account pool."""
    gateway, _ = _beans(request)
    model = str(payload.get("model") or "").strip()
    if not gateway.should_route("openai", model):
        return None
    compatibility = _chat_compatibility(request)
    try:
        outgoing = compatibility.to_responses(payload)
    except ValueError as exc:
        return _error(400, str(exc))

    result = await gateway.proxy_openai(outgoing, int(user["id"]))
    if result.status_code < 200 or result.status_code >= 300:
        return as_response(result)
    if result.stream is not None:
        if not bool(payload.get("stream")):
            try:
                body = await compatibility.from_responses_stream(result.stream, model)
            except ValueError as exc:
                return _error(502, str(exc), "upstream_error")
            encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            headers = dict(result.headers)
            headers["content-type"] = "application/json; charset=utf-8"
            return as_response(SubscriptionGatewayResponse(result.status_code, headers, body=encoded))
        stream_options = payload.get("stream_options")
        include_usage = bool(stream_options.get("include_usage")) if isinstance(stream_options, dict) else False
        headers = dict(result.headers)
        headers["content-type"] = "text/event-stream; charset=utf-8"
        headers.setdefault("cache-control", "no-cache")
        converted = SubscriptionGatewayResponse(
            status_code=result.status_code,
            headers=headers,
            stream=compatibility.stream_from_responses(
                result.stream,
                model,
                include_usage=include_usage,
            ),
        )
        return as_response(converted)
    try:
        body = compatibility.from_responses_bytes(result.body or b"", model)
    except ValueError as exc:
        return _error(502, str(exc), "upstream_error")
    encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = dict(result.headers)
    headers["content-type"] = "application/json; charset=utf-8"
    return as_response(SubscriptionGatewayResponse(result.status_code, headers, body=encoded))


async def maybe_proxy_claude_chat_subscription(request: Request, payload: dict[str, Any], user: dict[str, Any]):
    """Route matching Chat Completions models through the Claude account pool."""
    gateway, _ = _beans(request)
    model = str(payload.get("model") or "").strip()
    if not gateway.should_route("claude", model):
        return None
    compatibility = _claude_chat_compatibility(request)
    try:
        outgoing = compatibility.to_anthropic(payload)
    except ValueError as exc:
        return _error(400, str(exc))
    incoming = {key.lower(): value for key, value in request.headers.items()}
    result = await gateway.proxy_claude(outgoing, int(user["id"]), incoming)
    if result.status_code < 200 or result.status_code >= 300:
        headers = dict(result.headers)
        headers["content-type"] = "application/json; charset=utf-8"
        body = compatibility.error_from_anthropic_bytes(result.body or b"", result.status_code)
        return as_response(SubscriptionGatewayResponse(result.status_code, headers, body=body))
    if result.stream is not None:
        stream_options = payload.get("stream_options")
        include_usage = bool(stream_options.get("include_usage")) if isinstance(stream_options, dict) else False
        headers = dict(result.headers)
        headers["content-type"] = "text/event-stream; charset=utf-8"
        headers.setdefault("cache-control", "no-cache")
        converted = SubscriptionGatewayResponse(
            status_code=result.status_code,
            headers=headers,
            stream=compatibility.stream_from_anthropic(
                result.stream,
                model,
                include_usage=include_usage,
            ),
        )
        return as_response(converted)
    try:
        body = compatibility.from_anthropic_bytes(result.body or b"", model)
    except ValueError as exc:
        return _error(502, str(exc), "upstream_error")
    encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = dict(result.headers)
    headers["content-type"] = "application/json; charset=utf-8"
    return as_response(SubscriptionGatewayResponse(result.status_code, headers, body=encoded))


async def anthropic_messages(request: Request):
    gateway, auth = _beans(request)
    user = await asyncio.to_thread(auth.user_from_authorization, _local_authorization(request))
    if not user:
        return _error(401, "Invalid API key", "authentication_error", "local_auth")
    if not user.get("enabled"):
        return _error(403, "Account disabled", "permission_error")
    if not await asyncio.to_thread(gateway.store.has_usable_balance, int(user["id"])):
        return _error(402, "Insufficient balance", "insufficient_quota")
    try:
        payload = await request.json()
    except Exception:
        return _error(400, "request body must be valid JSON")
    if not isinstance(payload, dict):
        return _error(400, "request body must be a JSON object")
    model = str(payload.get("model") or "").strip()
    if not gateway.should_route("claude", model):
        return _error(503, f"没有可用于模型 {model or '<empty>'} 的 Claude 订阅账号", "service_unavailable")
    incoming = {key.lower(): value for key, value in request.headers.items()}
    result = await gateway.proxy_claude(payload, int(user["id"]), incoming)
    return as_response(result)


async def anthropic_count_tokens(request: Request):
    gateway, auth = _beans(request)
    user = await asyncio.to_thread(auth.user_from_authorization, _local_authorization(request))
    if not user:
        return _error(401, "Invalid API key", "authentication_error", "local_auth")
    if not user.get("enabled"):
        return _error(403, "Account disabled", "permission_error")
    try:
        payload = await request.json()
    except Exception:
        return _error(400, "request body must be valid JSON")
    if not isinstance(payload, dict):
        return _error(400, "request body must be a JSON object")
    model = str(payload.get("model") or "").strip()
    if not gateway.should_route("claude", model):
        return _error(503, f"没有可用于模型 {model or '<empty>'} 的 Claude 订阅账号", "service_unavailable")
    incoming = {key.lower(): value for key, value in request.headers.items()}
    result = await gateway.proxy_claude(payload, int(user["id"]), incoming, count_tokens=True)
    return as_response(result)


__all__ = [
    "anthropic_messages",
    "anthropic_count_tokens",
    "maybe_proxy_openai_subscription",
    "maybe_proxy_openai_chat_subscription",
    "maybe_proxy_claude_chat_subscription",
    "as_response",
]
