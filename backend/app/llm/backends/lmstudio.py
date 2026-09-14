"""Backend LM Studio — modèle unique, résident, jamais déchargé.

ADR-003 point 5 prévoyait un second backend derrière `LLMBackend` ; ADR-014
retient LM Studio. Trois écarts avec Ollama structurent ce module.

1. **La persistance ne se paramètre pas de la même façon.** Il n'existe pas
   de `keep_alive=-1` : LM Studio décharge un modèle chargé à la demande au
   bout d'un délai (`ttl`, en secondes). La persistance s'obtient donc par un
   `ttl` très long, transmis à chaque requête comme l'était `keep_alive`.

2. **`load_duration` n'existe pas.** Le critère de vérification de §6.2 est
   inapplicable tel quel. En revanche `/api/v0/models` publie l'état de
   chaque modèle (`loaded` / `not-loaded`) : la résidence s'observe
   *directement*, ce qui vaut mieux que de l'inférer d'une durée. Les champs
   de durée absents restent à `None`, jamais à zéro.

3. **Le raisonnement est séparé du texte.** Le champ `reasoning_content` est
   distinct de `content`. On ne restitue que `content` : un plan de thèse
   n'est pas le monologue qui y a mené, et le guardrail attend du JSON.

L'API native `/api/v0` est préférée à l'API OpenAI `/v1` : elle seule publie
`stats` et l'état de chargement des modèles.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from app.core.config import get_settings
from app.core.errors import LMStudioUnavailableError, ModelNotFoundError
from app.core.logging import get_logger
from app.llm.base import BackendHealth, LLMResult, s_to_ms

logger = get_logger(__name__)

# Analogue de `keep_alive=-1` : LM Studio n'accepte pas de valeur infinie,
# on demande donc une durée que le service ne dépassera pas en usage.
DEFAULT_TTL_SECONDS = 86_400


class LMStudioBackend:
    """Client du serveur local LM Studio."""

    name = "lmstudio"

    def __init__(self, model: str | None = None, base_url: str | None = None) -> None:
        settings = get_settings()
        # Le modèle de LM STUDIO, pas celui du moteur actif : sous Ollama, ce
        # dernier est un identifiant que LM Studio ne connaît pas.
        self.model = model or settings.lmstudio_model
        self.base_url = (base_url or settings.lmstudio_base_url).rstrip("/")
        self.ttl_seconds = settings.lmstudio_ttl_seconds
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(connect=5.0, read=600.0, write=30.0, pool=5.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # --- Diagnostic ------------------------------------------------------

    async def health(self) -> BackendHealth:
        try:
            response = await self._client.get("/api/v0/models")
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return BackendHealth(available=False, detail=str(exc))

        models = response.json().get("data", [])
        identifiants = [str(m.get("id", "")) for m in models]
        charges = [str(m.get("id", "")) for m in models if m.get("state") == "loaded"]
        return BackendHealth(
            available=True,
            expected_model_present=self.model in identifiants,
            loaded_models=charges,
            detail=f"{len(identifiants)} modèle(s) installé(s), {len(charges)} chargé(s)",
        )

    # --- Chargement ------------------------------------------------------

    async def ensure_loaded(self) -> None:
        """Force la résidence des poids et arme le `ttl`.

        Le modèle absent n'est **pas** téléchargé : le téléchargement relève
        du consentement `model_download` (ADR-010). La commande est proposée.
        """
        health = await self.health()
        if not health.available:
            raise LMStudioUnavailableError.actionable(self.base_url, health.detail)
        if not health.expected_model_present:
            raise ModelNotFoundError.with_command(self.model, f"lms get {self.model}")

        if health.is_resident(self.model):
            logger.info("Modèle %s déjà résident", self.model)
            return

        # Une génération d'un seul token suffit à déclencher le chargement à
        # la demande et à armer le ttl. Un `POST /api/v0/models/load` existe
        # mais n'accepte pas de ttl : le modèle serait chargé sans garantie
        # de le rester.
        await self._post(
            "/api/v0/chat/completions",
            {
                "model": self.model,
                "messages": [{"role": "user", "content": "."}],
                "max_tokens": 1,
                "stream": False,
                "ttl": self.ttl_seconds,
            },
        )
        logger.info("Modèle %s résident (ttl=%s s)", self.model, self.ttl_seconds)

    # --- Génération ------------------------------------------------------

    async def _post(self, route: str, body: dict[str, object]) -> dict[str, object]:
        try:
            response = await self._client.post(route, json=body)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LMStudioUnavailableError.actionable(self.base_url, str(exc)) from exc
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
        body: dict[str, object] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
            # Transmis à chaque requête, comme `keep_alive` sous Ollama :
            # le délai se réarme à chaque appel, et une seule requête sans
            # `ttl` rendrait le modèle éligible au déchargement.
            "ttl": self.ttl_seconds,
        }
        if stop:
            body["stop"] = stop
        return body

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
            "/api/v0/chat/completions",
            self._body(
                system,
                user,
                max_tokens=max_tokens,
                temperature=temperature,
                stop=stop,
                stream=False,
            ),
        )
        choices = payload.get("choices") or [{}]
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        usage = payload.get("usage") or {}
        stats = payload.get("stats") or {}

        return LLMResult(
            text=str(message.get("content") or ""),
            prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage.get("completion_tokens", 0) or 0),
            # `load_duration` n'est pas publié : rester à None plutôt que de
            # produire un zéro qui se lirait comme une persistance vérifiée.
            load_duration_ms=None,
            eval_duration_ms=s_to_ms(stats.get("generation_time")),
            total_duration_ms=s_to_ms(stats.get("generation_time")),
            time_to_first_token_ms=s_to_ms(stats.get("time_to_first_token")),
            engine_tokens_per_second=stats.get("tokens_per_second"),
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
            async with self._client.stream("POST", "/api/v0/chat/completions", json=body) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    fragment = _fragment_from_sse(line)
                    if fragment:
                        yield fragment
        except httpx.HTTPError as exc:
            raise LMStudioUnavailableError.actionable(self.base_url, str(exc)) from exc


def _fragment_from_sse(line: str) -> str:
    """Extrait le texte d'une ligne SSE. Le raisonnement est ignoré."""
    line = line.strip()
    if not line.startswith("data:"):
        return ""
    data = line[len("data:") :].strip()
    if not data or data == "[DONE]":
        return ""
    try:
        chunk = json.loads(data)
    except json.JSONDecodeError:
        return ""
    choices = chunk.get("choices") or []
    if not choices:
        return ""
    delta = choices[0].get("delta") or {}
    return str(delta.get("content") or "")
