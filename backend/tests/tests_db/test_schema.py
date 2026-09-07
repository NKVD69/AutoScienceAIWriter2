"""US-002 — schéma unique, extension vectorielle, intégrité et cascade. ADR-002."""

from __future__ import annotations

import random
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest
import sqlite_vec

from app.core.config import get_settings
from app.db.migrations.runner import run_migrations
from app.db.session import backup_project, connect, transaction, vec_version

NOW = datetime.now(UTC).isoformat()


def vector(seed: int) -> list[float]:
    rng = random.Random(seed)
    return [rng.random() for _ in range(get_settings().embedding_dim)]


async def make_project_db(db_path: Path) -> None:
    async with connect(db_path) as conn:
        await run_migrations(conn)
        async with transaction(conn):
            await conn.execute(
                "INSERT INTO project (id, name, subject, language, academic_level,"
                " created_at, updated_at) VALUES (1, 'These', 'Sujet', 'fr', 'doctorat', ?, ?)",
                (NOW, NOW),
            )


async def add_source(
    conn: aiosqlite.Connection,
    source_id: int,
    title: str,
    year: int = 2020,
    is_preprint: int = 0,
) -> None:
    await conn.execute(
        "INSERT INTO source_document (id, project_id, kind, title, year, doi,"
        " is_preprint, imported_at) VALUES (?, 1, 'article', ?, ?, ?, ?, ?)",
        (source_id, title, year, f"10.1000/{source_id}", is_preprint, NOW),
    )


async def test_load_sqlite_vec_extension(project_db: Path) -> None:
    async with connect(project_db) as conn:
        async with conn.execute("SELECT vec_version()") as cur:
            row = await cur.fetchone()
    assert row is not None and row[0]


async def test_vec_version_not_empty(project_db: Path) -> None:
    async with connect(project_db) as conn:
        assert await vec_version(conn) != ""


async def test_virtual_table_declares_no_foreign_key(project_db: Path) -> None:
    """ADR-002 : une clause REFERENCES sur vec0 serait acceptee puis ignoree."""
    await make_project_db(project_db)
    async with connect(project_db) as conn:
        async with conn.execute("SELECT sql FROM sqlite_master WHERE name = 'vec_chunk'") as cur:
            row = await cur.fetchone()
    assert row is not None
    assert "REFERENCES" not in row[0].upper()


async def test_foreign_keys_enforced_on_chunk(project_db: Path) -> None:
    await make_project_db(project_db)
    async with connect(project_db) as conn:
        with pytest.raises(aiosqlite.IntegrityError, match="FOREIGN KEY"):
            await conn.execute(
                "INSERT INTO chunk (source_id, ordinal, text) VALUES (999, 0, 'orphelin')"
            )


async def test_unique_source_ordinal(project_db: Path) -> None:
    await make_project_db(project_db)
    async with connect(project_db) as conn:
        async with transaction(conn):
            await add_source(conn, 1, "Source A")
            await conn.execute("INSERT INTO chunk (source_id, ordinal, text) VALUES (1, 0, 'a')")
        with pytest.raises(aiosqlite.IntegrityError, match="UNIQUE"):
            await conn.execute("INSERT INTO chunk (source_id, ordinal, text) VALUES (1, 0, 'b')")


async def test_rowid_invariant_chunk_vec(project_db: Path) -> None:
    await make_project_db(project_db)
    async with connect(project_db) as conn:
        async with transaction(conn):
            await add_source(conn, 1, "Source A")
            # id impose pour prouver l'invariant sur une valeur non triviale.
            await conn.execute(
                "INSERT INTO chunk (id, source_id, ordinal, text) VALUES (42, 1, 0, 'texte')"
            )
            await conn.execute(
                "INSERT INTO vec_chunk (rowid, embedding) VALUES (?, ?)",
                (42, sqlite_vec.serialize_float32(vector(1))),
            )
        async with conn.execute("SELECT rowid FROM vec_chunk") as cur:
            row = await cur.fetchone()
    assert row is not None and row[0] == 42


