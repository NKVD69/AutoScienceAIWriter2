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

import asyncio
import shutil
import weakref
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
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


# Un verrou par connexion. Référence faible : une connexion fermée et oubliée
# ne doit pas être retenue en vie par son verrou.
_verrous: weakref.WeakKeyDictionary[aiosqlite.Connection, asyncio.Lock] = (
    weakref.WeakKeyDictionary()
)
# Connexions dont le contexte d'exécution courant tient la transaction. asyncio
# copie le contexte dans les tâches enfants : une transaction ouverte depuis une
# tâche née à l'intérieur d'une autre est donc reconnue comme imbriquée, au lieu
# d'attendre un verrou que la tâche parente ne rendra jamais.
_tenues: ContextVar[frozenset[int]] = ContextVar("transactions_tenues", default=frozenset())


def _verrou(conn: aiosqlite.Connection) -> asyncio.Lock:
    verrou = _verrous.get(conn)
    if verrou is None:
        verrou = asyncio.Lock()
        _verrous[conn] = verrou
    return verrou


@asynccontextmanager
async def transaction(conn: aiosqlite.Connection) -> AsyncIterator[aiosqlite.Connection]:
    """Transaction explicite : toute opération multi-tables passe par ici (ADR-001).

    `aiosqlite` hérite du mode `autocommit` implicite de `sqlite3` ; un
    `BEGIN` explicite est le seul moyen d'obtenir une unité atomique
    couvrant plusieurs tables.

    **`BEGIN IMMEDIATE`, pas `BEGIN`.** Une transaction différée qui lit
    avant d'écrire commence en lecteur puis tente de passer en écrivain ;
    SQLite refuse alors immédiatement par `SQLITE_BUSY` **sans honorer
    `busy_timeout`**, parce qu'attendre créerait un interblocage entre deux
    lecteurs voulant tous deux écrire. C'est exactement le motif du journal
    d'audit — lire le dernier hash, puis insérer — et la promesse d'ADR-001
    (« `busy_timeout` absorbe la contention en écriture ») ne tient que si
    le verrou d'écriture est pris dès l'ouverture.

    **Les transactions d'une même connexion se succèdent.** Une connexion
    projet est partagée par toutes les requêtes (US-101), et SQLite porte
    l'état transactionnel sur la connexion, pas sur l'appelant. Deux
    coroutines ouvrant chacune une transaction faisaient échouer la seconde
    en « cannot start a transaction within a transaction » — ou, décalées
    d'un souffle, lisaient une valeur périmée et dupliquaient une écriture
    (constat de revue, reproduit). Ce n'est pas la file d'écriture qu'ADR-001
    proscrit : un verrou asyncio suspend la coroutine qui attend, il ne
    bloque pas la boucle d'événements, et la contention entre connexions
    distinctes reste l'affaire de WAL et de `busy_timeout`.
    """
    cle = id(conn)
    if cle in _tenues.get():
        raise RuntimeError(
            "Transaction imbriquée sur la même connexion. SQLite n'imbrique pas les "
            "transactions, et ouvrir la seconde attendrait indéfiniment la fin de la "
            "première. Passer à l'appelé la transaction déjà ouverte (paramètre tx=True)."
        )

    async with _verrou(conn):
        jeton = _tenues.set(_tenues.get() | {cle})
        try:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                await conn.rollback()
                raise
            else:
                await conn.commit()
        finally:
            _tenues.reset(jeton)


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
