"""Application entry point using SpringBootAI's application lifecycle."""

import asyncio
import logging

from springbootai import SpringBootApplication, create_app, get_config
from springbootai.orm import MapperScan

from backend.controller import register_payment_routes, register_proxy_route
from backend.middleware import UserGroupConcurrencyMiddleware


@SpringBootApplication(scan_base_packages=["backend"])
@MapperScan(base_packages=["backend.mappers"])
class RoseApplication:
    pass


app = create_app(RoseApplication)
app.add_middleware(UserGroupConcurrencyMiddleware)


async def _install_windows_disconnect_handler():
    """Ignore expected client disconnect noise from Windows Proactor.

    When a browser or Codex cancels an HTTP/SSE request, Windows may report
    ``ConnectionResetError(10054)`` while Uvicorn is closing the socket. This
    is not an application failure; all other loop exceptions retain the
    default asyncio logging behavior.
    """
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()

    def handle(loop, context):
        exc = context.get("exception")
        # On CPython/Windows the reset code is exposed as ``errno`` on some
        # builds and as ``winerror`` on others. Uvicorn reaches this handler
        # while closing a client-cancelled HTTP/SSE socket.
        if isinstance(exc, ConnectionResetError) and (
            getattr(exc, "winerror", None) == 10054
            or getattr(exc, "errno", None) == 10054
        ):
            return
        if previous:
            previous(loop, context)
        else:
            loop.default_exception_handler(context)

    loop.set_exception_handler(handle)
    # SpringBootAI's HandlerInterceptor is the single HTTP access-log source.
    # This also covers raw SSE protocol routes because the framework installs
    # the interceptor as global ASGI middleware.  Disable Uvicorn's duplicate
    # access line even when the app is launched through ``uvicorn app:app``.
    logging.getLogger("uvicorn.access").disabled = True


if hasattr(app, "add_event_handler"):
    app.add_event_handler("startup", _install_windows_disconnect_handler)

# OpenAI Responses/Chat and payment callbacks are protocol-level endpoints.
# They are registered on the ASGI app produced by SpringBootAI because they
# must preserve streaming, form-urlencoded and binary responses rather than
# the framework's standard Result envelope.
register_proxy_route(app)
register_payment_routes(app)

config = get_config()


if __name__ == "__main__":
    import uvicorn

    # Pass the already-created SpringBootAI ASGI app.  Importing ``app:app``
    # here would build the application context a second time when this file is
    # started with ``python app.py``.
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(config.get("server", {}).get("port", 8241)),
        reload=False,
        log_config=None,
        access_log=False,
    )
