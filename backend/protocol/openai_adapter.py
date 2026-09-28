from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from backend.common.routing_trace import routing_scope, record_mapping

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

from backend.service.ai_service import AiGatewayService, UpstreamRequestError
from backend.service.auth_service import AuthService
from backend.service.conversation_service import ConversationService
from backend.common.multimodal import (
    has_dsml_tool_call_marker,
    is_dsml_tool_call_prefix,
    normalize_chat_messages,
    normalize_content,
    parse_dsml_tool_calls,
)
from backend.protocol.subscription_adapter import (
    _client_model_response,
    maybe_proxy_claude_chat_subscription,
    maybe_proxy_compatible_chat_subscription,
    maybe_proxy_openai_chat_subscription,
    maybe_proxy_openai_subscription,
)


logger = logging.getLogger(__name__)


def _beans(request: Request) -> tuple[AiGatewayService, AuthService, ConversationService]:
    """Resolve SpringBootAI-managed services for the raw OpenAI protocol."""
    context = request.app.state.spring_application.application_context
    return context.get_bean("ai_gateway_service"), context.get_bean("auth_service"), context.get_bean("conversation_service")


def _responses_event(event_type: str, payload: dict) -> str:
    """Encode one OpenAI Responses SSE event without JSON wrapping."""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event_type}\ndata: {body}\n\n"


def _group_service(request: Request):
    context = request.app.state.spring_application.application_context
    return context.get_bean("user_group_service")


def _subscription_gateway(request: Request):
    """Resolve the shared gateway used only for live API activity telemetry."""
    context = request.app.state.spring_application.application_context
    return context.get_bean("subscription_gateway_service")


def _direct_api_activity(request: Request, service: AiGatewayService, payload: dict, user: dict, request_id: str, endpoint: str) -> str:
    """Start a live record for a configured YAML/API upstream request."""
    try:
        gateway = _subscription_gateway(request)
        begin = getattr(gateway, "begin_api_activity", None)
        if not callable(begin):
            return ""
        requested_model = str(payload.get("model") or service.model_name)
        # A subscription fallback keeps the client-facing model unchanged but
        # stores the effective direct model in ``_rose_model_id``.  Use that
        # model for telemetry, otherwise a DeepSeek Chat fallback is shown as
        # an anonymous OpenAI request and appears not to have been called.
        effective_id = str(payload.get("_rose_model_id") or requested_model)
        spec = getattr(service, "models", {}).get(effective_id, {})
        if not isinstance(spec, dict):
            spec = {}
        model = str(
            payload.get("_rose_upstream_model")
            or spec.get("upstream-model")
            or effective_id
            or requested_model
        )
        reasoning = payload.get("reasoning") if isinstance(payload.get("reasoning"), dict) else {}
        effort = reasoning.get("effort") or payload.get("reasoning_effort") or payload.get("reasoning-effort") or ""
        return str(begin(
            str(spec.get("provider") or "OpenAI API"), model, int(user["id"]),
            str(effort), endpoint, request_id,
        ) or "")
    except Exception:
        # Telemetry must never affect a valid upstream request.
        return ""


def _finish_direct_api_activity(request: Request, activity_id: str) -> None:
    if not activity_id:
        return
    try:
        finish = getattr(_subscription_gateway(request), "finish_api_activity", None)
        if callable(finish):
            finish(activity_id)
    except Exception:
        pass


async def _tracked_stream(stream, request: Request, activity_id: str):
    try:
        async for chunk in stream:
            yield chunk
    finally:
        _finish_direct_api_activity(request, activity_id)


async def _authenticated_user(request: Request, auth: AuthService):
    cached = request.scope.get("state", {}).get("rose_user")
    if cached:
        return cached
    authorization = request.headers.get("Authorization")
    if not authorization and request.headers.get("x-api-key"):
        authorization = f"Bearer {request.headers['x-api-key']}"
    return await asyncio.to_thread(auth.user_from_authorization, authorization)


def _model_permission_error(request: Request, user: dict, model: str):
    if _group_service(request).model_allowed(user, model):
        return None
    return _local_error(
        403,
        f"当前用户组不允许使用模型 {model or '<empty>'}",
        "model_not_allowed",
        "user_group_policy",
    )


def _estimate_payload_tokens(service: AiGatewayService, payload: dict, field: str) -> int:
    try:
        value = json.dumps(payload.get(field), ensure_ascii=False, separators=(",", ":"))
        return max(1, int(service.estimate_tokens(value)))
    except Exception:
        return 1


def _local_error(status: int, message: str, error_type: str, source: str) -> JSONResponse:
    """Mark gateway-owned failures so clients can distinguish them upstream."""
    return JSONResponse(
        {"error": {"message": message, "type": error_type}},
        status_code=status,
        headers={"X-Rose-Error-Source": source},
    )


