"""ADR-016 — redaction et ingestion ne s'executent jamais en meme temps."""

from __future__ import annotations

import asyncio

import pytest

from app.core.compute import GENERATION, INGESTION, ComputeArbiter, get_arbiter, reset_arbiter
from app.llm.manager import LLMManager
from app.llm.prompts.registry import AgentName
from app.rag.embeddings import EmbeddingService

from .test_manager import FakeBackend


class RecordingBackend:
    """Moteur d'embedding qui note les intervalles d'occupation du CPU."""

    def __init__(self, journal: list[tuple[str, str]], delay: float = 0.02) -> None:
        self.journal = journal
        self.delay = delay
        self.calls = 0

    def encode(self, texts):
        import time

        self.calls += 1
        self.journal.append(("ingestion", "debut"))
        time.sleep(self.delay)
        self.journal.append(("ingestion", "fin"))
        return [[0.1] * 768 for _ in texts]

    @property
    def identifier(self) -> str:
        return "fake:embedding"


class RecordingLLM(FakeBackend):
    """Moteur LLM qui note ses propres intervalles dans le meme journal."""

    def __init__(self, journal: list[tuple[str, str]], delay: float = 0.05) -> None:
        super().__init__(delay=delay)
        self.journal = journal

    async def generate(self, system, user, **kwargs):
        self.journal.append(("generation", "debut"))
        result = await super().generate(system, user, **kwargs)
        self.journal.append(("generation", "fin"))
        return result


def chevauchement(journal: list[tuple[str, str]]) -> bool:
    """Vrai si deux charges differentes sont ouvertes en meme temps."""
    ouvertes: set[str] = set()
    for charge, evenement in journal:
        if evenement == "debut":
            if ouvertes and charge not in ouvertes:
                return True
            ouvertes.add(charge)
        else:
            ouvertes.discard(charge)
    return False


@pytest.fixture(autouse=True)
def arbitre_propre():
    reset_arbiter()
    yield
    reset_arbiter()


# --- Exclusion mutuelle ---------------------------------------------------


async def test_generation_and_ingestion_never_overlap() -> None:
    """Le resultat qui porte ADR-016."""
    journal: list[tuple[str, str]] = []
    arbitre = ComputeArbiter()

    llm = LLMManager(RecordingLLM(journal), arbiter=arbitre)
    embeddings = EmbeddingService(
        model_name="fake", dim=768, batch_size=1, backend=RecordingBackend(journal), arbiter=arbitre
    )

    await asyncio.gather(
        llm.generate_for_agent(AgentName.WRITER, "redige"),
        embeddings.embed_documents([f"t{i}" for i in range(6)]),
        llm.generate_for_agent(AgentName.PLAN, "planifie"),
    )

    assert not chevauchement(journal), f"charges simultanées : {journal}"
    assert len(journal) == 2 * (6 + 2), "toutes les charges doivent avoir tourné"


async def test_ingestion_reserves_per_batch_not_per_run() -> None:
    """Une demande de redaction n'attend qu'un lot, pas les mille chunks.

    La granularite fait la politique de priorite (ADR-016 point 2).
    """
    journal: list[tuple[str, str]] = []
    arbitre = ComputeArbiter()
    llm = LLMManager(RecordingLLM(journal, delay=0.01), arbiter=arbitre)
    embeddings = EmbeddingService(
        model_name="fake",
        dim=768,
        batch_size=1,
        backend=RecordingBackend(journal, delay=0.02),
        arbiter=arbitre,
    )

    ingestion = asyncio.create_task(embeddings.embed_documents([f"t{i}" for i in range(20)]))
    await asyncio.sleep(0.03)
    await llm.generate_for_agent(AgentName.WRITER, "urgent")
    await ingestion

    # La generation s'est intercalee : elle n'a pas attendu les 20 lots.
    charges = [c for c, e in journal if e == "debut"]
    position = charges.index("generation")
    assert 0 < position < len(charges) - 1, (
        f"la génération a attendu toute l'ingestion (position {position}/{len(charges)})"
    )


async def test_reservation_is_reentrant_within_a_task() -> None:
    """Le redacteur interroge le RAG : sans reentrance, il s'auto-bloquerait."""
    arbitre = ComputeArbiter()
    embeddings = EmbeddingService(
        model_name="fake", dim=768, backend=RecordingBackend([]), arbiter=arbitre
    )

    async def redacteur_qui_interroge_le_rag() -> list[float]:
        async with arbitre.reserve(GENERATION):
            # Réservation imbriquée : doit passer sans attendre.
            return await embeddings.embed_query("de quoi parle cette source")

    vecteur = await asyncio.wait_for(redacteur_qui_interroge_le_rag(), timeout=2.0)
    assert len(vecteur) == 768


async def test_nested_reservation_keeps_the_outer_holder() -> None:
    arbitre = ComputeArbiter()
    async with arbitre.reserve(GENERATION):
        assert arbitre.holder == GENERATION
        async with arbitre.reserve(INGESTION):
            assert arbitre.holder == GENERATION, "l'imbriquée ne doit pas voler la réservation"
    assert arbitre.holder is None


# --- Observabilite --------------------------------------------------------


async def test_wait_time_is_measured_per_workload() -> None:
    """Une ingestion qui n'avance plus doit etre lisible, non confondue avec
    une panne (ADR-016 point 4)."""
    arbitre = ComputeArbiter()

    async def occupe() -> None:
        async with arbitre.reserve(GENERATION):
            await asyncio.sleep(0.08)

    async def attend() -> None:
        await asyncio.sleep(0.01)
        async with arbitre.reserve(INGESTION):
            pass

    await asyncio.gather(occupe(), attend())
    assert arbitre.total_wait_seconds(INGESTION) > 0.0
    assert arbitre.last_wait_seconds > 0.0


def test_arbiter_is_a_singleton() -> None:
    """Les deux charges doivent partager le meme arbitre, sinon il n'arbitre rien."""
    assert get_arbiter() is get_arbiter()
    premier = get_arbiter()
    reset_arbiter()
    assert get_arbiter() is not premier


async def test_manager_and_service_share_the_default_arbiter() -> None:
    """Cable par defaut : un module qui oublierait de reserver rouvrirait
    exactement le defaut corrige par ADR-016."""
    llm = LLMManager(FakeBackend())
    embeddings = EmbeddingService(model_name="fake", dim=768, backend=RecordingBackend([]))
    assert llm._arbiter is embeddings._arbiter is get_arbiter()
