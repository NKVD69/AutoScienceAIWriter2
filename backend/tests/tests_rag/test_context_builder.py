"""US-301 — contexte RAG cible par noeud : requete, budget, plafond, liste close."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.db.migrations.runner import run_migrations
from app.db.session import connect, transaction
from app.db.vector import insert_chunk_with_embedding
from app.models.plan import PlanNodeOut, PlanOut, PlanStatus
from app.rag import context_builder, retriever
from app.rag.context_builder import (
    MAX_CHUNKS_PER_SOURCE,
    MIN_CHUNKS,
    InsufficientContextError,
    bibtex_key,
    build_query,
    build_section_context,
)

NOW = datetime.now(UTC).isoformat()
DIM = 768


def axe(x: float) -> list[float]:
    """Vecteur porte par un seul axe.

    Un vecteur one-hot par contenu rendrait EGALES les distances entre tous
    les extraits distincts : aucun test de selection ne pourrait alors
    observer un ordre. Ici la position sur l'axe fixe le rang, et la requete
    se place a 1.0.
    """
    vecteur = [0.0] * DIM
    vecteur[0] = x
    return vecteur


class ServiceEspion:
    """Service d'embeddings factice. Retient les requetes vectorisees."""

    def __init__(self) -> None:
        self.requetes: list[str] = []

    async def embed_query(self, texte: str) -> list[float]:
        self.requetes.append(texte)
        return axe(1.0)


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> ServiceEspion:
    espion = ServiceEspion()
    # `retrieve` resout le service au module : c'est la qu'on l'intercepte.
    monkeypatch.setattr(retriever, "get_embedding_service", lambda: espion)
    return espion


def noeud(target_words: int = 1500) -> PlanNodeOut:
    return PlanNodeOut(
        id=1,
        parent_id=None,
        title="Exposition chronique et fonction renale",
        objective="Etablir le lien entre exposition prolongee et alteration de la filtration.",
        target_words=target_words,
        level=2,
        ordinal=0,
    )


def plan() -> PlanOut:
    return PlanOut(
        id=1,
        version=1,
        status=PlanStatus.VALIDATED,
        problematique="Dans quelle mesure les microplastiques alterent-ils la fonction renale ?",
        nodes=[noeud()],
    )


async def peupler(db: Path, contenus: list[tuple[int, str, float, str]]) -> None:
    """(source_id, texte, proximite, section_kind) -> base peuplee.

    Trois sources declarees ; seules celles qui portent un extrait comptent.
    """
    async with connect(db) as conn:
        await run_migrations(conn)
        async with transaction(conn):
            await conn.execute(
                "INSERT INTO project (id, name, subject, language, academic_level,"
                " created_at, updated_at) VALUES (1,'P','Sujet','fr','doctorat',?,?)",
                (NOW, NOW),
            )
            for sid, titre, annee in (
                (1, "Filtration renale et polymeres", 2021),
                (2, "Exposition chronique en milieu urbain", 2019),
                (3, "Revue des methodes de dosage", 2023),
            ):
                await conn.execute(
                    "INSERT INTO source_document (id, project_id, kind, title, year,"
                    " is_preprint, imported_at) VALUES (?,1,'article',?,?,0,?)",
                    (sid, titre, annee, NOW),
                )

        for ordinal, (sid, texte, proximite, genre) in enumerate(contenus):
            await insert_chunk_with_embedding(
                conn,
                source_id=sid,
                ordinal=ordinal,
                text=texte,
                embedding=axe(proximite),
                page_start=ordinal + 1,
                page_end=ordinal + 2,
                section_kind=genre,
            )


CORPUS = [
    (1, "La filtration glomerulaire decroit apres exposition prolongee.", 0.99, "body"),
    (1, "Les polymeres s'accumulent dans le tissu cortical.", 0.97, "body"),
    (1, "Le dosage urinaire reste peu sensible aux fragments fins.", 0.95, "body"),
    (1, "Quatrieme extrait de la meme source, tout aussi proche.", 0.93, "body"),
    (1, "Cinquieme extrait de la meme source.", 0.91, "body"),
    (2, "L'exposition urbaine moyenne est estimee par biomarqueurs.", 0.89, "body"),
    (2, "Les cohortes suivies depassent rarement cinq annees.", 0.87, "body"),
    (3, "La spectrometrie Raman identifie les polymeres majoritaires.", 0.85, "body"),
]