@routing_scope
async def openai_chat(request: Request):
    service, auth, conversations = _beans(request)
    user = await _authenticated_user(request, auth)
    if not user:
        return _local_error(401, "Invalid API key", "authentication_error", "local_auth")
    if not user.get("enabled"):
        return _local_error(403, "Account disabled", "permission_error", "local_auth")
    if not await asyncio.to_thread(service.store.has_usable_balance, user["id"]):
        return _local_error(402, "Insufficient balance", "insufficient_quota", "local_billing")
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": {"message": "request body must be valid JSON", "type": "invalid_request_error"}}, status_code=400)
    if not isinstance(payload, dict) or not payload.get("messages"):
        return JSONResponse({"error": {"message": "messages is required", "type": "invalid_request_error"}}, status_code=400)
    model_error = _model_permission_error(request, user, str(payload.get("model") or service.model_name))
    if model_error is not None:
        return model_error
    client_model = str(payload.get("model") or service.model_name)
    original_payload = payload
    payload, mapping = _group_service(request).apply_model_mapping(user, payload)
    record_mapping(user, original_payload, mapping)
    subscription_response = await maybe_proxy_openai_chat_subscription(request, payload, user, client_model)
    if subscription_response is not None:
        return subscription_response
    subscription_response = await maybe_proxy_claude_chat_subscription(request, payload, user)
    if subscription_response is not None:
        return subscription_response
    subscription_response = await maybe_proxy_compatible_chat_subscription(request, payload, user)
    if subscription_response is not None:
        return subscription_response
    payload = service.apply_openai_subscription_fallback(payload)
    request_id = "chatcmpl-" + uuid.uuid4().hex
    direct_activity_id = _direct_api_activity(request, service, payload, user, request_id, "chat.completions")
    is_stream = bool(payload.get("stream"))
    # Persist only usage metadata.  Run the small bookkeeping insert in the
    # default executor so it overlaps the upstream network wait and cannot
    # hold back the first streamed token (especially with a remote MySQL DB).
    conversation_task = asyncio.create_task(asyncio.to_thread(
        conversations.begin, user["id"], "chat_completions",
        {"model": payload.get("model")}, None, request_id,
    ))
    conversation = None

    async def resolve_conversation():
        nonlocal conversation
        if conversation is not None:
            return conversation
        try:
            conversation = await conversation_task
        except (Exception, asyncio.CancelledError):
            conversation = None
        return conversation

    async def fail_conversation(error):
        """Finish the deferred bookkeeping row without delaying success paths."""
        try:
            row = await resolve_conversation()
        except Exception:
            row = None
        await asyncio.to_thread(conversations.fail, row, error)

    async def complete_conversation(row, response, answer, usage, cost, status="SUCCEEDED", error_message=None):
        """Keep usage bookkeeping off the ASGI event loop."""
        await asyncio.to_thread(
            conversations.complete, row, response, answer, usage, cost,
            status, error_message,
        )
    try:
        if payload.get("stream"):
            async def events():
                iterator = service.astream_with_trace(payload)
                sentinel = object()
                answer_parts = []
                pending_text = ""
                dsml_mode = False
                streamed_tool_calls = []
                stream_finish_reason = None
                prompt_tokens = 0
                model = str(payload.get("model") or service.model_name)
                metadata = {}
                billing_started = False
                try:
                    first = True
                    while True:
                        event = await anext(iterator, sentinel)
                        if event is sentinel:
                            break
                        delta = str(event.get("delta") or "")
                        tool_call_delta = event.get("tool_call_delta") or []
                        finish_reason = event.get("finish_reason")
                        if finish_reason:
                            stream_finish_reason = str(finish_reason)
                        model = str(event.get("model") or model)
                        prompt_tokens = int(event.get("prompt_tokens") or prompt_tokens)
                        metadata = event.get("metadata") or metadata
                        if event.get("tool_calls"):
                            streamed_tool_calls = event.get("tool_calls")
                        if delta:
                            answer_parts.append(delta)
                            if not dsml_mode:
                                pending_text += delta
                                if has_dsml_tool_call_marker(pending_text):
                                    dsml_mode = True
                                    delta = ""
                                elif is_dsml_tool_call_prefix(pending_text):
                                    # Hold only a possible marker prefix; a
                                    # normal answer remains genuinely streamed.
                                    continue
                                else:
                                    delta = pending_text
                                    pending_text = ""
                            else:
                                continue
                        if delta or tool_call_delta:
                            delta_body = {"role": "assistant"} if first else {}
                            if delta:
                                delta_body["content"] = delta
                            if tool_call_delta:
                                delta_body["tool_calls"] = tool_call_delta
                            first = False
                            if finish_reason:
                                stream_finish_reason = str(finish_reason)
                            body = {
                                "id": request_id,
                                "object": "chat.completion.chunk",
                                "created": int(time.time()),
                                "model": model,
                                "choices": [{"index": 0, "delta": delta_body, "finish_reason": finish_reason}],
                            }
                            yield f"data: {json.dumps(body, ensure_ascii=False)}\n\n"
                    answer = "".join(answer_parts)
                    answer, dsml_tool_calls = parse_dsml_tool_calls(answer)
                    if dsml_tool_calls:
                        streamed_tool_calls = dsml_tool_calls
                        # Do not expose provider-specific markup to clients.
                        delta = {"tool_calls": dsml_tool_calls}
                        if first:
                            delta["role"] = "assistant"
                            first = False
                        tool_body = {
                            "id": request_id,
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": model,
                            "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
                        }
                        yield f"data: {json.dumps(tool_body, ensure_ascii=False)}\n\n"
                        stream_finish_reason = "tool_calls"
                    elif pending_text:
                        body = {"id": request_id, "object": "chat.completion.chunk", "created": int(time.time()), "model": model, "choices": [{"index": 0, "delta": {"content": pending_text}, "finish_reason": None}]}
                        yield f"data: {json.dumps(body, ensure_ascii=False)}\n\n"
                    # Some providers emit the finish reason on a usage-only
                    # terminal event. Emit the required empty delta so OpenAI
                    # clients can commit the answer or execute tool calls.
                    final_reason = "tool_calls" if streamed_tool_calls else (stream_finish_reason or "stop")
                    finish_body = {
                        "id": request_id,
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": model,
                        "choices": [{"index": 0, "delta": {}, "finish_reason": final_reason}],
                    }
                    yield f"data: {json.dumps(finish_body, ensure_ascii=False)}\n\n"
                    usage_meta = metadata.get("usage") if isinstance(metadata, dict) else {}
                    usage = {
                        "prompt_tokens": int((usage_meta or {}).get("prompt_tokens", (usage_meta or {}).get("input_tokens", prompt_tokens)) or prompt_tokens),
                        "completion_tokens": int((usage_meta or {}).get("completion_tokens", (usage_meta or {}).get("output_tokens", service.estimate_tokens(answer))) or service.estimate_tokens(answer)),
                    }
                    usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
                    billing_started = True
                    ok, cost, record = await asyncio.to_thread(service.charge_and_record, user["id"], model, usage)
                    if not ok:
                        await complete_conversation(await resolve_conversation(), {"error": {"message": "Insufficient balance"}}, "", usage, 0, "BILLING_FAILED", "Insufficient balance")
                        error_body = {"error": {"message": "Insufficient balance", "type": "insufficient_quota"}}
                        yield f"data: {json.dumps(error_body, ensure_ascii=False)}\n\n"
                        yield "data: [DONE]\n\n"
                        return
                    response_body = {
                        "id": request_id,
                        "object": "chat.completion",
                        "created": int(time.time()),
                        "model": model,
                        "choices": [{"index": 0, "message": {
                            "role": "assistant",
                            "content": answer,
                            **({"tool_calls": streamed_tool_calls} if streamed_tool_calls else {}),
                        }, "finish_reason": "tool_calls" if streamed_tool_calls else "stop"}],
                        "usage": usage,
                        "rose": {"cost_cny": cost},
                    }
                    await complete_conversation(await resolve_conversation(), {"proxy_response": response_body, "upstream": metadata}, answer, usage, cost)
                    # Usage is emitted as the final empty-choice chunk, which
                    # is the OpenAI-compatible way to report stream billing.
                    final_body = {"id": request_id, "object": "chat.completion.chunk", "created": int(time.time()), "model": model, "choices": [], "usage": usage, "rose": {"cost_cny": cost}}
                    yield f"data: {json.dumps(final_body, ensure_ascii=False)}\n\n"
                    yield "data: [DONE]\n\n"
                except (asyncio.CancelledError, GeneratorExit):
                    # A client disconnect must not turn already generated text
                    # into a free request. Settle the observed portion without
                    # buffering or delaying normal stream chunks.
                    answer = "".join(answer_parts)
                    usage = {
                        "prompt_tokens": prompt_tokens or _estimate_payload_tokens(service, payload, "messages"),
                        "completion_tokens": max(0, int(service.estimate_tokens(answer))) if answer else 0,
                    }
                    usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
                    if billing_started:
                        # The settlement thread may still complete after the
                        # response task is cancelled. Never submit it twice.
                        ok, cost = True, 0
                    else:
                        billing_started = True
                        ok, cost, _ = await asyncio.to_thread(service.charge_and_record, user["id"], model, usage)
                    await complete_conversation(
                        await resolve_conversation(), None, "", usage, cost if ok else 0,
                        "CANCELLED" if ok else "BILLING_FAILED", None,
                    )
                    try:
                        await iterator.aclose()
                    except Exception:
                        pass
                    raise
                except Exception as exc:
                    await fail_conversation(exc)
                    error_body = {"error": {"message": str(exc), "type": "upstream_error"}}
                    yield f"data: {json.dumps(error_body, ensure_ascii=False)}\n\n"
                    yield "data: [DONE]\n\n"
            response = StreamingResponse(_tracked_stream(events(), request, direct_activity_id), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
            return _client_model_response(response, client_model if mapping else "", "")
        answer, usage, model, upstream_trace = await service.ainvoke_with_trace(payload)
        conversation = await resolve_conversation()
        ok, cost, record = await asyncio.to_thread(service.charge_and_record, user["id"], model, usage)
        if not ok:
            await complete_conversation(await resolve_conversation(), {"error": {"message": "Insufficient balance"}}, "", usage, 0, "BILLING_FAILED", "Insufficient balance")
            return JSONResponse({"error": {"message": "Insufficient balance", "type": "insufficient_quota"}}, status_code=402)
        response_body = {
            "id": request_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "message": {
                "role": "assistant",
                "content": answer,
                **({"tool_calls": upstream_trace.get("tool_calls")} if isinstance(upstream_trace, dict) and upstream_trace.get("tool_calls") else {}),
            }, "finish_reason": "tool_calls" if isinstance(upstream_trace, dict) and upstream_trace.get("tool_calls") else "stop"}],
            "usage": usage,
            "rose": {"cost_cny": cost},
        }
        await complete_conversation(await resolve_conversation(), {"proxy_response": response_body, "upstream": upstream_trace}, answer, usage, cost)
        return _client_model_response(JSONResponse(response_body), client_model if mapping else "", "")
    except UpstreamRequestError as exc:
        await fail_conversation(exc)
        status = exc.status_code if 400 <= exc.status_code < 500 else 502
        return JSONResponse({"error": {"message": str(exc), "type": "invalid_request_error" if status < 500 else "upstream_error"}}, status_code=status)
    except ValueError as exc:
        await fail_conversation(exc)
        return JSONResponse({"error": {"message": str(exc), "type": "invalid_request_error"}}, status_code=400)
    except Exception as exc:
        await fail_conversation(exc)
        return JSONResponse({"error": {"message": str(exc), "type": "upstream_error"}}, status_code=502)
    finally:
        if not is_stream:
            _finish_direct_api_activity(request, direct_activity_id)


