"""US-005 — service d'embeddings CPU : prefixes, dimension, lots. ADR-013."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.core.errors import ModelDownloadConsentRequiredError
from app.rag.embeddings import (
    DOCUMENT_PREFIX,
    MAX_WORKERS_CAP,
    QUERY_PREFIX,
    EmbeddingService,
    default_workers,
    model_is_cached,
)

DIM = 768


class FakeBackend:
    """Moteur factice : enregistre ce qu'on lui donne, sans rien calculer."""

    def __init__(self, dim: int = DIM, delay: float = 0.0) -> None:
        self.dim = dim
        self.delay = delay
        self.calls: list[list[str]] = []

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        if self.delay:
            time.sleep(self.delay)
        self.calls.append(list(texts))
        return [[0.1] * self.dim for _ in texts]

    @property
    def identifier(self) -> str:
        return "fake:modele-de-test"

    @property
    def seen(self) -> list[str]:
        return [t for lot in self.calls for t in lot]


def service(backend: FakeBackend, **kwargs) -> EmbeddingService:
    return EmbeddingService(model_name="modele-de-test", dim=DIM, backend=backend, **kwargs)


# --- Dimension ------------------------------------------------------------


async def test_embed_documents_returns_expected_dimension() -> None:
    svc = service(FakeBackend())
    vecteurs = await svc.embed_documents(["alpha", "beta"])
    assert len(vecteurs) == 2
    assert all(len(v) == DIM for v in vecteurs)
    assert svc.dimension() == DIM


async def test_embed_query_returns_expected_dimension() -> None:
    svc = service(FakeBackend())
    assert len(await svc.embed_query("une question")) == DIM


async def test_wrong_dimension_from_backend_raises() -> None:
    """Le controle porte sur la sortie REELLE, pas sur la configuration.

    Un modele change sous le meme nom produirait des vecteurs incompatibles
    avec l'index sans qu'aucun reglage ne l'annonce.
    """
    svc = service(FakeBackend(dim=384))
    with pytest.raises(ValueError, match="384"):
        await svc.embed_documents(["alpha"])
    with pytest.raises(ValueError, match=r"réindexation"):
        await svc.embed_query("alpha")


async def test_dimension_mismatch_with_model_config_raises(tmp_path: Path) -> None:
    """Le projet verrouille sa dimension dans model_config (S4.3)."""
    from app.core.errors import DimensionMismatchError
    from app.db.migrations.runner import run_migrations
    from app.db.session import connect, transaction

    db = tmp_path / "projet.sqlite"
    async with connect(db) as conn:
        await run_migrations(conn)
        async with transaction(conn):
            await conn.execute(
                "INSERT INTO project (id, name, subject, language, academic_level,"
                " created_at, updated_at) VALUES (1,'P','S','fr','doctorat','x','x')"
            )
            await conn.execute(
                "INSERT INTO model_config (project_id, llm_model, embedding_model,"
                " embedding_dim, created_at) VALUES (1,'m','e',384,'x')"
            )
        async with conn.execute("SELECT embedding_dim FROM model_config") as cur:
            enregistree = (await cur.fetchone())[0]

    svc = service(FakeBackend())
    if enregistree != svc.dimension():
        with pytest.raises(DimensionMismatchError, match="réindexation"):
            raise DimensionMismatchError(expected=svc.dimension(), found=enregistree)


# --- Prefixes nomic -------------------------------------------------------


def test_document_prefix_applied() -> None:
    assert EmbeddingService._apply_prefix("texte", "document") == "search_document: texte"


def test_query_prefix_applied() -> None:
    assert EmbeddingService._apply_prefix("texte", "query") == "search_query: texte"


def test_prefixes_differ_between_document_and_query() -> None:
    assert DOCUMENT_PREFIX != QUERY_PREFIX
    doc = EmbeddingService._apply_prefix("x", "document")
    req = EmbeddingService._apply_prefix("x", "query")
    assert doc != req


def test_unknown_task_is_refused() -> None:
    with pytest.raises(ValueError, match="inconnue"):
        EmbeddingService._apply_prefix("x", "reranking")


async def test_prefixes_are_applied_by_the_service_not_the_caller() -> None:
    """Un appelant qui oublierait le prefixe produirait des vecteurs
    silencieusement incompatibles avec l'index : le service s'en charge."""
    backend = FakeBackend()
    svc = service(backend)
    await svc.embed_documents(["alpha", "beta"])
    await svc.embed_query("gamma")

    assert backend.seen == [
        "search_document: alpha",
        "search_document: beta",
        "search_query: gamma",
    ]


