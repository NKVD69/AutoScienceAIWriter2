"""US-003 — LLMManager : chargement unique, serialisation, keep_alive. ADR-003."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator

import httpx
import pytest

from app.core.config import get_settings
from app.core.errors import ModelNotFoundError, OllamaUnavailableError
from app.llm.backends.ollama import KEEP_ALIVE, OllamaBackend
from app.llm.base import BackendHealth, LLMBackend, LLMResult
from app.llm.manager import LLMManager, build_manager
from app.llm.prompts.registry import AgentName, get_system_prompt


class FakeBackend:
    """Backend conforme au Protocol, sans Ollama."""

    name = "fake"

    def __init__(self, model: str = "fake-model", delay: float = 0.0) -> None:
        self.model = model
        self.delay = delay
        self.load_calls = 0
        self.systems: list[str] = []
        self.max_concurrent = 0
        self._active = 0

    async def ensure_loaded(self) -> None:
        self.load_calls += 1

    async def generate(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 1024,
        temperature: float = 0.2,
        stop: list[str] | None = None,
    ) -> LLMResult:
        self._active += 1
        self.max_concurrent = max(self.max_concurrent, self._active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            self.systems.append(system)
            return LLMResult(text=f"reponse a {user}", model=self.model, backend=self.name)
        finally:
            self._active -= 1

    async def stream(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 1024,
        temperature: float = 0.2,
        stop: list[str] | None = None,
    ) -> AsyncIterator[str]:
        self.systems.append(system)
        for fragment in ("a", "b", "c"):
            yield fragment

    async def health(self) -> BackendHealth:
        return BackendHealth(available=True, expected_model_present=True)


def test_fake_backend_satisfies_protocol() -> None:
    assert isinstance(FakeBackend(), LLMBackend)


async def test_manager_loads_model_once_at_startup() -> None:
    backend = FakeBackend()
    manager = LLMManager(backend)
    await manager.startup()
    await manager.startup()
    assert backend.load_calls == 1
    assert manager.started


async def test_generate_for_agent_uses_registry_prompt() -> None:
    backend = FakeBackend()
    manager = LLMManager(backend)
    await manager.generate_for_agent(AgentName.WRITER, "redige la section 2")
    assert backend.systems == [get_system_prompt(AgentName.WRITER)]


async def test_manager_serializes_concurrent_generations() -> None:
    """Le modele est unique : deux generations simultanees se disputeraient
    le contexte sans rien gagner."""
    backend = FakeBackend(delay=0.02)
    manager = LLMManager(backend)
    await asyncio.gather(
        *(manager.generate_for_agent(AgentName.PLAN, f"requete {i}") for i in range(6))
    )
    assert backend.max_concurrent == 1
    assert len(backend.systems) == 6


# --- Backend Ollama, transport simule ------------------------------------


def mock_backend(handler) -> OllamaBackend:
    backend = OllamaBackend(model="modele-test")
    backend._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=backend.base_url
    )
    return backend


def default_handler(bodies: list[dict]) -> object:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "modele-test"}]})
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": []})
        body = json.loads(request.content or b"{}")
        bodies.append(body)
        return httpx.Response(
            200,
            json={
                "response": "texte",
                "prompt_eval_count": 12,
                "eval_count": 34,
                "load_duration": 4_000_000,
                "prompt_eval_duration": 20_000_000,
                "eval_duration": 500_000_000,
                "total_duration": 530_000_000,
                "done": True,
            },
        )

    return handler


async def test_manager_never_sends_keep_alive_zero() -> None:
    """ADR-003 : aucune requete ne doit reinitialiser la residence des poids."""
    bodies: list[dict] = []
    backend = mock_backend(default_handler(bodies))
    manager = LLMManager(backend)
    await manager.startup()
    await manager.generate_for_agent(AgentName.PLAN, "sujet")
    async for _ in manager.stream_for_agent(AgentName.WRITER, "sujet"):
        pass
    await backend.aclose()

    assert bodies, "aucune requete de generation observee"
    assert all(b.get("keep_alive") == KEEP_ALIVE for b in bodies)
    assert all(b.get("keep_alive") != 0 for b in bodies)
    assert KEEP_ALIVE == -1


async def test_metrics_come_from_engine_fields() -> None:
    """Les durees viennent des champs Ollama, converties de ns en ms."""
    backend = mock_backend(default_handler([]))
    result = await backend.generate("sys", "user")
    await backend.aclose()
    assert result.load_duration_ms == 4.0
    assert result.eval_duration_ms == 500.0
    assert result.prompt_tokens == 12
    assert result.completion_tokens == 34
    assert result.tokens_per_second == 68.0


async def test_model_not_found_raises_with_pull_command() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "autre-modele"}]})
        return httpx.Response(200, json={"models": []})

    backend = mock_backend(handler)
    with pytest.raises(ModelNotFoundError) as exc:
        await backend.ensure_loaded()
    await backend.aclose()
    assert "ollama pull modele-test" in exc.value.message
    assert exc.value.details["pull_command"] == "ollama pull modele-test"


async def test_no_automatic_model_download() -> None:
    """Le telechargement releve du consentement model_download (ADR-010)."""
    routes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        routes.append(request.url.path)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "autre"}]})
        return httpx.Response(200, json={"models": []})

    backend = mock_backend(handler)
    with pytest.raises(ModelNotFoundError):
        await backend.ensure_loaded()
    await backend.aclose()
    assert "/api/pull" not in routes


async def test_ollama_unavailable_raises_actionable_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connexion refusee", request=request)

    backend = mock_backend(handler)
    with pytest.raises(OllamaUnavailableError) as exc:
        await backend.ensure_loaded()
    await backend.aclose()
    message = exc.value.message
    assert "ollama.com" in message
    assert "Windows" in message and "Linux" in message


async def test_health_reports_loaded_models() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "modele-test"}]})
        return httpx.Response(
            200, json={"models": [{"name": "modele-test", "size_vram": 5_368_709_120}]}
        )

    backend = mock_backend(handler)
    health = await backend.health()
    await backend.aclose()
    assert health.available
    assert health.expected_model_present
    assert health.vram_size_mb == 5120


# --- Politique du second modele ------------------------------------------


async def test_code_model_not_loaded_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "code_model_enabled", False)
    manager = build_manager(backend_factory=FakeBackend)
    assert not manager.code_model_loaded
    assert manager.backend_for(AgentName.CODE) is manager.backend_for(AgentName.WRITER)


async def test_code_model_refused_below_12gb(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sous 12 Go l'option est refusee — et le service continue."""
    settings = get_settings()
    monkeypatch.setattr(settings, "code_model_enabled", True)
    monkeypatch.setattr("app.llm.manager.read_vram_total_mb", lambda: 10_240)
    manager = build_manager(backend_factory=FakeBackend)
    assert not manager.code_model_loaded
    await manager.startup()
    assert manager.started