def _responses_input_to_messages(value):
    """Translate the Responses API input shape to the local chat gateway."""
    if isinstance(value, str):
        return [{"role": "user", "content": value}]
    if isinstance(value, dict):
        value = [value]
    if isinstance(value, list):
        messages = []
        for item in value:
            if isinstance(item, dict) and item.get("role"):
                message = {
                    "role": str(item.get("role", "user")),
                    "content": normalize_content(item.get("content", ""), for_chat=True),
                }
                for field in ("tool_call_id", "tool_calls", "function_call", "name"):
                    if field in item:
                        message[field] = item[field]
                messages.append(message)
            elif isinstance(item, str):
                messages.append({"role": "user", "content": item})
            elif isinstance(item, dict) and item.get("type"):
                item_type = str(item.get("type") or "").lower()
                if item_type == "function_call_output":
                    messages.append({
                        "role": "tool",
                        "tool_call_id": str(item.get("call_id") or item.get("id") or ""),
                        "content": str(item.get("output") or ""),
                    })
                    continue
                if item_type == "function_call":
                    call_id = str(item.get("call_id") or item.get("id") or "")
                    namespace = str(item.get("namespace") or "").strip()
                    name = str(item.get("name") or "")
                    if namespace and name:
                        name = f"{namespace}__{name}"
                    messages.append({
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": str(item.get("arguments") or "{}"),
                            },
                        }],
                    })
                    continue
                # Responses also permits a flat input array consisting of
                # input_text/input_image parts without an enclosing role.
                parts = normalize_content([item], for_chat=True)
                if not parts:
                    continue
                if not messages or messages[-1].get("role") != "user":
                    messages.append({"role": "user", "content": []})
                current = messages[-1]["content"]
                if not isinstance(current, list):
                    current = [current]
                    messages[-1]["content"] = current
                part = parts[0]
                current.append(part)
        return normalize_chat_messages(messages)
    return []


