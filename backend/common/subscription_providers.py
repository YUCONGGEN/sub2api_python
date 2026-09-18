"""Provider metadata shared by subscription account and gateway services."""

from __future__ import annotations

from typing import Any


SUBSCRIPTION_PROVIDERS: dict[str, dict[str, Any]] = {
    "openai": {
        "label": "OpenAI / Codex",
        "protocol": "responses",
        "oauth": True,
        "models": [
            "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5",
            "gpt-5.4", "gpt-5.4-mini", "gpt-5.3-codex-spark",
        ],
    },
    "claude": {
        "label": "Claude / Anthropic",
        "protocol": "anthropic",
        "oauth": True,
        "models": [
            "claude-opus-5", "claude-sonnet-5", "claude-fable-5",
            "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
            "claude-sonnet-4-6", "claude-haiku-4-5-20251001",
        ],
    },
    "grok": {
        "label": "xAI / Grok",
        "protocol": "responses",
        "oauth": True,
        "base_url": "https://cli-chat-proxy.grok.com/v1",
        "models": [
            "grok-4.6", "grok-4.5", "grok-4.3", "grok-build-0.1",
            "grok-composer-2.5-fast",
        ],
    },
    "kimi": {
        "label": "Kimi Coding",
        "protocol": "chat",
        "oauth": False,
        "base_url": "https://api.kimi.com/coding/v1",
        "models": ["kimi-for-coding", "kimi-k2"],
    },
    "zhipu": {
        "label": "智谱 GLM Coding",
        "protocol": "chat",
        "oauth": False,
        "base_url": "https://open.bigmodel.cn/api/coding/paas/v4",
        "models": ["glm-5.3", "glm-5.3-flash", "glm-5.2", "glm-5.1", "glm-5"],
    },
    "minimax": {
        "label": "MiniMax Coding",
        "protocol": "chat",
        "oauth": False,
        "base_url": "https://api.minimaxi.com/v1",
        "models": ["MiniMax-M3", "MiniMax-M2.7", "MiniMax-M2.5"],
    },
}

DEFAULT_MODELS = {
    provider: list(metadata["models"])
    for provider, metadata in SUBSCRIPTION_PROVIDERS.items()
}

OAUTH_PROVIDERS = frozenset(
    provider for provider, metadata in SUBSCRIPTION_PROVIDERS.items() if metadata["oauth"]
)
RESPONSES_PROVIDERS = tuple(
    provider for provider, metadata in SUBSCRIPTION_PROVIDERS.items() if metadata["protocol"] == "responses"
)
CHAT_PROVIDERS = tuple(
    provider for provider, metadata in SUBSCRIPTION_PROVIDERS.items() if metadata["protocol"] == "chat"
)


def provider_label(provider: str) -> str:
    metadata = SUBSCRIPTION_PROVIDERS.get(str(provider or "").strip().lower()) or {}
    return str(metadata.get("label") or provider)


def provider_base_url(provider: str) -> str:
    metadata = SUBSCRIPTION_PROVIDERS.get(str(provider or "").strip().lower()) or {}
    return str(metadata.get("base_url") or "").rstrip("/")


__all__ = [
    "CHAT_PROVIDERS",
    "DEFAULT_MODELS",
    "OAUTH_PROVIDERS",
    "RESPONSES_PROVIDERS",
    "SUBSCRIPTION_PROVIDERS",
    "provider_base_url",
    "provider_label",
]
