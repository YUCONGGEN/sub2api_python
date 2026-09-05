"""Shared compatibility version for the Codex subscription transport.

Resolve once at service startup, never by spawning a CLI in the request path.
This is a transport compatibility setting, not an upstream authorization grant.
"""

import os
import re
from typing import Any


DEFAULT_CODEX_CLIENT_VERSION = "0.153.2"


def codex_client_version(config: dict[str, Any]) -> str:
    version = str(
        config.get("codex-client-version")
        or os.environ.get("ROSE_CODEX_CLIENT_VERSION")
        or DEFAULT_CODEX_CLIENT_VERSION
    ).strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?", version):
        raise ValueError("codex-client-version 必须是有效版本号，例如 0.153.2")
    return version


def codex_identity_headers(version: str) -> dict[str, str]:
    return {
        "Originator": "codex-tui",
        "User-Agent": f"codex-tui/{version}",
        "Version": version,
    }
