"""Thin ASGI protocol bridge to SpringBootAI-managed subscription services."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from fastapi import Request
from fastapi.responses import Response, StreamingResponse

from backend.service.auth_service import AuthService
from backend.service.claude_chat_compatibility_service import ClaudeChatCompatibilityService
from backend.service.openai_chat_compatibility_service import OpenAIChatCompatibilityService
from backend.service.subscription_gateway_service import SubscriptionGatewayResponse, SubscriptionGatewayService
from backend.common.subscription_providers import CHAT_PROVIDERS, RESPONSES_PROVIDERS


def _beans(request: Request) -> tuple[SubscriptionGatewayService, AuthService]:
    context = request.app.state.spring_application.application_context
    return context.get_bean("subscription_gateway_service"), context.get_bean("auth_service")


def _chat_compatibility(request: Request) -> OpenAIChatCompatibilityService:
    context = request.app.state.spring_application.application_context
    return context.get_bean("openai_chat_compatibility_service")


def _claude_chat_compatibility(request: Request) -> ClaudeChatCompatibilityService:
    context = request.app.state.spring_application.application_context
    return context.get_bean("claude_chat_compatibility_service")


def _groups(request: Request):
    context = request.app.state.spring_application.application_context
    return context.get_bean("user_group_service")


def _local_authorization(request: Request) -> str:
    authorization = str(request.headers.get("Authorization") or "").strip()
    if authorization:
        return authorization
    api_key = str(request.headers.get("x-api-key") or "").strip()
    return f"Bearer {api_key}" if api_key else ""


async def _authenticated_user(request: Request, auth: AuthService):
    cached = request.scope.get("state", {}).get("rose_user")
    if cached:
        return cached
    return await asyncio.to_thread(auth.user_from_authorization, _local_authorization(request))


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


def _sse_frame_end(buffer: bytearray) -> tuple[int, int] | None:
    """Return the end offset and delimiter length of the next SSE frame."""
    candidates = []
    for delimiter in (b"\r\n\r\n", b"\n\n"):
        index = buffer.find(delimiter)
        if index >= 0:
            candidates.append((index, len(delimiter)))
    return min(candidates, key=lambda item: item[0]) if candidates else None


def _sse_payload(frame: bytes) -> dict[str, Any] | str | None:
    for raw_line in frame.splitlines():
        line = raw_line.strip()
        if not line.startswith(b"data:"):
            continue
        value = line[5:].strip()
        if value == b"[DONE]":
            return "[DONE]"
        try:
            payload = json.loads(value.decode("utf-8"))
        except (UnicodeDecodeError, TypeError, json.JSONDecodeError):
            continue
        return payload if isinstance(payload, dict) else None
    return None


async def _ensure_responses_completion(raw_stream, requested_model: str):
    """Preserve a Responses stream and recover a missing terminal event.

    A shared subscription can close its HTTP stream after delivering the
    answer but before sending ``response.completed``. Codex treats that as a
    transport failure. Complete streams are yielded byte-for-byte; recovery
    only runs when the upstream has sent usable Responses events but no
    terminal event.
    """
    buffer = bytearray()
    response: dict[str, Any] = {}
    text_parts: list[str] = []
    tool_items: dict[str, dict[str, Any]] = {}
    max_sequence = 0
    saw_payload = False
    terminal = False

    def observe(payload: dict[str, Any] | str | None) -> None:
        nonlocal max_sequence, saw_payload, terminal
        if payload == "[DONE]":
            return
        if not isinstance(payload, dict):
            return
        saw_payload = True
        try:
            max_sequence = max(max_sequence, int(payload.get("sequence_number") or 0))
        except (TypeError, ValueError):
            pass
        event_type = str(payload.get("type") or "")
        embedded = payload.get("response")
        if isinstance(embedded, dict):
            response.update(embedded)
        if event_type in {"response.completed", "response.done", "response.incomplete", "response.failed", "response.error", "error"}:
            terminal = True
        elif event_type in {"response.output_text.delta", "response.refusal.delta"}:
            text_parts.append(str(payload.get("delta") or ""))
        elif event_type == "response.output_text.done" and not text_parts:
            text_parts.append(str(payload.get("text") or ""))
        elif event_type in {"response.output_item.added", "response.output_item.done"}:
            item = payload.get("item")
            if isinstance(item, dict) and str(item.get("type") or "") in {"function_call", "custom_tool_call"}:
                key = str(item.get("id") or item.get("call_id") or uuid.uuid4().hex)
                current = tool_items.setdefault(key, {})
                current.update(item)

    def recovered_frame() -> bytes:
        answer = "".join(text_parts)
        body = dict(response)
        body.setdefault("id", "resp_recovered_" + uuid.uuid4().hex)
        body.setdefault("object", "response")
        body.setdefault("created_at", int(time.time()))
        body.setdefault("model", str(requested_model or ""))
        output: list[dict[str, Any]] = []
        if answer:
            output.append({
                "id": "msg_" + uuid.uuid4().hex,
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": answer, "annotations": []}],
            })
        output.extend(tool_items.values())
        body["output"] = output
        body["output_text"] = answer
        body["status"] = "completed"
        body.setdefault("usage", {})
        payload = {
            "type": "response.completed",
            "sequence_number": max_sequence + 1,
            "response": body,
        }
        return (
            b"event: response.completed\n"
            + b"data: "
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            + b"\n\n"
        )

    async def emit_frame(frame: bytes):
        nonlocal terminal
        payload = _sse_payload(frame)
        observe(payload)
        if payload == "[DONE]" and saw_payload and not terminal:
            terminal = True
            yield recovered_frame()
        yield frame

    stream_error: Exception | None = None
    try:
        async for chunk in raw_stream:
            if not chunk:
                continue
            buffer.extend(chunk)
            while True:
                found = _sse_frame_end(buffer)
                if found is None:
                    break
                offset, delimiter_length = found
                end = offset + delimiter_length
                frame = bytes(buffer[:end])
                del buffer[:end]
                async for emitted in emit_frame(frame):
                    yield emitted
    except (asyncio.CancelledError, GeneratorExit):
        raise
    except Exception as exc:
        # httpx raises a transport error when an upstream closes the socket
        # before the final SSE frame.  If a usable Responses payload already
        # reached the caller, finish that response instead of turning it into
        # a protocol-level disconnect.  An empty/invalid stream still raises
        # so genuine upstream failures remain visible to the client.
        stream_error = exc

    if buffer.strip():
        frame = bytes(buffer)
        if not frame.endswith((b"\n\n", b"\r\n\r\n")):
            frame += b"\n\n"
        async for emitted in emit_frame(frame):
            yield emitted
    if saw_payload and not terminal:
        yield recovered_frame()
        yield b"data: [DONE]\n\n"
    elif stream_error is not None and not terminal:
        raise stream_error


def _client_model_frame(frame: bytes, client_model: str) -> bytes:
    """Change only response metadata, never generated text or tool arguments."""
    lines = frame.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if not line.startswith(b"data:"):
            continue
        try:
            event = json.loads(line[5:].strip())
        except (UnicodeDecodeError, ValueError, TypeError):
            break
        if not isinstance(event, dict):
            break
        changed = False
        if isinstance(event.get("model"), str):
            event["model"] = client_model
            changed = True
        embedded = event.get("response")
        if isinstance(embedded, dict) and isinstance(embedded.get("model"), str):
            embedded["model"] = client_model
            changed = True
        if changed:
            newline = b"\r\n" if line.endswith(b"\r\n") else b"\n"
            lines[index] = b"data: " + json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + newline
        break
    return b"".join(lines)


async def _client_model_stream(raw_stream, client_model: str):
    buffer = bytearray()
    async for chunk in raw_stream:
        if not chunk:
            continue
        buffer.extend(chunk.encode("utf-8") if isinstance(chunk, str) else chunk)
        while True:
            found = _sse_frame_end(buffer)
            if found is None:
                break
            offset, delimiter_length = found
            end = offset + delimiter_length
            frame = bytes(buffer[:end])
            del buffer[:end]
            yield _client_model_frame(frame, client_model)
    if buffer:
        yield _client_model_frame(bytes(buffer), client_model)


def _client_model_response(response: Response, client_model: str, upstream_model: str) -> Response:
    if not client_model or client_model == upstream_model or not 200 <= response.status_code < 300:
        return response
    if isinstance(response, StreamingResponse):
        response.body_iterator = _client_model_stream(response.body_iterator, client_model)
        if "content-length" in response.headers:
            del response.headers["content-length"]
        return response
    try:
        body = json.loads(response.body)
    except (UnicodeDecodeError, ValueError, TypeError):
        return response
    if not isinstance(body, dict) or not isinstance(body.get("model"), str):
        return response
    body["model"] = client_model
    response.body = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    response.headers["content-length"] = str(len(response.body))
    return response


async def maybe_proxy_openai_subscription(request: Request, payload: dict[str, Any], user: dict[str, Any], client_model: str = ""):
    gateway, _ = _beans(request)
    model = str(payload.get("model") or "").strip()
    for provider in RESPONSES_PROVIDERS:
        if gateway.should_route(provider, model):
            result = await gateway.proxy_responses(provider, payload, int(user["id"]))
            if provider == "openai" and result.stream is not None:
                result.stream = _ensure_responses_completion(result.stream, model)
            return _client_model_response(as_response(result), client_model, model)
    return None


async def maybe_proxy_openai_chat_subscription(request: Request, payload: dict[str, Any], user: dict[str, Any], client_model: str = ""):
    """Route matching Chat Completions models through the Responses account pool."""
    gateway, _ = _beans(request)
    model = str(payload.get("model") or "").strip()
    provider = next((item for item in RESPONSES_PROVIDERS if gateway.should_route(item, model)), "")
    if not provider:
        return None
    compatibility = _chat_compatibility(request)
    try:
        outgoing = compatibility.to_responses(payload)
    except ValueError as exc:
        return _error(400, str(exc))

    result = await gateway.proxy_responses(provider, outgoing, int(user["id"]))
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
            return _client_model_response(as_response(SubscriptionGatewayResponse(result.status_code, headers, body=encoded)), client_model, model)
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
        return _client_model_response(as_response(converted), client_model, model)
    try:
        body = compatibility.from_responses_bytes(result.body or b"", model)
    except ValueError as exc:
        return _error(502, str(exc), "upstream_error")
    encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = dict(result.headers)
    headers["content-type"] = "application/json; charset=utf-8"
    return _client_model_response(as_response(SubscriptionGatewayResponse(result.status_code, headers, body=encoded)), client_model, model)


async def maybe_proxy_compatible_chat_subscription(request: Request, payload: dict[str, Any], user: dict[str, Any]):
    """Pass Chat Completions through API-key coding-plan account pools."""
    gateway, _ = _beans(request)
    model = str(payload.get("model") or "").strip()
    provider = next((item for item in CHAT_PROVIDERS if gateway.should_route(item, model)), "")
    if not provider:
        return None
    return as_response(await gateway.proxy_chat(provider, payload, int(user["id"])))


async def maybe_proxy_claude_chat_subscription(request: Request, payload: dict[str, Any], user: dict[str, Any]):
    """Route matching Chat Completions models through the Claude account pool."""
    gateway, _ = _beans(request)
    model = str(payload.get("model") or "").strip()
    if not _groups(request).model_allowed(user, model):
        return _error(403, f"当前用户组不允许使用模型 {model or '<empty>'}", "model_not_allowed", "user_group_policy")
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
    user = await _authenticated_user(request, auth)
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
    if not _groups(request).model_allowed(user, model):
        return _error(403, f"当前用户组不允许使用模型 {model or '<empty>'}", "model_not_allowed", "user_group_policy")
    if not gateway.should_route("claude", model):
        return _error(503, f"没有可用于模型 {model or '<empty>'} 的 Claude 订阅账号", "service_unavailable")
    incoming = {key.lower(): value for key, value in request.headers.items()}
    result = await gateway.proxy_claude(payload, int(user["id"]), incoming)
    return as_response(result)


async def anthropic_count_tokens(request: Request):
    gateway, auth = _beans(request)
    user = await _authenticated_user(request, auth)
    if not user:
        return _error(401, "Invalid API key", "authentication_error", "local_auth")
    if not user.get("enabled"):
        return _error(403, "Account disabled", "permission_error")
    if not await asyncio.to_thread(gateway.store.has_usable_balance, int(user["id"])):
        return _error(402, "Insufficient balance", "insufficient_quota", "local_billing")
    try:
        payload = await request.json()
    except Exception:
        return _error(400, "request body must be valid JSON")
    if not isinstance(payload, dict):
        return _error(400, "request body must be a JSON object")
    model = str(payload.get("model") or "").strip()
    if not _groups(request).model_allowed(user, model):
        return _error(403, f"当前用户组不允许使用模型 {model or '<empty>'}", "model_not_allowed", "user_group_policy")
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
    "maybe_proxy_compatible_chat_subscription",
    "as_response",
]
