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


def normalize_content(content: Any) -> Any:
    """Normalize supported Responses parts to Chat Completions parts.

    Unknown parts are retained as shallow copies so provider extensions are
    not silently discarded.  The returned value is safe to pass as
    ``springbootai.ai.core.Message.content`` at runtime even though older
    SpringBootAI releases annotate that field as ``str``.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return content if content is not None else ""

    normalized: list[Any] = []
    for raw in content:
        if not isinstance(raw, dict):
            normalized.append(raw)
            continue
        part = dict(raw)
        part_type = str(part.get("type") or "").strip().lower()
        if part_type == "input_text":
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
        normalized.append(part)
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
__all__ = ["extract_image_source", "normalize_content", "text_content", "iter_image_sources", "parse_dsml_tool_calls"]
