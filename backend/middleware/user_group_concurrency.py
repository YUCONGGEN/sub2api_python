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
        try:
            lease = await groups.acquire(user)
        except OverflowError:
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
            return
        try:
            await self.app(scope, receive, send)
        finally:
            lease.release()


__all__ = ["UserGroupConcurrencyMiddleware"]
