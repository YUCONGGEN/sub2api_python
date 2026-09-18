"""Request-level reasoning defaults, shared by API and subscription transports."""

import re
from typing import Any


DEFAULT_GPT_REASONING_EFFORT = "high"
VALID_REASONING_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"})


def configured_gpt_reasoning_effort(config: dict[str, Any]) -> str:
    """Read once at startup; never parse YAML in the request/stream path."""
    raw = config.get("rose", {}).get("proxy", {}).get("default-gpt-reasoning-effort")
    effort = str(raw or DEFAULT_GPT_REASONING_EFFORT).strip().lower() or DEFAULT_GPT_REASONING_EFFORT
    if effort not in VALID_REASONING_EFFORTS:
        raise ValueError("rose.proxy.default-gpt-reasoning-effort 不是有效推理档位，例如 low、medium、high")
    return effort


# Only text/code GPT reasoning families. Do not inject this parameter into
# GPT-4/4o, chat-latest, image/audio models, or arbitrary third-party aliases.
_GPT_REASONING_MODEL = re.compile(
    r"gpt-(?:5(?:\.\d+)?(?:-(?:sol|terra|luna|mini|nano|pro|codex(?:-(?:mini|max|spark))?))?"
    r"|6-astra)(?:-\d{4}-\d{2}-\d{2})?"
)


def default_gpt_reasoning_effort(model: str | None, default_effort: str = DEFAULT_GPT_REASONING_EFFORT) -> str | None:
    """This gateway's default, not a claim about the provider's own default."""
    return default_effort if _GPT_REASONING_MODEL.fullmatch(str(model or "").strip().lower()) else None


def requested_reasoning_effort(payload: dict[str, Any]) -> Any:
    """Resolve explicit effort without treating 'none' as an omitted value.

    Responses' nested value takes precedence, then Chat's canonical field and
    the legacy hyphenated alias. Empty/null values are unspecified; other
    invalid values remain explicit so they are not silently promoted to high.
    """
    reasoning = payload.get("reasoning")
    values = (
        reasoning.get("effort") if isinstance(reasoning, dict) else None,
        payload.get("reasoning_effort"),
        payload.get("reasoning-effort"),
    )
    for value in values:
        if value is not None and not (isinstance(value, str) and not value.strip()):
            return value
    return None


def validated_reasoning_effort(payload: dict[str, Any]) -> str | None:
    """Validate an explicitly supplied effort at the subscription boundary.

    Generic API-key upstreams retain their existing provider-driven validation.
    Subscription transports are stricter because an invalid value would be
    retried against shared accounts even though the request itself is bad.
    """
    effort = requested_reasoning_effort(payload)
    if effort is None:
        return None
    if not isinstance(effort, str):
        raise ValueError("reasoning effort 必须是字符串")
    normalized = effort.strip().lower()
    if normalized not in VALID_REASONING_EFFORTS:
        allowed = ", ".join(sorted(VALID_REASONING_EFFORTS))
        raise ValueError(f"reasoning effort 必须是以下值之一：{allowed}")
    return normalized


def with_responses_reasoning(payload: dict[str, Any], default_effort: str = DEFAULT_GPT_REASONING_EFFORT) -> dict[str, Any]:
    """Copy the request and use the actual Responses wire format for effort."""
    outgoing = dict(payload)
    reasoning = payload.get("reasoning")
    if reasoning is not None and not isinstance(reasoning, dict):
        # Preserve malformed structures for normal validation, rather than
        # hiding a client error by replacing the entire object with a default.
        return outgoing
    effort = requested_reasoning_effort(payload)
    if effort is None:
        effort = default_gpt_reasoning_effort(payload.get("model"), default_effort)
    if effort is not None:
        outgoing["reasoning"] = {**(reasoning or {}), "effort": effort}
        outgoing.pop("reasoning_effort", None)
        outgoing.pop("reasoning-effort", None)
    return outgoing