async def test_code_model_loaded_above_12gb_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "code_model_enabled", True)
    monkeypatch.setattr("app.llm.manager.read_vram_total_mb", lambda: 24_576)
    manager = build_manager(backend_factory=FakeBackend)
    assert manager.code_model_loaded
    assert manager.backend_for(AgentName.CODE) is not manager.backend_for(AgentName.WRITER)


async def test_code_model_refused_when_vram_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "code_model_enabled", True)
    monkeypatch.setattr("app.llm.manager.read_vram_total_mb", lambda: None)
    assert not build_manager(backend_factory=FakeBackend).code_model_loaded


# --- Moteur interchangeable -----------------------------------------------


async def test_each_backend_defaults_to_its_own_engine_model() -> None:
    """Les identifiants de modeles ne passent pas d'un moteur a l'autre :
    `google/gemma-4-31b` n'existe pas chez Ollama. Chaque backend prenait
    pourtant par defaut le modele du moteur ACTIF : un OllamaBackend recevait
    l'identifiant LM Studio, et ses tests d'integration echouaient sur un
    modele introuvable."""
    from app.llm.backends.lmstudio import LMStudioBackend

    settings = get_settings()
    assert settings.llm_backend == "lmstudio"
    ollama, lmstudio = OllamaBackend(), LMStudioBackend()
    try:
        assert ollama.model == settings.ollama_model
        assert lmstudio.model == settings.lmstudio_model
    finally:
        await ollama.aclose()
        await lmstudio.aclose()


async def test_switching_backend_alone_switches_model_and_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """« LM Studio ou Ollama, indifferemment » : basculer ne demande que
    SAW_LLM_BACKEND. Il fallait changer aussi SAW_LLM_MODEL, faute de quoi le
    moteur recevait un identifiant qu'il ne connait pas."""
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_backend", "ollama")

    assert settings.llm_model == settings.ollama_model
    assert settings.llm_base_url == settings.ollama_base_url

    backend = build_manager().backend_for(AgentName.PLAN)
    try:
        assert isinstance(backend, OllamaBackend)
        assert backend.model == settings.ollama_model
    finally:
        await backend.aclose()


