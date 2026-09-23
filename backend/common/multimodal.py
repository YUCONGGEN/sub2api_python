"""Helpers for OpenAI-compatible text and multimodal message content.

The proxy accepts both Chat Completions parts (``text``/``image_url``) and
Responses parts (``input_text``/``input_image``).  Keeping this conversion in
one small module prevents protocol adapters, billing and conversation logging
from each implementing a subtly different parser.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from typing import Any


def _as_url(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return str(value.get("url") or value.get("image_url") or "").strip()
    return ""


def extract_image_source(part: Any) -> str | None:
    """Return an image URL/data URL from a supported content part."""
    if not isinstance(part, dict):
        return None
    part_type = str(part.get("type") or "").strip().lower()
    if part_type == "image_url":
        source = _as_url(part.get("image_url"))
    elif part_type == "input_image":
        source = _as_url(part.get("image_url") or part.get("source"))
    elif part_type in {"image", "input_image_url"}:
        source = _as_url(part.get("source") or part.get("url") or part.get("image_url"))
    else:
        return None
    return source or None


def normalize_content(content: Any, *, for_chat: bool = False) -> Any:
    """Normalize supported Responses parts to Chat Completions parts.

    Unknown parts are retained as shallow copies so provider extensions are
    not silently discarded.  The returned value is safe to pass as
    ``springbootai.ai.core.Message.content`` at runtime even though older
    SpringBootAI releases annotate that field as ``str``.  When ``for_chat``
    is true, Responses-only output and reasoning parts are removed or mapped
    to the Chat Completions vocabulary; strict Chat upstreams (for example
    DeepSeek) reject those original part names.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, dict) and for_chat and content.get("type"):
        content = [content]
    if not isinstance(content, list):
        return content if content is not None else ""

    normalized: list[Any] = []
    for raw in content:
        if not isinstance(raw, dict):
            normalized.append(raw)
            continue
        part = dict(raw)
        part_type = str(part.get("type") or "").strip().lower()
        if part_type in {"input_text", "output_text"}:
            part["type"] = "text"
            part["text"] = str(part.get("text") or "")
        elif part_type == "input_image":
            source = extract_image_source(part)
            if source:
                image = {"url": source}
                detail = part.get("detail")
                if detail:
                    image["detail"] = detail
                part = {"type": "image_url", "image_url": image}
            else:
                # Preserve the malformed part for a useful provider error.
                part["type"] = "image_url"
        elif part_type == "image_url":
            source = extract_image_source(part)
            if source and isinstance(part.get("image_url"), str):
                part["image_url"] = {"url": source}
        elif for_chat and part_type in {"reasoning", "compaction", "summary_text"}:
            # These are Responses/Codex bookkeeping parts, not user-visible
            # Chat Completions content.  Forwarding them causes strict
            # upstreams to reject the whole request (HTTP 422).
            continue
        elif for_chat and part_type == "refusal":
            # Chat-compatible providers have no refusal part; retain the
            # visible refusal text without the Responses-only discriminator.
            part = {"type": "text", "text": str(part.get("refusal") or part.get("text") or "")}
        elif for_chat and part_type == "input_file":
            # ``file`` is the closest Chat-compatible representation.  Keep
            # the provider payload intact while removing the Responses-only
            # input_ prefix.
            part["type"] = "file"
        elif for_chat and part_type not in {"text", "image_url", "file"}:
            # Do not leak arbitrary Responses event objects into a Chat
            # request.  If the part has visible text, preserve that text;
            # otherwise omit it.
            if part.get("text") is not None:
                part = {"type": "text", "text": str(part.get("text") or "")}
            else:
                continue
        normalized.append(part)
    return normalized


