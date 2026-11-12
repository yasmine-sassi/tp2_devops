"""PayFlex API - point d'entrée FastAPI."""

from __future__ import annotations

import asyncio
import logging
import sys
import time

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

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

    @app.middleware("http")
    async def access_log(request: Request, call_next):
        start = time.perf_counter()
        if settings.simulated_latency_ms and request.url.path != "/health":
            await asyncio.sleep(settings.simulated_latency_ms / 1000)
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000
        response.headers["X-Response-Time-Ms"] = f"{duration_ms:.1f}"
        logger.info(
            "%s %s -> %d (%.1f ms)",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response

    return app


app = create_app()
