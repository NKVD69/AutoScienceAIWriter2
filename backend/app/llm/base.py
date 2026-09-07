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


def ns_to_ms(value: int | float | None) -> float:
    """Convertit une durée Ollama (nanosecondes) en millisecondes."""
    return round((value or 0) / _NS_PER_MS, 3)


class LLMResult(BaseModel):
    """Résultat d'une génération, métriques comprises."""

    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    load_duration_ms: float = 0.0
    prompt_eval_duration_ms: float = 0.0
    eval_duration_ms: float = 0.0
    total_duration_ms: float = 0.0
    model: str = ""
    backend: str = ""

    @property
    def tokens_per_second(self) -> float:
        if self.eval_duration_ms <= 0:
            return 0.0
        return round(self.completion_tokens / (self.eval_duration_ms / 1000.0), 2)


class BackendHealth(BaseModel):
    """État observé du moteur, tel qu'il se déclare."""

    available: bool
    expected_model_present: bool = False
    loaded_models: list[str] = []
    vram_size_mb: int | None = None
    detail: str = ""


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
