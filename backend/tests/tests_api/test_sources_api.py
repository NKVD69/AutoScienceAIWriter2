"""US-102 — import, approbation, ingestion, recherche. Bout en bout."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import httpx
import pytest

from app.core.config import get_settings
from app.db.pool import reset_pool
from app.main import create_app
from app.rag import embeddings as embeddings_module
from app.rag.embeddings import DOCUMENT_PREFIX, QUERY_PREFIX, EmbeddingService
from app.services import task_service
from tests.fixtures import make_article_pdf, make_scanned_pdf, make_text_file

DIM = 768
PROJET = {
    "name": "Thèse microplastiques",
    "subject": "Impact des microplastiques sur la fonction rénale",
    "language": "fr",
    "academic_level": "doctorat",
}


class DeterministicBackend:
    """Même contenu, même vecteur. Les préfixes sont retirés avant calcul."""

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vecteur(t) for t in texts]

    @staticmethod
    def _vecteur(texte: str) -> list[float]:
        nu = texte.removeprefix(DOCUMENT_PREFIX).removeprefix(QUERY_PREFIX)
        vecteur = [0.0] * DIM
        vecteur[sum(ord(c) for c in nu if c.isalnum()) % DIM] = 1.0
        return vecteur

    @property
    def identifier(self) -> str:
        return "fake:deterministe"


@pytest.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path)
    await reset_pool()
    task_service.reset_queues()

    # Le vrai modèle mettrait des minutes ; ce qui est testé ici est le
    # pipeline, pas la qualité des vecteurs.
    faux = EmbeddingService(model_name="fake", dim=DIM, backend=DeterministicBackend())
    monkeypatch.setattr(embeddings_module, "_service", faux)

    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
        yield c
    await reset_pool()


async def projet(client: httpx.AsyncClient) -> int:
    r = await client.post("/api/v1/projects", json=PROJET)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def importer(client: httpx.AsyncClient, pid: int, chemin: Path) -> httpx.Response:
    with chemin.open("rb") as fichier:
        return await client.post(
            f"/api/v1/projects/{pid}/sources", files={"file": (chemin.name, fichier)}
        )


# --- Import ---------------------------------------------------------------


async def test_import_computes_sha256(client: httpx.AsyncClient, tmp_path: Path) -> None:
    import hashlib

    pid = await projet(client)
    chemin = make_article_pdf(tmp_path / "article.pdf")
    attendu = hashlib.sha256(chemin.read_bytes()).hexdigest()

    r = await importer(client, pid, chemin)
    assert r.status_code == 201, r.text
    source = r.json()
    assert source["sha256"] == attendu
    assert source["approved_at"] is None, "l'import ne vaut pas approbation"
    assert source["chunk_count"] == 0


async def test_import_duplicate_returns_existing_source(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    """Un doublon doublerait aussi ses chunks et fausserait toute recherche."""
    pid = await projet(client)
    chemin = make_article_pdf(tmp_path / "article.pdf")

    premier = (await importer(client, pid, chemin)).json()
    reponse = await importer(client, pid, chemin)

    assert reponse.status_code == 200, "un doublon n'est pas une création"
    second = reponse.json()
    assert second["id"] == premier["id"]
    assert second["duplicate"] is True
    assert len((await client.get(f"/api/v1/projects/{pid}/sources")).json()) == 1


async def test_import_rejects_unsupported_extension(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    pid = await projet(client)
    fichier = tmp_path / "tableur.xlsx"
    fichier.write_bytes(b"contenu binaire")
    r = await importer(client, pid, fichier)
    assert r.status_code == 422
    assert "xlsx" in r.json()["message"]


async def test_import_proposes_metadata_without_approving(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    """Un titre lu dans les propriétés d'un fichier n'est pas vérifié."""
    pid = await projet(client)
    source = (await importer(client, pid, make_article_pdf(tmp_path / "a.pdf"))).json()
    assert source["title"] == "Titre de l'article de test"
    assert source["approved_at"] is None


async def test_import_accepts_text_files(client: httpx.AsyncClient, tmp_path: Path) -> None:
    pid = await projet(client)
    r = await importer(client, pid, make_text_file(tmp_path / "note.txt"))
    assert r.status_code == 201


# --- Approbation ----------------------------------------------------------


async def test_unapproved_source_not_ingested(client: httpx.AsyncClient, tmp_path: Path) -> None:
    """Ce qui n'a pas été relu par l'auteur n'entre pas dans sa base."""
    pid = await projet(client)
    await importer(client, pid, make_article_pdf(tmp_path / "a.pdf"))

    r = await client.post(f"/api/v1/projects/{pid}/ingest")
    assert r.status_code == 409
    assert r.json()["required_state"] == "approved_source"