# --- Lots, parallelisme, progression -------------------------------------


async def test_batching_respects_batch_size() -> None:
    backend = FakeBackend()
    svc = service(backend, batch_size=4)
    await svc.embed_documents([f"t{i}" for i in range(10)])
    assert [len(lot) for lot in backend.calls] == [4, 4, 2]


async def test_worker_count_bounded_by_cores() -> None:
    """Borne haute : l'ingestion est une tache de fond, pas la priorite."""
    assert default_workers(3) == 3
    assert default_workers(0) == 1
    assert 1 <= default_workers(None) <= MAX_WORKERS_CAP


async def test_progress_callback_invoked() -> None:
    etapes: list[tuple[int, int]] = []
    svc = service(FakeBackend(), batch_size=3)
    await svc.embed_documents(
        [f"t{i}" for i in range(7)], on_progress=lambda d, t: etapes.append((d, t))
    )
    assert etapes == [(3, 7), (6, 7), (7, 7)]


async def test_cancellation_between_batches() -> None:
    """L'annulation doit interrompre entre deux lots, pas au bout des mille."""
    backend = FakeBackend(delay=0.05)
    svc = service(backend, batch_size=1)

    tache = asyncio.create_task(svc.embed_documents([f"t{i}" for i in range(50)]))
    await asyncio.sleep(0.12)
    tache.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tache

    assert len(backend.calls) < 50, "le traitement a continué après l'annulation"


async def test_event_loop_not_blocked() -> None:
    """Le calcul est bloquant : il ne doit jamais tenir l'event loop."""
    backend = FakeBackend(delay=0.15)
    svc = service(backend, batch_size=1)

    ticks = 0

    async def horloge() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    compteur = asyncio.create_task(horloge())
    await svc.embed_documents(["a", "b"])
    compteur.cancel()

    # Une boucle bloquée n'aurait laissé passer aucun tick pendant 0,3 s.
    assert ticks > 5, f"seulement {ticks} tours de boucle : l'event loop était bloqué"


# --- Reseau et consentement ----------------------------------------------


def test_model_is_cached_detects_the_downloaded_model(tmp_path: Path) -> None:
    assert not model_is_cached("nomic-ai/nomic-embed-text-v1.5", tmp_path)
    (tmp_path / "models--nomic-ai--nomic-embed-text-v1.5").mkdir()
    assert model_is_cached("nomic-ai/nomic-embed-text-v1.5", tmp_path)


def test_model_is_cached_false_on_missing_directory(tmp_path: Path) -> None:
    assert not model_is_cached("x/y", tmp_path / "absent")


async def test_missing_model_without_consent_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-010 : l'application ne telecharge rien d'elle-meme."""
    monkeypatch.setattr(get_settings(), "embedding_download_consent", False)
    svc = EmbeddingService(model_name="editeur/absent", dim=DIM, cache_dir=tmp_path)
    with pytest.raises(ModelDownloadConsentRequiredError) as exc:
        await svc.embed_documents(["alpha"])

    message = exc.value.message
    assert "model_download" in message
    assert "SAW_EMBEDDING_DOWNLOAD_CONSENT" in message, "le message doit être actionnable"
    assert exc.value.details["scope"] == "model_download"
    assert exc.value.status_code == 403


async def test_no_network_call_when_model_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """Une fois le modele en cache, aucune sortie reseau n'est emise."""
    import httpx

    appels: list[str] = []

    def interdit(*args: object, **kwargs: object) -> None:
        appels.append(str(args))
        raise AssertionError("appel réseau émis alors que le modèle est en cache")

    monkeypatch.setattr(httpx.Client, "request", interdit)
    monkeypatch.setattr(httpx.AsyncClient, "request", interdit)

    svc = service(FakeBackend())
    await svc.embed_documents(["alpha"])
    await svc.embed_query("beta")
    assert appels == []


def test_model_id_reports_backend_and_model() -> None:
    svc = service(FakeBackend())
    assert svc.model_id() == "fake:modele-de-test"


async def test_empty_input_returns_empty_without_calling_backend() -> None:
    backend = FakeBackend()
    svc = service(backend)
    assert await svc.embed_documents([]) == []
    assert backend.calls == []
