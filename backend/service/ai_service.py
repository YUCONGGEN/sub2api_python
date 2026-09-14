import math
import re
import json
import asyncio
import threading
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from typing import Any


from springbootai import Autowired, PostConstruct, PreDestroy, Service, get_config
from springbootai.ai.core import ChatClientBuilder, Message, ChatResponse, Generation, MessageType
from springbootai.ai.providers import OpenAIChatModel

from backend.common.multimodal import parse_dsml_tool_calls, text_content as multimodal_text_content
from backend.common.reasoning import DEFAULT_GPT_REASONING_EFFORT, configured_gpt_reasoning_effort, default_gpt_reasoning_effort, requested_reasoning_effort
from backend.service.store_service import StoreService


class UpstreamRequestError(RuntimeError):
    """An upstream rejected the request with a client-actionable status."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = int(status_code)
        self.detail = str(detail)
        super().__init__(f"上游返回 HTTP {self.status_code}：{self.detail}")


class ReliableOpenAIChatModel(OpenAIChatModel):
    """SpringBootAI OpenAI model with complete OpenAI SSE event handling.

    Older springbootAI releases only exposed ``delta.content`` in their
    streaming adapter. Vision/reasoning models also emit tool-call deltas and
    usage-only terminal events, so those events must remain in metadata.
    """

    @staticmethod
    def _content_text(value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "".join(
                str(part.get("text") or "")
                for part in value
                if isinstance(part, dict) and str(part.get("type") or "") in {"text", "output_text"}
            )
        return str(value or "")

    @staticmethod
    def _response_json(response) -> dict[str, Any]:
        """Decode provider JSON from bytes as UTF-8.

        A few compatible upstreams omit ``charset=utf-8`` on SSE responses.
        Requests then defaults to ISO-8859-1 and turns Chinese into mojibake
        (for example ``ä½ å¥½``). JSON APIs are UTF-8 by contract, so use the
        raw response bytes instead of the guessed ``response.encoding``.
        """
        raw = getattr(response, "content", b"") or b""
        if isinstance(raw, str):
            return json.loads(raw)
        return json.loads(bytes(raw).decode("utf-8"))

    @staticmethod
    def _provider_error(response) -> str:
        """Return a bounded, useful error from an OpenAI-compatible response.

        ``requests.HTTPError`` only contains the status line.  DeepSeek and
        other compatible providers put the actionable reason in the JSON body
        (for example ``error.code`` or ``error.param``), so preserving it is
        essential when diagnosing requests sent by Trae or an IDE plugin.
        """
        try:
            payload = ReliableOpenAIChatModel._response_json(response)
        except (UnicodeDecodeError, ValueError, TypeError):
            payload = None
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                parts = []
                for key in ("message", "code", "param", "type"):
                    value = error.get(key)
                    if value not in (None, ""):
                        parts.append(f"{key}={value}")
                if parts:
                    return "; ".join(parts)
            if payload:
                try:
                    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))[:2000]
                except (TypeError, ValueError):
                    pass
        body = str(getattr(response, "text", "") or "").strip()
        return body[:2000] or "未返回错误详情"

    @staticmethod
    def _serialize_msg(message) -> dict[str, Any]:
        """Serialize framework messages while passing raw protocol messages through."""
        if isinstance(message, dict):
            return dict(message)
        data = message.to_dict()
        if message.type == MessageType.TOOL:
            data["role"] = "tool"
            if message.metadata.get("tool_call_id"):
                data["tool_call_id"] = message.metadata["tool_call_id"]
        if message.type == MessageType.ASSISTANT and message.metadata.get("tool_calls"):
            data["tool_calls"] = message.metadata["tool_calls"]
        return data

    def __init__(
        self,
        *args,
        http2: bool = False,
        max_connections: int = 100,
        max_keepalive_connections: int = 20,
        keepalive_expiry: float = 30.0,
        connect_timeout: float | None = None,
        pool_timeout: float | None = None,
        trust_env: bool = False,
        **kwargs,
    ):
        """Create a SpringBootAI model with reusable transport resources.

        ``httpx.AsyncClient`` owns the TCP/TLS connection pool.  Constructing
        one inside every request defeats keep-alive and makes each call pay a
        fresh handshake.  A client is therefore created lazily per event loop
        and reused for the lifetime of that model.  Per-loop storage keeps the
        model safe when a test runner or an embedding application uses more
        than one asyncio loop.
        """
        super().__init__(*args, **kwargs)
        self.http2 = bool(http2)
        self.max_connections = max(1, int(max_connections))
        self.max_keepalive_connections = max(0, min(int(max_keepalive_connections), self.max_connections))
        self.keepalive_expiry = max(1.0, float(keepalive_expiry))
        timeout_value = float(kwargs.get("timeout", getattr(self, "timeout", 180)))
        self.connect_timeout = max(0.1, float(connect_timeout if connect_timeout is not None else min(timeout_value, 10.0)))
        self.pool_timeout = max(0.1, float(pool_timeout if pool_timeout is not None else min(timeout_value, 30.0)))
        self.trust_env = bool(trust_env)
        self._async_clients: dict[Any, Any] = {}
        self._async_clients_lock = threading.Lock()
        self._sync_sessions: dict[int, Any] = {}
        self._sync_sessions_lock = threading.Lock()

    def _async_client(self):
        """Return the keep-alive client associated with the current loop."""
        import httpx

        loop = asyncio.get_running_loop()
        # Keep the loop object as the key instead of ``id(loop)``.  A test
        # runner can recycle object ids after closing a loop; using the id
        # alone could then accidentally reuse a client bound to a dead loop.
        loop_key = loop
        client = self._async_clients.get(loop_key)
        if client is not None and not getattr(client, "is_closed", False):
            return client
        # Client construction is synchronous and very short.  Guard it so a
        # future embedding that invokes the same model from multiple threads
        # cannot create duplicate pools for one loop.
        with self._async_clients_lock:
            client = self._async_clients.get(loop_key)
            if client is None or getattr(client, "is_closed", False):
                timeout = httpx.Timeout(
                    float(self.timeout),
                    connect=self.connect_timeout,
                    pool=self.pool_timeout,
                )
                limits = httpx.Limits(
                    max_connections=self.max_connections,
                    max_keepalive_connections=self.max_keepalive_connections,
                    keepalive_expiry=self.keepalive_expiry,
                )
                try:
                    client = httpx.AsyncClient(
                        timeout=timeout,
                        limits=limits,
                        http2=self.http2,
                        trust_env=self.trust_env,
                    )
                except ImportError:
                    # httpx requires the optional ``h2`` package for HTTP/2.
                    # Keep startup/request handling reliable when it is not
                    # installed and transparently fall back to HTTP/1.1.
                    client = httpx.AsyncClient(
                        timeout=timeout,
                        limits=limits,
                        http2=False,
                        trust_env=self.trust_env,
                    )
                self._async_clients[loop_key] = client
            return client

    def _sync_session(self):
        """Return one requests Session per worker thread for sync fallbacks."""
        import requests

        thread_id = threading.get_ident()
        session = self._sync_sessions.get(thread_id)
        if session is None:
            with self._sync_sessions_lock:
                session = self._sync_sessions.get(thread_id)
                if session is None:
                    session = requests.Session()
                    adapter = requests.adapters.HTTPAdapter(
                        pool_connections=self.max_connections,
                        pool_maxsize=self.max_connections,
                        pool_block=False,
                    )
                    session.mount("http://", adapter)
                    session.mount("https://", adapter)
                    session.trust_env = self.trust_env
                    self._sync_sessions[thread_id] = session
        return session

    def _requests_timeout(self):
        """Use a short connect timeout but retain the configured read limit."""
        return (self.connect_timeout, float(self.timeout))

    @PreDestroy
    def close_transport(self):
        """Release pooled transports when SpringBootAI shuts the bean down."""
        clients = list(self._async_clients.values())
        self._async_clients.clear()
        if clients:
            async def close_all():
                await asyncio.gather(*(client.aclose() for client in clients), return_exceptions=True)

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                try:
                    asyncio.run(close_all())
                except Exception:
                    pass
            else:
                # Bean destruction is synchronous in SpringBootAI.  Schedule
                # the coroutine when shutdown itself runs on the event loop.
                loop.create_task(close_all())
        sessions = list(self._sync_sessions.values())
        self._sync_sessions.clear()
        for session in sessions:
            try:
                session.close()
            except Exception:
                pass

    def _http_payload(self, messages, options=None, *, stream: bool = False) -> dict[str, Any]:
        if isinstance(messages, list) and (not messages or isinstance(messages[0], dict)):
            # Protocol adapters already validated and normalized these raw
            # OpenAI messages.  Reuse the list as-is to avoid a second full
            # traversal/copy of large tool or multimodal payloads.
            serialized_messages = messages
        else:
            serialized_messages = [self._serialize_msg(message) for message in messages]
        payload = {
            "model": self.model,
            "messages": serialized_messages,
            "temperature": self.temperature,
        }
        if options:
            payload.update(options)
        payload["stream"] = bool(stream)
        if not stream:
            payload.pop("stream_options", None)
        return payload

    def _chat_response(self, data: dict[str, Any]) -> ChatResponse:
        choices = data.get("choices") or [{}]
        choice = choices[0] if isinstance(choices[0], dict) else {}
        message = choice.get("message") or {}
        content = message.get("content", "") or ""
        tool_calls = message.get("tool_calls")
        metadata = {"provider": "openai", "backend": "http", "usage": data.get("usage", {})}
        if tool_calls:
            metadata["tool_calls"] = tool_calls
        return ChatResponse(
            generations=[Generation(output=Message(
                content=content, type=MessageType.ASSISTANT,
                metadata={"tool_calls": tool_calls or []}))],
            metadata=metadata,
        )

    def _decode_stream_event(
        self,
        data: dict[str, Any],
        tool_calls: dict[int, dict[str, Any]],
        final_usage: dict[str, Any],
        finish_reason: str | None,
    ) -> tuple[ChatResponse | None, str | None]:
        usage = data.get("usage")
        if isinstance(usage, dict):
            final_usage.update(usage)
        choices = data.get("choices") or []
        if not choices:
            if final_usage:
                return ChatResponse(metadata={"provider": "openai", "backend": "http", "stream": True, "usage": final_usage}), finish_reason
            return None, finish_reason
        choice = choices[0] if isinstance(choices[0], dict) else {}
        delta = choice.get("delta") or {}
        content = self._content_text(delta.get("content"))
        reasoning = self._content_text(delta.get("reasoning_content") or delta.get("reasoning"))
        for raw_call in delta.get("tool_calls") or []:
            if not isinstance(raw_call, dict):
                continue
            index = int(raw_call.get("index", 0) or 0)
            current = tool_calls.setdefault(index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
            if raw_call.get("id"):
                current["id"] = raw_call["id"]
            if raw_call.get("type"):
                current["type"] = raw_call["type"]
            function = raw_call.get("function") or {}
            if function.get("name"):
                current["function"]["name"] += str(function["name"])
            if function.get("arguments"):
                current["function"]["arguments"] += str(function["arguments"])
        if choice.get("finish_reason"):
            finish_reason = choice.get("finish_reason")
        raw_tool_delta = delta.get("tool_calls") or []
        metadata: dict[str, Any] = {
            "tool_calls": [tool_calls[k] for k in sorted(tool_calls)],
            "tool_call_delta": raw_tool_delta,
            "finish_reason": finish_reason,
        }
        if reasoning:
            metadata["reasoning_content"] = reasoning
        if not (content or metadata.get("tool_calls") or reasoning):
            return None, finish_reason
        return ChatResponse(
            generations=[Generation(output=Message(content=content, type=MessageType.ASSISTANT, metadata=metadata))],
            metadata={"provider": "openai", "backend": "http", "stream": True, **metadata},
        ), finish_reason

    async def acall(self, messages, options=None):
        """Native async OpenAI-compatible request for DeepSeek and GPT."""
        import httpx

        payload = self._http_payload(messages, options, stream=False)
        try:
            # Reuse the per-loop client so keep-alive connections avoid a
            # repeated TCP/TLS handshake on every request.
            client = self._async_client()
            response = await client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"上游连接失败：{exc}") from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise UpstreamRequestError(response.status_code, self._provider_error(response))
        try:
            return self._chat_response(self._response_json(response))
        except (UnicodeDecodeError, ValueError, TypeError) as exc:
            raise RuntimeError("上游返回了无法解析的 JSON") from exc

    async def astream(self, messages, options=None):
        """Native async SSE reader; no worker-thread or process per request."""
        import httpx

        payload = self._http_payload(messages, options, stream=True)
        tool_calls: dict[int, dict[str, Any]] = {}
        final_usage: dict[str, Any] = {}
        finish_reason = None
        try:
            client = self._async_client()
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                    # Compression proxies may buffer small SSE frames until a
                    # larger block is available. Identity encoding keeps the
                    # first token flowing as soon as the provider emits it.
                    "Accept-Encoding": "identity",
                },
            ) as response:
                if response.status_code < 200 or response.status_code >= 300:
                    await response.aread()
                    raise UpstreamRequestError(response.status_code, self._provider_error(response))
                # A bytearray avoids allocating/copying the complete pending
                # buffer for every network chunk while retaining strict UTF-8
                # decoding for Chinese and other non-ASCII output.
                buffer = bytearray()
                done = False
                async for chunk in response.aiter_bytes():
                    buffer.extend(chunk)
                    while True:
                        newline = buffer.find(b"\n")
                        if newline < 0:
                            break
                        raw_line = bytes(buffer[:newline]).rstrip(b"\r")
                        del buffer[:newline + 1]
                        if not raw_line.startswith(b"data:"):
                            continue
                        data_text = raw_line[5:].strip()
                        if data_text == b"[DONE]":
                            # Finish the parser normally so the terminal
                            # usage/tool metadata event is still emitted.
                            # Some providers put usage in the preceding
                            # frame and send [DONE] immediately afterwards.
                            done = True
                            break
                        try:
                            data = json.loads(data_text.decode("utf-8"))
                        except (UnicodeDecodeError, TypeError, json.JSONDecodeError):
                            continue
                        response_chunk, finish_reason = self._decode_stream_event(data, tool_calls, final_usage, finish_reason)
                        if response_chunk is not None:
                            yield response_chunk
                    if done:
                        break
                if buffer.startswith(b"data:"):
                    data_text = bytes(buffer[5:]).strip()
                    if data_text and data_text != b"[DONE]":
                        try:
                            response_chunk, finish_reason = self._decode_stream_event(json.loads(data_text.decode("utf-8")), tool_calls, final_usage, finish_reason)
                            if response_chunk is not None:
                                yield response_chunk
                        except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError):
                            pass
                terminal = {"provider": "openai", "backend": "http", "stream": True, "usage": final_usage, "finish_reason": finish_reason}
                if tool_calls:
                    terminal["tool_calls"] = [tool_calls[k] for k in sorted(tool_calls)]
                yield ChatResponse(metadata=terminal)
        except UpstreamRequestError:
            raise
        except httpx.HTTPError as exc:
            raise RuntimeError(f"上游连接失败：{exc}") from exc

    def _call_via_http(self, messages, tool_registry=None, options=None):
        """Call the provider through SpringBootAI's OpenAI-compatible contract.

        The bundled SpringBootAI helper raises ``HTTPError`` without the
        response body.  Keeping the HTTP implementation here gives callers a
        stable error that includes the upstream provider's real reason while
        retaining the framework's message serialization and tool schema flow.
        """
        import requests

        serialized_messages = messages if isinstance(messages, list) and (not messages or isinstance(messages[0], dict)) else [self._serialize_msg(message) for message in messages]
        payload = {
            "model": self.model,
            "messages": serialized_messages,
            "temperature": self.temperature,
        }
        if options:
            payload.update(options)
        if tool_registry is not None and hasattr(tool_registry, "names") and tool_registry.names():
            payload["tools"] = tool_registry.schemas()

        # ``stream_options`` is only valid together with stream=true.  It can
        # be added by OpenAI clients even for a normal request; do not send it
        # to providers such as DeepSeek in that case.
        if not payload.get("stream"):
            payload.pop("stream_options", None)

        attempts = max(1, int(getattr(self, "max_retries", 0) or 0) + 1)
        response = None
        for attempt in range(attempts):
            try:
                response = self._sync_session().post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    timeout=self._requests_timeout(),
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt + 1 < attempts:
                    time.sleep(max(0.0, float(getattr(self, "retry_delay_ms", 500) or 0)) / 1000.0)
                    continue
                raise RuntimeError(f"上游连接失败：{exc}") from exc
            if 200 <= response.status_code < 300:
                break
            if response.status_code == 429 or response.status_code >= 500:
                if attempt + 1 < attempts:
                    time.sleep(max(0.0, float(getattr(self, "retry_delay_ms", 500) or 0)) / 1000.0)
                    continue
            detail = self._provider_error(response)
            raise UpstreamRequestError(response.status_code, detail)
        try:
            data = self._response_json(response)
        except (UnicodeDecodeError, ValueError, TypeError) as exc:
            raise RuntimeError("上游返回了无法解析的 JSON") from exc

        choices = data.get("choices") or [{}]
        choice = choices[0] if isinstance(choices[0], dict) else {}
        message = choice.get("message") or {}
        content = message.get("content", "") or ""
        tool_calls = message.get("tool_calls")
        metadata = {"provider": "openai", "backend": "http", "usage": data.get("usage", {})}
        if tool_calls:
            metadata["tool_calls"] = tool_calls
        return ChatResponse(
            generations=[Generation(output=Message(
                content=content, type=MessageType.ASSISTANT,
                metadata={"tool_calls": tool_calls or []}))],
            metadata=metadata,
        )

    def _stream_via_http(self, messages, options=None):
        """Read an OpenAI SSE stream using SpringBootAI's provider contract.

        SpringBootAI's ``OpenAIChatModel.stream`` calls this private adapter
        as ``_stream_via_http(messages, options)``.  Keeping the same signature
        is important: a three-argument override is never reached correctly
        and manifests to clients as an empty/retried response.
        """
        import time as _time

        serialized_messages = messages if isinstance(messages, list) and (not messages or isinstance(messages[0], dict)) else [self._serialize_msg(m) for m in messages]
        payload = {
            "model": self.model,
            "messages": serialized_messages,
            "temperature": self.temperature,
            "stream": True,
        }
        if options:
            payload.update(options)
        max_attempts = max(1, int(getattr(self, "max_retries", 0) or 0) + 1)
        retry_delay = max(0.0, float(getattr(self, "retry_delay_ms", 0) or 0)) / 1000.0
        for attempt in range(max_attempts):
            response = None
            emitted_any = False
            try:
                with self._sync_session().post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    stream=True,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                        "Accept": "text/event-stream",
                        "Accept-Encoding": "identity",
                    },
                    timeout=self._requests_timeout(),
                ) as response:
                    if response.status_code < 200 or response.status_code >= 300:
                        detail = self._provider_error(response)
                        raise UpstreamRequestError(response.status_code, detail)
                    tool_calls: dict[int, dict[str, Any]] = {}
                    final_usage: dict[str, Any] = {}
                    finish_reason = None
                    # Decode the SSE bytes ourselves. ``decode_unicode=True``
                    # trusts the upstream charset and corrupts UTF-8 Chinese
                    # when the provider responds with ``text/event-stream``
                    # without a charset parameter.
                    # ``requests`` defaults to a 512-byte read buffer.  Small
                    # SSE deltas would then wait for the buffer to fill before
                    # reaching the caller.  A one-byte read keeps this sync
                    # compatibility path as responsive as the native async
                    # path; the connection pool still handles reuse.
                    for raw_line in response.iter_lines(chunk_size=1, decode_unicode=False):
                        if not raw_line:
                            continue
                        line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else str(raw_line)
                        if not line.startswith("data:"):
                            continue
                        data_text = line[5:].strip()
                        if data_text == "[DONE]":
                            break
                        try:
                            data = json.loads(data_text)
                        except (TypeError, json.JSONDecodeError):
                            continue
                        usage = data.get("usage")
                        if isinstance(usage, dict):
                            final_usage.update(usage)
                        choices = data.get("choices") or []
                        if not choices:
                            if final_usage:
                                yield ChatResponse(metadata={"provider": "openai", "backend": "http", "stream": True, "usage": final_usage})
                            continue
                        choice = choices[0] if isinstance(choices[0], dict) else {}
                        delta = choice.get("delta") or {}
                        content = self._content_text(delta.get("content"))
                        # Keep provider reasoning in metadata for auditing, but
                        # never expose hidden chain-of-thought as answer text.
                        reasoning = self._content_text(delta.get("reasoning_content") or delta.get("reasoning"))
                        if reasoning:
                            metadata = {"reasoning_content": reasoning}
                        else:
                            metadata = {}
                        for raw_call in delta.get("tool_calls") or []:
                            if not isinstance(raw_call, dict):
                                continue
                            index = int(raw_call.get("index", 0) or 0)
                            current = tool_calls.setdefault(index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                            if raw_call.get("id"):
                                current["id"] = raw_call["id"]
                            if raw_call.get("type"):
                                current["type"] = raw_call["type"]
                            function = raw_call.get("function") or {}
                            if function.get("name"):
                                current["function"]["name"] += str(function["name"])
                            if function.get("arguments"):
                                current["function"]["arguments"] += str(function["arguments"])
                        raw_tool_delta = delta.get("tool_calls") or []
                        if choice.get("finish_reason"):
                            finish_reason = choice.get("finish_reason")
                        metadata.update({
                            "tool_calls": [tool_calls[k] for k in sorted(tool_calls)],
                            "tool_call_delta": raw_tool_delta,
                            "finish_reason": finish_reason,
                        })
                        if content or metadata.get("tool_calls") or metadata.get("reasoning_content"):
                            emitted_any = True
                            yield ChatResponse(
                                generations=[Generation(output=Message(content=content, type=MessageType.ASSISTANT, metadata=metadata))],
                                metadata={"provider": "openai", "backend": "http", "stream": True, **metadata},
                            )
                    terminal = {"provider": "openai", "backend": "http", "stream": True, "usage": final_usage, "finish_reason": finish_reason}
                    if tool_calls:
                        terminal["tool_calls"] = [tool_calls[k] for k in sorted(tool_calls)]
                    yield ChatResponse(metadata=terminal)
                return
            except Exception as exc:
                status = getattr(response, "status_code", None)
                # Retrying after bytes have already reached the caller would
                # replay the same answer and re-upload any image in the
                # request. Let the protocol layer terminate that stream so
                # the client can decide whether a fresh request is needed.
                if emitted_any:
                    raise exc
                if attempt + 1 < max_attempts and (status is None or status >= 500 or status == 429):
                    if retry_delay:
                        _time.sleep(retry_delay)
                    continue
                raise exc


@Service
class AiGatewayService:
    @Autowired
    def __init__(self, store: StoreService):
        self.store = store
        self.clients: dict[str, Any] = {}
        self.models: dict[str, dict[str, Any]] = {}
        self.model_name = "gpt-5.6-sol"
        self.gpt_default_reasoning_effort = DEFAULT_GPT_REASONING_EFFORT
        self.demo_mode = True
        self.pricing = {}
        self.health: dict[str, dict[str, Any]] = {}
        self._health_lock = threading.Lock()
        self._health_refreshing = False
        self._health_checked_at = 0.0
        self._health_interval = 60.0
        self._health_timeout = 5.0
        self._use_langchain = False

    @PostConstruct
    def init(self):
        cfg = get_config()
        self.gpt_default_reasoning_effort = configured_gpt_reasoning_effort(cfg)
        ai_cfg = cfg.get("springbootai", {}).get("ai", {})
        openai_cfg = ai_cfg.get("openai", {}) if isinstance(ai_cfg, dict) else {}
        rose_cfg = cfg.get("rose", {})
        proxy_cfg = rose_cfg.get("proxy", {})
        billing = cfg.get("rose", {}).get("billing", {})
        health_cfg = proxy_cfg.get("health-check", {}) if isinstance(proxy_cfg.get("health-check"), dict) else {}
        transport_cfg = proxy_cfg.get("transport", {}) if isinstance(proxy_cfg.get("transport"), dict) else {}
        self._health_interval = max(5.0, float(health_cfg.get("interval-seconds", proxy_cfg.get("health-check-interval-seconds", 60))))
        self._health_timeout = max(1.0, min(15.0, float(health_cfg.get("timeout-seconds", proxy_cfg.get("health-check-timeout-seconds", 5)))))
        self.model_name = str(proxy_cfg.get("default-model") or proxy_cfg.get("model") or openai_cfg.get("chat", {}).get("model") or "gpt-5.6-sol")
        self._use_langchain = self._as_bool(proxy_cfg.get("use-langchain", False), False)
        # Transport settings are shared by all configured upstreams.  They
        # control connection reuse/concurrency without coupling model entries
        # to a specific HTTP client implementation.
        self._transport = {
            "http2": self._as_bool(transport_cfg.get("http2", False), False),
            "max-connections": max(1, int(transport_cfg.get("max-connections", 100) or 100)),
            "max-keepalive-connections": max(0, int(transport_cfg.get("max-keepalive-connections", 20) or 20)),
            "keepalive-expiry-seconds": max(1.0, float(transport_cfg.get("keepalive-expiry-seconds", 60) or 60)),
            "connect-timeout-seconds": max(0.1, float(transport_cfg.get("connect-timeout-seconds", 10) or 10)),
            "pool-timeout-seconds": max(0.1, float(transport_cfg.get("pool-timeout-seconds", 30) or 30)),
            "trust-env": self._as_bool(transport_cfg.get("trust-env", False), False),
            "max-retries": max(0, int(transport_cfg.get("max-retries", 1) or 0)),
            "retry-delay-ms": max(0, int(transport_cfg.get("retry-delay-ms", 200) or 0)),
        }
        demo_value = proxy_cfg.get("demo-mode-when-key-missing", True)
        self.demo_mode = str(demo_value).strip().lower() in {"1", "true", "yes", "on"} if isinstance(demo_value, str) else bool(demo_value)
        self.pricing = billing
        configured = rose_cfg.get("models")
        if isinstance(configured, dict):
            configured = [{"id": key, **(value if isinstance(value, dict) else {})} for key, value in configured.items()]
        # An explicitly empty catalog is valid for subscription-only setups.
        # Only missing/legacy configuration should create the fallback model.
        if not isinstance(configured, list):
            configured = [{
                "id": self.model_name,
                "enabled": True,
                "provider": "OpenAI",
                "endpoint": "Chat",
                "group": "Default",
                "upstream-model": self.model_name,
                "base-url": openai_cfg.get("base-url", "https://api.openai.com/v1"),
                "api-key": openai_cfg.get("api-key", ""),
                "temperature": openai_cfg.get("chat", {}).get("temperature", 0.7),
                "timeout-seconds": openai_cfg.get("chat", {}).get("timeout", 180),
                "demo-mode-when-key-missing": proxy_cfg.get("demo-mode-when-key-missing", True),
                "pricing": {
                    "input-usd-per-million": billing.get("default-input-usd-per-million", 4.5),
                    "output-usd-per-million": billing.get("default-output-usd-per-million", 27),
                    "cache-usd-per-million": billing.get("default-cache-usd-per-million", 4.5),
                    "multiplier": billing.get("price-multiplier", 1.0),
                },
                "currency": str(billing.get("currency") or "CNY").upper(),
                "description": "OpenAI 兼容模型。",
            }]
        self.models = {}
        self.clients = {}
        for raw in configured:
            if not isinstance(raw, dict) or not raw.get("id"):
                continue
            spec = dict(raw)
            spec["id"] = str(spec["id"])
            spec["enabled"] = self._as_bool(spec.get("enabled", True), True)
            pricing = spec.get("pricing") if isinstance(spec.get("pricing"), dict) else {}
            provider = str(spec.get("provider", "")).strip().lower()
            currency = str(spec.get("currency") or ("CNY" if provider == "deepseek" else billing.get("currency", "CNY"))).upper()
            spec["pricing"] = {
                "input-usd-per-million": float(pricing.get("input-usd-per-million", billing.get("default-input-usd-per-million", 4.5))),
                "output-usd-per-million": float(pricing.get("output-usd-per-million", billing.get("default-output-usd-per-million", 27))),
                "cache-usd-per-million": float(pricing.get("cache-usd-per-million", billing.get("default-cache-usd-per-million", 4.5))),
                "input-cny-per-million": float(pricing.get("input-cny-per-million", 0)),
                "output-cny-per-million": float(pricing.get("output-cny-per-million", 0)),
                "cache-cny-per-million": float(pricing.get("cache-cny-per-million", 0)),
                "multiplier": float(pricing.get("multiplier", billing.get("price-multiplier", 1.0))),
                "usd-to-cny": float(pricing.get("usd-to-cny", billing.get("usd-to-cny", 7.2))),
            }
            spec["currency"] = currency
            self.models[spec["id"]] = spec
            api_key = str(spec.get("api-key") or "")
            if api_key:
                model = ReliableOpenAIChatModel(
                    api_key=api_key,
                    base_url=str(spec.get("base-url", "https://api.openai.com/v1")),
                    model=str(spec.get("upstream-model") or spec["id"]),
                    temperature=float(spec.get("temperature", 0.7)),
                    timeout=int(spec.get("timeout-seconds", 180)),
                    http2=self._as_bool(spec.get("http2", self._transport["http2"]), self._transport["http2"]),
                    max_connections=int(spec.get("max-connections", self._transport["max-connections"])),
                    max_keepalive_connections=int(spec.get("max-keepalive-connections", self._transport["max-keepalive-connections"])),
                    keepalive_expiry=float(spec.get("keepalive-expiry-seconds", self._transport["keepalive-expiry-seconds"])),
                    connect_timeout=float(spec.get("connect-timeout-seconds", self._transport["connect-timeout-seconds"])),
                    pool_timeout=float(spec.get("pool-timeout-seconds", self._transport["pool-timeout-seconds"])),
                    trust_env=self._as_bool(spec.get("trust-env", self._transport["trust-env"]), self._transport["trust-env"]),
                    max_retries=int(spec.get("max-retries", self._transport["max-retries"])),
                    retry_delay_ms=int(spec.get("retry-delay-ms", self._transport["retry-delay-ms"])),
                )
                # OpenAI-compatible reasoning models accept the effort as a
                # request option.  SpringBootAI's HTTP implementation passes
                # options through directly; when langchain-openai is present,
                # bind the same option to the runnable so both code paths have
                # identical behavior.
                reasoning_effort = str(spec.get("reasoning-effort") or "").strip()
                if reasoning_effort and getattr(model, "_llm", None) is not None:
                    try:
                        model._llm = model._llm.bind(reasoning_effort=reasoning_effort)
                    except Exception:
                        # Some older langchain-openai versions do not expose
                        # bind(); the direct HTTP path below still carries the
                        # option when the provider falls back to it.
                        pass
                # The LangChain path in older SpringBootAI releases silently
                # drops request options such as tools, max_tokens and stream
                # metadata.  Native SpringBootAI HTTP is the compatible path
                # for this gateway unless explicitly enabled in YAML.
                if not self._use_langchain:
                    model._llm = None
                self.clients[spec["id"]] = ChatClientBuilder(model).build()

        if self.model_name not in self.models and self.models:
            self.model_name = next(iter(self.models))
        # Do not block application startup on slow or unavailable upstreams.
        # The catalog initially reports "待检查" and the background probe
        # updates each model as soon as the providers respond.
        self.health = {
            model_id: self._health_record(spec, "unknown", "正在检查上游连接")
            for model_id, spec in self.models.items()
        }
        # Do not make the first public model request wait for every upstream.
        # The forced probe below runs in a daemon thread and replaces these
        # provisional records as providers respond.
        self._health_checked_at = time.monotonic()
        with self._health_lock:
            self._health_refreshing = True
        threading.Thread(target=self.refresh_health, kwargs={"force": True}, name="model-health-check", daemon=True).start()

    @PreDestroy
    def close_transports(self):
        """Close model connection pools when the SpringBootAI context stops."""
        for client in list(self.clients.values()):
            model = getattr(client, "chat_model", None)
            close = getattr(model, "close_transport", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    # Teardown is best effort; one provider pool must not
                    # prevent the application context from closing cleanly.
                    pass

    @staticmethod
    def _as_bool(value: Any, default: bool = False) -> bool:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value) if value is not None else default

    def model_spec(self, model_id: str | None) -> dict[str, Any]:
        selected = str(model_id or self.model_name)
        spec = self.models.get(selected)
        if not spec:
            raise ValueError(f"模型未配置: {selected}")
        if not spec.get("enabled", True):
            raise ValueError(f"模型已停用: {selected}")
        return spec

    def get_pricing(self, model_id: str | None) -> dict[str, Any]:
        return dict(self.model_spec(model_id).get("pricing", {}))

    def catalog(self) -> list[dict[str, Any]]:
        self.refresh_health()
        result = []
        for spec in self.models.values():
            currency = str(spec.get("currency") or "USD").upper()
            if currency == "CNY":
                input_price = self._cny_price(spec["pricing"], "input")
                output_price = self._cny_price(spec["pricing"], "output")
                cache_price = self._cny_price(spec["pricing"], "cache")
                public_pricing = {
                    "input-cny-per-million": input_price,
                    "output-cny-per-million": output_price,
                    "cache-cny-per-million": cache_price,
                }
            else:
                input_price = spec["pricing"].get("input-usd-per-million", 0)
                output_price = spec["pricing"].get("output-usd-per-million", 0)
                public_pricing = {
                    "input-usd-per-million": input_price,
                    "output-usd-per-million": output_price,
                    "cache-usd-per-million": spec["pricing"].get("cache-usd-per-million", 0),
                }
            health = self.health.get(spec["id"], self._health_record(spec, "unknown", "尚未检查"))
            result.append({
                "id": spec["id"],
                "provider": spec.get("provider", "OpenAI Compatible"),
                "endpoint": spec.get("endpoint", "Chat"),
                "group": spec.get("group", "Default"),
                "pricing": public_pricing,
                "currency": currency,
                # Keep both YAML-style names and concise display fields so a
                # client can render prices without knowing internal config keys.
                "input": input_price,
                "output": output_price,
                "description": spec.get("description", "OpenAI 兼容模型。"),
                "enabled": spec.get("enabled", True),
                "upstream_model": spec.get("upstream-model", spec["id"]),
                "reasoning_effort": spec.get("reasoning-effort"),
                "reasoning_levels": list(spec.get("reasoning-levels") or []),
                "has_upstream_key": bool(str(spec.get("api-key") or "").strip()),
                "supports_image": self._as_bool(spec.get("supports-image", False), False),
                "status": health["label"],
                "health": health,
            })
        return result

    @staticmethod
    def _health_record(spec: dict[str, Any], state: str, detail: str, *, latency_ms: int | None = None) -> dict[str, Any]:
        labels = {
            "ok": "正常",
            "missing_key": "未配置密钥",
            "auth_failed": "密钥无效",
            "unreachable": "上游不可达",
            "model_missing": "模型不存在",
            "disabled": "停用",
            "demo": "演示模式",
            "unknown": "待检查",
        }
        return {
            "state": state,
            "label": labels.get(state, state),
            "detail": detail,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "latency_ms": latency_ms,
        }

    @staticmethod
    def _cny_price(pricing: dict[str, Any], side: str) -> float:
        cny = float(pricing.get(f"{side}-cny-per-million", 0) or 0)
        if cny > 0:
            return cny
        usd = float(pricing.get(f"{side}-usd-per-million", 0) or 0)
        rate = float(pricing.get("usd-to-cny", 7.2) or 7.2)
        return usd * (rate if rate > 0 else 7.2)

    def refresh_health(self, force: bool = False) -> dict[str, dict[str, Any]]:
        now = time.monotonic()
        if not force and self.health and now - self._health_checked_at < self._health_interval:
            return self.health
        if not force:
            # Health checks are network operations.  Schedule one refresh and
            # keep serving the last known state instead of blocking API calls.
            with self._health_lock:
                if self._health_refreshing:
                    return self.health
                self._health_refreshing = True
            threading.Thread(target=self._refresh_health_worker, name="model-health-refresh", daemon=True).start()
            return self.health
        self._refresh_health_worker()
        return self.health

    def _refresh_health_worker(self) -> None:
        try:
            # Network probes intentionally run outside the lock.  Readers can
            # keep serving the previous snapshot while one provider times out.
            snapshot = {model_id: self._probe_model(spec) for model_id, spec in self.models.items()}
            with self._health_lock:
                self.health = snapshot
                self._health_checked_at = time.monotonic()
        finally:
            with self._health_lock:
                self._health_refreshing = False

    def _probe_model(self, spec: dict[str, Any]) -> dict[str, Any]:
        if not spec.get("enabled", True):
            return self._health_record(spec, "disabled", "模型已在 YAML 中停用")
        api_key = str(spec.get("api-key") or "").strip()
        demo_mode = self._as_bool(spec.get("demo-mode-when-key-missing", self.demo_mode), self.demo_mode)
        if not api_key:
            return self._health_record(spec, "demo" if demo_mode else "missing_key", "未配置上游 API Key" if not demo_mode else "未配置密钥，将使用演示模式")
        base_url = str(spec.get("base-url") or "").rstrip("/")
        if not base_url:
            return self._health_record(spec, "unreachable", "未配置上游地址")
        request = Request(f"{base_url}/models", headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"}, method="GET")
        started = time.monotonic()
        try:
            with urlopen(request, timeout=self._health_timeout) as response:
                body = response.read(2_000_000)
                latency = int((time.monotonic() - started) * 1000)
                if response.status < 200 or response.status >= 300:
                    return self._health_record(spec, "unreachable", f"上游返回 HTTP {response.status}", latency_ms=latency)
                try:
                    payload = json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    payload = {}
                items = payload.get("data") if isinstance(payload, dict) else None
                if isinstance(items, list) and items:
                    ids = {str(item.get("id")) for item in items if isinstance(item, dict) and item.get("id")}
                    upstream_model = str(spec.get("upstream-model") or spec["id"])
                    if ids and upstream_model not in ids:
                        return self._health_record(spec, "model_missing", f"上游未返回模型 {upstream_model}", latency_ms=latency)
                return self._health_record(spec, "ok", "上游连接正常", latency_ms=latency)
        except HTTPError as exc:
            state = "auth_failed" if exc.code in {401, 403} else "unreachable"
            detail = ""
            try:
                raw = exc.read(2000)
                payload = json.loads(raw.decode("utf-8", "replace")) if raw else None
                if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
                    error = payload["error"]
                    detail = str(error.get("message") or error.get("code") or "").strip()
                elif payload:
                    detail = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))[:500]
            except (OSError, ValueError, TypeError, UnicodeError):
                detail = ""
            suffix = f"：{detail}" if detail else ""
            return self._health_record(spec, state, f"上游返回 HTTP {exc.code}{suffix}")
        except (TimeoutError, URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            return self._health_record(spec, "unreachable", f"连接失败：{reason}")

    @staticmethod
    def text_content(content: Any) -> str:
        # Kept as a compatibility wrapper for existing callers.  It is used
        # only for token estimation; the original normalized content is still
        # passed to SpringBootAI below.
        return multimodal_text_content(content)

    @staticmethod
    def estimate_tokens(text: str) -> int:
        # 兼顾中英文的轻量估算；上游返回 usage 时会优先采用官方值。
        return max(1, math.ceil(len(re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9]+|[^\s]", text)) * 1.05))

    @classmethod
    def estimate_message_tokens(cls, messages: Any) -> int:
        """Estimate only when an upstream omits usage; never before forwarding."""
        if not isinstance(messages, list):
            return 1
        text = "\n".join(
            multimodal_text_content(item.get("content") if isinstance(item, dict) else item)
            for item in messages
        )
        return cls.estimate_tokens(text)

    def estimate_cost(self, model_id: str, prompt_tokens: int, completion_tokens: int) -> float:
        spec = self.model_spec(model_id)
        cfg = spec.get("pricing", {})
        multiplier = float(cfg.get("multiplier", 1.0))
        currency = str(spec.get("currency") or "USD").upper()
        if currency == "CNY":
            input_price = self._cny_price(cfg, "input")
            output_price = self._cny_price(cfg, "output")
            conversion = 1.0
        else:
            usd_to_cny = float(cfg.get("usd-to-cny", get_config().get("rose", {}).get("billing", {}).get("usd-to-cny", 7.2)))
            if usd_to_cny <= 0:
                usd_to_cny = 7.2
            input_price = float(cfg.get("input-usd-per-million", 4.5))
            output_price = float(cfg.get("output-usd-per-million", 27))
            conversion = usd_to_cny
        return round(((prompt_tokens * input_price + completion_tokens * output_price) / 1_000_000) * multiplier * conversion, 8)

    def invoke(self, payload: dict) -> tuple[str, dict[str, int], str]:
        answer, usage, model, _ = self.invoke_with_trace(payload)
        return answer, usage, model

    @staticmethod
    def _request_options(payload: dict, reasoning_effort: str = "", streaming: bool = False, *, model: str | None = None, default_effort: str = DEFAULT_GPT_REASONING_EFFORT) -> dict[str, Any]:
        """Forward OpenAI-compatible generation controls to the provider."""
        allowed = {
            "max_tokens", "max_completion_tokens", "temperature", "top_p", "n",
            "stop", "presence_penalty", "frequency_penalty", "tools", "tool_choice",
            "parallel_tool_calls", "response_format", "seed", "user", "logprobs",
            "top_logprobs", "modalities", "prediction", "service_tier",
        }
        options = {key: payload[key] for key in allowed if key in payload and payload[key] is not None}
        requested_reasoning = requested_reasoning_effort(payload)
        if requested_reasoning is None:
            # Use the resolved upstream name (not a public alias), including
            # requests that omit model and use the configured default model.
            requested_reasoning = default_gpt_reasoning_effort(model if model is not None else payload.get("model"), default_effort) or reasoning_effort
        if requested_reasoning is not None and requested_reasoning != "":
            options["reasoning_effort"] = str(requested_reasoning)
        if streaming:
            # OpenAI-compatible providers return token usage in the final SSE
            # event when this option is enabled. Older providers simply ignore
            # it, so token estimation remains the fallback.
            options["stream_options"] = {"include_usage": True}
        return options

    def invoke_with_trace(self, payload: dict) -> tuple[str, dict[str, int], str, dict[str, Any]]:
        """Invoke the configured SpringBootAI model and expose provider metadata.

        The regular ``invoke`` contract remains a three-tuple for existing
        callers. Protocol adapters use this trace-aware variant to persist the
        upstream response metadata alongside the generated answer.
        """
        spec = self.model_spec(payload.get("model"))
        model = spec["id"]
        messages = payload.get("messages") or []
        # Protocol requests are forwarded as-is.  In particular, do not
        # rebuild tool messages and accidentally discard tool_call_id.
        spring_messages = messages
        prompt_tokens = 0
        client = self.clients.get(model)
        demo_mode = self._as_bool(spec.get("demo-mode-when-key-missing", self.demo_mode), self.demo_mode)
        if client is None:
            if not demo_mode:
                raise RuntimeError(f"模型 {model} 未配置 api-key，且已关闭演示模式")
            answer = "演示模式已启用。请在 application.yml 或 OPENAI_API_KEY 中配置上游密钥后调用 gpt-5.6-sol。"
            prompt_tokens = self.estimate_message_tokens(messages)
            usage = {"prompt_tokens": prompt_tokens, "completion_tokens": self.estimate_tokens(answer), "total_tokens": prompt_tokens + self.estimate_tokens(answer)}
            return answer, usage, model, {"demo": True}
        reasoning_effort = str(spec.get("reasoning-effort") or "").strip()
        options = self._request_options(payload, reasoning_effort=reasoning_effort, model=spec.get("upstream-model") or model, default_effort=self.gpt_default_reasoning_effort)
        # Call the SpringBootAI ChatModel directly so request options reach
        # OpenAI-compatible HTTP providers.  This remains within the
        # SpringBootAI abstraction and avoids provider-specific SDK coupling.
        response = client.chat_model.call(spring_messages, options=options or None)
        answer = response.content()
        metadata = response.metadata or {}
        answer, dsml_tool_calls = parse_dsml_tool_calls(answer)
        if dsml_tool_calls:
            metadata = dict(metadata)
            metadata["tool_calls"] = dsml_tool_calls
        raw_usage = metadata.get("usage") or {}
        prompt_tokens = int(raw_usage.get("prompt_tokens", raw_usage.get("input_tokens", 0)) or 0)
        if prompt_tokens <= 0:
            prompt_tokens = self.estimate_message_tokens(messages)
        completion_tokens = int(raw_usage.get("completion_tokens", raw_usage.get("output_tokens", 0)) or 0)
        if completion_tokens <= 0:
            completion_tokens = self.estimate_tokens(answer)
        usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        }
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        return answer, usage, model, metadata

    async def ainvoke_with_trace(self, payload: dict) -> tuple[str, dict[str, int], str, dict[str, Any]]:
        """Async counterpart used by HTTP handlers for every configured model."""
        spec = self.model_spec(payload.get("model"))
        model = spec["id"]
        messages = payload.get("messages") or []
        client = self.clients.get(model)
        demo_mode = self._as_bool(spec.get("demo-mode-when-key-missing", self.demo_mode), self.demo_mode)
        if client is None:
            if not demo_mode:
                raise RuntimeError(f"模型 {model} 未配置 api-key，且已关闭演示模式")
            answer = "演示模式已启用。请在 application.yml 或 OPENAI_API_KEY 中配置上游密钥后调用 gpt-5.6-sol。"
            prompt_tokens = self.estimate_message_tokens(messages)
            completion_tokens = self.estimate_tokens(answer)
            return answer, {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "total_tokens": prompt_tokens + completion_tokens}, model, {"demo": True}
        reasoning_effort = str(spec.get("reasoning-effort") or "").strip()
        options = self._request_options(payload, reasoning_effort=reasoning_effort, model=spec.get("upstream-model") or model, default_effort=self.gpt_default_reasoning_effort)
        response = await client.chat_model.acall(messages, options=options or None)
        answer = response.content()
        metadata = response.metadata or {}
        answer, dsml_tool_calls = parse_dsml_tool_calls(answer)
        if dsml_tool_calls:
            metadata = dict(metadata)
            metadata["tool_calls"] = dsml_tool_calls
        raw_usage = metadata.get("usage") or {}
        prompt_tokens = int(raw_usage.get("prompt_tokens", raw_usage.get("input_tokens", 0)) or 0) or self.estimate_message_tokens(messages)
        completion_tokens = int(raw_usage.get("completion_tokens", raw_usage.get("output_tokens", 0)) or 0) or self.estimate_tokens(answer)
        usage = {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}
        usage["total_tokens"] = prompt_tokens + completion_tokens
        return answer, usage, model, metadata

    def stream_with_trace(self, payload: dict):
        """Yield real upstream text deltas through SpringBootAI's stream API.

        The caller can consume this iterator from an async response one chunk
        at a time.  No artificial post-hoc slicing is performed.
        """
        spec = self.model_spec(payload.get("model"))
        model = spec["id"]
        messages = payload.get("messages") or []
        spring_messages = messages
        prompt_tokens = 0
        client = self.clients.get(model)
        demo_mode = self._as_bool(spec.get("demo-mode-when-key-missing", self.demo_mode), self.demo_mode)
        if client is None:
            if not demo_mode:
                raise RuntimeError(f"模型 {model} 未配置 api-key，且已关闭演示模式")
            answer = "演示模式已启用。请在 application.yml 中配置上游密钥。"
            prompt_tokens = self.estimate_message_tokens(messages)
            yield {"delta": answer, "model": model, "prompt_tokens": prompt_tokens, "metadata": {"demo": True}}
            return
        reasoning_effort = str(spec.get("reasoning-effort") or "").strip()
        options = self._request_options(payload, reasoning_effort=reasoning_effort, streaming=True, model=spec.get("upstream-model") or model, default_effort=self.gpt_default_reasoning_effort)
        for response in client.chat_model.stream(spring_messages, options=options or None):
            delta = response.content() if response else ""
            metadata = response.metadata if response else {}
            usage = metadata.get("usage") if isinstance(metadata, dict) else None
            if isinstance(usage, dict):
                prompt_tokens = int(usage.get("prompt_tokens", usage.get("input_tokens", prompt_tokens)) or prompt_tokens)
            if delta or metadata:
                yield {
                    "delta": delta,
                    "model": model,
                    "prompt_tokens": prompt_tokens,
                    "metadata": metadata or {},
                    "tool_calls": (metadata or {}).get("tool_calls") or [],
                    "tool_call_delta": (metadata or {}).get("tool_call_delta") or [],
                    "finish_reason": (metadata or {}).get("finish_reason"),
                }
        if prompt_tokens <= 0:
            # Send the fallback count after upstream streaming has completed;
            # it cannot delay the first byte of the model response.
            yield {"delta": "", "model": model, "prompt_tokens": self.estimate_message_tokens(messages), "metadata": {}}

    async def astream_with_trace(self, payload: dict):
        """Yield provider SSE chunks without a worker-thread hop."""
        spec = self.model_spec(payload.get("model"))
        model = spec["id"]
        messages = payload.get("messages") or []
        client = self.clients.get(model)
        demo_mode = self._as_bool(spec.get("demo-mode-when-key-missing", self.demo_mode), self.demo_mode)
        if client is None:
            if not demo_mode:
                raise RuntimeError(f"模型 {model} 未配置 api-key，且已关闭演示模式")
            answer = "演示模式已启用。请在 application.yml 中配置上游密钥。"
            yield {"delta": answer, "model": model, "prompt_tokens": self.estimate_message_tokens(messages), "metadata": {"demo": True}}
            return
        reasoning_effort = str(spec.get("reasoning-effort") or "").strip()
        options = self._request_options(payload, reasoning_effort=reasoning_effort, streaming=True, model=spec.get("upstream-model") or model, default_effort=self.gpt_default_reasoning_effort)
        prompt_tokens = 0
        async for response in client.chat_model.astream(messages, options=options or None):
            delta = response.content() if response else ""
            metadata = response.metadata if response else {}
            usage = metadata.get("usage") if isinstance(metadata, dict) else None
            if isinstance(usage, dict):
                prompt_tokens = int(usage.get("prompt_tokens", usage.get("input_tokens", prompt_tokens)) or prompt_tokens)
            if delta or metadata:
                yield {
                    "delta": delta,
                    "model": model,
                    "prompt_tokens": prompt_tokens,
                    "metadata": metadata or {},
                    "tool_calls": (metadata or {}).get("tool_calls") or [],
                    "tool_call_delta": (metadata or {}).get("tool_call_delta") or [],
                    "finish_reason": (metadata or {}).get("finish_reason"),
                }
        if prompt_tokens <= 0:
            yield {"delta": "", "model": model, "prompt_tokens": self.estimate_message_tokens(messages), "metadata": {}}

    def charge_and_record(self, user_id: int, model: str, usage: dict[str, int]) -> tuple[bool, float, dict | None]:
        cost = self.estimate_cost(model, usage["prompt_tokens"], usage["completion_tokens"])
        ok, record = self.store.charge(user_id, model, usage["prompt_tokens"], usage["completion_tokens"], cost)
        return ok, cost, record