async def test_approve_is_logged(client: httpx.AsyncClient, tmp_path: Path) -> None:
    pid = await projet(client)
    source = (await importer(client, pid, make_article_pdf(tmp_path / "a.pdf"))).json()

    r = await client.post(f"/api/v1/projects/{pid}/sources/{source['id']}/approve")
    assert r.status_code == 200
    assert r.json()["approved_at"] is not None

    evenements = [
        e["event_type"] for e in (await client.get(f"/api/v1/projects/{pid}/audit")).json()
    ]
    assert "SOURCE_IMPORTED" in evenements
    assert "SOURCE_APPROVED" in evenements


async def test_approve_unknown_source_is_404(client: httpx.AsyncClient) -> None:
    pid = await projet(client)
    assert (await client.post(f"/api/v1/projects/{pid}/sources/999/approve")).status_code == 404


# --- Ingestion ------------------------------------------------------------


async def ingerer(client: httpx.AsyncClient, pid: int, chemin: Path) -> dict:
    source = (await importer(client, pid, chemin)).json()
    await client.post(f"/api/v1/projects/{pid}/sources/{source['id']}/approve")
    r = await client.post(f"/api/v1/projects/{pid}/ingest")
    assert r.status_code == 202, r.text
    return r.json()


async def test_ingest_indexes_chunks_with_pages(client: httpx.AsyncClient, tmp_path: Path) -> None:
    pid = await projet(client)
    tache = await ingerer(client, pid, make_article_pdf(tmp_path / "a.pdf"))

    assert tache["state"] == "SOURCES_READY"
    assert tache["progress"] == 1.0

    sources = (await client.get(f"/api/v1/projects/{pid}/sources")).json()
    assert sources[0]["chunk_count"] > 0

    hits = (
        await client.post(
            f"/api/v1/projects/{pid}/search",
            json={"query": "transport membranaire en milieu marin", "k": 5},
        )
    ).json()
    assert hits
    for hit in hits:
        assert hit["page_start"] is not None, "chunk sans pagination : défaut bloquant"
        assert hit["source_title"]


async def test_ingest_emits_progress_events(client: httpx.AsyncClient, tmp_path: Path) -> None:
    pid = await projet(client)
    tache = await ingerer(client, pid, make_article_pdf(tmp_path / "a.pdf"))
    types = [e.type for e in task_service.drain(tache["id"])]
    assert "progress" in types
    assert "state" in types
    assert "done" in types


