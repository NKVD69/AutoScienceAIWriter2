"""Fabrique d'exécuteurs et orchestration sous politique — US-004, ADR-005.

**La fabrique impose la règle d'isolation, personne d'autre.** L'ordre des cas
est la règle : une origine agent reçoit TOUJOURS le niveau 1, quel que soit le
niveau demandé ; le niveau natif demandé par un agent n'est pas une erreur, il
est silencieusement ramené et journalisé. Un agent ne choisit jamais son propre
niveau d'isolation, et c'est vérifié ici plutôt que déclaré ailleurs.

`run_sandboxed` ajoute la politique autour de l'exécution : le niveau 2 exige le
consentement `native_execution`, vérifié AVANT le lancement, et chaque exécution
laisse une ligne dans `code_execution`. L'exécuteur, lui, ne connaît ni la base
ni le consentement — il exécute, rien de plus.
"""

from __future__ import annotations

from datetime import UTC, datetime

import aiosqlite

from app.core.errors import ConsentRequiredError, UnsupportedPlatformError
from app.core.logging import get_logger
from app.db.session import transaction
from app.sandbox.base import (
    ExecutionResult,
    MountSpec,
    ResourceLimits,
    SandboxExecutor,
    SandboxLevel,
    SandboxMode,
    SandboxOrigin,
)
from app.sandbox.wasm import WasmSandbox

logger = get_logger(__name__)

# Périmètre de consentement du niveau natif. Absent, le niveau 2 est refusé.
NATIVE_CONSENT_SCOPE = "native_execution"
# stdout/stderr sont tronqués à cette taille en base : un journal d'exécution
# n'a pas à stocker des mégaoctets de sortie (US-004, point 6).
_MAX_CAPTURE = 64 * 1024


def select(
    origin: SandboxOrigin, mode: SandboxMode, platform: str | None = None
) -> SandboxExecutor:
    """Choisit l'exécuteur. L'origine agent verrouille le niveau 1."""
    import sys

    plateforme = platform or sys.platform

    if origin == SandboxOrigin.AGENT:
        if mode == SandboxMode.NATIVE:
            logger.info(
                "Origine agent : niveau natif demandé, ramené au niveau 1 (Wasm). "
                "Un agent ne choisit jamais son niveau d'isolation (ADR-005)."
            )
        return WasmSandbox()

    if mode == SandboxMode.WASM:
        return WasmSandbox()

    # Origine utilisateur + niveau natif : l'exécuteur de la plateforme.
    if plateforme == "win32":
        from app.sandbox.native_windows import NativeWindowsSandbox

        return NativeWindowsSandbox()
    if plateforme.startswith("linux"):
        from app.sandbox.native_linux import NativeLinuxSandbox

        return NativeLinuxSandbox()
    raise UnsupportedPlatformError.for_platform(plateforme)


async def native_consent_granted(conn: aiosqlite.Connection, project_id: int) -> bool:
    async with conn.execute(
        "SELECT granted FROM consent WHERE project_id = ? AND scope = ?",
        (project_id, NATIVE_CONSENT_SCOPE),
    ) as cur:
        row = await cur.fetchone()
    return bool(row and int(row[0]) == 1)


async def persist_execution(
    conn: aiosqlite.Connection,
    project_id: int,
    origin: SandboxOrigin,
    result: ExecutionResult,
    code: str,
) -> int:
    """Écrit une ligne dans code_execution. Le niveau y est en clair : « wasm »
    ou « native », pour qu'une relecture sache sous quelle garantie le code a tourné.
    """
    async with transaction(conn):
        cur = await conn.execute(
            "INSERT INTO code_execution (project_id, origin, sandbox_level, code, stdout,"
            " stderr, exit_code, duration_ms, started_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                project_id,
                origin.value,
                result.level.name.lower(),
                code,
                result.stdout[:_MAX_CAPTURE],
                result.stderr[:_MAX_CAPTURE],
                result.exit_code,
                result.duration_ms,
                datetime.now(UTC).isoformat(),
            ),
        )
        return int(cur.lastrowid or 0)


async def run_sandboxed(
    conn: aiosqlite.Connection,
    project_id: int,
    origin: SandboxOrigin,
    mode: SandboxMode,
    code: str,
    mounts: list[MountSpec],
    limits: ResourceLimits,
    output_dir,
    platform: str | None = None,
) -> ExecutionResult:
    """Sélectionne, vérifie le consentement au niveau 2, exécute, persiste.

    Le consentement est vérifié AVANT le lancement (US-004, point 7) : rien de
    natif ne démarre sans lui. L'origine agent, ramenée au niveau 1, n'en exige
    aucun.
    """
    executor = select(origin, mode, platform)
    if executor.level == SandboxLevel.NATIVE:
        if not await native_consent_granted(conn, project_id):
            raise ConsentRequiredError.native_execution()
        logger.info(
            "Exécution native (niveau 2) autorisée par consentement native_execution "
            "(projet %s). Réseau non isolé sous Windows.",
            project_id,
        )
    result = await executor.run(code, mounts, limits, output_dir)
    await persist_execution(conn, project_id, origin, result, code)
    return result
