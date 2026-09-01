"""Privacy-safe usage summary service for OpenAI-compatible proxy traffic.

The proxy deliberately does not retain prompts, completions, raw request or
response envelopes, or uploaded media.  The legacy ``conversation_records``
table is kept only as a compatibility surface for the administrator's usage
charts; its content columns are always empty.
"""

import logging
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Mapping

from springbootai import Autowired, PostConstruct, Scheduled, Service, get_config

from backend.repository.store import StoreRepository


logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@Service("conversation_service")
class ConversationService:
    """Persist usage metadata only; never persist user/model content."""

    @Autowired
    def __init__(self, store_repository: StoreRepository):
        # Parameter name intentionally matches @Repository("store_repository")
        # because older SpringBootAI builds cannot resolve postponed type hints.
        self.repository = store_repository
        self.mapper = store_repository.mapper
        self.enabled = True
        self.retention_days = 0

    @PostConstruct
    def init(self):
        cfg = get_config().get("rose", {}).get("conversation", {})
        self.enabled = self._bool(cfg.get("enabled", True), True)
        try:
            self.retention_days = max(0, int(cfg.get("retention-days", 0)))
        except (TypeError, ValueError):
            self.retention_days = 0
        logger.info(
            "调用统计记录已%s: 仅保存用户、模型、状态、Token、费用和时间，永久不保存问答正文或图片; retention_days=%s",
            "启用" if self.enabled else "禁用",
            self.retention_days,
        )
        # Remove content captured by versions prior to privacy mode even when
        # current usage-summary recording is disabled. The mapper owns the SQL
        # so this remains compatible with SQLite and MySQL deployments.
        self._scrub_legacy_content()
        # Do not synthesize rows from ``usage_records`` here. A usage row
        # already contains the authoritative counters, while creating a second
        # conversation row would double-count administrator totals after a
        # restart. New proxy calls create their own metadata row.

    @staticmethod
    def _bool(value: Any, default: bool = False) -> bool:
        if value is None:
            return default
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def begin(self, user_id: int, protocol: str, payload: Mapping[str, Any], messages: Any, request_id: str | None = None) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        request_id = str(request_id or ("conv_" + uuid.uuid4().hex))
        row = {
            "user_id": int(user_id), "request_id": request_id, "protocol": str(protocol),
            "model": str(payload.get("model") or ""),
            # These columns are retained for old schemas only.  They are
            # intentionally constant empty values and must never contain the
            # request, messages, image URLs, or other user data.
            "request_json": "{}", "messages_json": "[]", "response_json": None, "answer_text": "",
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost": 0.0,
            "status": "PROCESSING", "error_message": None, "created_at": _now(),
            "completed_at": None, "latency_ms": None,
        }
        try:
            started = time.monotonic()
            last_error = None
            for attempt in range(3):
                try:
                    inserted_id = self.mapper.insert_conversation(row)
                    # SpringBootAI mappers normally populate useGeneratedKeys;
                    # accepting the return value also keeps this compatible
                    # with older mapper implementations.
                    if not row.get("id") and inserted_id:
                        row["id"] = inserted_id
                    row["started_monotonic"] = started
                    return row
                except Exception as exc:
                    last_error = exc
                    # SQLite can briefly report a lock while the billing
                    # transaction is committing. A short bounded retry keeps
                    # logging reliable without delaying the proxy noticeably.
                    if "locked" not in str(exc).lower() or attempt >= 2:
                        break
                    time.sleep(0.05 * (attempt + 1))
            logger.warning("会话记录写入失败 request_id=%s: %s", request_id, last_error)
        except Exception as exc:
            logger.warning("会话记录初始化失败 request_id=%s: %s", request_id, exc)
        # Conversation logging must never take the proxy down.
        return None

    def complete(self, row: Mapping[str, Any] | None, response: Any, answer: str, usage: Mapping[str, Any], cost: float, status: str = "SUCCEEDED", error_message: str | None = None) -> None:
        if not row or not self.enabled:
            return
        changes = {
            # Never add response/answer/error payloads here.  In particular,
            # upstream exception strings can contain snippets of user data.
            "prompt_tokens": int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0),
            "completion_tokens": int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0),
            "total_tokens": int(usage.get("total_tokens", 0) or 0), "cost": float(cost or 0),
            "status": str(status),
            "completed_at": _now(),
        }
        started = row.get("started_monotonic")
        if started:
            changes["latency_ms"] = max(0, int((time.monotonic() - float(started)) * 1000))
        last_error = None
        for attempt in range(3):
            try:
                self.mapper.update_conversation(int(row["id"]), changes)
                return
            except Exception as exc:
                last_error = exc
                if "locked" not in str(exc).lower() or attempt >= 2:
                    break
                time.sleep(0.05 * (attempt + 1))
        logger.warning("会话记录更新失败 id=%s: %s", row.get("id"), last_error)

    def fail(self, row: Mapping[str, Any] | None, error: Exception | str, response: Any = None) -> None:
        # Failure details stay in the framework log, not in the usage table.
        self.complete(row, None, "", {}, 0, "FAILED", None)

    def _scrub_legacy_content(self) -> None:
        """Erase content written by older releases and all asset metadata."""
        try:
            self.mapper.clear_conversation_content()
            self.mapper.delete_conversation_assets()
        except Exception as exc:
            # A logging migration must not prevent the proxy from starting;
            # the next startup retries the scrub.
            logger.warning("旧版问答内容清理失败: %s", type(exc).__name__)

    def backfill_usage_summaries(self) -> int:
        """Deprecated compatibility hook; historical usage is not duplicated."""
        return 0

    def list_page(self, user_id: int | None, page: int = 1, page_size: int = 5) -> dict[str, Any]:
        page = max(1, int(page)); page_size = max(1, min(int(page_size), 5))
        total = int(self.mapper.count_conversations(user_id) or 0)
        rows = self.mapper.list_conversations(user_id, (page - 1) * page_size, page_size)
        pages = max(1, (total + page_size - 1) // page_size)
        return {"items": [dict(x) for x in rows], "total": total, "page": min(page, pages), "page_size": page_size, "pages": pages}

    def totals(self) -> dict[str, Any]:
        return dict(self.mapper.conversation_totals() or {})

    def model_totals(self) -> list[dict[str, Any]]:
        return [dict(row) for row in (self.mapper.conversation_model_totals() or [])]

    def detail(self, conversation_id: int, user_id: int | None = None) -> dict[str, Any] | None:
        row = self.mapper.find_conversation(int(conversation_id), user_id)
        return dict(row) if row else None

    @Scheduled(fixed_rate=3600000, initial_delay=60000)
    def cleanup(self) -> None:
        # Retention is optional. SQL remains in the mapper; a negative/zero
        # value means keep records indefinitely.
        if self.retention_days <= 0:
            return
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.retention_days)).isoformat()
        try:
            self.mapper.delete_conversations_before(cutoff)
        except AttributeError:
            pass


__all__ = ["ConversationService"]
