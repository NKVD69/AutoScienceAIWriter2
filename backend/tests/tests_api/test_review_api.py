"""US-302 — API de relecture : relire, consulter, ne jamais valider.

La chaîne complète est exercée — rédaction réelle puis relecture — contre un
backend factice alimenté de réponses préenregistrées. Aucun moteur réel.

Le fil rouge : une relecture, même « prête » et bien notée, ne franchit jamais
la porte de validation (ADR-004). La section reste à relire ; seul un humain la
valide, par un autre endpoint que la relecture n'appelle pas.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.core.config import get_settings
from app.db.pool import reset_pool
from app.main import create_app
from app.models.review import CATEGORIES, weighted_overall
from app.rag import retriever
from app.services import section_service, task_service
from tests.tests_agents.test_reviewer_agent import finding, rapport
from tests.tests_api.test_sections_api import brancher_modele, projet_pret, reponse_valide

DIM = 768
# Présent mot pour mot dans le remplissage de `reponse_valide` : sert d'extrait
# de constat que le contrôle de véracité de la relecture doit accepter.
EXTRAIT_PRESENT = "Le propos se developpe ici de maniere argumentee"


class ServiceEspion:
    """Embeddings factices : la requête pointe l'axe des extraits de `projet_pret`."""

    async def embed_query(self, texte: str) -> list[float]:
        return [1.0] + [0.0] * (DIM - 1)


@pytest.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Client HTTP sur l'application, base isolée et embeddings factices."""
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path)
    task_service.reset_queues()
    await reset_pool()
    monkeypatch.setattr(retriever, "get_embedding_service", ServiceEspion)
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
        yield c
    await reset_pool()
    task_service.reset_queues()


async def _section_redigee(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch):
    """Projet prêt + une section rédigée par le vrai chemin (writer → guardrails)."""
    pid, conn, feuille, chunk_ids = await projet_pret(client)
    brancher_modele(monkeypatch, [reponse_valide(chunk_ids[0])])
    r = await client.post(f"/api/v1/projects/{pid}/sections/{feuille}/draft")
    assert r.status_code == 202
    section_id = (await section_service.versions_of(conn, feuille))[0]
    return pid, conn, feuille, section_id


async def test_review_reports_the_fond_without_validating_the_section(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Même « prête » et notée 95 par catégorie, la section reste REVIEWING :
    la relecture conseille, elle n'autorise pas."""
    pid, _conn, _feuille, section_id = await _section_redigee(client, monkeypatch)
    avant = (await client.get(f"/api/v1/projects/{pid}/sections/{section_id}")).json()
    assert avant["status"] == "REVIEWING"

    constat = finding(excerpt=EXTRAIT_PRESENT, category="style", severity="minor")
    brancher_modele(
        monkeypatch,
        [rapport(verdict="ready", findings=[constat], scores={c: 95.0 for c in CATEGORIES})],
    )

    r = await client.post(f"/api/v1/projects/{pid}/sections/{section_id}/review")
    assert r.status_code == 202
    assert r.json()["state"] == "SECTION_REVIEWING"

    rap = (await client.get(f"/api/v1/projects/{pid}/sections/{section_id}/review")).json()
    assert rap["verdict"] == "ready"
    # Note recomposée côté serveur à partir des scores fondus et des poids ;
    # l'« overall » indicatif du modèle (50) est ignoré.
    assert rap["overall_score"] == weighted_overall(rap["scores"], get_settings().review_weights)
    assert rap["overall_score"] != 50.0

    # La section n'a PAS changé d'état : la relecture ne valide pas.
    apres = (await client.get(f"/api/v1/projects/{pid}/sections/{section_id}")).json()
    assert apres["status"] == "REVIEWING"
    # Le score de qualité est bien reporté sur la section.
    assert apres["quality_score"] == rap["overall_score"]


async def test_auto_correct_defaults_to_false(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sans le paramètre, aucune correction ne tourne : un rapport, puis arrêt,
    même quand le verdict appelle une reprise."""
    pid, conn, feuille, section_id = await _section_redigee(client, monkeypatch)
    constat = finding(excerpt=EXTRAIT_PRESENT, category="argumentation", severity="major")
    backend = brancher_modele(monkeypatch, [rapport(verdict="needs_work", findings=[constat])])

    r = await client.post(f"/api/v1/projects/{pid}/sections/{section_id}/review")
    assert r.status_code == 202

    # Un seul appel au modèle (la relecture) : le rédacteur de reprise n'a pas tourné.
    assert len(backend.users) == 1
    # Et donc aucune nouvelle version de section.
    assert len(await section_service.versions_of(conn, feuille)) == 1


async def test_a_hallucinated_excerpt_is_rejected_by_the_api(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le seul contrôle de véracité de la relecture : un extrait absent du texte
    est un rejet 422, au même titre qu'un JSON mal formé."""
    pid, _conn, _feuille, section_id = await _section_redigee(client, monkeypatch)
    invente = finding(excerpt="un passage que la section ne contient nulle part vraiment")
    brancher_modele(monkeypatch, [rapport(verdict="needs_work", findings=[invente])])

    r = await client.post(f"/api/v1/projects/{pid}/sections/{section_id}/review")
    assert r.status_code == 422
    assert r.json()["code"] == "SECTION_REVIEW_FAILED"


async def test_reviews_history_keeps_every_report(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deux relectures d'une section : deux rapports conservés, jamais écrasés —
    de quoi comparer les verdicts d'une version à l'autre."""
    pid, _conn, feuille, section_id = await _section_redigee(client, monkeypatch)
    constat = finding(excerpt=EXTRAIT_PRESENT, category="style", severity="minor")
    for _ in range(2):
        brancher_modele(monkeypatch, [rapport(verdict="needs_work", findings=[constat])])
        r = await client.post(f"/api/v1/projects/{pid}/sections/{section_id}/review")
        assert r.status_code == 202

    historique = (await client.get(f"/api/v1/projects/{pid}/sections/{feuille}/reviews")).json()
    assert len(historique) == 2


async def test_reading_a_review_before_any_exists_is_404(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid, _conn, _feuille, section_id = await _section_redigee(client, monkeypatch)
    r = await client.get(f"/api/v1/projects/{pid}/sections/{section_id}/review")
    assert r.status_code == 404


async def test_review_of_unknown_section_is_404(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid, _, _, _ = await projet_pret(client)
    brancher_modele(monkeypatch, [rapport()])
    r = await client.post(f"/api/v1/projects/{pid}/sections/99999/review")
    assert r.status_code == 404
