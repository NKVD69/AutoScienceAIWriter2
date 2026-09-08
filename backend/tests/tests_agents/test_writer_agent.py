"""US-301 — agent redacteur : message utilisateur, flux, temperature.

L'agent est exerce contre un backend factice alimente de reponses
preenregistrees. Aucun moteur reel : un modele de 31B produit du JSON
conforme la plupart du temps, ce qui rend ses echecs rares et donc
impossibles a provoquer autrement qu'en les injectant.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest

from app.agents.state import initial_state
from app.agents.writer_agent import MAX_OUTPUT_TOKENS, WriterAgent, build_user_message
from app.core.config import get_settings
from app.db.vector import ChunkHit
from app.llm.base import BackendHealth, LLMBackend, LLMResult
from app.llm.manager import LLMManager
from app.llm.prompts.registry import AgentName, get_system_prompt
from app.models.section import SectionDraft
from app.rag.context_builder import SectionContext
from app.services import task_service

SECTION_VALIDE = json.dumps(
    {
        "content_qmd": "La filtration decroit [@src1_2021_filtration].",
        "claims": [
            {
                "text": "La filtration decroit apres exposition prolongee.",
                "kind": "sourced",
                "citation_keys": ["src1_2021_filtration"],
                "chunk_ids": [11],
            }
        ],
        "word_count": 1000,
    },
    ensure_ascii=False,
)

CLE_INCONNUE = json.dumps(
    {
        "content_qmd": "La filtration decroit [@src9_2020_inventee].",
        "claims": [
            {
                "text": "La filtration decroit.",
                "kind": "sourced",
                "citation_keys": ["src9_2020_inventee"],
                "chunk_ids": [11],
            }
        ],
        "word_count": 1000,
    },
    ensure_ascii=False,
)

MYST = json.dumps(
    {
        "content_qmd": ":::{note}\nUn encadre MyST.\n:::",
        "claims": [{"text": "Point de synthese.", "kind": "synthesis"}],
        "word_count": 1000,
    },
    ensure_ascii=False,
)

HYPOTHESE_SOURCEE = json.dumps(
    {
        "content_qmd": "Le mecanisme pourrait relever d'une inflammation.",
        "claims": [
            {
                "text": "Le mecanisme pourrait relever d'une inflammation.",
                "kind": "hypothesis",
                "citation_keys": ["src1_2021_filtration"],
            }
        ],
        "word_count": 1000,
    },
    ensure_ascii=False,
)


class FakeBackend:
    """Backend conforme au Protocol, alimente de reponses preenregistrees.

    Le flux decoupe chaque reponse en fragments : c'est ce qui permet
    d'observer que des tokens sortent AVANT que quoi que ce soit soit valide.
    """

    def __init__(self, reponses: list[str] | None = None, fragments: int = 8) -> None:
        self.reponses = list(reponses or [SECTION_VALIDE])
        self.fragments = fragments
        self.systems: list[str] = []
        self.users: list[str] = []
        self.temperatures: list[float] = []
        self.max_tokens: list[int] = []

    def _suivante(self) -> str:
        # La derniere reponse se repete : modelise un modele qui echoue
        # indefiniment, ce que le circuit breaker doit arreter.
        return self.reponses.pop(0) if len(self.reponses) > 1 else self.reponses[0]

    async def ensure_loaded(self) -> None:
        return None

    async def generate(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 1024,
        temperature: float = 0.2,
        stop: list[str] | None = None,
    ) -> LLMResult:
        self.systems.append(system)
        self.users.append(user)
        return LLMResult(text=self._suivante(), backend="fake")

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
        self.users.append(user)
        self.temperatures.append(temperature)
        self.max_tokens.append(max_tokens)
        texte = self._suivante()
        taille = max(1, len(texte) // self.fragments)
        for depart in range(0, len(texte), taille):
            yield texte[depart : depart + taille]

    async def health(self) -> BackendHealth:
        return BackendHealth(available=True, expected_model_present=True)


def hit(chunk_id: int, source_id: int, titre: str, annee: int, page: int) -> ChunkHit:
    return ChunkHit(
        chunk_id=chunk_id,
        source_id=source_id,
        text=f"Extrait numero {chunk_id} de {titre}.",
        page_start=page,
        page_end=page + 1,
        distance=0.1,
        source_title=titre,
        source_year=annee,
        source_doi=None,
        is_preprint=False,
    )


def contexte() -> SectionContext:
    chunks = [
        hit(11, 1, "Filtration renale et polymeres", 2021, 4),
        hit(21, 2, "Exposition chronique urbaine", 2019, 12),
    ]
    return SectionContext(
        node_id=7,
        node_title="Exposition chronique et fonction renale",
        node_objective="Etablir le lien entre exposition prolongee et filtration.",
        target_words=1000,
        query="requete",
        chunks=chunks,
        allowed_keys=["src1_2021_filtration", "src2_2019_exposition"],
        token_estimate=50,
    )


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def manager(backend: FakeBackend) -> LLMManager:
    return LLMManager(backend)


@pytest.fixture(autouse=True)
def files_vides():
    task_service.reset_queues()
    yield
    task_service.reset_queues()


# --- Contrat d'agent ------------------------------------------------------


def test_fake_backend_satisfies_protocol() -> None:
    assert isinstance(FakeBackend(), LLMBackend)


def test_writer_agent_declares_its_output_model(manager: LLMManager) -> None:
    agent = WriterAgent(manager)
    assert agent.name is AgentName.WRITER
    assert agent.output_model is SectionDraft


async def test_agent_returns_raw_text_without_validating(manager: LLMManager) -> None:
    """L'agent ne valide pas : c'est ce qui rend le guardrail exercable seul."""
    agent = WriterAgent(manager)
    backend = FakeBackend([MYST])
    agent_fautif = WriterAgent(LLMManager(backend))

    brut = await agent_fautif.run(initial_state(1), {"context": contexte()})

    assert "MyST" in brut, "l'agent rend la sortie telle quelle, meme fautive"
    assert isinstance(brut, str)
    assert agent.output_model is SectionDraft


# --- Message utilisateur --------------------------------------------------


def test_user_message_presents_each_chunk_with_id_key_source_and_pages() -> None:
    """Le modele ne peut rattacher une affirmation qu'a un extrait qu'il a vu :
    sans identifiant, V2 rejetterait du texte que rien ne permettait de sourcer."""
    message = build_user_message(contexte())

    for identifiant in (11, 21):
        assert f"chunk_id={identifiant}" in message
    assert "src1_2021_filtration" in message
    assert "Filtration renale et polymeres" in message
    assert "p. 4-5" in message
    assert "p. 12-13" in message


def test_user_message_states_the_closed_key_list() -> None:
    message = build_user_message(contexte())
    assert "AUTORIS" in message
    assert "src1_2021_filtration, src2_2019_exposition" in message


def test_user_message_carries_title_objective_and_target() -> None:
    message = build_user_message(contexte())
    assert contexte().node_title in message
    assert contexte().node_objective in message
    assert "1000 mots" in message


def test_correction_goes_to_the_user_message_not_the_system_prompt(
    manager: LLMManager, backend: FakeBackend
) -> None:
    """Le prompt systeme doit rester stable octet pour octet d'un essai a
    l'autre : y injecter la correction annulerait le prefix caching (ADR-003)."""
    etat = initial_state(1)
    etat["last_error"] = "La cle src9_2020_inventee est inconnue."

    message = WriterAgent(manager).build_user_message(etat, {"context": contexte()})

    assert "src9_2020_inventee" in message
    assert "rejet" in message
    assert "src9_2020_inventee" not in get_system_prompt(AgentName.WRITER)


