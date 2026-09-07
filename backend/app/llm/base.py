"""Interface de backend LLM et modèles de résultat.

ADR-003. Les métriques de latence proviennent **des champs retournés par le
moteur**, jamais d'un chronomètre côté client : un `perf_counter` mesure aussi
la sérialisation, le réseau local et l'ordonnancement de l'event loop, et
noierait la seule grandeur qui décide — le temps de chargement des poids.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

_NS_PER_MS = 1_000_000


def ns_to_ms(value: int | float | None) -> float | None:
    """Convertit une durée en nanosecondes (Ollama) en millisecondes."""
    return None if value is None else round(value / _NS_PER_MS, 3)


def s_to_ms(value: int | float | None) -> float | None:
    """Convertit une durée en secondes (LM Studio) en millisecondes."""
    return None if value is None else round(value * 1000.0, 3)


class LLMResult(BaseModel):
    """Résultat d'une génération, métriques comprises.

    Les durées valent `None` quand le moteur ne les rapporte pas — jamais
    `0.0`. La distinction est structurante : un `load_duration_ms` à zéro se
    lirait comme « poids restés résidents », c'est-à-dire comme la preuve de
    ce qu'ADR-003 cherche à établir, alors qu'il signifierait seulement que
    le moteur ne publie pas la mesure. LM Studio est dans ce cas.
    """

    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    load_duration_ms: float | None = None
    prompt_eval_duration_ms: float | None = None
    eval_duration_ms: float | None = None
    total_duration_ms: float | None = None
    time_to_first_token_ms: float | None = None
    engine_tokens_per_second: float | None = None
    model: str = ""
    backend: str = ""

    @property
    def tokens_per_second(self) -> float:
        """Débit de génération. La valeur du moteur prime sur le calcul."""
        if self.engine_tokens_per_second is not None:
            return round(self.engine_tokens_per_second, 2)
        if not self.eval_duration_ms:
            return 0.0
        return round(self.completion_tokens / (self.eval_duration_ms / 1000.0), 2)


class BackendHealth(BaseModel):
    """État observé du moteur, tel qu'il se déclare.

    `loaded_models` est la seule observation de résidence commune aux deux
    moteurs : LM Studio ne publie pas de `load_duration`, mais expose l'état
    de chargement de chaque modèle.
    """

    available: bool
    expected_model_present: bool = False
    loaded_models: list[str] = []
    vram_size_mb: int | None = None
    detail: str = ""

    def is_resident(self, model: str) -> bool:
        return model in self.loaded_models


@runtime_checkable
class LLMBackend(Protocol):
    """Contrat minimal d'un moteur de génération.

    `llama-cpp-python` est prévu derrière cette interface (ADR-003) mais n'est
    pas implémenté au MVP.
    """

    async def ensure_loaded(self) -> None: ...

    async def generate(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int,
        temperature: float,
        stop: list[str] | None = None,
    ) -> LLMResult: ...

    def stream(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 1024,
        temperature: float = 0.2,
        stop: list[str] | None = None,
    ) -> AsyncIterator[str]: ...

    async def health(self) -> BackendHealth: ...