def test_code_model_follows_the_active_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le second modele suit le moteur actif. Sans identifiant configure pour
    ce moteur, l'option est refusee — jamais tentee avec l'identifiant d'un
    autre moteur."""
    settings = get_settings()
    monkeypatch.setattr(settings, "code_model_enabled", True)
    monkeypatch.setattr("app.llm.manager.read_vram_total_mb", lambda: 24_576)
    assert settings.code_model == settings.lmstudio_code_model

    monkeypatch.setattr(settings, "llm_backend", "ollama")
    monkeypatch.setattr(settings, "ollama_code_model", None)
    assert settings.code_model is None
    assert not build_manager(backend_factory=FakeBackend).code_model_loaded


# --- Integration : necessite un Ollama reel -------------------------------


def _raison_ollama_indisponible() -> str | None:
    """Raison d'ignorer les tests Ollama, ou None s'ils peuvent tourner.

    La sonde vise `ollama_base_url`, pas l'URL du moteur actif : elle
    interrogeait LM Studio, qui repondait, et les tests Ollama tournaient
    alors contre un modele qu'Ollama n'a pas. Le modele attendu est lui aussi
    verifie, puisqu'il n'est jamais telecharge d'office (ADR-010).
    """
    if os.environ.get("SAW_SKIP_OLLAMA"):
        return "SAW_SKIP_OLLAMA defini"
    settings = get_settings()
    try:
        with httpx.Client(timeout=2.0) as client:
            reponse = client.get(f"{settings.ollama_base_url}/api/tags")
    except httpx.HTTPError:
        return f"Ollama injoignable sur {settings.ollama_base_url}"
    if reponse.status_code != 200:
        return f"Ollama injoignable sur {settings.ollama_base_url}"
    installes = {m.get("name") for m in reponse.json().get("models", [])}
    attendu = settings.ollama_model
    if attendu not in installes and f"{attendu}:latest" not in installes:
        return f"modele {attendu} absent d'Ollama (ollama pull {attendu})"
    return None


_RAISON_OLLAMA = _raison_ollama_indisponible()
needs_ollama = pytest.mark.skipif(_RAISON_OLLAMA is not None, reason=_RAISON_OLLAMA or "")


@pytest.mark.integration
@needs_ollama
async def test_load_duration_below_threshold_on_second_request() -> None:
    """ADR-003 : les poids ne sont pas rechargés entre deux requetes.

    Le critere porte sur `load_duration`, pas sur le cache KV : l'API ne
    l'expose pas.
    """
    settings = get_settings()
    backend = OllamaBackend()
    manager = LLMManager(backend)
    await manager.startup()
    await manager.generate_for_agent(AgentName.PLAN, "Reponds par OK.", max_tokens=8)
    second = await manager.generate_for_agent(AgentName.WRITER, "Reponds par OK.", max_tokens=8)
    await backend.aclose()
    assert second.load_duration_ms < settings.llm_max_load_duration_ms


@pytest.mark.integration
@needs_ollama
async def test_time_to_first_token_below_threshold() -> None:
    import time

    settings = get_settings()
    backend = OllamaBackend()
    manager = LLMManager(backend)
    await manager.startup()
    debut = time.perf_counter()
    async for _ in manager.stream_for_agent(AgentName.PLAN, "Reponds par OK.", max_tokens=8):
        break
    ttft_ms = (time.perf_counter() - debut) * 1000
    await backend.aclose()
    assert ttft_ms < settings.llm_max_ttft_ms


async def test_context_size_identical_on_every_request() -> None:
    """Ollama recharge les poids quand `num_ctx` change.

    Un prechauffage au contexte par defaut serait annule par la premiere
    generation reelle. Sur le poste cible, ce rechargement coute 7 min 44 s :
    la stabilite de `num_ctx` est aussi structurante que `keep_alive=-1`.
    """
    bodies: list[dict] = []
    backend = mock_backend(default_handler(bodies))
    manager = LLMManager(backend)
    await manager.startup()
    await manager.generate_for_agent(AgentName.PLAN, "sujet")
    await manager.generate_for_agent(AgentName.WRITER, "autre sujet")
    async for _ in manager.stream_for_agent(AgentName.CODE, "sujet"):
        pass
    await backend.aclose()

    contextes = {b.get("options", {}).get("num_ctx") for b in bodies}
    assert len(bodies) >= 4, "prechauffage et generations doivent etre observes"
    assert contextes == {get_settings().llm_context_tokens}, (
        f"num_ctx varie entre requetes : {contextes}"
    )
