"""SpringBootAI-managed bridge between OpenAI Chat and Anthropic Messages."""

from __future__ import annotations

import codecs
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from springbootai import Service, Slf4j


@dataclass
class _ClaudeChatStreamState:
    response_id: str
    model: str
    created: int
    sent_role: bool = False
    saw_tool_call: bool = False
    finalized: bool = False
    next_tool_index: int = 0
    block_tools: dict[int, int] = field(default_factory=dict)
    stop_reason: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cache_creation_tokens: int = 0


@Service("claude_chat_compatibility_service")
@Slf4j
class ClaudeChatCompatibilityService:
    """Convert `/v1/chat/completions` calls to Anthropic Messages."""

    def to_anthropic(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        model = str(payload.get("model") or "").strip()
        if not model:
            raise ValueError("model is required")
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            raise ValueError("messages is required")

        system_parts: list[str] = []
        converted_messages: list[dict[str, Any]] = []
        for message in messages:
            if not isinstance(message, dict):
                raise ValueError("each message must be a JSON object")
            role = str(message.get("role") or "user").strip().lower()
            if role in {"system", "developer"}:
                text = self._text_content(message.get("content"))
                if text:
                    system_parts.append(text)
                continue
            if role == "assistant":
                blocks = self._assistant_blocks(message)
                self._append_message(converted_messages, "assistant", blocks or [{"type": "text", "text": ""}])
                continue
            if role in {"tool", "function"}:
                call_id = message.get("tool_call_id") or message.get("name")
                if not call_id:
                    raise ValueError("tool message requires tool_call_id")
                block = {
                    "type": "tool_result",
                    "tool_use_id": str(call_id),
                    "content": self._tool_output(message.get("content")),
                }
                self._append_message(converted_messages, "user", [block])
                continue
            self._append_message(converted_messages, "user", self._user_blocks(message.get("content")))

        if not converted_messages:
            raise ValueError("messages must contain at least one user or assistant message")
        max_tokens = payload.get("max_completion_tokens")
        if max_tokens is None:
            max_tokens = payload.get("max_tokens")
        try:
            max_tokens = int(max_tokens or 4096)
        except (TypeError, ValueError) as exc:
            raise ValueError("max_completion_tokens must be an integer") from exc

        outgoing: dict[str, Any] = {
            "model": model,
            "messages": converted_messages,
            "max_tokens": max(1, max_tokens),
            "stream": bool(payload.get("stream")),
        }
        instructions = payload.get("instructions")
        if isinstance(instructions, str) and instructions.strip():
            system_parts.insert(0, instructions)
        if system_parts:
            outgoing["system"] = "\n\n".join(system_parts)

        for key in ("temperature", "top_p", "top_k", "service_tier"):
            if payload.get(key) is not None:
                outgoing[key] = payload[key]
        stop = payload.get("stop")
        if isinstance(stop, str) and stop:
            outgoing["stop_sequences"] = [stop]
        elif isinstance(stop, list):
            outgoing["stop_sequences"] = [str(item) for item in stop if str(item)]

        tools = self._tools(payload.get("tools"), payload.get("functions"))
        if tools:
            outgoing["tools"] = tools
            tool_choice = self._tool_choice(payload.get("tool_choice"), payload.get("function_call"))
            if tool_choice:
                outgoing["tool_choice"] = tool_choice

        source_metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        session_id = source_metadata.get("user_id") or payload.get("prompt_cache_key") or payload.get("user")
        if session_id:
            outgoing["metadata"] = {"user_id": str(session_id)}
        return outgoing

    def from_anthropic_bytes(self, body: bytes, requested_model: str) -> dict[str, Any]:
        try:
            message = json.loads(body.decode("utf-8"))
        except (UnicodeError, ValueError, TypeError) as exc:
            raise ValueError("Claude subscription returned invalid JSON") from exc
        if not isinstance(message, dict):
            raise ValueError("Claude subscription returned a non-object response")
        return self.from_anthropic(message, requested_model)

    def from_anthropic(self, message: dict[str, Any], requested_model: str) -> dict[str, Any]:
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        blocks = message.get("content")
        if not isinstance(blocks, list):
            blocks = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "")
            if block_type == "text":
                content_parts.append(str(block.get("text") or ""))
            elif block_type in {"thinking", "redacted_thinking"}:
                reasoning_parts.append(str(block.get("thinking") or ""))
            elif block_type == "tool_use":
                tool_calls.append({
                    "id": str(block.get("id") or f"call_{uuid.uuid4().hex}"),
                    "type": "function",
                    "function": {
                        "name": str(block.get("name") or ""),
                        "arguments": self._json_arguments(block.get("input")),
                    },
                })
        assistant: dict[str, Any] = {
            "role": "assistant",
            "content": "".join(content_parts) if content_parts else (None if tool_calls else ""),
        }
        if reasoning_parts:
            assistant["reasoning_content"] = "".join(reasoning_parts)
        if tool_calls:
            assistant["tool_calls"] = tool_calls
        usage = self._chat_usage(message.get("usage"))
        return {
            "id": self._chat_id(message.get("id")),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": str(message.get("model") or requested_model or ""),
            "choices": [{
                "index": 0,
                "message": assistant,
                "finish_reason": self._finish_reason(message.get("stop_reason"), bool(tool_calls)),
            }],
            "usage": usage,
        }

    def error_from_anthropic_bytes(self, body: bytes, fallback_status: int) -> bytes:
        message = f"Claude subscription returned HTTP {int(fallback_status)}"
        error_type = "upstream_error"
        try:
            payload = json.loads(body.decode("utf-8"))
            error = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(error, dict):
                message = str(error.get("message") or message)
                error_type = str(error.get("type") or error_type)
        except (UnicodeError, ValueError, TypeError):
            pass
        return json.dumps(
            {"error": {"message": message, "type": error_type}},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

    async def stream_from_anthropic(
        self,
        raw_stream: AsyncIterator[bytes],
        requested_model: str,
        *,
        include_usage: bool = False,
    ) -> AsyncIterator[bytes]:
        state = _ClaudeChatStreamState(
            response_id=self._chat_id(None),
            model=str(requested_model or ""),
            created=int(time.time()),
        )
        try:
            async for event in self._iter_sse_events(raw_stream):
                if event == "[DONE]":
                    for chunk in self._finalize(state, include_usage):
                        yield chunk
                    return
                if not isinstance(event, dict):
                    continue
                for chunk in self._event_chunks(event, state, include_usage):
                    yield chunk
                if state.finalized:
                    return
        except Exception as exc:
            if not state.finalized:
                yield self._sse({"error": {"message": str(exc), "type": "upstream_error"}})
                yield b"data: [DONE]\n\n"
                state.finalized = True
            return
        for chunk in self._finalize(state, include_usage):
            yield chunk

    @classmethod
    def _append_message(cls, messages: list[dict[str, Any]], role: str, blocks: list[dict[str, Any]]) -> None:
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"].extend(blocks)
        else:
            messages.append({"role": role, "content": list(blocks)})

    @classmethod
    def _user_blocks(cls, content: Any) -> list[dict[str, Any]]:
        if content is None:
            return [{"type": "text", "text": ""}]
        if isinstance(content, str):
            return [{"type": "text", "text": content}]
        if not isinstance(content, list):
            return [{"type": "text", "text": str(content)}]
        blocks: list[dict[str, Any]] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type") or "")
            if part_type in {"text", "input_text"}:
                blocks.append({"type": "text", "text": str(part.get("text") or "")})
            elif part_type in {"image_url", "input_image"}:
                image = part.get("image_url")
                if isinstance(image, dict):
                    image = image.get("url")
                converted = cls._image_block(str(image or ""))
                if converted:
                    blocks.append(converted)
        return blocks or [{"type": "text", "text": ""}]

    @classmethod
    def _assistant_blocks(cls, message: dict[str, Any]) -> list[dict[str, Any]]:
        blocks = []
        text = cls._text_content(message.get("content"))
        if text:
            blocks.append({"type": "text", "text": text})
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list):
            for tool_call in tool_calls:
                if not isinstance(tool_call, dict) or not isinstance(tool_call.get("function"), dict):
                    continue
                function = tool_call["function"]
                if not function.get("name"):
                    continue
                blocks.append({
                    "type": "tool_use",
                    "id": str(tool_call.get("id") or f"call_{uuid.uuid4().hex}"),
                    "name": str(function["name"]),
                    "input": cls._argument_object(function.get("arguments")),
                })
        return blocks

    @staticmethod
    def _text_content(content: Any) -> str:
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return "" if content is None else str(content)
        return "".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict) and str(part.get("type") or "") in {"text", "input_text", "output_text"}
        )

    @classmethod
    def _tool_output(cls, content: Any) -> str:
        text = cls._text_content(content)
        if text:
            return text
        if content is None:
            return "(empty)"
        return json.dumps(content, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _image_block(url: str) -> dict[str, Any] | None:
        if not url:
            return None
        matched = re.match(r"^data:([^;,]+);base64,(.+)$", url, re.DOTALL)
        if matched:
            return {
                "type": "image",
                "source": {"type": "base64", "media_type": matched.group(1), "data": matched.group(2)},
            }
        return {"type": "image", "source": {"type": "url", "url": url}}

    @classmethod
    def _tools(cls, tools: Any, functions: Any) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        candidates = []
        if isinstance(tools, list):
            candidates.extend(
                tool.get("function") for tool in tools
                if isinstance(tool, dict) and str(tool.get("type") or "function") == "function"
            )
        if isinstance(functions, list):
            candidates.extend(functions)
        for function in candidates:
            if not isinstance(function, dict) or not function.get("name"):
                continue
            tool = {
                "name": str(function["name"]),
                "input_schema": function.get("parameters") if isinstance(function.get("parameters"), dict) else {"type": "object"},
            }
            if function.get("description") is not None:
                tool["description"] = str(function["description"])
            result.append(tool)
        return result

    @staticmethod
    def _tool_choice(tool_choice: Any, function_call: Any) -> dict[str, Any] | None:
        value = tool_choice if tool_choice is not None else function_call
        if isinstance(value, str):
            if value == "auto":
                return {"type": "auto"}
            if value in {"required", "any"}:
                return {"type": "any"}
            return None
        if not isinstance(value, dict):
            return None
        nested = value.get("function") if isinstance(value.get("function"), dict) else value
        if nested.get("name"):
            return {"type": "tool", "name": str(nested["name"])}
        return None

    @staticmethod
    def _argument_object(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str) and value.strip():
            try:
                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    return parsed
                return {"input": parsed}
            except ValueError:
                return {"input": value}
        return {}

    @staticmethod
    def _json_arguments(value: Any) -> str:
        if isinstance(value, str):
            return value or "{}"
        return json.dumps(value if value is not None else {}, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _finish_reason(stop_reason: Any, has_tools: bool) -> str:
        reason = str(stop_reason or "")
        if reason == "max_tokens":
            return "length"
        if reason == "tool_use" or has_tools:
            return "tool_calls"
        if reason == "refusal":
            return "content_filter"
        return "stop"

    @staticmethod
    def _chat_usage(value: Any) -> dict[str, Any]:
        usage = value if isinstance(value, dict) else {}
        cached = int(usage.get("cache_read_input_tokens") or 0)
        created = int(usage.get("cache_creation_input_tokens") or 0)
        prompt = int(usage.get("input_tokens") or 0) + cached + created
        completion = int(usage.get("output_tokens") or 0)
        result: dict[str, Any] = {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        }
        if cached or created:
            result["prompt_tokens_details"] = {
                "cached_tokens": cached,
                "cache_creation_tokens": created,
            }
        return result

    @staticmethod
    def _chat_id(value: Any) -> str:
        raw = str(value or "").strip()
        if raw.startswith("chatcmpl-"):
            return raw
        if raw.startswith("msg_"):
            return "chatcmpl-" + raw[4:]
        return "chatcmpl-" + (raw.replace("_", "-") if raw else uuid.uuid4().hex)

    @classmethod
    def _event_chunks(
        cls,
        event: dict[str, Any],
        state: _ClaudeChatStreamState,
        include_usage: bool,
    ) -> list[bytes]:
        event_type = str(event.get("type") or "")
        chunks: list[bytes] = []
        if event_type == "message_start":
            message = event.get("message") if isinstance(event.get("message"), dict) else {}
            if message.get("id"):
                state.response_id = cls._chat_id(message["id"])
            if message.get("model"):
                state.model = str(message["model"])
            cls._update_usage(state, message.get("usage"))
            chunks.extend(cls._ensure_role(state))
        elif event_type == "content_block_start":
            block = event.get("content_block") if isinstance(event.get("content_block"), dict) else {}
            if str(block.get("type") or "") == "tool_use":
                block_index = cls._index(event, state.next_tool_index)
                tool_index = state.next_tool_index
                state.next_tool_index += 1
                state.block_tools[block_index] = tool_index
                state.saw_tool_call = True
                chunks.extend(cls._ensure_role(state))
                function = {"name": str(block.get("name") or "")}
                if block.get("input"):
                    function["arguments"] = cls._json_arguments(block.get("input"))
                chunks.append(cls._sse(cls._chunk(state, {"tool_calls": [{
                    "index": tool_index,
                    "id": str(block.get("id") or f"call_{uuid.uuid4().hex}"),
                    "type": "function",
                    "function": function,
                }]})))
        elif event_type == "content_block_delta":
            delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
            delta_type = str(delta.get("type") or "")
            if delta_type == "text_delta" and delta.get("text") is not None:
                chunks.extend(cls._ensure_role(state))
                chunks.append(cls._sse(cls._chunk(state, {"content": str(delta.get("text") or "")})))
            elif delta_type == "thinking_delta" and delta.get("thinking") is not None:
                chunks.extend(cls._ensure_role(state))
                chunks.append(cls._sse(cls._chunk(state, {"reasoning_content": str(delta.get("thinking") or "")})))
            elif delta_type == "input_json_delta" and delta.get("partial_json") is not None:
                block_index = cls._index(event, 0)
                tool_index = state.block_tools.get(block_index)
                if tool_index is not None:
                    chunks.append(cls._sse(cls._chunk(state, {"tool_calls": [{
                        "index": tool_index,
                        "function": {"arguments": str(delta.get("partial_json") or "")},
                    }]})))
        elif event_type == "message_delta":
            delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
            state.stop_reason = str(delta.get("stop_reason") or state.stop_reason)
            cls._update_usage(state, event.get("usage"))
        elif event_type == "message_stop":
            chunks.extend(cls._finalize(state, include_usage))
        elif event_type == "error":
            error = event.get("error") if isinstance(event.get("error"), dict) else {
                "message": "Claude subscription stream failed", "type": "upstream_error",
            }
            chunks.append(cls._sse({"error": error}))
            chunks.append(b"data: [DONE]\n\n")
            state.finalized = True
        return chunks

    @classmethod
    def _update_usage(cls, state: _ClaudeChatStreamState, value: Any) -> None:
        if not isinstance(value, dict):
            return
        state.prompt_tokens = max(state.prompt_tokens, int(value.get("input_tokens") or 0))
        state.completion_tokens = max(state.completion_tokens, int(value.get("output_tokens") or 0))
        state.cached_tokens = max(state.cached_tokens, int(value.get("cache_read_input_tokens") or 0))
        state.cache_creation_tokens = max(state.cache_creation_tokens, int(value.get("cache_creation_input_tokens") or 0))

    @classmethod
    def _ensure_role(cls, state: _ClaudeChatStreamState) -> list[bytes]:
        if state.sent_role:
            return []
        state.sent_role = True
        return [cls._sse(cls._chunk(state, {"role": "assistant"}))]

    @classmethod
    def _finalize(cls, state: _ClaudeChatStreamState, include_usage: bool) -> list[bytes]:
        if state.finalized:
            return []
        state.finalized = True
        chunks = cls._ensure_role(state)
        chunks.append(cls._sse(cls._chunk(
            state, {}, finish_reason=cls._finish_reason(state.stop_reason, state.saw_tool_call),
        )))
        if include_usage:
            prompt = state.prompt_tokens + state.cached_tokens + state.cache_creation_tokens
            usage = {
                "prompt_tokens": prompt,
                "completion_tokens": state.completion_tokens,
                "total_tokens": prompt + state.completion_tokens,
            }
            chunks.append(cls._sse({
                "id": state.response_id,
                "object": "chat.completion.chunk",
                "created": state.created,
                "model": state.model,
                "choices": [],
                "usage": usage,
            }))
        chunks.append(b"data: [DONE]\n\n")
        return chunks

    @staticmethod
    def _chunk(
        state: _ClaudeChatStreamState,
        delta: dict[str, Any],
        *,
        finish_reason: str | None = None,
    ) -> dict[str, Any]:
        return {
            "id": state.response_id,
            "object": "chat.completion.chunk",
            "created": state.created,
            "model": state.model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }

    @staticmethod
    def _sse(payload: dict[str, Any]) -> bytes:
        return ("data: " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n\n").encode("utf-8")

    @staticmethod
    def _index(event: dict[str, Any], fallback: int) -> int:
        try:
            return int(event.get("index", fallback))
        except (TypeError, ValueError):
            return int(fallback)

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
                parsed = ClaudeChatCompatibilityService._parse_sse_block(block)
                if parsed is not None:
                    yield parsed
        buffer += decoder.decode(b"", final=True)
        parsed = ClaudeChatCompatibilityService._parse_sse_block(buffer.replace("\r\n", "\n").strip())
        if parsed is not None:
            yield parsed

    @staticmethod
    def _parse_sse_block(block: str) -> dict[str, Any] | str | None:
        data = "\n".join(line[5:].lstrip() for line in block.split("\n") if line.startswith("data:")).strip()
        if not data:
            return None
        if data == "[DONE]":
            return data
        try:
            value = json.loads(data)
        except ValueError:
            return None
        return value if isinstance(value, dict) else None


__all__ = ["ClaudeChatCompatibilityService"]
