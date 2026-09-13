"""US-001 — connexion, PRAGMA, concurrence, sauvegarde. ADR-001."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import aiosqlite
import pytest

from app.db.session import assert_pragmas, backup_project, connect, transaction

SCHEMA = """
CREATE TABLE parent (id INTEGER PRIMARY KEY, label TEXT NOT NULL);
CREATE TABLE child (
  id INTEGER PRIMARY KEY,
  parent_id INTEGER NOT NULL REFERENCES parent(id) ON DELETE CASCADE,
  value TEXT NOT NULL
);
"""


async def _seed(db_path: Path) -> None:
    async with connect(db_path) as conn:
        await conn.executescript(SCHEMA)
        await conn.commit()


async def test_pragmas_applied(project_db: Path) -> None:
    async with connect(project_db) as conn:
        await assert_pragmas(conn)
        async with conn.execute("PRAGMA busy_timeout") as cur:
            row = await cur.fetchone()
    assert row is not None and row[0] == 5000


async def test_foreign_keys_enforced(project_db: Path) -> None:
    await _seed(project_db)
    async with connect(project_db) as conn:
        with pytest.raises(aiosqlite.IntegrityError, match="FOREIGN KEY"):
            await conn.execute("INSERT INTO child (parent_id, value) VALUES (999, 'orphelin')")


async def test_transaction_rolls_back(project_db: Path) -> None:
    await _seed(project_db)
    async with connect(project_db) as conn:
        with pytest.raises(RuntimeError):
            async with transaction(conn):
                await conn.execute("INSERT INTO parent (label) VALUES ('a')")
                raise RuntimeError("échec au milieu d'une opération multi-tables")
        async with conn.execute("SELECT count(*) FROM parent") as cur:
            row = await cur.fetchone()
    assert row is not None and row[0] == 0


async def test_concurrent_writes_no_lock(project_db: Path) -> None:
    """ADR-001 : WAL + busy_timeout suffisent, sans file d'écriture applicative.

    Chaque tâche ouvre sa propre connexion, comme le feraient l'ingestion RAG,
    le journal d'audit et un agent s'exécutant en parallèle.
    """
    await _seed(project_db)
    async with connect(project_db) as conn:
        await conn.execute("INSERT INTO parent (id, label) VALUES (1, 'racine')")
        await conn.commit()

    async def writer(n: int) -> None:
        async with connect(project_db) as conn:
            async with transaction(conn):
                await conn.execute("INSERT INTO child (parent_id, value) VALUES (1, ?)", (f"v{n}",))

    await asyncio.gather(*(writer(i) for i in range(24)))

    async with connect(project_db) as conn:
        async with conn.execute("SELECT count(*) FROM child") as cur:
            row = await cur.fetchone()
    assert row is not None and row[0] == 24


async def test_backup_single_file_contains_everything(project_db: Path, tmp_path: Path) -> None:
    """La copie obtenue après checkpoint est autonome : un seul fichier suffit."""
    await _seed(project_db)
    async with connect(project_db) as conn:
        async with transaction(conn):
            await conn.execute("INSERT INTO parent (id, label) VALUES (1, 'racine')")
            for i in range(50):
                await conn.execute("INSERT INTO child (parent_id, value) VALUES (1, ?)", (f"v{i}",))

        # Les écritures vivent encore dans le -wal à cet instant.
        dest = tmp_path / "sauvegarde" / "projet.sqlite"
        await backup_project(project_db, dest)

    # La copie est lue seule, sans ses fichiers -wal/-shm, et hors aiosqlite :
    # c'est exactement le geste d'un directeur de recherche qui reçoit le fichier.
    assert not (dest.parent / "projet.sqlite-wal").exists()
    con = sqlite3.connect(dest)
    try:
        assert con.execute("SELECT count(*) FROM child").fetchone()[0] == 50
        assert con.execute("SELECT label FROM parent WHERE id=1").fetchone()[0] == "racine"
    finally:
        con.close()


async def test_cascade_delete(project_db: Path) -> None:
    await _seed(project_db)
    async with connect(project_db) as conn:
        async with transaction(conn):
            await conn.execute("INSERT INTO parent (id, label) VALUES (1, 'racine')")
            await conn.execute("INSERT INTO child (parent_id, value) VALUES (1, 'x')")
        async with transaction(conn):
            await conn.execute("DELETE FROM parent WHERE id=1")
        async with conn.execute("SELECT count(*) FROM child") as cur:
            row = await cur.fetchone()
    assert row is not None and row[0] == 0


def test_no_write_queue_in_codebase() -> None:
    """ADR-001 : aucune file d'écriture applicative devant SQLite.

    Exigé par la matrice de traçabilité. Une file bloquante placée devant
    SQLite bloque l'event loop de FastAPI : elle déplace le problème au lieu
    de le résoudre, et WAL rend la sérialisation applicative inutile.
    """
    app_root = Path(__file__).resolve().parents[2] / "app"
    interdits = ("DbWriteQueue", "write_queue", "WriteQueue")
    fautes = [
        f"{path.relative_to(app_root)} : {motif}"
        for path in sorted(app_root.rglob("*.py"))
        for motif in interdits
        if motif in path.read_text(encoding="utf-8")
    ]
    assert not fautes, "file d'écriture applicative détectée :\n" + "\n".join(fautes)


def test_application_code_never_imports_sqlite3() -> None:
    """ADR-001 : `aiosqlite` exclusivement dans le code applicatif.

    La règle ruff TID251 le vérifie déjà ; ce test la double parce qu'une
    règle de lint se désactive par un commentaire, pas un test.
    """
    import ast

    app_root = Path(__file__).resolve().parents[2] / "app"
    fautes: list[str] = []
    for path in sorted(app_root.rglob("*.py")):
        arbre = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for noeud in ast.walk(arbre):
            if isinstance(noeud, ast.Import):
                fautes += [
                    f"{path.relative_to(app_root)}:{noeud.lineno}"
                    for a in noeud.names
                    if a.name == "sqlite3"
                ]
            elif isinstance(noeud, ast.ImportFrom) and noeud.module == "sqlite3":
                fautes.append(f"{path.relative_to(app_root)}:{noeud.lineno}")
    assert not fautes, "import sqlite3 dans le code applicatif :\n" + "\n".join(fautes)


# --- Connexion partagee : constats de revue --------------------------------


async def test_concurrent_transactions_on_shared_connection_are_serialized(
    project_db: Path,
) -> None:
    """Une connexion projet est PARTAGEE par toutes les requetes (US-101).

    Deux coroutines ouvrant `transaction()` sur la meme connexion faisaient
    echouer la seconde par « cannot start a transaction within a transaction »,
    erreur brute remontee en 500 — ou, decalees d'un souffle, lisaient une
    valeur perimee et dupliquaient une ecriture. Les transactions doivent se
    succeder.
    """
    await _seed(project_db)
    async with connect(project_db) as conn:
        await conn.execute("INSERT INTO parent (id, label) VALUES (1, 'racine')")
        await conn.commit()

        async def ecrivain() -> None:
            async with transaction(conn):
                async with conn.execute("SELECT count(*) FROM child") as cur:
                    avant = (await cur.fetchone())[0]
                # Suspension au milieu de la transaction : c'est la que
                # l'autre coroutine s'intercalait.
                await asyncio.sleep(0)
                await conn.execute(
                    "INSERT INTO child (parent_id, value) VALUES (1, ?)", (f"v{avant}",)
                )

        await asyncio.wait_for(asyncio.gather(*(ecrivain() for _ in range(8))), timeout=30)

        async with conn.execute("SELECT count(*), count(DISTINCT value) FROM child") as cur:
            total, distincts = await cur.fetchone()
    # Serialisees, les huit transactions voient chacune le compte laisse par la
    # precedente : aucune lecture perimee, donc aucune valeur en double.
    assert (total, distincts) == (8, 8)


async def _ouvrir_une_seconde_transaction(conn: aiosqlite.Connection) -> None:
    async with transaction(conn):
        pass


async def test_nested_transaction_on_same_connection_fails_clearly(project_db: Path) -> None:
    """Imbriquer deux transactions sur la meme connexion est un defaut d'appel.
    Avec un verrou, il deviendrait un interblocage silencieux : il doit au
    contraire lever tout de suite, en disant pourquoi — y compris depuis une
    tache enfant, comme celle que cree `asyncio.wait_for`."""
    await _seed(project_db)
    async with connect(project_db) as conn:
        with pytest.raises(RuntimeError, match="imbriqu"):
            async with transaction(conn):
                await asyncio.wait_for(_ouvrir_une_seconde_transaction(conn), timeout=5)


def test_project_writes_go_through_transaction() -> None:
    """Sur une connexion partagee, un `commit()` emis hors `transaction()`
    VALIDE la transaction qu'une autre coroutine a laissee ouverte : il en
    publie la moitie, et le `rollback` qui suivrait n'annulerait plus rien.

    Seuls le module de session, le registre (base distincte, une connexion
    par appel) et les migrations (appliquees avant tout partage) appellent
    `commit()` directement.
    """
    import re

    app_root = Path(__file__).resolve().parents[2] / "app"
    autorises = {Path("db/session.py"), Path("db/registry.py"), Path("db/migrations/runner.py")}
    fautes = [
        f"{path.relative_to(app_root)}:{numero}"
        for path in sorted(app_root.rglob("*.py"))
        if path.relative_to(app_root) not in autorises
        for numero, ligne in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if re.search(r"\.commit\(\)", ligne)
    ]
    assert not fautes, "commit() hors transaction() :\n" + "\n".join(fautes)
