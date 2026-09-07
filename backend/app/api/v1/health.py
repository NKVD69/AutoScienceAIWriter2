"""Endpoint `/health` — contrat `contracts/openapi.yaml`, schéma `Health`."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import assert_pragmas, connect

logger = get_logger(__name__)
router = APIRouter(tags=["system"])


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    database: Literal["ok", "error"]
    llm: Literal["ok", "unavailable"]


async def _probe_database() -> Literal["ok", "error"]:
    """Ouvre une base jetable et vérifie que les PRAGMA d'ADR-001 prennent.

    La sonde porte sur un fichier réel : une base en mémoire accepte
    `journal_mode=WAL` sans l'appliquer et validerait une installation
    défaillante.
    """
    try:
        with tempfile.TemporaryDirectory() as tmp:
            async with connect(Path(tmp) / "health.sqlite") as conn:
                await assert_pragmas(conn)
        return "ok"
    except Exception:
        logger.exception("Sonde base de données en échec")
        return "error"


@router.get("/health", response_model=Health, summary="État du service et de ses dépendances")
async def health() -> Health:
    settings = get_settings()
    database = await _probe_database()
    # `llm` reste `unavailable` tant que US-003 n'a pas livré le LLMManager :
    # rapporter `ok` sur la seule présence d'Ollama annoncerait une capacité
    # que le service n'expose pas encore.
    llm: Literal["ok", "unavailable"] = "unavailable"
    return Health(
        status="ok" if database == "ok" else "degraded",
        version=settings.version,
        database=database,
        llm=llm,
    )
