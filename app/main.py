"""PayFlex API - point d'entrée FastAPI."""

from __future__ import annotations

import asyncio
import logging
import sys
import time

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app import __version__
from app.config import Settings, load_settings
from app.masking import PanRedactingFilter
from app.routes import router
from app.store import InMemoryStore, StoreError

logger = logging.getLogger("payflex")


def configure_logging() -> None:
    if logger.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(PanRedactingFilter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    # On ne renvoie jamais la valeur saisie (« input ») : elle peut contenir un PAN.
    errors = [
        {"loc": list(err.get("loc", [])), "msg": err.get("msg"), "type": err.get("type")}
        for err in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": errors})


async def store_error(_: Request, exc: StoreError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})


def create_app(settings: Settings | None = None) -> FastAPI:
    configure_logging()
    settings = settings or load_settings()
    app = FastAPI(title="PayFlex API", version=__version__)
    app.state.settings = settings
    app.state.store = InMemoryStore()
    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_exception_handler(StoreError, store_error)
    app.include_router(router)

    if settings.demo_password is None:
        logger.warning("PAYFLEX_DEMO_PASSWORD non défini : toute authentification sera refusée")
    if settings.simulated_latency_ms:
        logger.warning("latence simulée active : %d ms par requête", settings.simulated_latency_ms)

    app.add_middleware(AccessLogMiddleware, simulated_latency_ms=settings.simulated_latency_ms)
    return app


class AccessLogMiddleware:
    """Middleware ASGI pur : journal d'accès (PAN masqués), en-tête de durée et latence simulée.

    Implémenté sans BaseHTTPMiddleware, dont le surcoût est mesurable sous charge.
    """

    def __init__(self, app: ASGIApp, simulated_latency_ms: int = 0) -> None:
        self.app = app
        self.delay = simulated_latency_ms / 1000

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        start = time.perf_counter()
        path = scope["path"]
        if self.delay and path != "/health":
            await asyncio.sleep(self.delay)
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                elapsed = (time.perf_counter() - start) * 1000
                headers = MutableHeaders(scope=message)
                headers.append("X-Response-Time-Ms", f"{elapsed:.1f}")
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            logger.info("%s %s -> %d (%.1f ms)", scope["method"], path, status_code, duration_ms)


app = create_app()
