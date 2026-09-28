"""Request-scoped routing metadata; never added to upstream payloads."""

from contextvars import ContextVar
from functools import wraps

from backend.common.reasoning import requested_reasoning_effort


_trace = ContextVar("rose_routing_trace", default=None)


def routing_scope(handler):
    @wraps(handler)
    async def wrapped(*args, **kwargs):
        token = _trace.set(None)
        try:
            return await handler(*args, **kwargs)
        finally:
            _trace.reset(token)
    return wrapped


def record_mapping(user, payload, mapping):
    _trace.set({
        "requested_model": str(payload.get("model") or ""),
        "requested_effort": str(requested_reasoning_effort(payload) or ""),
        "mapping_id": (mapping or {}).get("id"),
        "mapping_name": (mapping or {}).get("name"),
        "group_id": user.get("effective_group_id"),
        "group_name": user.get("group_name"),
    })


def current_routing_trace():
    return dict(_trace.get() or {})
