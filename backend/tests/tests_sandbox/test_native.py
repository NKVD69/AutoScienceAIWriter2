"""US-004 — bac à sable natif (niveau 2) : bornes, consentement, persistance.

Les bornes de ressources sont éprouvées contre de VRAIS processus : une
allocation qui dépasse la mémoire, une boucle qui épuise le CPU, une attente qui
franchit le délai mural. Ces tests sont marqués par plateforme — les primitives
diffèrent —, mais la garantie honnête « le réseau n'est pas isolé sous Windows »,
elle, est vérifiée là où elle s'applique.

Le consentement et la persistance passent par `run_sandboxed`, l'orchestration
qui entoure l'exécuteur : c'est elle qui refuse le niveau 2 sans accord et qui
laisse une trace en base.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime

import pytest

from app.core.errors import ConsentRequiredError
from app.db.migrations.runner import run_migrations
from app.db.session import connect, transaction
from app.sandbox.base import ResourceLimits, SandboxLevel, SandboxMode, SandboxOrigin
from app.sandbox.factory import run_sandboxed, select

NOW = datetime.now(UTC).isoformat()

_NATIVE = sys.platform == "win32" or sys.platform.startswith("linux")
native_only = pytest.mark.skipif(not _NATIVE, reason="niveau natif : win32 ou linux uniquement")
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="spécifique Windows")


@pytest.fixture
async def projet_db(tmp_path):
    """Fichier projet migré avec une ligne `project`. Rend la connexion."""
    chemin = tmp_path / "projet.sqlite"
    async with connect(chemin) as conn:
        await run_migrations(conn)
        async with transaction(conn):
            await conn.execute(
                "INSERT INTO project (id, name, subject, language, academic_level,"
                " created_at, updated_at) VALUES (1, 'P', 'Sujet', 'fr', 'doctorat', ?, ?)",
                (NOW, NOW),
            )
        yield conn


# --- Bornes de ressources (processus réels) -------------------------------


@windows_only
async def test_native_memory_limit_kills_process(tmp_path) -> None:
    executor = select(SandboxOrigin.USER, SandboxMode.NATIVE)
    code = "b = bytearray(400 * 1024 * 1024)\nprint('COMPLETED')"
    result = await executor.run(
        code, [], ResourceLimits(memory_mb=128, wall_seconds=30), tmp_path / "out"
    )
    assert "COMPLETED" not in result.stdout, "l'allocation aurait dû échouer sous la borne"
    assert result.exit_code != 0
    assert result.limit_exceeded == "memory"


@windows_only
async def test_native_cpu_limit_enforced(tmp_path) -> None:
    executor = select(SandboxOrigin.USER, SandboxMode.NATIVE)
    code = "x = 0\nwhile True:\n    x += 1\nprint('COMPLETED')"
    result = await executor.run(
        code, [], ResourceLimits(cpu_seconds=1, wall_seconds=30), tmp_path / "out"
    )
    assert "COMPLETED" not in result.stdout
    assert result.timed_out is False, "arrêté par la borne CPU, pas par le délai mural"
    assert result.limit_exceeded == "cpu"
    assert result.duration_ms < 30_000


@native_only
async def test_native_wall_timeout_kills_process_group(tmp_path) -> None:
    """Une attente qui ne consomme pas de CPU : seul le délai mural l'arrête."""
    executor = select(SandboxOrigin.USER, SandboxMode.NATIVE)
    code = "import time\ntime.sleep(30)\nprint('COMPLETED')"
    result = await executor.run(
        code, [], ResourceLimits(cpu_seconds=60, wall_seconds=2), tmp_path / "out"
    )
    assert result.timed_out is True
    assert result.limit_exceeded == "wall"
    assert "COMPLETED" not in result.stdout
    assert result.duration_ms < 10_000


@windows_only
async def test_native_windows_reports_network_not_guaranteed(tmp_path) -> None:
    """La garantie honnête : les Job Objects n'isolent pas le réseau (ADR-005)."""
    executor = select(SandboxOrigin.USER, SandboxMode.NATIVE, platform="win32")
    result = await executor.run("print('ok')\n", [], ResourceLimits(), tmp_path / "out")
    assert result.network_isolation_guaranteed is False
    assert result.level == SandboxLevel.NATIVE


# --- Consentement et persistance (orchestration) --------------------------


@native_only
async def test_native_requires_consent(projet_db, tmp_path) -> None:
    """Le niveau 2 sans consentement native_execution ne démarre pas."""
    with pytest.raises(ConsentRequiredError):
        await run_sandboxed(
            projet_db,
            1,
            SandboxOrigin.USER,
            SandboxMode.NATIVE,
            "print('x')\n",
            [],
            ResourceLimits(),
            tmp_path / "out",
        )
    async with projet_db.execute("SELECT count(*) FROM code_execution") as cur:
        assert (await cur.fetchone())[0] == 0, "rien n'a démarré, rien n'est persisté"


@native_only
async def test_execution_persisted_with_level(projet_db, tmp_path) -> None:
    async with transaction(projet_db):
        await projet_db.execute(
            "INSERT INTO consent (project_id, scope, granted, granted_at)"
            " VALUES (1, 'native_execution', 1, ?)",
            (NOW,),
        )
    result, execution_id = await run_sandboxed(
        projet_db,
        1,
        SandboxOrigin.USER,
        SandboxMode.NATIVE,
        "print('bonjour')\n",
        [],
        ResourceLimits(),
        tmp_path / "out",
    )
    assert result.level == SandboxLevel.NATIVE
    assert execution_id > 0
    async with projet_db.execute(
        "SELECT id, origin, sandbox_level, exit_code, network_isolation_guaranteed"
        " FROM code_execution"
    ) as cur:
        lignes = await cur.fetchall()
    assert len(lignes) == 1
    assert lignes[0][0] == execution_id
    assert lignes[0][1] == "user"
    assert lignes[0][2] == "native"  # le niveau est en clair, pas un entier
    assert lignes[0][3] == 0
    # La garantie OBSERVÉE est persistée, jamais réinférée du niveau (ADR-005).
    assert lignes[0][4] == (1 if result.network_isolation_guaranteed else 0)