def _responses_namespace_map(payload: dict) -> dict[str, tuple[str, str]]:
    """Map flattened Chat tool names back to Responses namespaces."""
    declared = list(payload.get("tools") or []) if isinstance(payload.get("tools"), list) else []
    input_items = payload.get("input")
    if isinstance(input_items, list):
        for item in input_items:
            if isinstance(item, dict) and item.get("type") == "additional_tools" and isinstance(item.get("tools"), list):
                declared.extend(item["tools"])
    result: dict[str, tuple[str, str]] = {}
    for tool in declared:
        if not isinstance(tool, dict) or str(tool.get("type") or "").lower() != "namespace":
            continue
        namespace = str(tool.get("name") or "").strip()
        children = tool.get("tools") if isinstance(tool.get("tools"), list) else tool.get("children")
        if not namespace or not isinstance(children, list):
            continue
        for child in children:
            if not isinstance(child, dict) or not child.get("name"):
                continue
            name = str(child["name"])
            result[f"{namespace}__{name}"] = (namespace, name)
            result[f"{namespace}.{name}"] = (namespace, name)
    return result


def _responses_tool_output(call: dict, namespaces: dict[str, tuple[str, str]]) -> dict:
    function = call.get("function") or {}
    raw_name = str(function.get("name") or "")
    namespace, name = namespaces.get(raw_name, ("", raw_name))
    call_id = str(call.get("id") or ("call_" + uuid.uuid4().hex))
    arguments = function.get("arguments") or "{}"
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments, ensure_ascii=False)
    item = {
        "id": call_id, "type": "function_call", "status": "completed",
        "call_id": call_id, "name": name, "arguments": arguments,
    }
    if namespace:
        item["namespace"] = namespace
    return item


