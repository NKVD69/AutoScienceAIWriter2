"""Backend Ollama — modèle unique, résident, jamais déchargé.

ADR-003. `keep_alive=-1` accompagne **chaque** requête : Ollama réévalue la
durée de résidence à chaque appel, et une seule requête sans ce paramètre
suffirait à réarmer l'expiration par défaut et à faire décharger les poids
entre deux agents.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from app.core.config import get_settings
from app.core.errors import ModelNotFoundError, OllamaUnavailableError
from app.core.logging import get_logger
from app.llm.base import BackendHealth, LLMResult, ns_to_ms

logger = get_logger(__name__)

# Valeur unique de persistance. Elle n'est définie qu'ici : `keep_alive=0` ou
# une route de déchargement sont interdits par ADR-003.
KEEP_ALIVE = -1


class OllamaBackend:
    """Client du démon Ollama local."""

    name = "ollama"

    def __init__(self, model: str | None = None, base_url: str | None = None) -> None:
        settings = get_settings()
        self.model = model or settings.llm_model
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(connect=5.0, read=300.0, write=30.0, pool=5.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # --- Diagnostic ------------------------------------------------------

    async def health(self) -> BackendHealth:
        try:
            tags = await self._client.get("/api/tags")
            tags.raise_for_status()
            running = await self._client.get("/api/ps")
            running.raise_for_status()
        except httpx.HTTPError as exc:
            return BackendHealth(available=False, detail=str(exc))

        available_models = [m.get("name", "") for m in tags.json().get("models", [])]
        loaded = running.json().get("models", [])
        vram = None
        for entry in loaded:
            if entry.get("name") == self.model or entry.get("model") == self.model:
                size = entry.get("size_vram") or entry.get("size")
                vram = int(size / 1_048_576) if size else None
        return BackendHealth(
            available=True,
            expected_model_present=self.model in available_models,
            loaded_models=[m.get("name", "") for m in loaded],
            vram_size_mb=vram,
            detail=f"{len(available_models)} modèle(s) disponible(s)",
        )

    # --- Chargement ------------------------------------------------------

    async def ensure_loaded(self) -> None:
        """Force la résidence des poids sans produire de texte.

        Une requête de génération vide charge le modèle et arme `keep_alive`.
        Le modèle absent n'est **pas** téléchargé : le téléchargement relève
        du consentement `model_download` (ADR-010), pas d'un effet de bord au
        démarrage.

        **`num_ctx` est passé dès le préchauffage.** Ollama recharge
        intégralement les poids lorsque la taille de contexte change : un
        préchauffage au contexte par défaut serait annulé par la première
        génération réelle, qui paierait le chargement complet. Mesuré à
        7 min 44 s sur le poste cible (spikes/RESULTATS.md, addendum) — soit
        exactement la latence qu'ADR-003 cherche à éliminer.
        """
        health = await self.health()
        if not health.available:
            raise OllamaUnavailableError.actionable(self.base_url, health.detail)
        if not health.expected_model_present:
            raise ModelNotFoundError.with_pull_command(self.model)

        await self._post(
            "/api/generate",
            {
                "model": self.model,
                "keep_alive": KEEP_ALIVE,
                "options": {"num_ctx": get_settings().llm_context_tokens},
            },
        )
        logger.info("Modèle %s résident (keep_alive=%s)", self.model, KEEP_ALIVE)

    # --- Génération ------------------------------------------------------

    async def _post(self, route: str, body: dict[str, object]) -> dict[str, object]:
        try:
            response = await self._client.post(route, json=body)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OllamaUnavailableError.actionable(self.base_url, str(exc)) from exc
        return response.json()

    def _body(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int,
        temperature: float,
        stop: list[str] | None,
        stream: bool,
    ) -> dict[str, object]:
        options: dict[str, object] = {
            "temperature": temperature,
            "num_predict": max_tokens,
            "num_ctx": get_settings().llm_context_tokens,
        }
        if stop:
            options["stop"] = stop
        return {
            "model": self.model,
            "system": system,
            "prompt": user,
            "stream": stream,
            "keep_alive": KEEP_ALIVE,
            "options": options,
        }

    async def generate(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 1024,
        temperature: float = 0.2,
        stop: list[str] | None = None,
    ) -> LLMResult:
        payload = await self._post(
            "/api/generate",
            self._body(
                system,
                user,
                max_tokens=max_tokens,
                temperature=temperature,
                stop=stop,
                stream=False,
            ),
        )
        return LLMResult(
            text=str(payload.get("response", "")),
            prompt_tokens=int(payload.get("prompt_eval_count", 0) or 0),
            completion_tokens=int(payload.get("eval_count", 0) or 0),
            load_duration_ms=ns_to_ms(payload.get("load_duration")),  # type: ignore[arg-type]
            prompt_eval_duration_ms=ns_to_ms(payload.get("prompt_eval_duration")),  # type: ignore[arg-type]
            eval_duration_ms=ns_to_ms(payload.get("eval_duration")),  # type: ignore[arg-type]
            total_duration_ms=ns_to_ms(payload.get("total_duration")),  # type: ignore[arg-type]
            model=self.model,
            backend=self.name,
        )

    async def stream(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 1024,
        temperature: float = 0.2,
        stop: list[str] | None = None,
    ) -> AsyncIterator[str]:
        body = self._body(
            system, user, max_tokens=max_tokens, temperature=temperature, stop=stop, stream=True
        )
        try:
            async with self._client.stream("POST", "/api/generate", json=body) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    fragment = chunk.get("response", "")
                    if fragment:
                        yield fragment
                    if chunk.get("done"):
                        break
        except httpx.HTTPError as exc:
            raise OllamaUnavailableError.actionable(self.base_url, str(exc)) from exc
