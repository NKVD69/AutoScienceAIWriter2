"""Endpoint `/system/capabilities` — schéma `Capabilities` du contrat.

Ce endpoint permet au frontend de **désactiver** une fonctionnalité plutôt
que de la laisser échouer à l'usage. Il ne doit donc jamais lever : chaque
détection est indépendante, et une dépendance absente vaut `false` ou `null`.
Un 500 ici priverait l'interface du seul moyen qu'elle a de savoir ce que la
machine sait faire.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["system"])


class Capabilities(BaseModel):
    platform: Literal["windows", "linux", "darwin"]
    gpu_available: bool
    vram_total_mb: int | None
    ollama_available: bool
    llm_model_loaded: str | None
    code_model_available: bool
    quarto_version: str | None
    wasm_sandbox_available: bool
    native_sandbox_available: bool
    native_network_isolation_guaranteed: bool
    sqlite_vec_version: str | None


def _platform() -> Literal["windows", "linux", "darwin"]:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"


def _safe(label: str, fn, default):
    """Exécute une détection ; toute panne vaut la valeur par défaut."""
    try:
        return fn()
    except Exception:  # une capacité indétectable est une capacité absente
        logger.warning("Détection « %s » indisponible", label, exc_info=True)
        return default


def _vram_total_mb() -> int | None:
    from app.llm.vram import read_vram_total_mb

    return read_vram_total_mb()


def _quarto_version() -> str | None:
    binary = shutil.which("quarto")
    if binary is None:
        return None
    completed = subprocess.run(  # binaire résolu par which, argument constant
        [binary, "--version"], capture_output=True, text=True, timeout=10, check=False
    )
    return completed.stdout.strip() or None


async def _sqlite_vec_version() -> str | None:
    """Version de l'extension vectorielle, via aiosqlite.

    ADR-001 interdit `sqlite3` synchrone dans le code applicatif, y compris
    pour une sonde : la règle n'aurait aucune valeur si on la contournait
    dès qu'elle gêne. La base en mémoire suffit — on n'interroge que
    l'extension, jamais de données.
    """
    import aiosqlite

    from app.db.session import load_vec_extension, vec_version

    conn = await aiosqlite.connect(":memory:")
    try:
        await load_vec_extension(conn)
        return await vec_version(conn) or None
    finally:
        await conn.close()


async def _llm_state() -> tuple[bool, str | None]:
    """(moteur joignable, modèle résident). Aucune exception ne remonte."""
    from app.llm.manager import resolve_backend_factory

    settings = get_settings()
    backend = None
    try:
        backend = resolve_backend_factory(settings.llm_backend)()  # type: ignore[call-arg]
        health = await backend.health()
        resident = settings.llm_model if health.is_resident(settings.llm_model) else None
        return health.available, resident
    except Exception:
        logger.warning("Détection du moteur LLM indisponible", exc_info=True)
        return False, None
    finally:
        aclose = getattr(backend, "aclose", None)
        if aclose is not None:
            await aclose()


@router.get(
    "/system/capabilities",
    response_model=Capabilities,
    summary="Capacités détectées de l'environnement",
)
async def capabilities() -> Capabilities:
    settings = get_settings()
    vram = _safe("vram", _vram_total_mb, None)
    moteur_joignable, modele_resident = await _llm_state()
    try:
        vec = await _sqlite_vec_version()
    except Exception:
        logger.warning("Détection « sqlite-vec » indisponible", exc_info=True)
        vec = None

    return Capabilities(
        platform=_platform(),
        gpu_available=vram is not None,
        vram_total_mb=vram,
        ollama_available=moteur_joignable,
        llm_model_loaded=modele_resident,
        # Le second résident exige 12 Go de VRAM, l'option activée (ADR-003) ET
        # un identifiant de modèle de code pour le moteur actif.
        code_model_available=(
            settings.code_model_enabled
            and settings.code_model is not None
            and vram is not None
            and vram >= settings.code_model_min_vram_mb
        ),
        quarto_version=_safe("quarto", _quarto_version, None),
        wasm_sandbox_available=_safe("wasm", lambda: shutil.which("node") is not None, False),
        native_sandbox_available=True,
        # ADR-005 : faux sous Windows, sans condition. Les Job Objects
        # contraignent la mémoire et le processeur, jamais le réseau.
        native_network_isolation_guaranteed=_platform() == "linux",
        sqlite_vec_version=vec,
    )
