"""US-102 — recherche semantique : prefixes, filtres, bibliographie exclue."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.db.migrations.runner import run_migrations
from app.db.session import connect, transaction
from app.db.vector import insert_chunk_with_embedding
from app.rag.embeddings import DOCUMENT_PREFIX, QUERY_PREFIX, EmbeddingService
from app.rag.retriever import retrieve

DIM = 768
NOW = datetime.now(UTC).isoformat()


class DeterministicBackend:
    """Moteur factice : meme contenu -> meme vecteur.

    Les prefixes sont retires avant le calcul, ce qui modelise un modele
    bien entraine — chez lequel `search_document: X` et `search_query: X`
    se ressemblent. Sans cela, aucun test de recherche ne pourrait relier
    une requete a son document.
    """

    def __init__(self) -> None:
        self.vus: list[str] = []

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        self.vus.extend(texts)
        return [self._vecteur(t) for t in texts]

    @staticmethod
    def _vecteur(texte: str) -> list[float]:
        nu = texte.removeprefix(DOCUMENT_PREFIX).removeprefix(QUERY_PREFIX)
        graine = sum(ord(c) for c in nu if c.isalnum()) % DIM
        vecteur = [0.0] * DIM
        vecteur[graine] = 1.0
        return vecteur

    @property
    def identifier(self) -> str:
        return "fake:deterministe"


@pytest.fixture
def backend() -> DeterministicBackend:
    return DeterministicBackend()


@pytest.fixture
def service(backend: DeterministicBackend) -> EmbeddingService:
    return EmbeddingService(model_name="fake", dim=DIM, backend=backend)


async def peupler(db: Path, service: EmbeddingService) -> None:
    """Trois sources, dont une prepublication et une bibliographie."""
    async with connect(db) as conn:
        await run_migrations(conn)
        async with transaction(conn):
            await conn.execute(
                "INSERT INTO project (id, name, subject, language, academic_level,"
                " created_at, updated_at) VALUES (1,'P','Sujet','fr','doctorat',?,?)",
                (NOW, NOW),
            )
            for sid, titre, annee, preprint in (
                (1, "Article ancien", 1998, 0),
                (2, "Article recent", 2023, 0),
                (3, "Prepublication", 2024, 1),
            ):
                await conn.execute(
                    "INSERT INTO source_document (id, project_id, kind, title, year,"
                    " is_preprint, imported_at) VALUES (?,1,'article',?,?,?,?)",
                    (sid, titre, annee, preprint, NOW),
                )

        contenus = [
            (1, 0, "transport membranaire chez les mammiferes marins", "body"),
            (1, 1, "Dupont A. Etude des microplastiques 2021", "references"),
            (2, 0, "filtration renale et exposition chronique", "body"),
            (3, 0, "resultats preliminaires non relus par les pairs", "body"),
        ]
        for sid, ordinal, texte, genre in contenus:
            vecteur = (await service.embed_documents([texte]))[0]
            await insert_chunk_with_embedding(
                conn,
                source_id=sid,
                ordinal=ordinal,
                text=texte,
                embedding=vecteur,
                page_start=ordinal + 1,
                page_end=ordinal + 1,
                section_kind=genre,
            )


# --- Prefixes -------------------------------------------------------------


async def test_retrieve_applies_query_prefix(
    project_db: Path, service: EmbeddingService, backend: DeterministicBackend
) -> None:
    """Le prefixe est applique par le service, une seule fois (US-005)."""
    await peupler(project_db, service)
    backend.vus.clear()

    async with connect(project_db) as conn:
        await retrieve(conn, "filtration renale", k=3, service=service)

    assert backend.vus == ["search_query: filtration renale"]
    assert not any(t.startswith(DOCUMENT_PREFIX) for t in backend.vus)
    # Un prefixe applique deux fois serait un defaut silencieux.
    assert backend.vus[0].count(QUERY_PREFIX) == 1


# --- Bibliographie --------------------------------------------------------


async def test_retrieve_excludes_references_by_default(
    project_db: Path, service: EmbeddingService
) -> None:
    """Rendre la bibliographie au redacteur lui donnerait la matiere premiere
    d'une citation inventee."""
    await peupler(project_db, service)
    async with connect(project_db) as conn:
        resultats = await retrieve(
            conn, "Dupont A. Etude des microplastiques 2021", k=10, service=service
        )

    assert resultats, "la recherche doit retourner du corps"
    assert all("Dupont A." not in r.text for r in resultats)


async def test_retrieve_can_include_references_explicitly(
    project_db: Path, service: EmbeddingService
) -> None:
    """L'extraction bibliographique, elle, en a besoin."""
    await peupler(project_db, service)
    async with connect(project_db) as conn:
        resultats = await retrieve(
            conn,
            "Dupont A. Etude des microplastiques 2021",
            k=10,
            include_references=True,
            service=service,
        )
    assert any("Dupont A." in r.text for r in resultats)


# --- Filtres --------------------------------------------------------------


async def test_retrieve_year_filter(project_db: Path, service: EmbeddingService) -> None:
    await peupler(project_db, service)
    async with connect(project_db) as conn:
        resultats = await retrieve(
            conn, "transport membranaire", k=10, year_min=2020, service=service
        )
    assert resultats
    assert all(r.source_year is not None and r.source_year >= 2020 for r in resultats)


async def test_retrieve_exclude_preprints(project_db: Path, service: EmbeddingService) -> None:
    await peupler(project_db, service)
    async with connect(project_db) as conn:
        toutes = await retrieve(conn, "resultats preliminaires", k=10, service=service)
        filtrees = await retrieve(
            conn, "resultats preliminaires", k=10, exclude_preprints=True, service=service
        )

    assert any(r.is_preprint for r in toutes), "le jeu doit contenir une prepublication"
    assert all(not r.is_preprint for r in filtrees)


async def test_retrieve_returns_provenance(project_db: Path, service: EmbeddingService) -> None:
    """Sans provenance, un extrait n'est pas citable."""
    await peupler(project_db, service)
    async with connect(project_db) as conn:
        resultats = await retrieve(conn, "transport membranaire", k=1, service=service)

    hit = resultats[0]
    assert hit.source_title
    assert hit.page_start is not None and hit.page_end is not None
    assert hit.chunk_id > 0 and hit.source_id > 0
    assert hit.distance >= 0.0


async def test_retrieve_ranks_the_matching_chunk_first(
    project_db: Path, service: EmbeddingService
) -> None:
    await peupler(project_db, service)
    async with connect(project_db) as conn:
        resultats = await retrieve(
            conn, "filtration renale et exposition chronique", k=3, service=service
        )
    assert resultats[0].text == "filtration renale et exposition chronique"
    assert resultats[0].distance == pytest.approx(0.0, abs=1e-5)