async def test_join_chunk_vec_returns_row(project_db: Path) -> None:
    await make_project_db(project_db)
    async with connect(project_db) as conn:
        async with transaction(conn):
            await add_source(conn, 1, "Source A")
            await conn.execute(
                "INSERT INTO chunk (id, source_id, ordinal, text) VALUES (42, 1, 0, 'texte')"
            )
            await conn.execute(
                "INSERT INTO vec_chunk (rowid, embedding) VALUES (?, ?)",
                (42, sqlite_vec.serialize_float32(vector(1))),
            )
        async with conn.execute(
            "SELECT c.id, c.text FROM chunk c JOIN vec_chunk v ON c.id = v.rowid"
        ) as cur:
            rows = await cur.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == 42


async def _seed_ten_vectorised_chunks(conn: aiosqlite.Connection) -> None:
    async with transaction(conn):
        await add_source(conn, 1, "Source A")
        for i in range(10):
            cur = await conn.execute(
                "INSERT INTO chunk (source_id, ordinal, text) VALUES (1, ?, ?)",
                (i, f"chunk {i}"),
            )
            await conn.execute(
                "INSERT INTO vec_chunk (rowid, embedding) VALUES (?, ?)",
                (cur.lastrowid, sqlite_vec.serialize_float32(vector(i))),
            )


async def test_cascade_delete_source_removes_chunks(project_db: Path) -> None:
    await make_project_db(project_db)
    async with connect(project_db) as conn:
        await _seed_ten_vectorised_chunks(conn)
        async with transaction(conn):
            await conn.execute("DELETE FROM source_document WHERE id = 1")
        async with conn.execute("SELECT count(*) FROM chunk") as cur:
            row = await cur.fetchone()
    assert row is not None and row[0] == 0


async def test_cascade_delete_source_removes_vectors(project_db: Path) -> None:
    """La cascade vectorielle passe par le TRIGGER : ON DELETE CASCADE ne
    traverse pas une table virtuelle."""
    await make_project_db(project_db)
    async with connect(project_db) as conn:
        await _seed_ten_vectorised_chunks(conn)
        async with conn.execute("SELECT count(*) FROM vec_chunk") as cur:
            before = (await cur.fetchone())[0]
        async with transaction(conn):
            await conn.execute("DELETE FROM source_document WHERE id = 1")
        async with conn.execute("SELECT count(*) FROM vec_chunk") as cur:
            after = (await cur.fetchone())[0]
    assert before == 10
    assert after == 0


async def test_backup_single_file_contains_everything(project_db: Path, tmp_path: Path) -> None:
    """Un seul fichier transporte le relationnel ET le vectoriel (ADR-001)."""
    await make_project_db(project_db)
    async with connect(project_db) as conn:
        await _seed_ten_vectorised_chunks(conn)
        dest = tmp_path / "sauvegarde" / "projet.sqlite"
        await backup_project(project_db, dest)

    con = sqlite3.connect(dest)
    try:
        con.enable_load_extension(True)
        sqlite_vec.load(con)
        con.enable_load_extension(False)
        assert con.execute("SELECT count(*) FROM chunk").fetchone()[0] == 10
        assert con.execute("SELECT count(*) FROM vec_chunk").fetchone()[0] == 10
        joined = con.execute(
            "SELECT count(*) FROM chunk c JOIN vec_chunk v ON c.id = v.rowid"
        ).fetchone()[0]
        assert joined == 10
    finally:
        con.close()


async def test_citation_source_delete_is_restricted(project_db: Path) -> None:
    """§4.2 : on ne supprime pas une source encore citee dans une section."""
    await make_project_db(project_db)
    async with connect(project_db) as conn:
        async with transaction(conn):
            await add_source(conn, 1, "Source A")
            await conn.execute(
                "INSERT INTO plan (id, project_id, problematique, status, created_at)"
                " VALUES (1, 1, 'P', 'draft', ?)",
                (NOW,),
            )
            await conn.execute(
                "INSERT INTO plan_node (id, plan_id, ordinal, level, title)"
                " VALUES (1, 1, 0, 1, 'Chapitre')"
            )
            await conn.execute(
                "INSERT INTO draft_section (id, plan_node_id, status) VALUES (1, 1, 'draft')"
            )
            await conn.execute(
                "INSERT INTO citation (draft_section_id, source_id, bibtex_key)"
                " VALUES (1, 1, 'a_2020_x')"
            )
        with pytest.raises(aiosqlite.IntegrityError, match="FOREIGN KEY"):
            await conn.execute("DELETE FROM source_document WHERE id = 1")
