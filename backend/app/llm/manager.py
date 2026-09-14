"""LLMManager — un seul modèle généraliste, résident, partagé par tous les agents.

ADR-003. La spécialisation passe par le **prompt système**, jamais par les
poids : charger 5 à 6 Go depuis un SSD à chaque transition d'agent coûterait,
sur les centaines de transitions d'une thèse, une latence cumulée qui rend
l'outil inutilisable.

Deux erreurs déjà commises dans ce projet et évitées ici :

- affirmer que le cache KV est réutilisé entre deux requêtes — l'API Ollama
  ne l'expose pas, et deux prompts différents ne partagent rien ; la seule
  grandeur observable est `load_duration` ;
- router les embeddings par ce manager — Ollama les chargerait sur GPU et
  évincerait le modèle principal pendant l'ingestion (ADR-013, US-005).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.core.compute import GENERATION, ComputeArbiter, get_arbiter
from app.core.config import get_settings
from app.core.logging import get_logger
from app.llm.base import BackendHealth, LLMBackend, LLMResult
from app.llm.prompts.registry import AgentName, get_system_prompt, prompt_version
from app.llm.vram import policy_allows_code_model, read_vram_total_mb

logger = get_logger(__name__)


class LLMManager:
    """Point d'accès unique au moteur de génération."""

    def __init__(
        self,
        backend: LLMBackend,
        code_backend: LLMBackend | None = None,
        arbiter: ComputeArbiter | None = None,
    ) -> None:
        self._backend = backend
        self._code_backend = code_backend
        # L'arbitre remplit deux rôles à la fois (ADR-016) : le modèle est
        # unique, donc deux générations simultanées se disputeraient son
        # contexte ; et la génération déverse sur le CPU, qu'elle ne doit pas
        # disputer à l'ingestion.
        self._arbiter = arbiter or get_arbiter()
        self._started = False

    # --- Cycle de vie ----------------------------------------------------

    @property
    def started(self) -> bool:
        return self._started

    @property
    def code_model_loaded(self) -> bool:
        return self._code_backend is not None

    async def startup(self) -> None:
        """Charge le modèle principal une fois, au démarrage du service."""
        if self._started:
            return
        await self._backend.ensure_loaded()
        if self._code_backend is not None:
            await self._code_backend.ensure_loaded()
        self._started = True

    async def health(self) -> BackendHealth:
        return await self._backend.health()

    # --- Génération ------------------------------------------------------

    def backend_for(self, agent: AgentName) -> LLMBackend:
        """Le second résident, s'il existe, ne sert que l'agent code."""
        if agent is AgentName.CODE and self._code_backend is not None:
            return self._code_backend
        return self._backend

    async def generate_for_agent(
        self,
        agent: AgentName,
        user: str,
        *,
        max_tokens: int = 1024,
        temperature: float | None = None,
        stop: list[str] | None = None,
    ) -> LLMResult:
        settings = get_settings()
        system = get_system_prompt(agent)
        backend = self.backend_for(agent)
        async with self._arbiter.reserve(GENERATION):
            result = await backend.generate(
                system,
                user,
                max_tokens=max_tokens,
                temperature=(
                    settings.llm_temperature_default if temperature is None else temperature
                ),
                stop=stop,
            )
        # Les durées valent `None` quand le moteur ne les publie pas
        # (ADR-014) : un format `%.1f` lèverait ici, sur un chemin de
        # journalisation, ce qui ferait échouer une génération réussie.
        logger.debug(
            "agent=%s prompt=%s load=%s eval=%s",
            agent.value,
            prompt_version(agent),
            result.load_duration_ms,
            result.eval_duration_ms,
        )
        return result

    async def stream_for_agent(
        self,
        agent: AgentName,
        user: str,
        *,
        max_tokens: int = 1024,
        temperature: float | None = None,
        stop: list[str] | None = None,
    ) -> AsyncIterator[str]:
        settings = get_settings()
        system = get_system_prompt(agent)
        backend = self.backend_for(agent)
        async with self._arbiter.reserve(GENERATION):
            async for fragment in backend.stream(
                system,
                user,
                max_tokens=max_tokens,
                temperature=(
                    settings.llm_temperature_default if temperature is None else temperature
                ),
                stop=stop,
            ):
                yield fragment


def resolve_backend_factory(name: str) -> type[LLMBackend]:
    """Classe de backend correspondant à `settings.llm_backend`.

    Les imports sont locaux : charger les deux moteurs à l'import du module
    ferait dépendre le démarrage de bibliothèques qu'un poste donné n'utilise
    pas.
    """
    if name == "lmstudio":
        from app.llm.backends.lmstudio import LMStudioBackend

        return LMStudioBackend  # type: ignore[return-value]
    if name == "ollama":
        from app.llm.backends.ollama import OllamaBackend

        return OllamaBackend  # type: ignore[return-value]
    raise ValueError(
        f"Backend LLM inconnu : « {name} ». Valeurs admises : « lmstudio », « ollama »."
    )


def build_manager(
    backend_factory: type[LLMBackend] | None = None,
) -> LLMManager:
    """Construit le manager selon la configuration et la VRAM disponible.

    L'option de second modèle est un confort, pas une exigence : sous 12 Go
    elle est refusée avec un message explicite et le service poursuit avec le
    modèle généraliste seul.
    """
    settings = get_settings()
    factory = backend_factory or resolve_backend_factory(settings.llm_backend)
    principal = factory(settings.llm_model)  # type: ignore[call-arg]

    code_backend: LLMBackend | None = None
    if settings.code_model_enabled and settings.code_model is None:
        logger.warning(
            "Option code_model_enabled refusée : aucun modèle de code n'est configuré "
            "pour le moteur « %s » (SAW_%s_CODE_MODEL). Le modèle généraliste assurera "
            "la génération de code.",
            settings.llm_backend,
            settings.llm_backend.upper(),
        )
    elif settings.code_model_enabled:
        total = read_vram_total_mb()
        if policy_allows_code_model(total):
            code_backend = factory(settings.code_model)  # type: ignore[call-arg]
            logger.info("Second résident %s activé (VRAM %s Mo)", settings.code_model, total)
        else:
            logger.warning(
                "Option code_model_enabled refusée : %s Mo de VRAM détectés, "
                "%s Mo requis. Le modèle généraliste assurera la génération de code.",
                total if total is not None else "inconnus",
                settings.code_model_min_vram_mb,
            )

    return LLMManager(principal, code_backend)
