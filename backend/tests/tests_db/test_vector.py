"""US-002 — insertion vectorielle et recherche KNN avec provenance. ADR-002."""

from __future__ import annotations

import random
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest

from app.core.config import get_settings
from app.db.migrations.runner import run_migrations
from app.db.session import connect, transaction
from app.db.vector import ChunkHit, insert_chunk_with_embedding, search_similar_chunks

NOW = datetime.now(UTC).isoformat()
DIM = get_settings().embedding_dim


def vector(seed: int) -> list[float]:
    rng = random.Random(seed)
    return [rng.random() for _ in range(DIM)]


def near(base: list[float], epsilon: float = 1e-4) -> list[float]:
    """Vecteur volontairement proche de `base` : le plus proche voisin est connu."""
    return [v + epsilon for v in base]


async def seed(
    db_path: Path,
    sources: list[tuple[int, str, int, int]],
    chunks_per_source: int = 5,
) -> None:
    """Crée un projet, ses sources et leurs chunks vectorisés.

    `sources` : (id, titre, année, is_preprint).
    """
    async with connect(db_path) as conn:
        await run_migrations(conn)
        async with transaction(conn):
            await conn.execute(
                "INSERT INTO project (id, name, subject, language, academic_level,"
                " created_at, updated_at) VALUES (1, 'These', 'Sujet', 'fr', 'doctorat', ?, ?)",
                (NOW, NOW),
            )
            for sid, title, year, preprint in sources:
                await conn.execute(
                    "INSERT INTO source_document (id, project_id, kind, title, year, doi,"
                    " is_preprint, imported_at) VALUES (?, 1, 'article', ?, ?, ?, ?, ?)",
                    (sid, title, year, f"10.1000/{sid}", preprint, NOW),
                )
        for sid, _, _, _ in sources:
            for ordinal in range(chunks_per_source):
                await insert_chunk_with_embedding(
                    conn,
                    source_id=sid,
                    ordinal=ordinal,
                    text=f"source {sid} chunk {ordinal}",
                    embedding=vector(sid * 100 + ordinal),
                    page_start=ordinal + 1,
                    page_end=ordinal + 2,
                )


async def test_insert_rejects_wrong_dimension(project_db: Path) -> None:
    await seed(project_db, [(1, "Source A", 2020, 0)], chunks_per_source=0)
    async with connect(project_db) as conn:
        with pytest.raises(ValueError, match=str(DIM)):
            await insert_chunk_with_embedding(
                conn, source_id=1, ordinal=0, text="t", embedding=[0.1] * (DIM - 1)
            )


async def test_insert_leaves_no_orphan_on_failure(project_db: Path) -> None:
    """Un embedding refuse ne doit pas laisser un chunk sans vecteur."""
    await seed(project_db, [(1, "Source A", 2020, 0)], chunks_per_source=0)
    async with connect(project_db) as conn:
        with pytest.raises(ValueError):
            await insert_chunk_with_embedding(
                conn, source_id=1, ordinal=0, text="t", embedding=[0.1] * (DIM + 1)
            )
        async with conn.execute("SELECT count(*) FROM chunk") as cur:
            row = await cur.fetchone()
    assert row is not None and row[0] == 0


async def test_insert_returns_id_matching_vec_rowid(project_db: Path) -> None:
    await seed(project_db, [(1, "Source A", 2020, 0)], chunks_per_source=0)
    async with connect(project_db) as conn:
        chunk_id = await insert_chunk_with_embedding(
            conn, source_id=1, ordinal=0, text="t", embedding=vector(7)
        )
        async with conn.execute("SELECT rowid FROM vec_chunk") as cur:
            row = await cur.fetchone()
    assert row is not None and row[0] == chunk_id


async def test_knn_returns_k_results_with_provenance(project_db: Path) -> None:
    await seed(
        project_db,
        [(1, "Source A", 2020, 0), (2, "Source B", 2021, 0), (3, "Source C", 2022, 0)],
        chunks_per_source=34,
    )
    async with connect(project_db) as conn:
        hits = await search_similar_chunks(conn, near(vector(100)), k=5)

    assert len(hits) == 5
    assert all(isinstance(h, ChunkHit) for h in hits)
    first = hits[0]
    # Le voisin le plus proche est le vecteur dont on est parti.
    assert first.source_id == 1
    assert first.source_title == "Source A"
    assert first.source_year == 2020
    assert first.source_doi == "10.1000/1"
    assert first.page_start == 1
    assert first.distance >= 0.0
    assert [h.distance for h in hits] == sorted(h.distance for h in hits)