async def test_ingest_failed_source_does_not_block_pipeline(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    """Une source illisible ne doit jamais bloquer l'ingestion des autres."""
    pid = await projet(client)
    for chemin in (
        make_scanned_pdf(tmp_path / "numerise.pdf"),
        make_article_pdf(tmp_path / "bon.pdf"),
    ):
        source = (await importer(client, pid, chemin)).json()
        await client.post(f"/api/v1/projects/{pid}/sources/{source['id']}/approve")

    tache = (await client.post(f"/api/v1/projects/{pid}/ingest")).json()
    assert tache["state"] == "SOURCES_READY"
    assert tache["last_error"] and "non ingérée" in tache["last_error"]

    sources = {
        s["title"]: s["chunk_count"]
        for s in (await client.get(f"/api/v1/projects/{pid}/sources")).json()
    }
    assert sources["Titre de l'article de test"] > 0, "la source valide doit être ingérée"

    evenements = [
        e["event_type"] for e in (await client.get(f"/api/v1/projects/{pid}/audit")).json()
    ]
    assert "INGESTION_FAILED" in evenements
    assert "INGESTION_COMPLETED" in evenements


async def test_ingest_retries_twice_then_marks_failed(
    client: httpx.AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import ingestion_service

    essais = {"n": 0}
    vrai_extract = ingestion_service.extract

    def compte(chemin: Path):
        essais["n"] += 1
        raise RuntimeError("lecture impossible")

    monkeypatch.setattr(ingestion_service, "extract", compte)

    pid = await projet(client)
    source = (await importer(client, pid, make_article_pdf(tmp_path / "a.pdf"))).json()
    await client.post(f"/api/v1/projects/{pid}/sources/{source['id']}/approve")
    await client.post(f"/api/v1/projects/{pid}/ingest")

    assert essais["n"] == ingestion_service.MAX_ATTEMPTS == 2
    assert vrai_extract is not compte


async def test_ingest_transaction_per_source(
    client: httpx.AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Une source interrompue ne laisse aucun chunk partiel.

    Les DEUX tentatives echouent au meme point : sinon la reprise
    reinsererait tout et masquerait l'invariant qu'on veut etablir.
    """
    from app.db import vector

    vrai_insert = vector.insert_chunk_with_embedding
    compteur = {"n": 0}

    async def echoue_apres_deux(*args: object, **kwargs: object):
        compteur["n"] += 1
        if compteur["n"] >= 3:
            raise RuntimeError("panne au milieu de la source")
        return await vrai_insert(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        "app.services.ingestion_service.insert_chunk_with_embedding", echoue_apres_deux
    )

    pid = await projet(client)
    await ingerer(client, pid, make_article_pdf(tmp_path / "a.pdf"))

    sources = (await client.get(f"/api/v1/projects/{pid}/sources")).json()
    assert sources[0]["chunk_count"] == 0, "des chunks partiels ont été laissés en base"


async def test_ingest_retry_completes_after_a_transient_failure(
    client: httpx.AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Une panne passagere doit etre rattrapee par la seconde tentative, et
    la source finir complete — pas partiellement indexee."""
    from app.db import vector

    vrai_insert = vector.insert_chunk_with_embedding
    compteur = {"n": 0}

    async def echoue_une_fois(*args: object, **kwargs: object):
        compteur["n"] += 1
        if compteur["n"] == 3:
            raise RuntimeError("panne passagere")
        return await vrai_insert(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        "app.services.ingestion_service.insert_chunk_with_embedding", echoue_une_fois
    )

    pid = await projet(client)
    tache = await ingerer(client, pid, make_article_pdf(tmp_path / "a.pdf"))

    sources = (await client.get(f"/api/v1/projects/{pid}/sources")).json()
    assert sources[0]["chunk_count"] > 0
    assert tache["last_error"] is None, "la source a finalement ete ingeree"


async def test_task_resumes_from_persisted_state(client: httpx.AsyncClient, tmp_path: Path) -> None:
    """Une ingestion interrompue reprend, elle ne recommence pas."""
    from app.db.pool import get_pool
    from app.models.source import TaskState
    from app.services import task_service as ts

    pid = await projet(client)
    source = (await importer(client, pid, make_article_pdf(tmp_path / "a.pdf"))).json()
    await client.post(f"/api/v1/projects/{pid}/sources/{source['id']}/approve")

    # Tâche laissée en cours, comme après un arrêt du backend.
    async with __import__("app.db.registry", fromlist=["x"]).connect_registry() as reg:
        ref = await __import__("app.db.registry", fromlist=["x"]).get(reg, pid)
    conn = await get_pool().acquire(pid, ref.db_path)
    interrompue = await ts.create(conn, pid, TaskState.SOURCES_INGESTING, agent="ingestion")

    reprise = (await client.post(f"/api/v1/projects/{pid}/ingest")).json()
    assert reprise["id"] == interrompue.id, "une nouvelle tâche a été créée au lieu de reprendre"
    assert reprise["state"] == "SOURCES_READY"


# --- Suppression ----------------------------------------------------------


async def test_delete_source_refused_when_cited(client: httpx.AsyncClient, tmp_path: Path) -> None:
    """`citation.source_id` est en RESTRICT : on ne supprime pas une source citée."""
    from app.db import registry
    from app.db.pool import get_pool
    from app.db.session import transaction

    pid = await projet(client)
    source = (await importer(client, pid, make_article_pdf(tmp_path / "a.pdf"))).json()

    async with registry.connect_registry() as reg:
        ref = await registry.get(reg, pid)
    conn = await get_pool().acquire(pid, ref.db_path)
    async with transaction(conn):
        await conn.execute(
            "INSERT INTO plan (id, project_id, problematique, status, created_at)"
            " VALUES (1,1,'P','draft','x')"
        )
        await conn.execute(
            "INSERT INTO plan_node (id, plan_id, ordinal, level, title) VALUES (1,1,0,1,'C')"
        )
        await conn.execute(
            "INSERT INTO draft_section (id, plan_node_id, status) VALUES (1,1,'draft')"
        )
        await conn.execute(
            "INSERT INTO citation (draft_section_id, source_id, bibtex_key)"
            " VALUES (1, ?, 'a_2020_x')",
            (source["id"],),
        )

    r = await client.delete(f"/api/v1/projects/{pid}/sources/{source['id']}")
    assert r.status_code == 409
    assert r.json()["current_state"] == "cited"


async def test_delete_uncited_source_succeeds(client: httpx.AsyncClient, tmp_path: Path) -> None:
    pid = await projet(client)
    source = (await importer(client, pid, make_article_pdf(tmp_path / "a.pdf"))).json()
    assert (
        await client.delete(f"/api/v1/projects/{pid}/sources/{source['id']}")
    ).status_code == 204
    assert (await client.get(f"/api/v1/projects/{pid}/sources")).json() == []
