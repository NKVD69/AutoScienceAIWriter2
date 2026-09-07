"""Accès SQLite du projet — connexion, PRAGMA, transactions, sauvegarde.

ADR-001. Trois règles portent ce module :

1. **`aiosqlite` exclusivement.** Le code applicatif n'importe jamais `sqlite3` ;
   une règle `ruff` (TID251) le vérifie.
2. **Aucune file d'écriture applicative.** WAL autorise des lectures concurrentes
   illimitées pendant une écriture, et `busy_timeout` absorbe la contention en
   écriture. Une file bloquante placée devant SQLite bloquerait l'*event loop*
   de FastAPI : elle déplacerait le problème au lieu de le résoudre.
3. **Les PRAGMA sont appliqués à chaque connexion.** `journal_mode` persiste
   dans le fichier, mais `busy_timeout` et `foreign_keys` sont propres à la
   connexion : les omettre une seule fois suffit à laisser passer une
   violation de clé étrangère.
"""

from __future__ import annotations

import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
import sqlite_vec

from app.core.config import get_settings
from app.core.errors import ExtensionLoadError
from app.core.logging import get_logger

logger = get_logger(__name__)


async def apply_pragmas(conn: aiosqlite.Connection) -> None:
    """PRAGMA obligatoires (ADR-001). À appeler sur toute connexion, sans exception."""
    settings = get_settings()
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute(f"PRAGMA busy_timeout={settings.sqlite_busy_timeout_ms}")
    await conn.execute("PRAGMA foreign_keys=ON")
    await conn.execute("PRAGMA synchronous=NORMAL")


async def assert_pragmas(conn: aiosqlite.Connection) -> None:
    """Relit les PRAGMA depuis le moteur.

    Écrire un PRAGMA n'est pas la preuve qu'il a pris : `journal_mode=WAL`
    échoue silencieusement sur une base en mémoire, et `foreign_keys` est
    ignoré à l'intérieur d'une transaction. On relit donc plutôt que de
    supposer.
    """
    async with conn.execute("PRAGMA journal_mode") as cur:
        row = await cur.fetchone()
        journal = (row[0] if row else "").lower()
    async with conn.execute("PRAGMA foreign_keys") as cur:
        row = await cur.fetchone()
        fk = row[0] if row else 0
    if journal != "wal":
        raise RuntimeError(f"journal_mode attendu 'wal', obtenu '{journal}' (ADR-001)")
    if not fk:
        raise RuntimeError("foreign_keys inactif sur cette connexion (ADR-001)")


async def load_vec_extension(conn: aiosqlite.Connection) -> None:
    """Charge `sqlite-vec` sur la connexion (ADR-002).

    Certains interpréteurs Python embarquent un binaire SQLite compilé sans
    support des extensions chargeables. Ce cas est détecté et signalé par une
    erreur actionnable : on ne bascule pas en silence sur une recherche
    vectorielle en Python pur, qui ferait passer un défaut d'installation pour
    une lenteur inexpliquée sur 50 000 chunks.
    """
    if not hasattr(aiosqlite.Connection, "enable_load_extension"):
        raise ExtensionLoadError.unsupported()
    try:
        await conn.enable_load_extension(True)
        await conn.load_extension(sqlite_vec.loadable_path())
        await conn.enable_load_extension(False)
    except AttributeError as exc:
        raise ExtensionLoadError.unsupported() from exc
    except Exception as exc:
        raise ExtensionLoadError(
            f"{ExtensionLoadError.REMEDIATION} Détail du moteur : {exc}"
        ) from exc


async def vec_version(conn: aiosqlite.Connection) -> str:
    """Version de l'extension vectorielle, telle que rapportée par le moteur."""
    async with conn.execute("SELECT vec_version()") as cur:
        row = await cur.fetchone()
    return str(row[0]) if row else ""


@asynccontextmanager
async def connect(
    db_path: Path | str, *, load_extension: bool = True
) -> AsyncIterator[aiosqlite.Connection]:
    """Ouvre une connexion projet : PRAGMA d'ADR-001 puis extension d'ADR-002.

    `load_extension=False` sert aux sondes qui n'ont rien à faire des vecteurs
    (`/health`) et aux tests qui vérifient le comportement sans extension.
    """
    conn = await aiosqlite.connect(str(db_path))
    try:
        conn.row_factory = aiosqlite.Row
        await apply_pragmas(conn)
        if load_extension:
            await load_vec_extension(conn)
        yield conn
    finally:
        await conn.close()


@asynccontextmanager
async def transaction(conn: aiosqlite.Connection) -> AsyncIterator[aiosqlite.Connection]:
    """Transaction explicite : toute opération multi-tables passe par ici (ADR-001).

    `aiosqlite` hérite du mode `autocommit` implicite de `sqlite3` ; un
    `BEGIN` explicite est le seul moyen d'obtenir une unité atomique
    couvrant plusieurs tables.
    """
    await conn.execute("BEGIN")
    try:
        yield conn
    except BaseException:
        await conn.rollback()
        raise
    else:
        await conn.commit()


async def checkpoint(conn: aiosqlite.Connection) -> None:
    """Replie le WAL dans le fichier principal et le tronque.

    Préalable obligatoire à toute copie : sans cela, les écritures les plus
    récentes vivent dans `-wal` et une copie du seul `.sqlite` les perd.
    """
    await conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    await conn.commit()


async def backup_project(db_path: Path | str, destination: Path | str) -> Path:
    """Sauvegarde applicative : checkpoint puis copie du fichier unique.

    ADR-001 « Conséquences » : la sauvegarde est une fonction applicative,
    pas une copie naïve. Le résultat est un fichier autonome contenant les
    données relationnelles **et** vectorielles.
    """
    src, dst = Path(db_path), Path(destination)
    dst.parent.mkdir(parents=True, exist_ok=True)
    async with connect(src) as conn:
        await checkpoint(conn)
    shutil.copy2(src, dst)
    logger.info("Sauvegarde projet : %s → %s", src.name, dst)
    return dst
