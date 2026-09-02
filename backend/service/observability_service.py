"""Administrative analytics, request health and host telemetry.

The service deliberately keeps SQL in ``StoreMapper.xml``. Runtime counters
are cheap in-memory values while historical charts and warning/error records
are read from SQLite through the mapper.
"""

from __future__ import annotations

import platform
import math
import socket
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psutil
from springbootai import Autowired, PostConstruct, Service, get_config

from backend.common.time_utils import business_date_keys, business_day_start_utc


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _page_result(items: list[dict], total: int, page: int, page_size: int) -> dict[str, Any]:
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 5))
    total = max(0, int(total))
    pages = max(1, (total + page_size - 1) // page_size)
    return {
        "items": [dict(item) for item in items],
        "total": total,
        "page": min(page, pages),
        "page_size": page_size,
        "pages": pages,
    }


@Service("observability_service")
class ObservabilityService:
    """Collect admin-safe operational metrics without storing request bodies."""

    @Autowired
    def __init__(self, store_repository):
        self.mapper = store_repository.mapper
        self._lock = threading.Lock()
        self._started_at = time.monotonic()
        self._total_requests = 0
        self._failed_requests = 0
        self._dropped_requests = 0
        self._warning_count = 0
        self._exception_count = 0
        self._latency_total_ms = 0
        self._last_request_at: str | None = None
        self._recent_requests: deque[float] = deque()
        self._recent_samples: deque[tuple[float, int, int, str]] = deque(maxlen=50000)

    @PostConstruct
    def init(self) -> None:
        self._started_at = time.monotonic()

    def record_request(
        self,
        method: str,
        path: str,
        status_code: int,
        latency_ms: int,
        request_id: str = "",
        exception: Exception | None = None,
    ) -> None:
        """Record one completed HTTP request and persist only bad events."""
        try:
            status = int(status_code or 0)
        except (TypeError, ValueError):
            status = 500 if exception else 200
        latency = max(0, int(latency_ms or 0))
        created_at = _now()
        now_mono = time.monotonic()
        failed = bool(exception) or status >= 400
        dropped = bool(exception) or status >= 500
        level = "ERROR" if dropped else "WARN" if failed else ""
        event_type = "EXCEPTION" if exception else "HTTP_ERROR" if failed else "REQUEST"
        message = str(exception)[:1000] if exception else f"HTTP {status}"
        with self._lock:
            self._total_requests += 1
            self._failed_requests += int(failed)
            self._dropped_requests += int(dropped)
            self._warning_count += int(level == "WARN")
            self._exception_count += int(level == "ERROR")
            self._latency_total_ms += latency
            self._last_request_at = created_at
            self._recent_requests.append(now_mono)
            self._recent_samples.append((now_mono, latency, status, str(path or "")))
            self._prune_recent(now_mono)
        if level:
            try:
                self.mapper.insert_admin_event({
                    "level": level,
                    "event_type": event_type,
                    "message": message,
                    "method": str(method or "")[:16],
                    "path": str(path or "")[:500],
                    "status_code": status,
                    "latency_ms": latency,
                    "request_id": str(request_id or "")[:128],
                    "created_at": created_at,
                })
            except Exception:
                # Observability must never turn a completed request into a 500.
                pass

    def _prune_recent(self, now_mono: float | None = None) -> None:
        now_mono = now_mono or time.monotonic()
        cutoff = now_mono - 60
        while self._recent_requests and self._recent_requests[0] < cutoff:
            self._recent_requests.popleft()
        history_cutoff = now_mono - 86400
        while self._recent_samples and self._recent_samples[0][0] < history_cutoff:
            self._recent_samples.popleft()

    def request_metrics(self) -> dict[str, Any]:
        with self._lock:
            self._prune_recent()
            total = self._total_requests
            failed = self._failed_requests
            dropped = self._dropped_requests
            recent = len(self._recent_requests)
            latency = self._latency_total_ms / total if total else 0
            samples = list(self._recent_samples)
            latencies = sorted(item[1] for item in samples)
            p95_index = max(0, min(len(latencies) - 1, math.ceil(len(latencies) * 0.95) - 1)) if latencies else 0
            snapshot = {
                "runtime_requests": total,
                "runtime_failed_requests": failed,
                "runtime_dropped_requests": dropped,
                "warnings": self._warning_count,
                "exceptions": self._exception_count,
                "requests_last_minute": recent,
                "throughput_rpm": recent,
                "throughput_rps": round(recent / 60, 3),
                "failure_rate": round((failed / total) * 100, 2) if total else 0,
                "packet_loss_rate": round((dropped / total) * 100, 2) if total else 0,
                "average_latency_ms": round(latency, 1),
                "p95_latency_ms": latencies[p95_index] if latencies else 0,
                "requests_24h_runtime": len(samples),
                "rate_limited_24h_runtime": sum(1 for item in samples if item[2] == 429),
                "last_request_at": self._last_request_at,
                "uptime_seconds": max(0, int(time.monotonic() - self._started_at)),
            }
        try:
            snapshot["persisted_warning_events"] = int(self.mapper.count_admin_events("WARN") or 0)
            snapshot["persisted_exception_events"] = int(self.mapper.count_admin_events("ERROR") or 0)
        except Exception:
            snapshot["persisted_warning_events"] = snapshot["warnings"]
            snapshot["persisted_exception_events"] = snapshot["exceptions"]
        return snapshot

    def logs_page(self, page: int = 1, page_size: int = 5, level: str | None = None) -> dict[str, Any]:
        level = str(level or "").upper().strip()
        if level not in {"WARN", "ERROR"}:
            level = ""
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 5))
        total = int(self.mapper.count_admin_events(level) or 0)
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        rows = self.mapper.list_admin_events(level, (page - 1) * page_size, page_size)
        return _page_result([dict(row) for row in rows], total, page, page_size)

    def analytics(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        start = business_day_start_utc(now - timedelta(days=13)).isoformat()
        today_start = business_day_start_utc(now).isoformat()
        days = business_date_keys(14, now)
        daily_rows = {}
        for value in self.mapper.admin_daily_usage(start, 30) or []:
            row = dict(value)
            daily_rows[str(row.get("day"))] = row
        activity_rows = {}
        for value in self.mapper.admin_daily_activity(start, 30) or []:
            row = dict(value)
            activity_rows[str(row.get("day"))] = row
        return {
            "daily": [{"day": day, "requests": 0, "total_tokens": 0, "total_cost": 0, "failed_requests": 0, **daily_rows.get(day, {})} for day in days],
            "activity": [{"day": day, "active_users": 0, "requests": 0, **activity_rows.get(day, {})} for day in days],
            "today_users": [dict(row) for row in (self.mapper.admin_today_user_usage(today_start, 10) or [])],
            "users": [dict(row) for row in (self.mapper.admin_user_usage(10) or [])],
            "statuses": [dict(row) for row in (self.mapper.admin_status_usage() or [])],
            "billing_sources": [dict(row) for row in (self.mapper.admin_billing_usage() or [])],
            "orders": [dict(row) for row in (self.mapper.admin_order_status() or [])],
        }

    def device_info(self) -> dict[str, Any]:
        """Return host and process measurements; no user request data is read."""
        root = Path.cwd().anchor or str(Path.cwd())
        try:
            disk = psutil.disk_usage(root)
            storage = {"total": disk.total, "used": disk.used, "free": disk.free, "percent": disk.percent}
        except (OSError, ValueError):
            storage = {"total": 0, "used": 0, "free": 0, "percent": 0}
        memory = psutil.virtual_memory()
        process = psutil.Process()
        try:
            process_memory = process.memory_info().rss
        except (psutil.Error, OSError):
            process_memory = 0
        try:
            # Non-blocking sampling keeps every admin summary request from
            # paying a fixed 50 ms sleep. psutil returns the delta since the
            # previous call, which is sufficient for dashboard telemetry.
            cpu_percent = psutil.cpu_percent(interval=None)
        except (psutil.Error, OSError):
            cpu_percent = 0
        try:
            net = psutil.net_io_counters()
            network = {
                "bytes_sent": net.bytes_sent,
                "bytes_recv": net.bytes_recv,
                "packets_sent": net.packets_sent,
                "packets_recv": net.packets_recv,
                "errors_in": net.errin,
                "errors_out": net.errout,
                "drops_in": net.dropin,
                "drops_out": net.dropout,
            }
        except (psutil.Error, OSError):
            network = {key: 0 for key in ("bytes_sent", "bytes_recv", "packets_sent", "packets_recv", "errors_in", "errors_out", "drops_in", "drops_out")}
        return {
            "updated_at": _now(),
            "host": socket.gethostname(),
            "platform": f"{platform.system()} {platform.release()}",
            "python": platform.python_version(),
            "cpu": {"percent": round(float(cpu_percent), 1), "logical": psutil.cpu_count(logical=True) or 0, "physical": psutil.cpu_count(logical=False) or 0},
            "memory": {"total": memory.total, "used": memory.used, "available": memory.available, "percent": memory.percent, "process_used": process_memory},
            "storage": storage,
            "network": network,
        }

    def dashboard(self) -> dict[str, Any]:
        recent_logs = self.logs_page(1, 5)
        return {
            "analytics": self.analytics(),
            "request_metrics": self.request_metrics(),
            "device": self.device_info(),
            "recent_logs": recent_logs["items"],
            "recent_logs_pagination": {key: recent_logs[key] for key in ("total", "page", "page_size", "pages")},
        }


__all__ = ["ObservabilityService"]
