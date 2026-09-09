"""ASGI middleware holding a user concurrency slot through streamed responses."""

from __future__ import annotations

import asyncio
import json


class UserGroupConcurrencyMiddleware:
    PROTECTED_PATHS = {
        "/v1/chat/completions", "/chat/completions",
        "/v1/responses", "/responses",
        "/v1/messages", "/messages",
        "/v1/messages/count_tokens",
    }

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("method") != "POST" or scope.get("path") not in self.PROTECTED_PATHS:
            await self.app(scope, receive, send)
            return
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        authorization = str(headers.get("authorization") or "").strip()
        if not authorization and headers.get("x-api-key"):
            authorization = f"Bearer {headers['x-api-key']}"
        context = scope["app"].state.spring_application.application_context
        auth = context.get_bean("auth_service")
        groups = context.get_bean("user_group_service")
        user = await asyncio.to_thread(auth.user_from_authorization, authorization)
        if not user:
            await self.app(scope, receive, send)
            return
        # Reuse the authenticated user in the protocol adapter. This removes a
        # second user/group lookup and a duplicate API-key last_used write from
        # every forwarding request.
        scope.setdefault("state", {})["rose_user"] = user
        await self._dispatch(scope, receive, send, groups, user)

    async def _dispatch(self, scope, receive, send, groups, user):
        loop = asyncio.get_running_loop()
        body_ready = loop.create_future()
        admitted = loop.create_future()
        disconnect_task = None
        acquire_task = None
        response_complete = False

        async def guarded_send(message):
            nonlocal response_complete
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                response_complete = True

        async def watch_disconnect():
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return message

        async def guarded_receive():
            if body_ready.done():
                # StreamingResponse may also listen for a disconnect. Keep
                # exactly one reader of the ASGI receive channel, and do not
                # let cancellation of that listener cancel our watcher.
                await admitted
                return await asyncio.shield(disconnect_task)
            message = await receive()
            if message["type"] == "http.request" and not message.get("more_body", False):
                # All protected handlers read their JSON body before proxying.
                # Gate the last body frame: no extra body copy, unbounded
                # receive queue, or concurrent reader is needed while waiting.
                body_ready.set_result(None)
                await admitted
            return message

        app_task = asyncio.create_task(self.app(scope, guarded_receive, guarded_send))
        try:
            await asyncio.wait({app_task, body_ready}, return_when=asyncio.FIRST_COMPLETED)
            if app_task.done():
                await app_task
                return

            disconnect_task = asyncio.create_task(watch_disconnect())
            acquire_task = asyncio.create_task(groups.acquire(user))
            await asyncio.wait({app_task, acquire_task, disconnect_task}, return_when=asyncio.FIRST_COMPLETED)
            if disconnect_task.done():
                return
            if app_task.done():
                await app_task
                return
            try:
                acquire_task.result()
            except OverflowError:
                # Handle queue errors outside request.json(), whose exception
                # handler would otherwise misreport them as invalid JSON.
                app_task.cancel()
                await asyncio.gather(app_task, return_exceptions=True)
                await self._queue_full(send)
                return

            admitted.set_result(None)
            await asyncio.wait({app_task, disconnect_task}, return_when=asyncio.FIRST_COMPLETED)
            # Uvicorn also emits http.disconnect after a normal response ends.
            # Do not cancel post-response cleanup on a healthy completed call.
            if app_task.done() or response_complete:
                await app_task
        finally:
            tasks = [task for task in (app_task, acquire_task, disconnect_task) if task is not None]
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            # Admission and disconnect can win in the same event-loop turn.
            # Inspect the settled acquire task so even an undelivered lease
            # is released, exactly once. A cancelled acquire cleans its queue.
            if acquire_task is not None and not acquire_task.cancelled() and acquire_task.exception() is None:
                acquire_task.result().release()
            for future in (body_ready, admitted):
                if not future.done():
                    future.cancel()

    @staticmethod
    async def _queue_full(send):
        body = json.dumps({
            "error": {
                "message": "用户组并发等待队列已满，请稍后重试",
                "type": "user_group_queue_full",
            }
        }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 429,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"retry-after", b"1"),
                (b"x-rose-error-source", b"user_group_concurrency"),
            ],
        })
        await send({"type": "http.response.body", "body": body})


__all__ = ["UserGroupConcurrencyMiddleware"]
