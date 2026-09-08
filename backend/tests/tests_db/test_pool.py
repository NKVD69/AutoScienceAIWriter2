"""US-101 — cache de connexions : reutilisation, eviction LRU, fichier absent."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.errors import ProjectNotFoundError
from app.db.migrations.runner import run_migrations
from app.db.pool import ConnectionPool
from app.db.session import connect


async def make_project_file(path: Path) -> Path:
    async with connect(path) as conn:
        await run_migrations(conn)
    return path


async def test_pool_reuses_connection(tmp_path: Path) -> None:
    fichier = await make_project_file(tmp_path / "a.sqlite")
    pool = ConnectionPool(max_open=5)
    try:
        premiere = await pool.acquire(1, fichier)
        seconde = await pool.acquire(1, fichier)
        assert premiere is seconde
        assert pool.open_count == 1
    finally:
        await pool.close_all()


async def test_pool_loads_vector_extension_on_open(tmp_path: Path) -> None:
    """Les PRAGMA d'ADR-001 et l'extension d'ADR-002 sont appliques a
    l'ouverture, sans quoi chaque requete les repaierait."""
    fichier = await make_project_file(tmp_path / "a.sqlite")
    pool = ConnectionPool(max_open=5)
    try:
        conn = await pool.acquire(1, fichier)
        async with conn.execute("SELECT vec_version()") as cur:
            assert (await cur.fetchone())[0]
        async with conn.execute("PRAGMA foreign_keys") as cur:
            assert (await cur.fetchone())[0] == 1
    finally:
        await pool.close_all()


async def test_pool_evicts_lru_beyond_limit(tmp_path: Path) -> None:
    fichiers = {i: await make_project_file(tmp_path / f"p{i}.sqlite") for i in range(1, 5)}
    pool = ConnectionPool(max_open=2)
    try:
        await pool.acquire(1, fichiers[1])
        await pool.acquire(2, fichiers[2])
        # Reutiliser 1 le rend le plus recent : c'est 2 qui doit sortir.
        await pool.acquire(1, fichiers[1])
        await pool.acquire(3, fichiers[3])

        assert pool.open_count == 2
        assert pool.is_open(1)
        assert not pool.is_open(2)
        assert pool.is_open(3)
    finally:
        await pool.close_all()


async def test_evicted_connection_is_closed_not_leaked(tmp_path: Path) -> None:
    fichiers = {i: await make_project_file(tmp_path / f"p{i}.sqlite") for i in (1, 2)}
    pool = ConnectionPool(max_open=1)
    try:
        premiere = await pool.acquire(1, fichiers[1])
        await pool.acquire(2, fichiers[2])
        assert not pool.is_open(1)
        # Une connexion evincee mais non fermee garderait un descripteur et,
        # sous Windows, un verrou sur le fichier.
        with pytest.raises(ValueError, match="no active connection"):
            await premiere.execute("SELECT 1")
    finally:
        await pool.close_all()


async def test_release_closes_connection(tmp_path: Path) -> None:
    fichier = await make_project_file(tmp_path / "a.sqlite")
    pool = ConnectionPool(max_open=5)
    try:
        await pool.acquire(1, fichier)
        await pool.release(1)
        assert not pool.is_open(1)
        assert pool.open_count == 0
        # Relacher un projet non ouvert ne doit pas lever.
        await pool.release(999)
    finally:
        await pool.close_all()


async def test_missing_file_raises_actionable_error(tmp_path: Path) -> None:
    """Cas reel : l'utilisateur possede ses fichiers et peut en deplacer un."""
    pool = ConnectionPool(max_open=5)
    absent = tmp_path / "disparu.sqlite"
    try:
        with pytest.raises(ProjectNotFoundError) as exc:
            await pool.acquire(7, absent)
    finally:
        await pool.close_all()

    message = exc.value.message
    assert "disparu.sqlite" in message
    assert "déplacé" in message
    assert "/api/v1/projects/7" in message, "le message doit proposer une action"
    assert exc.value.status_code == 404


async def test_close_all_empties_the_pool(tmp_path: Path) -> None:
    fichiers = [await make_project_file(tmp_path / f"p{i}.sqlite") for i in range(3)]
    pool = ConnectionPool(max_open=5)
    for i, f in enumerate(fichiers):
        await pool.acquire(i, f)
    assert pool.open_count == 3
    await pool.close_all()
    assert pool.open_count == 0
