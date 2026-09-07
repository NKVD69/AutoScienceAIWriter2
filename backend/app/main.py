"""Point d'entrée FastAPI.

Monolithe modulaire (spécifications §2) : les frontières de modules sont
maintenues par l'absence d'import croisé, pas par des processus séparés.
ADR-012 — aucun conteneur au MVP.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.v1 import health
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import configure_logging, get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    settings.projects_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Science AI Writer IDE %s — données : %s", settings.version, settings.data_dir)
    yield
    logger.info("Arrêt du service")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Science AI Writer IDE — API locale",
        version=settings.version,
        lifespan=lifespan,
    )

    @app.exception_handler(AppError)
    async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        """Projette toute erreur métier sur la forme `{code, message, details}`
        imposée par le contrat. Sans ce relais, une erreur métier sortirait en
        500 opaque et le frontend ne pourrait pas la distinguer d'une panne."""
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())

    app.include_router(health.router, prefix=settings.api_prefix)
    return app


app = create_app()