async def test_knn_with_year_filter(project_db: Path) -> None:
    await seed(
        project_db,
        [(1, "Ancienne", 1998, 0), (2, "Recente", 2021, 0), (3, "Tres recente", 2024, 0)],
        chunks_per_source=20,
    )
    async with connect(project_db) as conn:
        hits = await search_similar_chunks(conn, vector(4242), k=10, year_min=2020)

    assert hits
    assert all(h.source_year is not None and h.source_year >= 2020 for h in hits)
    assert {h.source_id for h in hits} <= {2, 3}


async def test_knn_excludes_preprints(project_db: Path) -> None:
    await seed(
        project_db,
        [(1, "Article", 2020, 0), (2, "Preprint", 2021, 1)],
        chunks_per_source=20,
    )
    async with connect(project_db) as conn:
        toutes = await search_similar_chunks(conn, vector(999), k=10)
        filtrees = await search_similar_chunks(conn, vector(999), k=10, exclude_preprints=True)

    assert any(h.is_preprint for h in toutes), "le jeu de test doit contenir une preprint"
    assert all(not h.is_preprint for h in filtrees)


async def test_knn_filter_expansion_still_fills_k(project_db: Path) -> None:
    """Le KNN precede la jointure : sans elargissement interne, un filtre
    selectif renverrait moins de k resultats alors que la base en contient assez."""
    await seed(
        project_db,
        [(1, "Bruit", 1990, 0), (2, "Cible", 2023, 0)],
        chunks_per_source=40,
    )
    async with connect(project_db) as conn:
        hits = await search_similar_chunks(conn, vector(31337), k=5, year_min=2020)
    assert len(hits) == 5


async def test_knn_project_filter(project_db: Path) -> None:
    await seed(project_db, [(1, "Source A", 2020, 0)], chunks_per_source=10)
    async with connect(project_db) as conn:
        assert await search_similar_chunks(conn, vector(1), k=5, project_id=1)
        assert await search_similar_chunks(conn, vector(1), k=5, project_id=999) == []


async def test_search_rejects_wrong_dimension(project_db: Path) -> None:
    await seed(project_db, [(1, "Source A", 2020, 0)], chunks_per_source=3)
    async with connect(project_db) as conn:
        with pytest.raises(ValueError, match=str(DIM)):
            await search_similar_chunks(conn, [0.1] * 16, k=5)


async def test_no_vector_without_chunk(project_db: Path) -> None:
    """Invariant d'ADR-002 : toute ligne de vec_chunk a son chunk."""
    await seed(project_db, [(1, "Source A", 2020, 0)], chunks_per_source=7)
    async with connect(project_db) as conn:
        async with conn.execute(
            "SELECT count(*) FROM vec_chunk v LEFT JOIN chunk c ON c.id = v.rowid"
            " WHERE c.id IS NULL"
        ) as cur:
            orphelins = (await cur.fetchone())[0]
        async with conn.execute(
            "SELECT count(*) FROM chunk c LEFT JOIN vec_chunk v ON c.id = v.rowid"
            " WHERE v.rowid IS NULL"
        ) as cur:
            sans_vecteur = (await cur.fetchone())[0]
    assert orphelins == 0
    assert sans_vecteur == 0


async def test_insert_is_usable_from_concurrent_connections(project_db: Path) -> None:
    """ADR-001 : pas de file d'ecriture ; WAL et busy_timeout suffisent."""
    import asyncio

    await seed(project_db, [(1, "Source A", 2020, 0)], chunks_per_source=0)

    async def writer(ordinal: int) -> None:
        async with connect(project_db) as conn:
            await insert_chunk_with_embedding(
                conn, source_id=1, ordinal=ordinal, text=f"c{ordinal}", embedding=vector(ordinal)
            )

    await asyncio.gather(*(writer(i) for i in range(12)))

    async with connect(project_db) as conn:
        conn: aiosqlite.Connection
        async with conn.execute("SELECT count(*) FROM vec_chunk") as cur:
            row = await cur.fetchone()
    assert row is not None and row[0] == 12