def normalize_chat_messages(messages: Any) -> list[dict[str, Any]]:
    """Make converted Responses messages valid for strict Chat providers.

    OpenAI Responses history can contain an assistant ``tool_calls`` item
    without the corresponding ``function_call_output`` item (for example
    after a cancelled Codex turn).  Chat Completions requires every tool call
    to be immediately followed by a matching tool message.  Keep complete
    pairs and remove orphaned calls/messages instead of forwarding a payload
    that DeepSeek rejects with HTTP 400.
    """
    if not isinstance(messages, list):
        return []

    normalized: list[dict[str, Any]] = []
    index = 0
    while index < len(messages):
        raw = messages[index]
        if not isinstance(raw, dict):
            index += 1
            continue
        message = dict(raw)
        role = str(message.get("role") or "").strip().lower()
        tool_calls = message.get("tool_calls")
        if role != "assistant" or not isinstance(tool_calls, list):
            if role != "tool":
                normalized.append(message)
            index += 1
            continue

        following: list[dict[str, Any]] = []
        cursor = index + 1
        while cursor < len(messages):
            candidate = messages[cursor]
            if not isinstance(candidate, dict) or str(candidate.get("role") or "").strip().lower() != "tool":
                break
            following.append(dict(candidate))
            cursor += 1
        by_call_id = {
            str(item.get("tool_call_id") or ""): item
            for item in following
            if str(item.get("tool_call_id") or "").strip()
        }
        valid_calls = [
            call for call in tool_calls
            if isinstance(call, dict) and str(call.get("id") or "").strip() in by_call_id
        ]
        if valid_calls:
            message["tool_calls"] = valid_calls
            normalized.append(message)
            valid_ids = {str(call.get("id")) for call in valid_calls}
            normalized.extend(item for item in following if str(item.get("tool_call_id") or "") in valid_ids)
        else:
            # A text-bearing assistant message remains useful; an empty
            # orphan tool-call message is safe to omit completely.
            message.pop("tool_calls", None)
            content = message.get("content")
            if content not in (None, "", []):
                normalized.append(message)
        index = cursor
    return normalized


def text_content(content: Any) -> str:
    """Extract text for token estimation without changing forwarded content."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        values: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type") or "").lower()
            if part_type in {"text", "input_text", "output_text"}:
                values.append(str(part.get("text") or ""))
        return "\n".join(values)
    if isinstance(content, dict):
        return str(content.get("text") or content.get("content") or "")
    return str(content or "")


def iter_image_sources(value: Any):
    """Yield image sources recursively from a request envelope."""
    if isinstance(value, dict):
        source = extract_image_source(value)
        if source:
            yield source
        for child in value.values():
            yield from iter_image_sources(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_image_sources(child)


def parse_dsml_tool_calls(content: Any) -> tuple[str, list[dict[str, Any]]]:
    """Convert DeepSeek DSML tool markup into OpenAI tool-call objects.

    Some compatible upstreams emit ``<｜｜DSML｜｜invoke ...>`` as ordinary
    assistant text even when the request contains OpenAI ``tools``.  Clients
    such as Codex cannot execute that text.  Returning the same call shape as
    an OpenAI provider lets the protocol adapter expose it consistently.
    """
    if not isinstance(content, str):
        return str(content or ""), []
    open_tag = "<\uff5c\uff5cDSML\uff5c\uff5ctool_calls>"
    close_tag = "</\uff5c\uff5cDSML\uff5c\uff5ctool_calls>"
    if open_tag not in content:
        return content, []
    invoke_open = re.escape("<\uff5c\uff5cDSML\uff5c\uff5cinvoke")
    invoke_close = re.escape("</\uff5c\uff5cDSML\uff5cinvoke>")
    invoke_open = re.escape("<" + chr(0xff5c) * 2 + "DSML" + chr(0xff5c) * 2 + "invoke")
    invoke_close = re.escape("</" + chr(0xff5c) * 2 + "DSML" + chr(0xff5c) * 2 + "invoke>")
    invoke_pattern = re.compile(invoke_open + r'\s+name=["\']([^"\']+)["\']\s*>(.*?)' + invoke_close, re.DOTALL)
    parameter_open = re.escape("<" + chr(0xff5c) * 2 + "DSML" + chr(0xff5c) * 2 + "parameter")
    parameter_close = re.escape("</" + chr(0xff5c) * 2 + "DSML" + chr(0xff5c) * 2 + "parameter>")
    parameter_pattern = re.compile(
        parameter_open + r'\s+name=["\']([^"\']+)["\'][^>]*>(.*?)' + parameter_close,
        re.DOTALL,
    )
    calls: list[dict[str, Any]] = []
    for index, match in enumerate(invoke_pattern.finditer(content)):
        name = html.unescape(match.group(1)).strip()
        arguments: dict[str, Any] = {}
        for parameter in parameter_pattern.finditer(match.group(2)):
            key = html.unescape(parameter.group(1)).strip()
            value = html.unescape(parameter.group(2))
            arguments[key] = value
        call_seed = f"{index}:{name}:{json.dumps(arguments, ensure_ascii=False, sort_keys=True)}"
        calls.append({
            "id": "call_dsml_" + hashlib.sha256(call_seed.encode("utf-8")).hexdigest()[:24],
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))},
        })
    if not calls:
        return content, []
    cleaned = invoke_pattern.sub("", content)
    cleaned = cleaned.replace(open_tag, "").replace(close_tag, "").strip()
    return cleaned, calls
__all__ = [
    "extract_image_source",
    "normalize_content",
    "normalize_chat_messages",
    "text_content",
    "iter_image_sources",
    "parse_dsml_tool_calls",
]
