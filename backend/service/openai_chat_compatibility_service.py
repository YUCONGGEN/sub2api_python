"""SpringBootAI-managed bridge between Chat Completions and Responses."""

from __future__ import annotations

import codecs
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from springbootai import Service, Slf4j

from backend.common.reasoning import validated_reasoning_effort


@dataclass
class _ChatStreamState:
    response_id: str
    model: str
    created: int
    service_tier: str = ""
    sent_role: bool = False
    saw_text: bool = False
    saw_tool_call: bool = False
    finalized: bool = False
    next_tool_index: int = 0
    tool_indexes: dict[int, int] = field(default_factory=dict)
    tool_argument_seen: set[int] = field(default_factory=set)
    usage: dict[str, Any] | None = None


@Service("openai_chat_compatibility_service")
@Slf4j
class OpenAIChatCompatibilityService:
    """Translate subscription Chat Completions requests to Codex Responses.

    The raw ASGI route remains a thin wire adapter. All protocol compatibility
    rules live in this SpringBootAI service so they are discovered, managed,
    and testable in the same way as the rest of the application services.
    Native Responses and ordinary API-key upstreams do not use this service.
    """

    def to_responses(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        model = str(payload.get("model") or "").strip()
        if not model:
            raise ValueError("model is required")
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            raise ValueError("messages is required")

        outgoing: dict[str, Any] = {
            "model": model,
            "input": self._messages_to_input(messages),
            # The ChatGPT Codex subscription transport is streaming-first.
            # Non-streaming Chat clients are buffered back into one JSON
            # response by ``from_responses_stream`` below.
            "stream": True,
            "store": False,
        }

        instructions = payload.get("instructions")
        if isinstance(instructions, str) and instructions.strip():
            outgoing["instructions"] = instructions

        # This is the subscription transport, not the public Responses API.
        # Only forward supported fields: metadata, safety_identifier,
        # truncation and sampling defaults (including Astra's) are rejected
        # by the Codex upstream. stream_options is handled locally by the
        # adapter when returning Chat SSE usage.
        for key in (
            "parallel_tool_calls",
            "prompt_cache_key",
            "service_tier",
        ):
            if key in payload and payload[key] is not None:
                outgoing[key] = payload[key]

        # Clients such as Trae send output limits by default. Validate them,
        # but do not send any spelling to Codex: converting to another token
        # limit name still produces an upstream 400. This transport cannot
        # enforce the requested output cap; normal gateway quotas still apply.
        for key in ("max_tokens", "max_completion_tokens", "max_output_tokens"):
            raw_limit = payload.get(key)
            if raw_limit is None:
                continue
            try:
                if isinstance(raw_limit, bool):
                    raise ValueError
                limit = int(raw_limit)
                if limit <= 0 or (isinstance(raw_limit, float) and not raw_limit.is_integer()):
                    raise ValueError
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"{key} must be a positive integer") from exc

        reasoning = payload.get("reasoning")
        if reasoning is not None and not isinstance(reasoning, dict):
            raise ValueError("reasoning must be a JSON object")
        if isinstance(reasoning, dict):
            outgoing["reasoning"] = dict(reasoning)
        # A summary-only/empty Responses object must not hide an explicit
        # Chat effort. Missing effort is defaulted once at the gateway boundary.
        effort = validated_reasoning_effort(payload)
        if effort is not None:
            outgoing.setdefault("reasoning", {})["effort"] = effort

        tools = self._tools_to_responses(payload.get("tools"), payload.get("functions"))
        if tools:
            outgoing["tools"] = tools

        tool_choice = self._tool_choice_to_responses(payload.get("tool_choice"))
        if tool_choice is None and payload.get("function_call") is not None:
            tool_choice = self._legacy_function_choice(payload.get("function_call"))
        if tool_choice is not None and tools:
            outgoing["tool_choice"] = tool_choice

        text_config = self._text_config(payload)
        if text_config:
            outgoing["text"] = text_config
        return outgoing

    def from_responses_bytes(self, body: bytes, requested_model: str) -> dict[str, Any]:
        try:
            response = json.loads(body.decode("utf-8"))
        except (UnicodeError, ValueError, TypeError) as exc:
            raise ValueError("OpenAI subscription returned invalid JSON") from exc
        if not isinstance(response, dict):
            raise ValueError("OpenAI subscription returned a non-object response")
        return self.from_responses(response, requested_model)

    def from_responses(self, response: dict[str, Any], requested_model: str) -> dict[str, Any]:
        model = str(response.get("model") or requested_model or "")
        content, reasoning, tool_calls, refusal = self._response_output(response)
        message: dict[str, Any] = {
            "role": "assistant",
            "content": content if content else (None if tool_calls else ""),
        }
        if tool_calls:
            message["tool_calls"] = tool_calls
        if reasoning:
            message["reasoning_content"] = reasoning
        if refusal:
            message["refusal"] = refusal

        result: dict[str, Any] = {
            "id": self._chat_id(response.get("id")),
            "object": "chat.completion",
            "created": int(response.get("created_at") or time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": message,
                "finish_reason": self._finish_reason(response, bool(tool_calls)),
            }],
            "usage": self._chat_usage(response.get("usage")),
        }
        if response.get("service_tier") is not None:
            result["service_tier"] = response["service_tier"]
        return result

    async def stream_from_responses(
        self,
        raw_stream: AsyncIterator[bytes],
        requested_model: str,
        *,
        include_usage: bool = False,
    ) -> AsyncIterator[bytes]:
        state = _ChatStreamState(
            response_id=self._chat_id(None),
            model=str(requested_model or ""),
            created=int(time.time()),
        )
        try:
            async for event in self._iter_sse_events(raw_stream):
                # Send terminal chunks promptly, then let the source finish
                # normally so the gateway settles actual usage and releases
                # its account slot. Returning here closes it as a disconnect.
                if state.finalized:
                    continue
                if event == "[DONE]":
                    for chunk in self._finalize_stream(state, include_usage):
                        yield chunk
                    continue
                if not isinstance(event, dict):
                    continue
                for chunk in self._event_to_chat_chunks(event, state, include_usage):
                    yield chunk
        except Exception as exc:
            if not state.finalized:
                error = {"error": {"message": str(exc), "type": "upstream_error"}}
                yield self._sse(error)
                yield b"data: [DONE]\n\n"
                state.finalized = True
            return
        for chunk in self._finalize_stream(state, include_usage):
            yield chunk

    async def from_responses_stream(
        self,
        raw_stream: AsyncIterator[bytes],
        requested_model: str,
    ) -> dict[str, Any]:
        """Buffer an upstream Responses SSE stream for a non-stream Chat client."""
        response: dict[str, Any] = {
            "id": "",
            "object": "response",
            "created_at": int(time.time()),
            "model": str(requested_model or ""),
            "status": "completed",
            "output": [],
        }
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        tool_indexes: dict[int, int] = {}
        terminal: dict[str, Any] | None = None

        async for event in self._iter_sse_events(raw_stream):
            if event == "[DONE]":
                # Consume through EOF to finish normal gateway settlement.
                continue
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("type") or "")
            embedded = event.get("response") if isinstance(event.get("response"), dict) else None
            if event_type == "response.created" and embedded:
                for key in ("id", "created_at", "model", "service_tier"):
                    if embedded.get(key) is not None:
                        response[key] = embedded[key]
            elif event_type == "response.output_text.delta" and event.get("delta") is not None:
                text_parts.append(str(event.get("delta") or ""))
            elif event_type in {"response.reasoning_summary_text.delta", "response.reasoning_text.delta"}:
                reasoning_parts.append(str(event.get("delta") or ""))
            elif event_type == "response.output_item.added":
                item = event.get("item")
                if isinstance(item, dict) and str(item.get("type") or "") in {"function_call", "custom_tool_call"}:
                    output_index = self._output_index(event, len(tool_calls))
                    tool_indexes[output_index] = len(tool_calls)
                    tool_calls.append({
                        "type": str(item.get("type") or "function_call"),
                        "call_id": str(item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex}"),
                        "name": str(item.get("name") or ""),
                        "arguments": str(item.get("arguments") or ""),
                        "input": str(item.get("input") or ""),
                    })
            elif event_type in {"response.function_call_arguments.delta", "response.custom_tool_call_input.delta"}:
                output_index = self._output_index(event, 0)
                tool_index = tool_indexes.get(output_index)
                if tool_index is not None:
                    key = "input" if event_type == "response.custom_tool_call_input.delta" else "arguments"
                    tool_calls[tool_index][key] += str(event.get("delta") or "")
            elif event_type == "response.output_item.done":
                item = event.get("item")
                if isinstance(item, dict) and str(item.get("type") or "") in {"function_call", "custom_tool_call"}:
                    output_index = self._output_index(event, len(tool_calls))
                    tool_index = tool_indexes.get(output_index)
                    if tool_index is None:
                        tool_indexes[output_index] = len(tool_calls)
                        tool_calls.append(dict(item))
                    else:
                        for key in ("call_id", "id", "name", "arguments", "input"):
                            if item.get(key) and not tool_calls[tool_index].get(key):
                                tool_calls[tool_index][key] = item[key]
            elif event_type in {"response.completed", "response.done", "response.incomplete"} and embedded:
                terminal = dict(embedded)
            elif event_type in {"response.failed", "error", "response.error"}:
                error = event.get("error")
                if not isinstance(error, dict) and embedded:
                    error = embedded.get("error")
                if isinstance(error, dict):
                    raise ValueError(str(error.get("message") or error.get("type") or "OpenAI subscription response failed"))
                raise ValueError("OpenAI subscription response failed")

        completed = terminal or response
        output = completed.get("output")
        if not isinstance(output, list) or not output:
            output = []
            if reasoning_parts:
                output.append({
                    "type": "reasoning",
                    "summary": [{"type": "summary_text", "text": "".join(reasoning_parts)}],
                })
            if text_parts:
                output.append({
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "".join(text_parts)}],
                })
            output.extend(tool_calls)
            completed["output"] = output
        for key in ("id", "created_at", "model", "service_tier"):
            if not completed.get(key) and response.get(key):
                completed[key] = response[key]
        completed.setdefault("status", "completed")
        return self.from_responses(completed, requested_model)

    @classmethod
    def _messages_to_input(cls, messages: list[Any]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for raw_message in messages:
            if not isinstance(raw_message, dict):
                raise ValueError("each message must be a JSON object")
            role = str(raw_message.get("role") or "user").strip().lower()
            if role in {"system", "developer", "user"}:
                items.append({
                    "role": role,
                    "content": cls._input_content(raw_message.get("content")),
                })
                continue
            if role == "assistant":
                assistant_content = cls._assistant_content(raw_message.get("content"))
                if assistant_content:
                    items.append({"role": "assistant", "content": assistant_content})
                tool_calls = raw_message.get("tool_calls")
                if isinstance(tool_calls, list):
                    for tool_call in tool_calls:
                        converted = cls._assistant_tool_call(tool_call)
                        if converted:
                            items.append(converted)
                legacy = raw_message.get("function_call")
                if isinstance(legacy, dict) and legacy.get("name"):
                    items.append({
                        "type": "function_call",
                        "call_id": str(raw_message.get("tool_call_id") or f"call_{uuid.uuid4().hex}"),
                        "name": str(legacy["name"]),
                        "arguments": cls._arguments(legacy.get("arguments")),
                    })
                continue
            if role in {"tool", "function"}:
                call_id = raw_message.get("tool_call_id") or raw_message.get("name")
                if not call_id:
                    raise ValueError("tool message requires tool_call_id")
                items.append({
                    "type": "function_call_output",
                    "call_id": str(call_id),
                    "output": cls._tool_output(raw_message.get("content")),
                })
                continue
            items.append({"role": "user", "content": cls._input_content(raw_message.get("content"))})
        return items

    @staticmethod
    def _input_content(content: Any) -> str | list[dict[str, Any]]:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return str(content)
        converted: list[dict[str, Any]] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type") or "")
            if part_type in {"text", "input_text"} and part.get("text") is not None:
                converted.append({"type": "input_text", "text": str(part.get("text") or "")})
            elif part_type in {"image_url", "input_image"}:
                image = part.get("image_url")
                detail = part.get("detail")
                if isinstance(image, dict):
                    detail = image.get("detail", detail)
                    image = image.get("url")
                if image:
                    value = {"type": "input_image", "image_url": str(image)}
                    if detail:
                        value["detail"] = detail
                    converted.append(value)
            elif part_type in {"file", "input_file"}:
                source = part.get("file") if isinstance(part.get("file"), dict) else part
                value = {"type": "input_file"}
                for key in ("file_id", "file_data", "file_url", "filename"):
                    if source.get(key):
                        value[key] = source[key]
                if len(value) > 1:
                    converted.append(value)
        return converted or ""

    @classmethod
    def _assistant_content(cls, content: Any) -> list[dict[str, Any]]:
        if isinstance(content, str):
            return [{"type": "output_text", "text": content}] if content else []
        if not isinstance(content, list):
            return []
        parts: list[dict[str, Any]] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type") or "")
            if part_type in {"text", "output_text"} and part.get("text") is not None:
                parts.append({"type": "output_text", "text": str(part.get("text") or "")})
        return parts

    @classmethod
    def _assistant_tool_call(cls, tool_call: Any) -> dict[str, Any] | None:
        if not isinstance(tool_call, dict):
            return None
        function = tool_call.get("function")
        if str(tool_call.get("type") or "function") != "function" or not isinstance(function, dict):
            return None
        name = str(function.get("name") or "").strip()
        if not name:
            return None
        return {
            "type": "function_call",
            "call_id": str(tool_call.get("id") or f"call_{uuid.uuid4().hex}"),
            "name": name,
            "arguments": cls._arguments(function.get("arguments")),
        }

    @staticmethod
    def _arguments(value: Any) -> str:
        if isinstance(value, str):
            return value or "{}"
        if value is None:
            return "{}"
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _tool_output(value: Any) -> str:
        if isinstance(value, str):
            return value or "(empty)"
        if value is None:
            return "(empty)"
        if isinstance(value, list):
            text = "".join(
                str(part.get("text") or "")
                for part in value
                if isinstance(part, dict) and str(part.get("type") or "") in {"text", "input_text", "output_text"}
            )
            if text:
                return text
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def _tools_to_responses(cls, tools: Any, functions: Any) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        if isinstance(tools, list):
            for tool in tools:
                if not isinstance(tool, dict):
                    continue
                tool_type = str(tool.get("type") or "function")
                if tool_type != "function":
                    converted.append(dict(tool))
                    continue
                function = tool.get("function")
                if not isinstance(function, dict) or not function.get("name"):
                    continue
                converted.append(cls._function_tool(function))
        if isinstance(functions, list):
            for function in functions:
                if isinstance(function, dict) and function.get("name"):
                    converted.append(cls._function_tool(function))
        return converted

    @staticmethod
    def _function_tool(function: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {
            "type": "function",
            "name": str(function.get("name") or ""),
            "parameters": function.get("parameters") if isinstance(function.get("parameters"), dict) else {},
            "strict": bool(function.get("strict", False)),
        }
        if function.get("description") is not None:
            result["description"] = str(function["description"])
        return result

    @staticmethod
    def _tool_choice_to_responses(value: Any) -> Any:
        if isinstance(value, str):
            return value
        if not isinstance(value, dict):
            return None
        tool_type = str(value.get("type") or "")
        nested = value.get(tool_type) if tool_type else None
        if tool_type in {"function", "custom"} and isinstance(nested, dict) and nested.get("name"):
            return {"type": tool_type, "name": str(nested["name"])}
        if tool_type and value.get("name"):
            return {"type": tool_type, "name": str(value["name"])}
        return value

    @staticmethod
    def _legacy_function_choice(value: Any) -> Any:
        if isinstance(value, str):
            return value
        if isinstance(value, dict) and value.get("name"):
            return {"type": "function", "name": str(value["name"])}
        return None

    @staticmethod
    def _text_config(payload: dict[str, Any]) -> dict[str, Any]:
        text: dict[str, Any] = {}
        response_format = payload.get("response_format")
        if isinstance(response_format, dict):
            format_type = str(response_format.get("type") or "")
            if format_type in {"text", "json_object"}:
                text["format"] = {"type": format_type}
            elif format_type == "json_schema" and isinstance(response_format.get("json_schema"), dict):
                text["format"] = {"type": "json_schema", **response_format["json_schema"]}
        if payload.get("verbosity") is not None:
            text["verbosity"] = payload["verbosity"]
        return text

    @classmethod
    def _response_output(cls, response: dict[str, Any]) -> tuple[str, str, list[dict[str, Any]], str]:
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        refusal_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        output = response.get("output")
        if not isinstance(output, list):
            output = []
        for item in output:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "")
            if item_type == "message":
                parts = item.get("content")
                if isinstance(parts, str):
                    content_parts.append(parts)
                elif isinstance(parts, list):
                    for part in parts:
                        if not isinstance(part, dict):
                            continue
                        part_type = str(part.get("type") or "")
                        if part_type in {"output_text", "text"} and part.get("text") is not None:
                            content_parts.append(str(part.get("text") or ""))
                        elif part_type == "refusal" and part.get("refusal") is not None:
                            refusal_parts.append(str(part.get("refusal") or ""))
            elif item_type == "function_call":
                tool_calls.append(cls._chat_tool_call(item))
            elif item_type == "custom_tool_call":
                custom = dict(item)
                custom["arguments"] = cls._arguments({"input": item.get("input", "")})
                tool_calls.append(cls._chat_tool_call(custom))
            elif item_type == "reasoning":
                summary = item.get("summary")
                if isinstance(summary, list):
                    for part in summary:
                        if isinstance(part, dict) and part.get("text") is not None:
                            reasoning_parts.append(str(part.get("text") or ""))
        if not content_parts and isinstance(response.get("output_text"), str):
            content_parts.append(response["output_text"])
        return "".join(content_parts), "".join(reasoning_parts), tool_calls, "".join(refusal_parts)

    @classmethod
    def _chat_tool_call(cls, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex}"),
            "type": "function",
            "function": {
                "name": str(item.get("name") or ""),
                "arguments": cls._arguments(item.get("arguments")),
            },
        }

    @staticmethod
    def _finish_reason(response: dict[str, Any], has_tool_calls: bool) -> str:
        status = str(response.get("status") or "completed")
        details = response.get("incomplete_details")
        reason = str(details.get("reason") or "") if isinstance(details, dict) else ""
        if status == "incomplete" and reason == "max_output_tokens":
            return "length"
        if status == "incomplete" and reason == "content_filter":
            return "content_filter"
        return "tool_calls" if has_tool_calls else "stop"

    @staticmethod
    def _chat_usage(value: Any) -> dict[str, Any]:
        usage = value if isinstance(value, dict) else {}
        prompt = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
        completion = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
        result: dict[str, Any] = {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": int(usage.get("total_tokens") or (prompt + completion)),
        }
        input_details = usage.get("input_tokens_details")
        if isinstance(input_details, dict) and input_details:
            result["prompt_tokens_details"] = dict(input_details)
        output_details = usage.get("output_tokens_details")
        if isinstance(output_details, dict) and output_details:
            result["completion_tokens_details"] = dict(output_details)
        return result

    @classmethod
    def _chat_id(cls, value: Any) -> str:
        raw = str(value or "").strip()
        if raw.startswith("chatcmpl-"):
            return raw
        if raw.startswith("resp_"):
            return "chatcmpl-" + raw[5:]
        if raw:
            return "chatcmpl-" + raw.replace("_", "-")
        return "chatcmpl-" + uuid.uuid4().hex

    @classmethod
    def _event_to_chat_chunks(
        cls,
        event: dict[str, Any],
        state: _ChatStreamState,
        include_usage: bool,
    ) -> list[bytes]:
        event_type = str(event.get("type") or "")
        response = event.get("response") if isinstance(event.get("response"), dict) else None
        chunks: list[bytes] = []
        if response:
            cls._update_stream_meta(state, response)

        if event_type == "response.created":
            chunks.extend(cls._ensure_role_chunk(state))
        elif event_type in {"response.output_text.delta", "response.refusal.delta"}:
            delta = str(event.get("delta") or "")
            if delta:
                chunks.extend(cls._ensure_role_chunk(state))
                field_name = "refusal" if event_type == "response.refusal.delta" else "content"
                chunks.append(cls._sse(cls._chat_chunk(state, {field_name: delta})))
                state.saw_text = True
        elif event_type in {"response.reasoning_summary_text.delta", "response.reasoning_text.delta"}:
            delta = str(event.get("delta") or "")
            if delta:
                chunks.extend(cls._ensure_role_chunk(state))
                chunks.append(cls._sse(cls._chat_chunk(state, {"reasoning_content": delta})))
        elif event_type == "response.output_item.added":
            chunks.extend(cls._stream_tool_added(event, state))
        elif event_type in {"response.function_call_arguments.delta", "response.custom_tool_call_input.delta"}:
            chunks.extend(cls._stream_tool_arguments(event, state))
        elif event_type == "response.output_item.done":
            chunks.extend(cls._stream_tool_done(event, state))
        elif event_type in {"response.completed", "response.done", "response.incomplete"}:
            if response:
                chunks.extend(cls._supplement_completed_response(response, state))
                state.usage = cls._chat_usage(response.get("usage"))
            elif isinstance(event.get("usage"), dict):
                state.usage = cls._chat_usage(event.get("usage"))
            chunks.extend(cls._finalize_stream(state, include_usage, response=response))
        elif event_type in {"response.failed", "error", "response.error"}:
            error = event.get("error")
            if not isinstance(error, dict) and response:
                error = response.get("error")
            if not isinstance(error, dict):
                error = {"message": "OpenAI subscription response failed", "type": "upstream_error"}
            chunks.append(cls._sse({"error": error}))
            chunks.append(b"data: [DONE]\n\n")
            state.finalized = True
        return chunks

    @classmethod
    def _update_stream_meta(cls, state: _ChatStreamState, response: dict[str, Any]) -> None:
        if response.get("id"):
            state.response_id = cls._chat_id(response["id"])
        if response.get("model"):
            state.model = str(response["model"])
        if response.get("created_at"):
            state.created = int(response["created_at"])
        if response.get("service_tier") is not None:
            state.service_tier = str(response["service_tier"])

    @classmethod
    def _ensure_role_chunk(cls, state: _ChatStreamState) -> list[bytes]:
        if state.sent_role:
            return []
        state.sent_role = True
        return [cls._sse(cls._chat_chunk(state, {"role": "assistant"}))]

    @classmethod
    def _stream_tool_added(cls, event: dict[str, Any], state: _ChatStreamState) -> list[bytes]:
        item = event.get("item")
        if not isinstance(item, dict) or str(item.get("type") or "") not in {"function_call", "custom_tool_call"}:
            return []
        output_index = cls._output_index(event, state.next_tool_index)
        if output_index in state.tool_indexes:
            return []
        tool_index = state.next_tool_index
        state.next_tool_index += 1
        state.tool_indexes[output_index] = tool_index
        state.saw_tool_call = True
        chunks = cls._ensure_role_chunk(state)
        function: dict[str, Any] = {"name": str(item.get("name") or "")}
        arguments = item.get("arguments")
        if arguments:
            function["arguments"] = str(arguments)
            state.tool_argument_seen.add(output_index)
        delta = {"tool_calls": [{
            "index": tool_index,
            "id": str(item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex}"),
            "type": "function",
            "function": function,
        }]}
        chunks.append(cls._sse(cls._chat_chunk(state, delta)))
        return chunks

    @classmethod
    def _stream_tool_arguments(cls, event: dict[str, Any], state: _ChatStreamState) -> list[bytes]:
        delta = str(event.get("delta") or "")
        if not delta:
            return []
        output_index = cls._output_index(event, 0)
        chunks: list[bytes] = []
        if output_index not in state.tool_indexes:
            synthetic = {
                "output_index": output_index,
                "item": {
                    "type": "function_call",
                    "call_id": event.get("call_id"),
                    "name": event.get("name"),
                },
            }
            chunks.extend(cls._stream_tool_added(synthetic, state))
        tool_index = state.tool_indexes.get(output_index)
        if tool_index is None:
            return chunks
        state.tool_argument_seen.add(output_index)
        chunks.append(cls._sse(cls._chat_chunk(state, {
            "tool_calls": [{"index": tool_index, "function": {"arguments": delta}}],
        })))
        return chunks

    @classmethod
    def _stream_tool_done(cls, event: dict[str, Any], state: _ChatStreamState) -> list[bytes]:
        item = event.get("item")
        if not isinstance(item, dict) or str(item.get("type") or "") not in {"function_call", "custom_tool_call"}:
            return []
        output_index = cls._output_index(event, state.next_tool_index)
        chunks: list[bytes] = []
        if output_index not in state.tool_indexes:
            chunks.extend(cls._stream_tool_added({"output_index": output_index, "item": item}, state))
        arguments = item.get("arguments")
        if arguments and output_index not in state.tool_argument_seen:
            chunks.extend(cls._stream_tool_arguments({"output_index": output_index, "delta": arguments}, state))
        return chunks

    @classmethod
    def _supplement_completed_response(cls, response: dict[str, Any], state: _ChatStreamState) -> list[bytes]:
        chunks: list[bytes] = []
        content, reasoning, tool_calls, refusal = cls._response_output(response)
        if content and not state.saw_text:
            chunks.extend(cls._ensure_role_chunk(state))
            chunks.append(cls._sse(cls._chat_chunk(state, {"content": content})))
            state.saw_text = True
        if refusal and not state.saw_text:
            chunks.extend(cls._ensure_role_chunk(state))
            chunks.append(cls._sse(cls._chat_chunk(state, {"refusal": refusal})))
            state.saw_text = True
        if reasoning:
            chunks.extend(cls._ensure_role_chunk(state))
            chunks.append(cls._sse(cls._chat_chunk(state, {"reasoning_content": reasoning})))
        if tool_calls and not state.saw_tool_call:
            for item_index, tool_call in enumerate(tool_calls):
                function = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
                chunks.extend(cls._stream_tool_added({
                    "output_index": item_index,
                    "item": {
                        "type": "function_call",
                        "call_id": tool_call.get("id"),
                        "name": function.get("name"),
                        "arguments": function.get("arguments"),
                    },
                }, state))
        return chunks

    @classmethod
    def _finalize_stream(
        cls,
        state: _ChatStreamState,
        include_usage: bool,
        *,
        response: dict[str, Any] | None = None,
    ) -> list[bytes]:
        if state.finalized:
            return []
        state.finalized = True
        chunks = cls._ensure_role_chunk(state)
        finish_reason = cls._finish_reason(response or {}, state.saw_tool_call)
        chunks.append(cls._sse(cls._chat_chunk(state, {}, finish_reason=finish_reason)))
        if include_usage and state.usage is not None:
            usage_chunk: dict[str, Any] = {
                "id": state.response_id,
                "object": "chat.completion.chunk",
                "created": state.created,
                "model": state.model,
                "choices": [],
                "usage": state.usage,
            }
            if state.service_tier:
                usage_chunk["service_tier"] = state.service_tier
            chunks.append(cls._sse(usage_chunk))
        chunks.append(b"data: [DONE]\n\n")
        return chunks

    @staticmethod
    def _output_index(event: dict[str, Any], fallback: int) -> int:
        try:
            return int(event.get("output_index", fallback))
        except (TypeError, ValueError):
            return int(fallback)

    @staticmethod
    def _chat_chunk(
        state: _ChatStreamState,
        delta: dict[str, Any],
        *,
        finish_reason: str | None = None,
    ) -> dict[str, Any]:
        chunk: dict[str, Any] = {
            "id": state.response_id,
            "object": "chat.completion.chunk",
            "created": state.created,
            "model": state.model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        if state.service_tier:
            chunk["service_tier"] = state.service_tier
        return chunk

    @staticmethod
    def _sse(payload: dict[str, Any]) -> bytes:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return f"data: {data}\n\n".encode("utf-8")

    @staticmethod
    async def _iter_sse_events(raw_stream: AsyncIterator[bytes]) -> AsyncIterator[dict[str, Any] | str]:
        decoder = codecs.getincrementaldecoder("utf-8")()
        buffer = ""
        async for raw in raw_stream:
            if not raw:
                continue
            buffer += decoder.decode(raw)
            buffer = buffer.replace("\r\n", "\n")
            while "\n\n" in buffer:
                block, buffer = buffer.split("\n\n", 1)
                parsed = OpenAIChatCompatibilityService._parse_sse_block(block)
                if parsed is not None:
                    yield parsed
        buffer += decoder.decode(b"", final=True)
        buffer = buffer.replace("\r\n", "\n").strip()
        if buffer:
            parsed = OpenAIChatCompatibilityService._parse_sse_block(buffer)
            if parsed is not None:
                yield parsed

    @staticmethod
    def _parse_sse_block(block: str) -> dict[str, Any] | str | None:
        data_lines = []
        for line in block.split("\n"):
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if not data_lines:
            return None
        raw = "\n".join(data_lines).strip()
        if not raw:
            return None
        if raw == "[DONE]":
            return raw
        try:
            value = json.loads(raw)
        except ValueError:
            return None
        return value if isinstance(value, dict) else None


__all__ = ["OpenAIChatCompatibilityService"]