def test_system_prompt_forbids_inventing_and_demands_json() -> None:
    prompt = get_system_prompt(AgentName.WRITER)
    for exigence in ("JAMAIS", "JSON", "Quarto", "MyST", "sourced", "hypothesis"):
        assert exigence in prompt
    # Aucune donnee variable : le prefixe doit etre identique a chaque appel.
    assert "{" not in prompt and "}" not in prompt


# --- Flux -----------------------------------------------------------------


async def test_tokens_streamed_before_validation(manager: LLMManager) -> None:
    """A ~2,3 tokens/s, une section demande un quart d'heure : attendre la fin
    pour montrer quoi que ce soit laisserait l'utilisateur sans repere.

    La sortie injectee ici est INVALIDE. Les tokens doivent sortir quand
    meme : la diffusion precede le verdict, elle n'en depend pas.
    """
    backend = FakeBackend([CLE_INCONNUE])
    agent = WriterAgent(LLMManager(backend))

    brut = await agent.run(initial_state(1), {"context": contexte(), "task_id": 42})

    evenements = task_service.drain(42)
    assert len(evenements) > 1, "le texte doit sortir en plusieurs fragments"
    assert {e.type for e in evenements} == {"token"}
    assert "".join(e.payload["text"] for e in evenements) == brut
    # Et la sortie diffusee est bien celle qui sera rejetee ensuite.
    assert "src9_2020_inventee" in brut


async def test_no_task_id_means_no_event(manager: LLMManager, backend: FakeBackend) -> None:
    """Un appel interne du graphe n'a pas toujours de tache : il ne doit pas
    remplir une file que personne ne lit."""
    await WriterAgent(manager).run(initial_state(1), {"context": contexte()})
    assert task_service.drain(0) == []


# --- Reglages -------------------------------------------------------------


async def test_temperature_comes_from_settings(
    manager: LLMManager, backend: FakeBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "writer_temperature", 0.42)
    await WriterAgent(manager).run(initial_state(1), {"context": contexte()})

    assert backend.temperatures == [0.42]


async def test_default_writer_temperature_is_higher_than_the_plan_temperature() -> None:
    """Un plan est une structure, un texte academique a besoin d'un peu de
    liberte de formulation. La veracite ne depend pas de cette valeur."""
    settings = get_settings()
    assert settings.writer_temperature == 0.3
    assert settings.writer_temperature > settings.llm_temperature_default


async def test_output_budget_leaves_room_for_a_full_section(
    manager: LLMManager, backend: FakeBackend
) -> None:
    """Une section de 1500 mots pese ~2000 tokens ; le JSON qui l'enveloppe
    en ajoute autant. Un budget trop court coupe la sortie en fin de course."""
    await WriterAgent(manager).run(initial_state(1), {"context": contexte()})

    assert backend.max_tokens == [MAX_OUTPUT_TOKENS]
    assert MAX_OUTPUT_TOKENS >= 4096


async def test_writer_uses_the_writer_system_prompt(
    manager: LLMManager, backend: FakeBackend
) -> None:
    await WriterAgent(manager).run(initial_state(1), {"context": contexte()})
    assert backend.systems == [get_system_prompt(AgentName.WRITER)]
