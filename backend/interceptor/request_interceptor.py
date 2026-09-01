"""Small request-context interceptor for request IDs and timing."""

from __future__ import annotations

import time
import uuid
from typing import Any

from springbootai.annotations import Autowired, Component, Slf4j
from springbootai.web import HandlerInterceptor

from backend.service.observability_service import ObservabilityService


@Component
@Slf4j
class RequestContextInterceptor(HandlerInterceptor):
    """Attach a correlation ID without changing request or response bodies."""

    @Autowired
    def __init__(self, observability_service: ObservabilityService):
        # Parameter name matches @Service("observability_service") for older
        # SpringBootAI builds that resolve constructor dependencies by name.
        self.observability = observability_service

    def pre_handle(self, request: Any, handler: Any) -> bool:
        request.state.rose_started_at = time.perf_counter()
        request.state.rose_request_id = (
            request.headers.get("X-Request-ID", "").strip() or uuid.uuid4().hex
        )
        return True

    def post_handle(self, request: Any, response: Any, handler: Any) -> None:
        request_id = getattr(request.state, "rose_request_id", "")
        if request_id and hasattr(response, "headers"):
            response.headers.setdefault("X-Request-ID", request_id)
        self._record(request, response, None)

    def after_completion(
        self,
        request: Any,
        response: Any,
        handler: Any,
        exception: Exception | None = None,
    ) -> None:
        started = getattr(request.state, "rose_started_at", None)
        if started is None:
            return
        self._record(request, response, exception)
        elapsed_ms = (time.perf_counter() - started) * 1000
        path = getattr(getattr(request, "url", None), "path", "unknown")
        status = getattr(response, "status_code", 500 if exception else 200)
        request_id = getattr(request.state, "rose_request_id", "")

    def _record(self, request: Any, response: Any, exception: Exception | None) -> None:
        if getattr(request.state, "rose_recorded", False) and exception is None:
            return
        started = getattr(request.state, "rose_started_at", None)
        if started is None:
            return
        request.state.rose_recorded = True
        elapsed_ms = (time.perf_counter() - started) * 1000
        path = getattr(getattr(request, "url", None), "path", "unknown")
        status = getattr(response, "status_code", 500 if exception else 200)
        request_id = getattr(request.state, "rose_request_id", "")
        try:
            self.observability.record_request(
                getattr(request, "method", ""),
                path,
                status,
                int(elapsed_ms),
                request_id,
                exception,
            )
        except Exception as exc:
            # Metrics are best effort and must not interfere with responses.
            self.logger.warning("request metrics record skipped: %s", exc)
        message = "HTTP method=%s path=%s status=%s duration_ms=%.1f request_id=%s"
        if exception:
            self.logger.warning(
                message + " error=%s",
                getattr(request, "method", "GET"), path, status, elapsed_ms,
                request_id, type(exception).__name__,
            )
        elif int(status or 0) >= 500:
            self.logger.error(
                message,
                getattr(request, "method", "GET"), path, status, elapsed_ms, request_id,
            )
        elif int(status or 0) >= 400:
            self.logger.warning(
                message,
                getattr(request, "method", "GET"), path, status, elapsed_ms, request_id,
            )
        else:
            self.logger.info(
                message,
                getattr(request, "method", "GET"), path, status, elapsed_ms, request_id,
            )


__all__ = ["RequestContextInterceptor"]
