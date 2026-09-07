"""US-002 — runner de migrations : ordre, idempotence, detection de derive."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.db.migrations.runner import (
    MIGRATIONS_DIR,
    MigrationChecksumError,
    applied_versions,
    discover,
    render,
    run_migrations,
)
from app.db.session import connect


@pytest.fixture
def migrations_copy(tmp_path: Path) -> Path:
    """Copie modifiable des migrations reelles."""
    dest = tmp_path / "migrations"
    dest.mkdir()
    for _, path in discover(MIGRATIONS_DIR):
        shutil.copy2(path, dest / path.name)
    return dest


async def test_discover_is_ordered(migrations_copy: Path) -> None:
    (migrations_copy / "010_dix.sql").write_text("SELECT 1;", encoding="utf-8")
    (migrations_copy / "002_deux.sql").write_text("SELECT 1;", encoding="utf-8")
    versions = [v for v, _ in discover(migrations_copy)]
    assert versions == sorted(versions)
    assert versions == [1, 2, 10]


async def test_migrations_apply_schema(project_db: Path) -> None:
    async with connect(project_db) as conn:
        applied = await run_migrations(conn)
        assert applied == [1]
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','trigger')"
        ) as cur:
            names = {r[0] for r in await cur.fetchall()}

    for table in (
        "project",
        "source_document",
        "chunk",
        "plan",
        "plan_node",
        "draft_section",
        "citation",
        "code_execution",
        "task",
        "audit_log",
        "consent",
        "model_config",
        "vec_chunk",
        "chunk_after_delete",
    ):
        assert table in names, f"{table} absente du schema applique"


async def test_migrations_idempotent(project_db: Path) -> None:
    async with connect(project_db) as conn:
        first = await run_migrations(conn)
        second = await run_migrations(conn)
        versions = await applied_versions(conn)
    assert first == [1]
    assert second == []
    assert set(versions) == {1}


async def test_migrations_detect_modified_file(project_db: Path, migrations_copy: Path) -> None:
    async with connect(project_db) as conn:
        await run_migrations(conn, directory=migrations_copy)

        target = migrations_copy / "001_initial.sql"
        target.write_text(
            target.read_text(encoding="utf-8") + "\n-- modification apres application\n",
            encoding="utf-8",
        )

        with pytest.raises(MigrationChecksumError, match=r"001_initial\.sql"):
            await run_migrations(conn, directory=migrations_copy)


async def test_render_substitutes_embedding_dimension() -> None:
    sql = "CREATE VIRTUAL TABLE vec_chunk USING vec0(embedding float[{embedding_dim}]);"
    assert render(sql, embedding_dim=768) == (
        "CREATE VIRTUAL TABLE vec_chunk USING vec0(embedding float[768]);"
    )
    assert "{embedding_dim}" not in render(sql)


async def test_embedding_dimension_appears_only_as_placeholder() -> None:
    """La dimension ne doit pas etre codee en dur dans le DDL (US-002, exigence 3)."""
    for _, path in discover(MIGRATIONS_DIR):
        sql = path.read_text(encoding="utf-8")
        assert "float[{embedding_dim}]" in sql
        assert "float[768]" not in sql


async def test_schema_and_migration_agree(project_db: Path, tmp_path: Path) -> None:
    """schema.sql reste le document canonique : la migration 001 doit produire
    exactement le meme schema, sinon les deux divergent en silence."""
    schema_sql = (MIGRATIONS_DIR.parent / "schema.sql").read_text(encoding="utf-8")

    async with connect(project_db) as conn:
        await run_migrations(conn)
        async with conn.execute(
            "SELECT type, name, sql FROM sqlite_master"
            " WHERE name NOT LIKE 'sqlite_%' AND name <> 'schema_migration'"
            " AND name NOT LIKE 'vec_chunk_%' ORDER BY name"
        ) as cur:
            depuis_migration = [tuple(r) for r in await cur.fetchall()]

    direct = tmp_path / "direct.sqlite"
    async with connect(direct) as conn:
        await conn.executescript(render(schema_sql))
        await conn.commit()
        async with conn.execute(
            "SELECT type, name, sql FROM sqlite_master"
            " WHERE name NOT LIKE 'sqlite_%' AND name NOT LIKE 'vec_chunk_%' ORDER BY name"
        ) as cur:
            depuis_schema = [tuple(r) for r in await cur.fetchall()]

    assert depuis_migration == depuis_schema
