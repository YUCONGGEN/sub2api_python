from __future__ import annotations

import asyncio
import json
import time
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

from backend.service.ai_service import AiGatewayService, UpstreamRequestError
from backend.service.auth_service import AuthService
from backend.service.conversation_service import ConversationService
from backend.common.multimodal import normalize_content, parse_dsml_tool_calls
from backend.protocol.subscription_adapter import (
    maybe_proxy_claude_chat_subscription,
    maybe_proxy_openai_chat_subscription,
    maybe_proxy_openai_subscription,
)


def _beans(request: Request) -> tuple[AiGatewayService, AuthService, ConversationService]:
    """Resolve SpringBootAI-managed services for the raw OpenAI protocol."""
    context = request.app.state.spring_application.application_context
    return context.get_bean("ai_gateway_service"), context.get_bean("auth_service"), context.get_bean("conversation_service")


def _responses_event(event_type: str, payload: dict) -> str:
    """Encode one OpenAI Responses SSE event without JSON wrapping."""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event_type}\ndata: {body}\n\n"


async def openai_chat(request: Request):
    service, auth, conversations = _beans(request)
    user = await asyncio.to_thread(auth.user_from_authorization, request.headers.get("Authorization"))
    if not user:
        return JSONResponse({"error": {"message": "Invalid API key", "type": "authentication_error"}}, status_code=401)
    if not user.get("enabled"):
        return JSONResponse({"error": {"message": "Account disabled", "type": "permission_error"}}, status_code=403)
    if not await asyncio.to_thread(service.store.has_usable_balance, user["id"]):
        return JSONResponse({"error": {"message": "Insufficient balance", "type": "insufficient_quota"}}, status_code=402)
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": {"message": "request body must be valid JSON", "type": "invalid_request_error"}}, status_code=400)
    if not isinstance(payload, dict) or not payload.get("messages"):
        return JSONResponse({"error": {"message": "messages is required", "type": "invalid_request_error"}}, status_code=400)
    subscription_response = await maybe_proxy_openai_chat_subscription(request, payload, user)
    if subscription_response is not None:
        return subscription_response
    subscription_response = await maybe_proxy_claude_chat_subscription(request, payload, user)
    if subscription_response is not None:
        return subscription_response
    request_id = "chatcmpl-" + uuid.uuid4().hex
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
                dsml_marker = "<\uff5c\uff5cDSML\uff5c\uff5ctool_calls>"
                streamed_tool_calls = []
                stream_finish_reason = None
                prompt_tokens = 0
                model = str(payload.get("model") or service.model_name)
                metadata = {}
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
                                if dsml_marker in pending_text:
                                    dsml_mode = True
                                    delta = ""
                                elif len(pending_text) < len(dsml_marker) and dsml_marker.startswith(pending_text):
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
                except Exception as exc:
                    await fail_conversation(exc)
                    error_body = {"error": {"message": str(exc), "type": "upstream_error"}}
                    yield f"data: {json.dumps(error_body, ensure_ascii=False)}\n\n"
                    yield "data: [DONE]\n\n"
            return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
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
        return JSONResponse(response_body)
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
                    "content": normalize_content(item.get("content", "")),
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
                    messages.append({
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": str(item.get("name") or ""),
                                "arguments": str(item.get("arguments") or "{}"),
                            },
                        }],
                    })
                    continue
                # Responses also permits a flat input array consisting of
                # input_text/input_image parts without an enclosing role.
                if not messages or messages[-1].get("role") != "user":
                    messages.append({"role": "user", "content": []})
                current = messages[-1]["content"]
                if not isinstance(current, list):
                    current = [current]
                    messages[-1]["content"] = current
                part = normalize_content([item])[0]
                current.append(part)
        return messages
    return []