# --- Requete --------------------------------------------------------------


def test_context_query_uses_title_objective_and_problematique() -> None:
    """Le titre seul ne ressemble a rien dans un espace vectoriel."""
    requete = build_query(noeud(), plan().problematique)

    assert "Exposition chronique et fonction renale" in requete
    assert "alteration de la filtration" in requete
    assert "microplastiques" in requete


async def test_query_sent_to_the_embedding_service_carries_the_three_parts(
    project_db: Path, service: ServiceEspion
) -> None:
    await peupler(project_db, CORPUS)
    async with connect(project_db) as conn:
        await build_section_context(conn, noeud(), plan())

    assert len(service.requetes) == 1
    envoyee = service.requetes[0]
    assert noeud().title in envoyee
    assert noeud().objective in envoyee
    assert plan().problematique in envoyee


# --- Budget et plafonds ---------------------------------------------------


async def test_context_respects_token_budget(project_db: Path, service: ServiceEspion) -> None:
    """Le contexte fait 8192 tokens : la matiere ne doit pas devorer la sortie."""
    await peupler(project_db, CORPUS)
    async with connect(project_db) as conn:
        serre = await build_section_context(conn, noeud(), plan(), budget_tokens=60)
        large = await build_section_context(conn, noeud(), plan(), budget_tokens=10_000)

    assert serre.token_estimate <= 60
    assert len(serre.chunks) < len(large.chunks), "le budget doit reellement couper"


