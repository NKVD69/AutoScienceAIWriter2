"""Cache de connexions projet — US-101.

Ouvrir un fichier `.sqlite` coûte les PRAGMA d'ADR-001 et le chargement de
l'extension vectorielle d'ADR-002 : le refaire à chaque requête serait payé
sur le chemin le plus fréquenté de l'application.

Le cache est borné et à éviction LRU. Garder ouverts des dizaines de fichiers
consommerait des descripteurs sans rien accélérer : un utilisateur travaille
sur un projet à la fois, deux ou trois lorsqu'il compare.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from pathlib import Path

import aiosqlite

from app.core.config import get_settings
from app.core.errors import ProjectNotFoundError
from app.core.logging import get_logger
from app.db.session import apply_pragmas, load_vec_extension

logger = get_logger(__name__)


class ConnectionPool:
    """Connexions projet ouvertes, par identifiant de projet."""

    def __init__(self, max_open: int | None = None) -> None:
        self._max_open = max_open or get_settings().max_open_projects
        self._connections: OrderedDict[int, aiosqlite.Connection] = OrderedDict()
        # Sérialise l'ouverture et l'éviction : deux requêtes simultanées sur
        # un projet non encore ouvert créeraient sinon deux connexions dont
        # une serait perdue sans jamais être fermée.
        self._lock = asyncio.Lock()

    @property
    def open_count(self) -> int:
        return len(self._connections)

    def is_open(self, project_id: int) -> bool:
        return project_id in self._connections

    async def acquire(self, project_id: int, db_path: Path | str) -> aiosqlite.Connection:
        """Connexion du projet, ouverte si nécessaire.

        Lève `ProjectNotFoundError` si le registre référence un fichier que
        le disque ne contient plus — cas réel dès lors que l'utilisateur
        possède ses fichiers et peut en déplacer un.
        """
        path = Path(db_path)
        async with self._lock:
            existing = self._connections.get(project_id)
            if existing is not None:
                self._connections.move_to_end(project_id)
                return existing

            # Un `stat` local sur un poste mono-utilisateur : le coût est
            # celui d'un appel système, sans commune mesure avec l'ouverture
            # de connexion qui suit.
            if not path.exists():  # noqa: ASYNC240
                raise ProjectNotFoundError.file_missing(project_id, str(path))

            conn = await aiosqlite.connect(str(path))
            conn.row_factory = aiosqlite.Row
            await apply_pragmas(conn)
            await load_vec_extension(conn)
            self._connections[project_id] = conn
            logger.debug("Projet %s ouvert (%s connexions)", project_id, len(self._connections))

            await self._evict_if_needed()
            return conn

    async def _evict_if_needed(self) -> None:
        """Ferme les connexions les moins récemment utilisées. Appelé sous verrou."""
        while len(self._connections) > self._max_open:
            evicted_id, evicted = self._connections.popitem(last=False)
            await evicted.close()
            logger.debug("Projet %s fermé par éviction LRU", evicted_id)

    async def release(self, project_id: int) -> None:
        """Ferme explicitement la connexion d'un projet, si elle est ouverte.

        Indispensable avant de déplacer le fichier : sous Windows, un fichier
        encore ouvert ne se renomme pas.
        """
        async with self._lock:
            conn = self._connections.pop(project_id, None)
        if conn is not None:
            await conn.close()
            logger.debug("Projet %s fermé", project_id)

    async def close_all(self) -> None:
        async with self._lock:
            connections = list(self._connections.values())
            self._connections.clear()
        for conn in connections:
            await conn.close()


_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    """Cache applicatif unique."""
    global _pool
    if _pool is None:
        _pool = ConnectionPool()
    return _pool


async def reset_pool() -> None:
    """Ferme et oublie le cache. Destiné aux tests et à l'arrêt du service."""
    global _pool
    if _pool is not None:
        await _pool.close_all()
        _pool = None
