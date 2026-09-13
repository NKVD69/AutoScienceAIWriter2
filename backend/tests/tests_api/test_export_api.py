"""US-501, US-502 — API d'export : validation du contrat, blocage, etat de tache.

La compilation Quarto est remplacee par un double : ces tests portent sur ce
que l'endpoint promet — 422 hors contrat, 409 nommant le passage fautif, 202
et tache terminee — pas sur le binaire, que `tests_export` exerce deja.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.core.config import get_settings
from app.db import registry
from app.db.pool import get_pool, reset_pool
from app.db.session import transaction
from app.export import quarto as module_quarto
from app.export.quarto import QuartoResult, QuartoUnavailableError
from app.main import create_app
from app.models.plan import PlanStatus, PlanTree
from app.models.section import SectionStatus
from app.services import plan_service, task_service
from tests.tests_agents.test_plan_agent import arbre_valide

NOW = datetime.now(UTC).isoformat()

PROJET = {
    "name": "These microplastiques",
    "subject": "Impact des microplastiques sur la fonction renale",
    "language": "fr",
    "academic_level": "doctorat",
    "target_words": 16000,
}


@pytest.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path)
    task_service.reset_queues()
    await reset_pool()
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
        yield c
    await reset_pool()
    task_service.reset_queues()


def brancher_quarto(monkeypatch: pytest.MonkeyPatch, resultat: QuartoResult | Exception) -> list:
    """Remplace la compilation. Rend la liste des appels recus."""
    appels: list = []

    async def faux_render(repertoire: Path, formats: list[str], *args, **kwargs):
        appels.append((repertoire, formats))
        if isinstance(resultat, Exception):
            raise resultat
        return resultat, "Output created: document.pdf\n"

    monkeypatch.setattr(module_quarto, "render", faux_render)
    return appels


async def projet_redige(client: httpx.AsyncClient, verifiee: bool = True) -> tuple[int, object]:
    """Projet avec plan valide, une section redigee et deux sources citees."""
    pid = (await client.post("/api/v1/projects", json=PROJET)).json()["id"]
    async with registry.connect_registry() as reg:
        ref = await registry.get(reg, pid)
    conn = await get_pool().acquire(pid, ref.db_path)

    await plan_service.save_tree(
        conn, pid, PlanTree.model_validate(arbre_valide(1000)), PlanStatus.REVIEW
    )
    await plan_service.validate(conn, pid)
    plan = (await client.get(f"/api/v1/projects/{pid}/plan")).json()
    feuille = plan["nodes"][0]["children"][0]["children"][0]["id"]

    async with transaction(conn):
        for sid, titre, auteurs, annee, preprint in (
            (1, "Filtration renale", "Dupont, Alice", 2019, 0),
            (2, "Resultats recents", "Nguyen, Chi", 2024, 1),
        ):
            await conn.execute(
                "INSERT INTO source_document (id, project_id, kind, title, authors, year,"
                " is_preprint, imported_at, approved_at) VALUES (?,?,'article',?,?,?,?,?,?)",
                (sid, pid, titre, auteurs, annee, preprint, NOW, NOW),
            )
        await conn.execute(
            "INSERT INTO draft_section (id, plan_node_id, content_qmd, status, version,"
            " generated_at) VALUES (1,?,?,?,1,?)",
            (
                feuille,
                "La filtration decroit [@src1_2019_filtration], ce que confirment des"
                " resultats recents [@src2_2024_resultats].",
                str(SectionStatus.REVIEWING),
                NOW,
            ),
        )
        for cid, source_id, cle in ((1, 1, "src1_2019_filtration"), (2, 2, "src2_2024_resultats")):
            await conn.execute(
                "INSERT INTO citation (id, draft_section_id, source_id, bibtex_key, verified)"
                " VALUES (?,1,?,?,?)",
                (cid, source_id, cle, 1 if verifiee or cid == 1 else 0),
            )
    return pid, conn


def repertoires_exports(pid: int) -> list[Path]:
    racine = get_settings().exports_dir(pid)
    return sorted(racine.iterdir()) if racine.exists() else []


# --- Validation du contrat ------------------------------------------------


@pytest.mark.parametrize(
    "corps",
    [
        {"formats": []},
        {"formats": ["epub"]},
        {"formats": ["pdf"], "template": "//hote/partage"},
        {"formats": ["pdf"], "template": "../.."},
        {},
    ],
    ids=["formats_vide", "format_inconnu", "gabarit_unc", "gabarit_remontant", "sans_formats"],
)
async def test_export_rejects_requests_outside_the_contract(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, corps: dict
) -> None:
    """Refuse AVANT toute ecriture : pas de tache, pas de repertoire, pas de
    compilation."""
    pid, _ = await projet_redige(client)
    appels = brancher_quarto(monkeypatch, QuartoResult(returncode=0))

    r = await client.post(f"/api/v1/projects/{pid}/export", json=corps)

    assert r.status_code == 422
    assert appels == []
    assert repertoires_exports(pid) == []


async def test_export_unknown_project_is_404(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/v1/projects/9999/export", json={"formats": ["pdf"]})
    assert r.status_code == 404


# --- Blocage sur citation non verifiee ------------------------------------


async def test_export_blocked_by_unverified_citation_names_the_passage(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-007 : une citation non verifiee bloque l'export, et la reponse mene
    au texte fautif plutot que d'annoncer un echec sans suite."""
    pid, _ = await projet_redige(client, verifiee=False)
    appels = brancher_quarto(monkeypatch, QuartoResult(returncode=0))

    r = await client.post(f"/api/v1/projects/{pid}/export", json={"formats": ["pdf"]})

    assert r.status_code == 409
    corps = r.json()
    assert corps["code"] == "UNVERIFIED_CITATION"
    assert corps["required_state"] == "ALL_CITATIONS_VERIFIED"
    bloquant = corps["blocking_items"][0]
    assert bloquant["bibtex_key"] == "src2_2024_resultats"
    assert bloquant["section_id"] == 1
    assert "filtration decroit" in bloquant["excerpt"]
    # Rien n'est produit, pas meme un repertoire, et Quarto n'est pas appele.
    assert appels == []
    assert repertoires_exports(pid) == []


