import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

from backend.middleware.user_group_concurrency import UserGroupConcurrencyMiddleware
from backend.service.user_group_service import UserGroupService


USER = {"id": 7, "username": "test-user", "group_concurrency_limit": 1}


class RequestHarness:
    def __init__(self, groups, app, *, path="/v1/responses", user=USER):
        beans = {
            "auth_service": SimpleNamespace(user_from_authorization=lambda value: user),
            "user_group_service": groups,
        }
        context = SimpleNamespace(get_bean=beans.__getitem__)
        self.scope = {
            "type": "http", "method": "POST", "path": path, "http_version": "1.1",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "headers": [(b"authorization", b"Bearer test-only")],
            "app": SimpleNamespace(state=SimpleNamespace(spring_application=SimpleNamespace(application_context=context))),
        }
        self.incoming = asyncio.Queue()
        self.sent = []
        self.response_started = asyncio.Event()
        self.middleware = UserGroupConcurrencyMiddleware(app)

    async def send(self, message):
        self.sent.append(message)
        if message["type"] == "http.response.start":
            self.response_started.set()

    def body(self, body=b'{"model":"gpt-6-astra","input":"hello"}', *, more=False):
        self.incoming.put_nowait({"type": "http.request", "body": body, "more_body": more})

    def disconnect(self):
        self.incoming.put_nowait({"type": "http.disconnect"})

    def start(self):
        return asyncio.create_task(self.middleware(self.scope, self.incoming.get, self.send))


async def wait_until(predicate):
    async def poll():
        while not predicate():
            await asyncio.sleep(0)
    await asyncio.wait_for(poll(), timeout=2)


def assert_idle(groups):
    assert groups.active_for_user(7) == 0
    assert groups.queue_metrics()["queue_waiting"] == 0


def test_disconnected_queued_request_is_removed_without_forwarding():
    async def scenario():
        groups = UserGroupService()
        occupied = await groups.acquire(USER)
        forwarded = []

        async def app(scope, receive, send):
            forwarded.append(await Request(scope, receive).json())
            await JSONResponse({"ok": True})(scope, receive, send)

        request = RequestHarness(groups, app)
        request.body()
        task = request.start()
        await wait_until(lambda: groups.queue_metrics()["queue_waiting"] == 1)
        request.disconnect()
        await asyncio.wait_for(task, 2)
        assert groups.queue_metrics()["queue_waiting"] == 0
        assert groups.active_for_user(7) == 1  # Do not release another request's lease.
        assert forwarded == []
        assert request.sent == []
        occupied.release()
        assert_idle(groups)

    asyncio.run(scenario())


def test_connected_queued_request_still_runs_after_slot_becomes_available():
    async def scenario():
        groups = UserGroupService()
        occupied = await groups.acquire(USER)
        forwarded = []

        async def app(scope, receive, send):
            forwarded.append(await Request(scope, receive).json())
            await JSONResponse({"ok": True})(scope, receive, send)

        request = RequestHarness(groups, app)
        request.body()
        task = request.start()
        await wait_until(lambda: groups.queue_metrics()["queue_waiting"] == 1)
        assert not task.done()
        assert forwarded == []
        occupied.release()
        await asyncio.wait_for(task, 2)
        assert forwarded == [{"model": "gpt-6-astra", "input": "hello"}]
        assert request.sent[0]["status"] == 200
        assert_idle(groups)

    asyncio.run(scenario())


@pytest.mark.parametrize("path", sorted(UserGroupConcurrencyMiddleware.PROTECTED_PATHS))
def test_disconnect_before_response_headers_releases_user_slot(path):
    async def scenario():
        groups = UserGroupService()
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def app(scope, receive, send):
            await Request(scope, receive).json()
            started.set()
            try:
                await asyncio.Event().wait()  # Waiting for upstream headers/account slot.
            finally:
                cancelled.set()

        request = RequestHarness(groups, app, path=path)
        request.body()
        task = request.start()
        await asyncio.wait_for(started.wait(), 2)
        assert groups.active_for_user(7) == 1
        request.disconnect()
        await asyncio.wait_for(task, 2)
        assert cancelled.is_set()
        assert_idle(groups)

    asyncio.run(scenario())


@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
def test_stream_disconnect_releases_lease_and_next_user_request_runs(spec_version):
    async def scenario():
        groups = UserGroupService()
        stream_closed = asyncio.Event()

        async def events():
            try:
                yield b"data: first\n\n"
                await asyncio.Event().wait()
            finally:
                stream_closed.set()

        async def app(scope, receive, send):
            await Request(scope, receive).json()
            await StreamingResponse(events(), media_type="text/event-stream")(scope, receive, send)

        request = RequestHarness(groups, app)
        request.scope["asgi"]["spec_version"] = spec_version
        request.body()
        task = request.start()
        await asyncio.wait_for(request.response_started.wait(), 2)
        next_task = asyncio.create_task(groups.acquire(USER))
        await wait_until(lambda: groups.queue_metrics()["queue_waiting"] == 1)
        request.disconnect()
        await asyncio.wait_for(task, 2)
        assert stream_closed.is_set()
        next_lease = await asyncio.wait_for(next_task, 2)
        assert groups.active_for_user(7) == 1
        next_lease.release()
        assert_idle(groups)

    asyncio.run(scenario())


