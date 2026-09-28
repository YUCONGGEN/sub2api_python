"""Verified reasoning efforts for model IDs used by mapping rules.

Keep this intentionally small. Unknown/provider-defined IDs have no verified
capability set and must not be represented as supporting every effort.
"""

import re


_MODEL_EFFORTS = {
    "gpt-6-astra": ("low", "medium", "high", "xhigh", "max"),
    "gpt-6-sol": ("none", "low", "medium", "high", "xhigh", "max"),
    "gpt-6-luna": ("none", "low", "medium", "high", "xhigh", "max"),
    "gpt-5.6": ("none", "low", "medium", "high", "xhigh", "max"),
    "gpt-5.6-sol": ("none", "low", "medium", "high", "xhigh", "max"),
    "gpt-5.6-terra": ("none", "low", "medium", "high", "xhigh", "max"),
    "gpt-5.6-luna": ("none", "low", "medium", "high", "xhigh", "max"),
    "gpt-5.5": ("none", "low", "medium", "high", "xhigh"),
}
_DATED_SNAPSHOT = re.compile(r"-\d{4}-\d{2}-\d{2}$")


def known_reasoning_efforts(model: str | None) -> tuple[str, ...] | None:
    model_id = str(model or "").strip().lower()
    return _MODEL_EFFORTS.get(_DATED_SNAPSHOT.sub("", model_id))


def model_reasoning_capabilities() -> dict[str, list[str]]:
    return {model: list(efforts) for model, efforts in _MODEL_EFFORTS.items()}
