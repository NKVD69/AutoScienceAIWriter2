"""Arbitre des charges de calcul lourdes — ADR-016.

**Le problème.** ADR-015 fait délibérément déverser le modèle de rédaction sur
le CPU ; ADR-013 y place délibérément les embeddings. Les deux décisions sont
bonnes séparément et se disputent la même ressource ensemble. Mesuré le
8 septembre 2026 : la vectorisation de 500 textes passe de 180 s attendues à
348 s pendant qu'une génération tourne, et quadrupler les threads ne récupère
que 19 % — le parallélisme n'est pas le goulot, la contention l'est.

**La règle.** Rédaction et ingestion ne s'exécutent jamais en même temps.

**La granularité fait la politique.** Une génération réserve pour toute sa
durée ; l'ingestion réserve **par lot**. Une demande de rédaction attend donc
au plus un lot d'embeddings — quelques secondes — tandis que l'ingestion
attend la fin d'une génération. C'est l'ordre de priorité voulu : l'ingestion
est une tâche de fond, la rédaction est ce que l'utilisateur regarde.

**Réentrance.** L'agent rédacteur interroge le RAG, qui vectorise la requête.
Sans réentrance, il attendrait un verrou qu'il détient lui-même. La réservation
est donc suivie par tâche asyncio : une réservation imbriquée passe.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar

from app.core.logging import get_logger

logger = get_logger(__name__)

# Charges reconnues. Le libellé sert au journal et au tableau de bord.
GENERATION = "generation"
INGESTION = "ingestion"

# Suit la réservation détenue par la tâche courante, pour la réentrance.
_held_by_task: ContextVar[str | None] = ContextVar("compute_reservation", default=None)


class ComputeArbiter:
    """Exclusion mutuelle entre rédaction et ingestion (ADR-016)."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._holder: str | None = None
        self._last_wait_s: float = 0.0
        self._total_wait_s: dict[str, float] = {}

    @property
    def holder(self) -> str | None:
        """Charge qui détient la réservation, ou None."""
        return self._holder

    @property
    def last_wait_seconds(self) -> float:
        return self._last_wait_s

    def total_wait_seconds(self, workload: str) -> float:
        """Attente cumulée d'une charge. Destiné au tableau de bord."""
        return self._total_wait_s.get(workload, 0.0)

    @asynccontextmanager
    async def reserve(self, workload: str) -> AsyncIterator[None]:
        """Réserve la ressource de calcul pour la durée du bloc.

        Une réservation imbriquée dans la même tâche est accordée sans
        attendre : c'est le cas du rédacteur qui interroge le RAG.
        """
        deja_detenue = _held_by_task.get()
        if deja_detenue is not None:
            logger.debug("Réservation imbriquée %s dans %s", workload, deja_detenue)
            yield
            return

        debut = time.perf_counter()
        async with self._lock:
            attente = time.perf_counter() - debut
            self._last_wait_s = attente
            self._total_wait_s[workload] = self._total_wait_s.get(workload, 0.0) + attente
            if attente > 1.0:
                logger.info(
                    "%s a attendu %.1f s la fin de %s", workload, attente, self._holder or "?"
                )
            self._holder = workload
            jeton = _held_by_task.set(workload)
            try:
                yield
            finally:
                _held_by_task.reset(jeton)
                self._holder = None


_arbiter: ComputeArbiter | None = None


def get_arbiter() -> ComputeArbiter:
    """Arbitre applicatif unique. Les deux charges doivent partager le même."""
    global _arbiter
    if _arbiter is None:
        _arbiter = ComputeArbiter()
    return _arbiter


def reset_arbiter() -> None:
    """Oublie l'arbitre. Destiné aux tests."""
    global _arbiter
    _arbiter = None
