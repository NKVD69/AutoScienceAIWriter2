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
    """L'ordre est numerique, pas lexicographique : 010 vient apres 002."""
    (migrations_copy / "010_dix.sql").write_text("SELECT 1;", encoding="utf-8")
    (migrations_copy / "005_cinq.sql").write_text("SELECT 1;", encoding="utf-8")
    versions = [v for v, _ in discover(migrations_copy)]
    assert versions == sorted(versions)
    assert versions == [1, 2, 3, 4, 5, 10]


async def test_migrations_apply_schema(project_db: Path) -> None:
    async with connect(project_db) as conn:
        applied = await run_migrations(conn)
        assert applied == [1, 2, 3, 4]
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


async def test_migration_003_adds_section_kind(project_db: Path) -> None:
    """US-102 : un chunk de bibliographie doit pouvoir etre marque."""
    async with connect(project_db) as conn:
        await run_migrations(conn)
        async with conn.execute("PRAGMA table_info(chunk)") as cur:
            colonnes = {str(r[1]): (str(r[2]), r[4]) for r in await cur.fetchall()}
    assert "section_kind" in colonnes
    assert colonnes["section_kind"] == ("TEXT", "'body'")


async def test_migration_004_lets_a_section_survive_its_node(project_db: Path) -> None:
    """US-PLAN-001 : supprimer silencieusement un texte redige serait
    detruire du travail. La cle etrangere d'origine le faisait."""
    async with connect(project_db) as conn:
        await run_migrations(conn)
        async with conn.execute("PRAGMA foreign_key_list(draft_section)") as cur:
            cles = {str(r[3]): str(r[6]) for r in await cur.fetchall()}
        async with conn.execute("PRAGMA table_info(draft_section)") as cur:
            nullable = {str(r[1]): int(r[3]) for r in await cur.fetchall()}

    assert cles.get("plan_node_id") == "SET NULL", "la cascade détruirait la section"
    assert nullable["plan_node_id"] == 0, "une section orpheline n'a plus de nœud"


async def test_migrations_idempotent(project_db: Path) -> None:
    async with connect(project_db) as conn:
        first = await run_migrations(conn)
        second = await run_migrations(conn)
        versions = await applied_versions(conn)
    assert first == [1, 2, 3, 4]
    assert second == []
    assert set(versions) == {1, 2, 3, 4}


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
    declarations = 0
    for _, path in discover(MIGRATIONS_DIR):
        sql = path.read_text(encoding="utf-8")
        assert "float[768]" not in sql, f"dimension codee en dur dans {path.name}"
        declarations += sql.count("float[{embedding_dim}]")
    # Une seule declaration de la table vectorielle, portee par un parametre.
    assert declarations == 1


async def _effective_schema(conn) -> dict:
    """Schema tel que le moteur le comprend : objets, colonnes, types.

    La comparaison porte sur le schema EFFECTIF, non sur le texte du DDL.
    `ALTER TABLE ADD COLUMN` fait reecrire par SQLite la chaine stockee dans
    `sqlite_master` : deux schemas identiques y apparaissent differemment,
    et comparer les textes signalerait une divergence qui n'existe pas.
    """
    async with conn.execute(
        "SELECT type, name FROM sqlite_master"
        " WHERE name NOT LIKE 'sqlite_%' AND name <> 'schema_migration'"
        " AND name NOT LIKE 'vec_chunk_%' ORDER BY type, name"
    ) as cur:
        objets = [(str(r[0]), str(r[1])) for r in await cur.fetchall()]

    colonnes: dict[str, list] = {}
    for kind, name in objets:
        if kind != "table":
            continue
        async with conn.execute(f"PRAGMA table_info({name})") as cur:
            colonnes[name] = [
                (str(r[1]), str(r[2]).upper(), int(r[3]), r[4], int(r[5]))
                for r in await cur.fetchall()
            ]
    return {"objets": objets, "colonnes": colonnes}


async def test_schema_and_migration_agree(project_db: Path, tmp_path: Path) -> None:
    """schema.sql decrit le schema COURANT : la suite complete des migrations
    doit produire exactement le meme, sinon les deux divergent en silence."""
    schema_sql = (MIGRATIONS_DIR.parent / "schema.sql").read_text(encoding="utf-8")

    async with connect(project_db) as conn:
        await run_migrations(conn)
        depuis_migration = await _effective_schema(conn)

    direct = tmp_path / "direct.sqlite"
    async with connect(direct) as conn:
        await conn.executescript(render(schema_sql))
        await conn.commit()
        depuis_schema = await _effective_schema(conn)

    assert depuis_migration["objets"] == depuis_schema["objets"]
    assert depuis_migration["colonnes"] == depuis_schema["colonnes"]