def test_body_and_sse_are_unchanged_and_stream_is_not_buffered():
    async def scenario():
        groups = UserGroupService()
        finish = asyncio.Event()
        chunks = [b"data: first\n\n", b"data: second\n\n", b"data: [DONE]\n\n"]
        body_parts = [b'{"model":"gpt-6-astra",', b'"input":"hello"}']

        async def events():
            yield chunks[0]
            await finish.wait()
            yield chunks[1]
            yield chunks[2]

        async def app(scope, receive, send):
            body = await Request(scope, receive).body()
            assert body == b"".join(body_parts)
            assert groups.active_for_user(7) == 1
            await StreamingResponse(events(), media_type="text/event-stream")(scope, receive, send)

        request = RequestHarness(groups, app)
        request.body(body_parts[0], more=True)
        task = request.start()
        await asyncio.sleep(0)
        assert groups.active_for_user(7) == 0  # Uploading a partial body is not an upstream task.
        request.body(body_parts[1])
        await wait_until(lambda: any(message.get("body") == chunks[0] for message in request.sent))
        assert not task.done()
        assert groups.active_for_user(7) == 1
        finish.set()
        await asyncio.wait_for(task, 2)
        assert b"".join(message.get("body", b"") for message in request.sent) == b"".join(chunks)
        assert_idle(groups)

    asyncio.run(scenario())


def test_queue_full_still_returns_429_not_invalid_json():
    async def scenario():
        groups = UserGroupService()
        occupied = await groups.acquire(USER)
        groups.max_queued_requests = 0

        async def app(scope, receive, send):
            try:
                await Request(scope, receive).json()
            except Exception:
                await JSONResponse({"error": "invalid JSON"}, status_code=400)(scope, receive, send)
                return
            raise AssertionError("full queue must not forward")

        request = RequestHarness(groups, app)
        request.body()
        await asyncio.wait_for(request.start(), 2)
        assert request.sent[0]["status"] == 429
        assert dict(request.sent[0]["headers"])[b"retry-after"] == b"1"
        assert json.loads(request.sent[1]["body"])["error"]["type"] == "user_group_queue_full"
        assert groups.active_for_user(7) == 1
        occupied.release()
        assert_idle(groups)

    asyncio.run(scenario())


@pytest.mark.parametrize("queued", [False, True])
def test_server_cancellation_releases_owned_lease_or_queue(queued):
    async def scenario():
        groups = UserGroupService()
        occupied = await groups.acquire(USER) if queued else None
        started = asyncio.Event()

        async def app(scope, receive, send):
            await Request(scope, receive).json()
            started.set()
            await asyncio.Event().wait()

        request = RequestHarness(groups, app)
        request.body()
        task = request.start()
        if queued:
            await wait_until(lambda: groups.queue_metrics()["queue_waiting"] == 1)
        else:
            await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        if occupied:
            assert groups.active_for_user(7) == 1
            occupied.release()
        assert_idle(groups)

    asyncio.run(scenario())


def test_disconnect_racing_with_admission_does_not_leak_a_slot():
    async def scenario():
        groups = UserGroupService()
        occupied = await groups.acquire(USER)

        async def app(scope, receive, send):
            await Request(scope, receive).json()
            await asyncio.Event().wait()

        request = RequestHarness(groups, app)
        request.body()
        task = request.start()
        await wait_until(lambda: groups.queue_metrics()["queue_waiting"] == 1)
        occupied.release()
        request.disconnect()
        await asyncio.wait_for(task, 2)
        assert_idle(groups)

    asyncio.run(scenario())


def test_handler_exception_releases_slot():
    async def scenario():
        groups = UserGroupService()

        async def app(scope, receive, send):
            await Request(scope, receive).json()
            raise RuntimeError("test handler failure")

        request = RequestHarness(groups, app)
        request.body()
        with pytest.raises(RuntimeError, match="test handler failure"):
            await asyncio.wait_for(request.start(), 2)
        assert_idle(groups)

    asyncio.run(scenario())


def test_normal_response_completion_does_not_cancel_background_cleanup():
    async def scenario():
        groups = UserGroupService()
        cleanup_started = asyncio.Event()
        cleanup_finished = asyncio.Event()
        allow_cleanup = asyncio.Event()

        async def app(scope, receive, send):
            await Request(scope, receive).json()
            await JSONResponse({"ok": True})(scope, receive, send)
            cleanup_started.set()
            await allow_cleanup.wait()
            cleanup_finished.set()

        request = RequestHarness(groups, app)
        request.body()
        task = request.start()
        await asyncio.wait_for(cleanup_started.wait(), 2)
        request.disconnect()  # Uvicorn's receive returns this once response_complete is true.
        for _ in range(10):
            await asyncio.sleep(0)
        assert not task.done()
        allow_cleanup.set()
        await asyncio.wait_for(task, 2)
        assert cleanup_finished.is_set()
        assert_idle(groups)

    asyncio.run(scenario())


@pytest.mark.parametrize("user", [None, USER])
def test_early_rejection_without_body_does_not_take_or_wait_for_slot(user):
    async def scenario():
        groups = UserGroupService()
        occupied = await groups.acquire(USER)

        async def app(scope, receive, send):
            await JSONResponse({"error": "rejected"}, status_code=403)(scope, receive, send)

        request = RequestHarness(groups, app, user=user)
        await asyncio.wait_for(request.start(), 2)
        assert request.sent[0]["status"] == 403
        assert groups.active_for_user(7) == 1
        assert groups.queue_metrics()["queue_waiting"] == 0
        occupied.release()
        assert_idle(groups)

    asyncio.run(scenario())