@routing_scope
async def openai_responses(request: Request):
    """OpenAI Responses-compatible facade for Codex CLI/App/IDE clients."""
    service, auth, conversations = _beans(request)
    user = await _authenticated_user(request, auth)
    if not user:
        return _local_error(401, "Invalid API key", "authentication_error", "local_auth")
    if not user.get("enabled"):
        return _local_error(403, "Account disabled", "permission_error", "local_auth")
    if not await asyncio.to_thread(service.store.has_usable_balance, user["id"]):
        return _local_error(402, "Insufficient balance", "insufficient_quota", "local_billing")
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": {"message": "request body must be valid JSON", "type": "invalid_request_error"}}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"error": {"message": "request body must be a JSON object", "type": "invalid_request_error"}}, status_code=400)
    model_error = _model_permission_error(request, user, str(payload.get("model") or service.model_name))
    if model_error is not None:
        return model_error
    client_model = str(payload.get("model") or service.model_name)
    original_payload = payload
    payload, mapping = _group_service(request).apply_model_mapping(user, payload)
    record_mapping(user, original_payload, mapping)
    subscription_response = await maybe_proxy_openai_subscription(request, payload, user, client_model)
    if subscription_response is not None:
        return subscription_response
    payload = service.apply_openai_subscription_fallback(payload)
    try:
        namespace_tools = _responses_namespace_map(payload)
        messages = _responses_input_to_messages(payload.get("input"))
        if not messages:
            return JSONResponse({"error": {"message": "input is required", "type": "invalid_request_error"}}, status_code=400)
        instructions = payload.get("instructions")
        if isinstance(instructions, str) and instructions.strip():
            # Responses 的 instructions（系统提示词）在转普通 Chat 上游时需显式
            # 转成 system 角色消息，否则会在 fallback 路径被静默丢弃。
            if messages[0].get("role") == "system":
                messages[0]["content"] = f"{instructions.strip()}\n\n{messages[0].get('content', '')}"
            else:
                messages.insert(0, {"role": "system", "content": instructions.strip()})
        response_id = "resp_" + uuid.uuid4().hex
        direct_activity_id = _direct_api_activity(request, service, payload, user, response_id, "responses")
        is_stream = bool(payload.get("stream"))
        conversation_task = asyncio.create_task(asyncio.to_thread(
            conversations.begin, user["id"], "responses",
            {"model": payload.get("model")}, None, response_id,
        ))
        conversation = None

        async def resolve_conversation():
            nonlocal conversation
            if conversation is not None:
                return conversation
            try:
                conversation = await conversation_task
            except (Exception, asyncio.CancelledError):
                conversation = None
            return conversation

        async def fail_conversation(error):
            await asyncio.to_thread(conversations.fail, await resolve_conversation(), error)

        async def complete_conversation(row, response, answer, usage, cost, status="SUCCEEDED", error_message=None):
            await asyncio.to_thread(
                conversations.complete, row, response, answer, usage, cost,
                status, error_message,
            )
        if payload.get("stream"):
            async def events():
                iterator = service.astream_with_trace({**payload, "messages": messages})
                sentinel = object()
                answer_parts: list[str] = []
                pending_text = ""
                text_sent = ""
                dsml_mode = False
                tool_calls: list[dict] = []
                metadata: dict = {}
                prompt_tokens = 0
                model = str(payload.get("model") or service.model_name)
                item_id = "msg_" + uuid.uuid4().hex
                sequence = 0
                item_started = False
                billing_started = False

                def next_sequence() -> int:
                    nonlocal sequence
                    sequence += 1
                    return sequence

                try:
                    created_response = {
                        "id": response_id,
                        "object": "response",
                        "created_at": int(time.time()),
                        "model": model,
                        "output": [],
                        "status": "in_progress",
                    }
                    yield _responses_event("response.created", {
                        "type": "response.created",
                        "sequence_number": next_sequence(),
                        "response": created_response,
                    })
                    while True:
                        event = await anext(iterator, sentinel)
                        if event is sentinel:
                            break
                        delta = str(event.get("delta") or "")
                        model = str(event.get("model") or model)
                        prompt_tokens = int(event.get("prompt_tokens") or prompt_tokens)
                        metadata = event.get("metadata") or metadata
                        if event.get("tool_calls"):
                            tool_calls = event.get("tool_calls")
                        if not delta:
                            continue
                        answer_parts.append(delta)
                        if dsml_mode:
                            continue
                        pending_text += delta
                        if has_dsml_tool_call_marker(pending_text):
                            dsml_mode = True
                            continue
                        if is_dsml_tool_call_prefix(pending_text):
                            continue
                        visible = pending_text
                        pending_text = ""
                        if not visible:
                            continue
                        if not item_started:
                            item_started = True
                            yield _responses_event("response.output_item.added", {
                                "type": "response.output_item.added",
                                "sequence_number": next_sequence(),
                                "output_index": 0,
                                "item": {"id": item_id, "type": "message", "status": "in_progress", "role": "assistant", "content": []},
                            })
                            yield _responses_event("response.content_part.added", {
                                "type": "response.content_part.added",
                                "sequence_number": next_sequence(),
                                "item_id": item_id,
                                "output_index": 0,
                                "content_index": 0,
                                "part": {"type": "output_text", "text": "", "annotations": []},
                            })
                        text_sent += visible
                        yield _responses_event("response.output_text.delta", {
                            "type": "response.output_text.delta",
                            "sequence_number": next_sequence(),
                            "item_id": item_id,
                            "output_index": 0,
                            "content_index": 0,
                            "delta": visible,
                        })

                    answer_raw = "".join(answer_parts)
                    answer, dsml_tool_calls = parse_dsml_tool_calls(answer_raw)
                    if dsml_tool_calls:
                        tool_calls = dsml_tool_calls
                    # A marker can arrive split across SSE chunks. Flush only
                    # the visible prefix that was held while checking it.
                    if not tool_calls and pending_text:
                        answer = answer or pending_text
                    unsent = answer[len(text_sent):] if answer.startswith(text_sent) else answer
                    if unsent and not tool_calls:
                        if not item_started:
                            item_started = True
                            yield _responses_event("response.output_item.added", {
                                "type": "response.output_item.added", "sequence_number": next_sequence(), "output_index": 0,
                                "item": {"id": item_id, "type": "message", "status": "in_progress", "role": "assistant", "content": []},
                            })
                            yield _responses_event("response.content_part.added", {
                                "type": "response.content_part.added", "sequence_number": next_sequence(), "item_id": item_id,
                                "output_index": 0, "content_index": 0,
                                "part": {"type": "output_text", "text": "", "annotations": []},
                            })
                        text_sent += unsent
                        yield _responses_event("response.output_text.delta", {
                            "type": "response.output_text.delta", "sequence_number": next_sequence(), "item_id": item_id,
                            "output_index": 0, "content_index": 0, "delta": unsent,
                        })

                    if tool_calls:
                        output_items = []
                        for index, call in enumerate(tool_calls):
                            function_item = _responses_tool_output(call, namespace_tools)
                            call_id = function_item["call_id"]
                            arguments = function_item["arguments"]
                            output_items.append(function_item)
                            yield _responses_event("response.output_item.added", {
                                "type": "response.output_item.added", "sequence_number": next_sequence(),
                                "output_index": index, "item": {**function_item, "status": "in_progress", "arguments": ""},
                            })
                            yield _responses_event("response.function_call_arguments.delta", {
                                "type": "response.function_call_arguments.delta", "sequence_number": next_sequence(),
                                "item_id": call_id, "output_index": index, "delta": arguments,
                            })
                            yield _responses_event("response.function_call_arguments.done", {
                                "type": "response.function_call_arguments.done", "sequence_number": next_sequence(),
                                "item_id": call_id, "output_index": index, "arguments": arguments,
                            })
                            yield _responses_event("response.output_item.done", {
                                "type": "response.output_item.done", "sequence_number": next_sequence(),
                                "output_index": index, "item": function_item,
                            })
                        output = output_items
                    else:
                        output = [{"id": item_id, "type": "message", "status": "completed", "role": "assistant", "content": [{"type": "output_text", "text": answer, "annotations": []}]}]
                        if item_started:
                            yield _responses_event("response.output_text.done", {
                                "type": "response.output_text.done", "sequence_number": next_sequence(),
                                "item_id": item_id, "output_index": 0, "content_index": 0, "text": answer,
                            })
                            yield _responses_event("response.content_part.done", {
                                "type": "response.content_part.done", "sequence_number": next_sequence(),
                                "item_id": item_id, "output_index": 0, "content_index": 0,
                                "part": {"type": "output_text", "text": answer, "annotations": []},
                            })
                            yield _responses_event("response.output_item.done", {
                                "type": "response.output_item.done", "sequence_number": next_sequence(),
                                "output_index": 0, "item": output[0],
                            })

                    usage_meta = metadata.get("usage") if isinstance(metadata, dict) else {}
                    usage = {
                        "prompt_tokens": int((usage_meta or {}).get("prompt_tokens", (usage_meta or {}).get("input_tokens", prompt_tokens)) or prompt_tokens),
                        "completion_tokens": int((usage_meta or {}).get("completion_tokens", (usage_meta or {}).get("output_tokens", service.estimate_tokens(answer))) or service.estimate_tokens(answer)),
                    }
                    usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
                    billing_started = True
                    ok, cost, record = await asyncio.to_thread(service.charge_and_record, user["id"], model, usage)
                    if not ok:
                        await complete_conversation(await resolve_conversation(), {"error": {"message": "Insufficient balance"}}, "", usage, 0, "BILLING_FAILED", "Insufficient balance")
                        yield _responses_event("response.failed", {"type": "response.failed", "sequence_number": next_sequence(), "response": {**created_response, "status": "failed", "error": {"message": "Insufficient balance"}}})
                        yield "data: [DONE]\n\n"
                        return
                    response_body = {
                        "id": response_id, "object": "response", "created_at": int(time.time()), "model": model,
                        "output": output, "output_text": "" if tool_calls else answer, "status": "completed",
                        "usage": {"input_tokens": usage["prompt_tokens"], "output_tokens": usage["completion_tokens"], "total_tokens": usage["total_tokens"]},
                        "rose": {"cost_cny": cost, "usage_id": record.get("id") if record else None},
                    }
                    await complete_conversation(await resolve_conversation(), {"proxy_response": response_body, "upstream": metadata}, answer, usage, cost)
                    yield _responses_event("response.completed", {"type": "response.completed", "sequence_number": next_sequence(), "response": response_body})
                    yield "data: [DONE]\n\n"
                except (asyncio.CancelledError, GeneratorExit):
                    logger.info(
                        "Responses 直连流被客户端取消 requested_model=%s effective_model=%s",
                        payload.get("model"), payload.get("_rose_model_id") or payload.get("model"),
                    )
                    answer = "".join(answer_parts)
                    usage = {
                        "prompt_tokens": prompt_tokens or _estimate_payload_tokens(service, payload, "input"),
                        "completion_tokens": max(0, int(service.estimate_tokens(answer))) if answer else 0,
                    }
                    usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
                    if billing_started:
                        ok, cost = True, 0
                    else:
                        billing_started = True
                        ok, cost, _ = await asyncio.to_thread(service.charge_and_record, user["id"], model, usage)
                    await complete_conversation(
                        await resolve_conversation(), None, "", usage, cost if ok else 0,
                        "CANCELLED" if ok else "BILLING_FAILED", None,
                    )
                    try:
                        await iterator.aclose()
                    except Exception:
                        pass
                    raise
                except Exception as exc:
                    logger.warning(
                        "Responses 直连上游失败 requested_model=%s effective_model=%s error_type=%s detail=%s",
                        payload.get("model"), payload.get("_rose_model_id") or payload.get("model"),
                        type(exc).__name__, str(exc)[:500],
                    )
                    await fail_conversation(exc)
                    yield _responses_event("error", {"type": "error", "error": {"message": str(exc), "type": "upstream_error"}})
                    yield "data: [DONE]\n\n"
            response = StreamingResponse(_tracked_stream(events(), request, direct_activity_id), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
            return _client_model_response(response, client_model if mapping else "", "")
        answer, usage, model, upstream_trace = await service.ainvoke_with_trace({**payload, "messages": messages})
        conversation = await resolve_conversation()
        ok, cost, record = await asyncio.to_thread(service.charge_and_record, user["id"], model, usage)
        if not ok:
            await complete_conversation(await resolve_conversation(), {"error": {"message": "Insufficient balance"}}, "", usage, 0, "BILLING_FAILED", "Insufficient balance")
            return JSONResponse({"error": {"message": "Insufficient balance", "type": "insufficient_quota"}}, status_code=402)
        output_tool_calls = (upstream_trace or {}).get("tool_calls") if isinstance(upstream_trace, dict) else []
        if output_tool_calls:
            output = [_responses_tool_output(call, namespace_tools) for call in output_tool_calls]
            output_text = ""
        else:
            output = [{"id": "msg_" + uuid.uuid4().hex, "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": answer}]}]
            output_text = answer
        response_body = {
            "id": response_id,
            "object": "response",
            "created_at": int(time.time()),
            "model": model,
            "output": output,
            "output_text": output_text,
            "status": "completed",
            "usage": {"input_tokens": usage["prompt_tokens"], "output_tokens": usage["completion_tokens"], "total_tokens": usage["total_tokens"]},
            "rose": {"cost_cny": cost, "usage_id": record.get("id") if record else None},
        }
        await complete_conversation(await resolve_conversation(), {"proxy_response": response_body, "upstream": upstream_trace}, answer, usage, cost)
        return _client_model_response(JSONResponse(response_body), client_model if mapping else "", "")
    except UpstreamRequestError as exc:
        resolver = locals().get("resolve_conversation")
        row = await resolver() if callable(resolver) else locals().get("conversation")
        await asyncio.to_thread(conversations.fail, row, exc)
        status = exc.status_code if 400 <= exc.status_code < 500 else 502
        return JSONResponse({"error": {"message": str(exc), "type": "invalid_request_error" if status < 500 else "upstream_error"}}, status_code=status)
    except ValueError as exc:
        resolver = locals().get("resolve_conversation")
        row = await resolver() if callable(resolver) else locals().get("conversation")
        await asyncio.to_thread(conversations.fail, row, exc)
        return JSONResponse({"error": {"message": str(exc), "type": "invalid_request_error"}}, status_code=400)
    except Exception as exc:
        resolver = locals().get("resolve_conversation")
        row = await resolver() if callable(resolver) else locals().get("conversation")
        await asyncio.to_thread(conversations.fail, row, exc)
        return JSONResponse({"error": {"message": str(exc), "type": "upstream_error"}}, status_code=502)
    finally:
        if not locals().get("is_stream", False):
            _finish_direct_api_activity(request, locals().get("direct_activity_id", ""))