# --- Etat de la tache -----------------------------------------------------


async def test_export_success_returns_exported_task(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid, _ = await projet_redige(client)
    appels = brancher_quarto(
        monkeypatch,
        QuartoResult(returncode=0, outputs=["document.pdf"], duration_s=1.0, version="1.10.18"),
    )

    r = await client.post(f"/api/v1/projects/{pid}/export", json={"formats": ["pdf"]})

    assert r.status_code == 202
    tache = r.json()
    assert tache["state"] == "EXPORTED"
    assert tache["progress"] == 1.0
    assert len(appels) == 1 and appels[0][1] == ["pdf"]
    # Le rapport de l'export est ecrit dans son repertoire date.
    (repertoire,) = repertoires_exports(pid)
    assert (repertoire / "rapport.json").exists()


async def test_export_compilation_failure_marks_task_in_error(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un document qui ne compile pas n'est pas un export : la tache le dit."""
    pid, _ = await projet_redige(client)
    brancher_quarto(monkeypatch, QuartoResult(returncode=1, version="1.10.18"))

    r = await client.post(f"/api/v1/projects/{pid}/export", json={"formats": ["pdf"]})

    assert r.status_code == 202
    assert r.json()["state"] == "ERROR_STATE"


async def test_export_without_quarto_is_503_and_task_in_error(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Quarto absent : erreur exploitable, et aucune tache laissee EXPORTING."""
    pid, conn = await projet_redige(client)
    brancher_quarto(monkeypatch, QuartoUnavailableError.missing())

    r = await client.post(f"/api/v1/projects/{pid}/export", json={"formats": ["pdf"]})

    assert r.status_code == 503
    assert r.json()["code"] == "QUARTO_UNAVAILABLE"
    async with conn.execute("SELECT state FROM task WHERE agent = 'export'") as cur:
        etats = [str(row[0]) for row in await cur.fetchall()]
    assert etats == ["ERROR_STATE"]
