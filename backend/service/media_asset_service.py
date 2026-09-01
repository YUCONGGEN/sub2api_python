"""Compatibility bean for the disabled media-retention feature.

Images and other multimodal parts are forwarded unchanged by the protocol
adapters. This bean intentionally performs no filesystem or database work:
the proxy records only usage metadata (tokens, cost, status and timings).
"""

from typing import Any

from springbootai import PostConstruct, Service, get_config


@Service("media_asset_service")
class MediaAssetService:
    """No-op compatibility service; media is never persisted."""

    def __init__(self):
        self.enabled = False

    @PostConstruct
    def init(self):
        # Read the section only to remain tolerant of legacy YAML files. The
        # value is deliberately ignored so storage cannot be turned on.
        get_config().get("rose", {}).get("conversation", {}).get("media", {})
        self.enabled = False

    def persist_request(self, conversation_id: int | None, request_id: str, messages: Any) -> list[dict[str, Any]]:
        """Return no assets; multimodal data is forwarded but not retained."""
        return []

    def persist_and_record(self, mapper, conversation_id: int | None, request_id: str, messages: Any) -> int:
        """Legacy integration hook that intentionally does nothing."""
        return 0


__all__ = ["MediaAssetService"]