async def openai_models(request: Request):
    """Expose a small OpenAI-compatible model directory for client probing."""
    service, auth, _ = _beans(request)
    user = await _authenticated_user(request, auth)
    if not user:
        return _local_error(401, "Invalid API key", "authentication_error", "local_auth")
    now = int(time.time())
    data = []
    context = request.app.state.spring_application.application_context
    subscription_gateway = context.get_bean("subscription_gateway_service")
    catalog = _group_service(request).filter_catalog(user, service.catalog() + subscription_gateway.catalog())
    seen = set()
    for item in catalog:
        if not item.get("enabled"):
            continue
        if item.get("id") in seen:
            continue
        seen.add(item.get("id"))
        reasoning_levels = list(item.get("reasoning_levels") or ["none", "low", "medium", "high", "xhigh"])
        # Codex's model catalog does not use a plain string list here.  Each
        # level is a preset object (the OpenAI-compatible clients may still
        # consume the legacy ``supported_reasoning_efforts`` string list).
        reasoning_presets = [
            {
                "effort": str(level),
                "description": f"Reasoning effort: {level}",
            }
            for level in reasoning_levels
        ]
        default_reasoning_level = item.get("reasoning_effort") or "high"
        data.append({
            "id": item["id"],
            # Codex's model manager uses ``slug`` as the stable model key;
            # OpenAI-compatible clients ignore this additional metadata.
            "slug": item["id"],
            "display_name": item.get("id", "Rose model"),
            "description": item.get("description", "OpenAI-compatible model"),
            "supported_reasoning_efforts": reasoning_levels,
            "supported_reasoning_levels": reasoning_presets,
            "default_reasoning_effort": default_reasoning_level,
            "default_reasoning_level": default_reasoning_level,
            "input_modalities": ["text", "image"] if item.get("supports_image") else ["text"],
            "output_modalities": ["text"],
            "supports_reasoning_summaries": True,
            "supports_parallel_tool_calls": True,
            # Codex 0.153+ 解码模型目录时强制要求该字段（serde 无默认值），
            # 缺失会导致 list_models 刷新失败。unified_exec 与 OpenAI 官方
            # 模型目录一致，是非 OpenAI 上游最通用的 shell 工具类型声明。
            "shell_type": "unified_exec",
            "context_window": 200000,
            "object": "model",
            "created": now,
            "owned_by": item.get("provider", "rose"),
        })
    # OpenAI clients consume ``data``. Codex's model manager also accepts the
    # gateway's historical ``models`` envelope and rejects a response that only
    # contains ``data`` while refreshing its catalog. Keep both aliases so
    # startup discovery is compatible with both client families.
    return JSONResponse({"object": "list", "data": data, "models": data})


def register_proxy_route(app):
    app.add_api_route("/v1/chat/completions", openai_chat, methods=["POST"], tags=["OpenAI Compatible"])
    app.add_api_route("/v1/responses", openai_responses, methods=["POST"], tags=["OpenAI Responses Compatible"])
    app.add_api_route("/v1/models", openai_models, methods=["GET"], tags=["OpenAI Compatible"])
    # 兼容将主机地址作为 base URL 的客户端；规范地址仍使用 /v1。
    app.add_api_route("/chat/completions", openai_chat, methods=["POST"], tags=["OpenAI Compatible"])
    app.add_api_route("/responses", openai_responses, methods=["POST"], tags=["OpenAI Responses Compatible"])
    app.add_api_route("/models", openai_models, methods=["GET"], tags=["OpenAI Compatible"])
