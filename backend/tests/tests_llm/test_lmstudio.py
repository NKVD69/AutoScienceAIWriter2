"""Backend LM Studio — residence, metriques, selection. ADR-003, ADR-014."""

from __future__ import annotations

import json
import os

import httpx
import pytest

from app.core.config import get_settings
from app.core.errors import BackendUnavailableError, LMStudioUnavailableError, ModelNotFoundError
from app.llm.backends.lmstudio import DEFAULT_TTL_SECONDS, LMStudioBackend
from app.llm.base import LLMBackend
from app.llm.manager import LLMManager, resolve_backend_factory
from app.llm.prompts.registry import AgentName

MODELE = "google/gemma-4-e4b"


def models_payload(state: str = "loaded") -> dict:
    return {
        "data": [
            {"id": MODELE, "type": "llm", "state": state, "quantization": "Q4_K_M"},
            {"id": "qwen/qwen3-coder-next", "type": "llm", "state": "not-loaded"},
            {"id": "text-embedding-nomic-embed-text-v1.5", "type": "embeddings", "state": "loaded"},
        ]
    }


def completion_payload() -> dict:
    """Forme reelle observee sur LM Studio 0.3 / api v0."""
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": MODELE,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Introduction generale",
                    "reasoning_content": "Reflechissons au titre...",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 24, "completion_tokens": 8, "total_tokens": 32},
        "stats": {
            "tokens_per_second": 42.5,
            "time_to_first_token": 0.184,
            "generation_time": 1.25,
            "stop_reason": "eosFound",
        },
        "model_info": {"arch": "gemma4", "quant": "Q4_K_M"},
    }


def mock_backend(handler, model: str = MODELE) -> LMStudioBackend:
    backend = LMStudioBackend(model=model)
    backend._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=backend.base_url
    )
    return backend


def default_handler(bodies: list[dict], state: str = "loaded") -> object:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v0/models":
            return httpx.Response(200, json=models_payload(state))
        bodies.append(json.loads(request.content or b"{}"))
        return httpx.Response(200, json=completion_payload())

    return handler


# --- Conformite a l'interface --------------------------------------------


def test_backend_satisfies_protocol() -> None:
    backend = LMStudioBackend(model=MODELE)
    assert isinstance(backend, LLMBackend)


def test_factory_resolves_both_engines() -> None:
    from app.llm.backends.ollama import OllamaBackend

    assert resolve_backend_factory("lmstudio") is LMStudioBackend
    assert resolve_backend_factory("ollama") is OllamaBackend


def test_factory_refuses_unknown_engine() -> None:
    with pytest.raises(ValueError, match="inconnu"):
        resolve_backend_factory("vllm")


def test_default_backend_is_lmstudio() -> None:
    assert get_settings().llm_backend == "lmstudio"
    assert get_settings().llm_base_url == get_settings().lmstudio_base_url


# --- Residence -----------------------------------------------------------


async def test_health_reports_loaded_models() -> None:
    backend = mock_backend(default_handler([]))
    health = await backend.health()
    await backend.aclose()
    assert health.available
    assert health.expected_model_present
    assert health.is_resident(MODELE)
    assert not health.is_resident("qwen/qwen3-coder-next")


async def test_ensure_loaded_skips_request_when_already_resident() -> None:
    """Un modele deja charge ne se recharge pas : ce serait exactement le
    swap qu'ADR-003 elimine."""
    bodies: list[dict] = []
    backend = mock_backend(default_handler(bodies, state="loaded"))
    await backend.ensure_loaded()
    await backend.aclose()
    assert bodies == []


async def test_ensure_loaded_triggers_load_when_absent_from_memory() -> None:
    bodies: list[dict] = []
    backend = mock_backend(default_handler(bodies, state="not-loaded"))
    await backend.ensure_loaded()
    await backend.aclose()
    assert len(bodies) == 1
    assert bodies[0]["ttl"] == DEFAULT_TTL_SECONDS


async def test_ttl_sent_on_every_request() -> None:
    """Analogue de keep_alive=-1 : le delai se rearme a chaque appel, et une
    seule requete sans ttl rendrait le modele eligible au dechargement."""
    bodies: list[dict] = []
    backend = mock_backend(default_handler(bodies, state="not-loaded"))
    manager = LLMManager(backend)
    await manager.startup()
    await manager.generate_for_agent(AgentName.PLAN, "sujet")
    async for _ in manager.stream_for_agent(AgentName.WRITER, "sujet"):
        pass
    await backend.aclose()

    assert len(bodies) >= 3
    assert all(b.get("ttl") == DEFAULT_TTL_SECONDS for b in bodies)
    assert all(b.get("ttl") != 0 for b in bodies)