async def openai_responses(request: Request):
    """OpenAI Responses-compatible facade for Codex CLI/App/IDE clients."""
    service, auth, conversations = _beans(request)
    user = await asyncio.to_thread(auth.user_from_authorization, request.headers.get("Authorization"))
    if not user:
        return JSONResponse({"error": {"message": "Invalid API key", "type": "authentication_error"}}, status_code=401)
    if not user.get("enabled"):
        return JSONResponse({"error": {"message": "Account disabled", "type": "permission_error"}}, status_code=403)
    if not await asyncio.to_thread(service.store.has_usable_balance, user["id"]):
        return JSONResponse({"error": {"message": "Insufficient balance", "type": "insufficient_quota"}}, status_code=402)
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": {"message": "request body must be valid JSON", "type": "invalid_request_error"}}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"error": {"message": "request body must be a JSON object", "type": "invalid_request_error"}}, status_code=400)
    subscription_response = await maybe_proxy_openai_subscription(request, payload, user)
    if subscription_response is not None:
        return subscription_response
    try:
        messages = _responses_input_to_messages(payload.get("input"))
        if not messages:
            return JSONResponse({"error": {"message": "input is required", "type": "invalid_request_error"}}, status_code=400)
        response_id = "resp_" + uuid.uuid4().hex
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
                marker = "<\uff5c\uff5cDSML\uff5c\uff5ctool_calls>"
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
                        if marker in pending_text:
                            dsml_mode = True
                            continue
                        if len(pending_text) < len(marker) and marker.startswith(pending_text):
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
                            function = call.get("function") or {}
                            call_id = str(call.get("id") or ("call_" + uuid.uuid4().hex))
                            name = str(function.get("name") or "")
                            arguments = function.get("arguments") or "{}"
                            arguments = arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)
                            function_item = {
                                "id": call_id, "type": "function_call", "status": "completed",
                                "call_id": call_id, "name": name, "arguments": arguments,
                            }
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
                except Exception as exc:
                    await fail_conversation(exc)
                    yield _responses_event("error", {"type": "error", "error": {"message": str(exc), "type": "upstream_error"}})
                    yield "data: [DONE]\n\n"
            return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
        answer, usage, model, upstream_trace = await service.ainvoke_with_trace({**payload, "messages": messages})
        conversation = await resolve_conversation()
        ok, cost, record = await asyncio.to_thread(service.charge_and_record, user["id"], model, usage)
        if not ok:
            await complete_conversation(await resolve_conversation(), {"error": {"message": "Insufficient balance"}}, "", usage, 0, "BILLING_FAILED", "Insufficient balance")
            return JSONResponse({"error": {"message": "Insufficient balance", "type": "insufficient_quota"}}, status_code=402)
        response_body = {
            "id": response_id,
            "object": "response",
            "created_at": int(time.time()),
            "model": model,
            "output": [{"id": "msg_" + uuid.uuid4().hex, "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": answer}]}],
            "output_text": answer,
            "status": "completed",
            "usage": {"input_tokens": usage["prompt_tokens"], "output_tokens": usage["completion_tokens"], "total_tokens": usage["total_tokens"]},
            "rose": {"cost_cny": cost, "usage_id": record.get("id") if record else None},
        }
        await complete_conversation(await resolve_conversation(), {"proxy_response": response_body, "upstream": upstream_trace}, answer, usage, cost)
        return JSONResponse(response_body)
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


async def openai_models(request: Request):
    """Expose a small OpenAI-compatible model directory for client probing."""
    service, auth, _ = _beans(request)
    user = await asyncio.to_thread(auth.user_from_authorization, request.headers.get("Authorization"))
    if not user:
        return JSONResponse({"error": {"message": "Invalid API key", "type": "authentication_error"}}, status_code=401)
    now = int(time.time())
    data = []
    context = request.app.state.spring_application.application_context
    subscription_gateway = context.get_bean("subscription_gateway_service")
    catalog = service.catalog() + subscription_gateway.catalog()
    seen = set()
    for item in catalog:
        if not item.get("enabled"):
            continue
        if item.get("id") in seen:
            continue
        seen.add(item.get("id"))
        data.append({
            "id": item["id"],
            "object": "model",
            "created": now,
            "owned_by": item.get("provider", "rose"),
        })
    return JSONResponse({"object": "list", "data": data})


def register_proxy_route(app):
    app.add_api_route("/v1/chat/completions", openai_chat, methods=["POST"], tags=["OpenAI Compatible"])
    app.add_api_route("/v1/responses", openai_responses, methods=["POST"], tags=["OpenAI Responses Compatible"])
    app.add_api_route("/v1/models", openai_models, methods=["GET"], tags=["OpenAI Compatible"])
    # 兼容将主机地址作为 base URL 的客户端；规范地址仍使用 /v1。
    app.add_api_route("/chat/completions", openai_chat, methods=["POST"], tags=["OpenAI Compatible"])
    app.add_api_route("/responses", openai_responses, methods=["POST"], tags=["OpenAI Responses Compatible"])
    app.add_api_route("/models", openai_models, methods=["GET"], tags=["OpenAI Compatible"])