async def test_default_budget_comes_from_settings(
    project_db: Path, service: ServiceEspion, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le budget par defaut vient de settings.writer_context_tokens, pas
    d'une constante recopiee dans le constructeur de contexte."""
    monkeypatch.setattr(get_settings(), "writer_context_tokens", 60)
    await peupler(project_db, CORPUS)
    async with connect(project_db) as conn:
        implicite = await build_section_context(conn, noeud(), plan())
        explicite = await build_section_context(conn, noeud(), plan(), budget_tokens=60)

    assert implicite.chunk_ids() == explicite.chunk_ids()
    assert implicite.token_estimate <= 60


async def test_context_caps_chunks_per_source(project_db: Path, service: ServiceEspion) -> None:
    """Sans plafond, l'article le mieux indexe ecrase les autres et la
    section devient sa paraphrase."""
    await peupler(project_db, CORPUS)
    async with connect(project_db) as conn:
        contexte = await build_section_context(conn, noeud(), plan(), budget_tokens=10_000)

    par_source: dict[int, int] = {}
    for hit in contexte.chunks:
        par_source[hit.source_id] = par_source.get(hit.source_id, 0) + 1

    # La source 1 porte cinq extraits, tous mieux classes que les autres.
    assert par_source[1] == MAX_CHUNKS_PER_SOURCE
    assert len(par_source) > 1, "le plafond doit laisser entrer d'autres sources"


async def test_context_excludes_reference_chunks(project_db: Path, service: ServiceEspion) -> None:
    """Rendre une bibliographie au redacteur, c'est lui fournir la matiere
    premiere d'une citation inventee (US-102)."""
    corpus = [*CORPUS, (2, "Dupont A. Microplastiques et rein, 2018.", 0.995, "references")]
    await peupler(project_db, corpus)
    async with connect(project_db) as conn:
        contexte = await build_section_context(conn, noeud(), plan(), budget_tokens=10_000)

    assert contexte.chunks, "la recherche doit rendre du corps"
    assert all("Dupont A." not in hit.text for hit in contexte.chunks)


# --- Refus de rediger -----------------------------------------------------


async def test_context_insufficient_raises_rather_than_writing(
    project_db: Path, service: ServiceEspion
) -> None:
    """Mieux vaut refuser d'ecrire que produire une section non sourcee."""
    await peupler(project_db, CORPUS[:2])
    async with connect(project_db) as conn:
        with pytest.raises(InsufficientContextError) as exc:
            await build_section_context(conn, noeud(), plan())

    assert exc.value.status_code == 409
    assert str(MIN_CHUNKS) in exc.value.message
    # Le message propose une action, il ne constate pas une absence.
    assert "sources" in exc.value.message
    assert exc.value.details["chunks_found"] == 2


async def test_insufficient_context_names_the_node(
    project_db: Path, service: ServiceEspion
) -> None:
    await peupler(project_db, CORPUS[:1])
    async with connect(project_db) as conn:
        with pytest.raises(InsufficientContextError) as exc:
            await build_section_context(conn, noeud(), plan())

    assert noeud().title in exc.value.message


# --- Liste close des cles -------------------------------------------------


async def test_allowed_keys_derived_only_from_selected_chunks(
    project_db: Path, service: ServiceEspion
) -> None:
    """La liste close est ce qui rend V1 utilisable : une cle plausible ne
    doit pas etre indistinguable d'une cle reelle."""
    await peupler(project_db, CORPUS)
    async with connect(project_db) as conn:
        contexte = await build_section_context(conn, noeud(), plan(), budget_tokens=60)

    attendues = {bibtex_key(hit) for hit in contexte.chunks}
    assert set(contexte.allowed_keys) == attendues

    # Une source presente en base mais non retenue n'apporte aucune cle.
    retenues = {hit.source_id for hit in contexte.chunks}
    assert retenues != {1, 2, 3}, "le budget doit ecarter au moins une source"
    for cle in contexte.allowed_keys:
        assert any(cle.startswith(f"src{sid}_") for sid in retenues)


async def test_allowed_keys_are_deterministic(project_db: Path, service: ServiceEspion) -> None:
    """Un .bib genere a l'export doit correspondre au texte rediges des
    semaines plus tot : la cle ne peut pas dependre de l'execution."""
    await peupler(project_db, CORPUS)
    async with connect(project_db) as conn:
        premier = await build_section_context(conn, noeud(), plan(), budget_tokens=10_000)
        second = await build_section_context(conn, noeud(), plan(), budget_tokens=10_000)

    assert premier.allowed_keys == second.allowed_keys


async def test_context_carries_chunk_ids_for_the_guardrail(
    project_db: Path, service: ServiceEspion
) -> None:
    """V1 compare les chunk_ids declares a ceux reellement fournis."""
    await peupler(project_db, CORPUS)
    async with connect(project_db) as conn:
        contexte = await build_section_context(conn, noeud(), plan(), budget_tokens=10_000)

    assert contexte.chunk_ids() == {hit.chunk_id for hit in contexte.chunks}
    assert all(identifiant > 0 for identifiant in contexte.chunk_ids())


def test_select_under_budget_keeps_the_closest_first() -> None:
    """Sous budget serre, ce sont les extraits les plus proches qui restent."""
    from app.db.vector import ChunkHit

    hits = [
        ChunkHit(
            chunk_id=i,
            source_id=i,
            text="x" * 400,
            page_start=1,
            page_end=1,
            distance=distance,
            source_title=f"Source {i}",
            source_year=2020,
            source_doi=None,
            is_preprint=False,
        )
        for i, distance in enumerate([0.5, 0.1, 0.9], start=1)
    ]
    retenus = context_builder.select_under_budget(hits, budget_tokens=100)

    assert [h.chunk_id for h in retenus] == [2]


def test_bibtex_key_transliterates_accents() -> None:
    """Les titres francais commencent souvent par une capitale accentuee.
    Supprimer les lettres non ASCII au lieu de les translitterer donnait
    `tude` pour « Étude », et sautait « État » entier : la cle montree au
    redacteur, et citee dans les rejets V1, devenait illisible. Constat de
    revue, reproduit."""
    from app.db.vector import ChunkHit

    def cle(titre: str) -> str:
        return bibtex_key(
            ChunkHit(
                chunk_id=1,
                source_id=3,
                text="x",
                page_start=1,
                page_end=1,
                distance=0.0,
                source_title=titre,
                source_year=2021,
                source_doi=None,
                is_preprint=False,
            )
        )

    assert cle("Étude de la filtration rénale") == "src3_2021_etude"
    assert cle("État des lieux clinique") == "src3_2021_etat"
    assert cle("Évaluation médicale") == "src3_2021_evaluation"