async def test_no_unload_route_is_called() -> None:
    routes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        routes.append(request.url.path)
        if request.url.path == "/api/v0/models":
            return httpx.Response(200, json=models_payload("not-loaded"))
        return httpx.Response(200, json=completion_payload())

    backend = mock_backend(handler)
    manager = LLMManager(backend)
    await manager.startup()
    await manager.generate_for_agent(AgentName.PLAN, "sujet")
    await backend.aclose()
    assert not any("unload" in r for r in routes)


# --- Metriques -----------------------------------------------------------


async def test_load_duration_is_none_not_zero() -> None:
    """LM Studio ne publie pas cette duree. Un zero se lirait comme la preuve
    de la persistance que le moteur ne fournit pas."""
    backend = mock_backend(default_handler([]))
    result = await backend.generate("sys", "user")
    await backend.aclose()
    assert result.load_duration_ms is None


async def test_metrics_converted_from_seconds() -> None:
    backend = mock_backend(default_handler([]))
    result = await backend.generate("sys", "user")
    await backend.aclose()
    assert result.time_to_first_token_ms == 184.0
    assert result.eval_duration_ms == 1250.0
    assert result.prompt_tokens == 24
    assert result.completion_tokens == 8
    # Le debit publie par le moteur prime sur le calcul local.
    assert result.tokens_per_second == 42.5
    assert result.backend == "lmstudio"


async def test_reasoning_content_is_not_returned() -> None:
    """Un plan de these n'est pas le monologue qui y a mene, et le guardrail
    attend du JSON."""
    backend = mock_backend(default_handler([]))
    result = await backend.generate("sys", "user")
    await backend.aclose()
    assert result.text == "Introduction generale"
    assert "Reflechissons" not in result.text


async def test_stream_yields_only_content_fragments() -> None:
    lignes = [
        'data: {"choices":[{"delta":{"reasoning_content":"je reflechis"}}]}',
        'data: {"choices":[{"delta":{"content":"Intro"}}]}',
        "",
        'data: {"choices":[{"delta":{"content":"duction"}}]}',
        "data: [DONE]",
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v0/models":
            return httpx.Response(200, json=models_payload())
        return httpx.Response(200, content="\n".join(lignes).encode("utf-8"))

    backend = mock_backend(handler)
    fragments = [f async for f in backend.stream("sys", "user")]
    await backend.aclose()
    assert fragments == ["Intro", "duction"]


# --- Erreurs actionnables -------------------------------------------------


async def test_server_down_raises_actionable_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connexion refusee", request=request)

    backend = mock_backend(handler)
    with pytest.raises(LMStudioUnavailableError) as exc:
        await backend.ensure_loaded()
    await backend.aclose()
    message = exc.value.message
    assert "lms server start" in message
    assert "Developer" in message
    # Le gestionnaire du lifespan intercepte la classe parente.
    assert isinstance(exc.value, BackendUnavailableError)


async def test_missing_model_proposes_command_without_downloading() -> None:
    routes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        routes.append(request.url.path)
        return httpx.Response(200, json={"data": [{"id": "autre/modele", "state": "loaded"}]})

    backend = mock_backend(handler)
    with pytest.raises(ModelNotFoundError) as exc:
        await backend.ensure_loaded()
    await backend.aclose()
    assert f"lms get {MODELE}" in exc.value.message
    # ADR-010 : aucun telechargement automatique.
    assert not any("download" in r or "pull" in r for r in routes)


# --- Integration : necessite LM Studio reel -------------------------------


def _lmstudio_absent() -> bool:
    if os.environ.get("SAW_SKIP_LMSTUDIO"):
        return True
    try:
        with httpx.Client(timeout=2.0) as client:
            url = f"{get_settings().lmstudio_base_url}/api/v0/models"
            return client.get(url).status_code != 200
    except httpx.HTTPError:
        return True


needs_lmstudio = pytest.mark.skipif(_lmstudio_absent(), reason="LM Studio injoignable")


@pytest.mark.integration
@needs_lmstudio
async def test_installed_models_are_reported() -> None:
    backend = LMStudioBackend()
    health = await backend.health()
    await backend.aclose()
    assert health.available
    assert health.detail


@pytest.mark.integration
@needs_lmstudio
async def test_model_stays_resident_across_requests() -> None:
    """Critere de persistance sous LM Studio : l'etat du modele, faute de
    `load_duration` (ADR-014)."""
    settings = get_settings()
    backend = LMStudioBackend()
    manager = LLMManager(backend)
    await manager.startup()
    await manager.generate_for_agent(AgentName.PLAN, "Reponds par OK.", max_tokens=8)
    await manager.generate_for_agent(AgentName.WRITER, "Reponds par OK.", max_tokens=8)
    health = await manager.health()
    await backend.aclose()
    assert health.is_resident(settings.llm_model)
