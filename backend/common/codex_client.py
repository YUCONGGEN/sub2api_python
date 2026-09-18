"""Shared compatibility version for the Codex subscription transport.

Resolve once at service startup, never by spawning a CLI in the request path.
This is a transport compatibility setting, not an upstream authorization grant.
"""

import os
import re
from typing import Any


DEFAULT_CODEX_CLIENT_VERSION = "0.153.2"
DEFAULT_CODEX_RELEASE_URL = "https://api.github.com/repos/openai/codex/releases/latest"


def normalize_codex_client_version(value: Any) -> str:
    """Return a safe semantic Codex version or an empty string.

    GitHub release tags currently use forms such as ``rust-v0.153.2`` while
    the upstream request headers must contain only ``0.153.2``.  Keeping the
    parser here gives configuration and automatic release discovery the same
    injection-safe validation rules.
    """
    text = str(value or "").strip()
    match = re.fullmatch(
        r"(?:rust-)?v?([0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?)",
        text,
    )
    return match.group(1) if match else ""


def codex_client_version(config: dict[str, Any]) -> str:
    raw = str(
        config.get("codex-client-version")
        or os.environ.get("ROSE_CODEX_CLIENT_VERSION")
        or DEFAULT_CODEX_CLIENT_VERSION
    ).strip()
    version = normalize_codex_client_version(raw)
    if not version:
        raise ValueError("codex-client-version 必须是有效版本号，例如 0.153.2")
    return version


def codex_client_version_is_pinned(config: dict[str, Any]) -> bool:
    """Whether an administrator explicitly fixed the outbound version."""
    return bool(
        str(config.get("codex-client-version") or "").strip()
        or str(os.environ.get("ROSE_CODEX_CLIENT_VERSION") or "").strip()
    )


def codex_identity_headers(version: str) -> dict[str, str]:
    return {
        "Originator": "codex-tui",
        "User-Agent": f"codex-tui/{version}",
        "Version": version,
    }
