"""US-301 — API des sections : verrou de plan, citations verifiees, edition.

La chaine complete est exercee — RAG, agent, guardrail Pydantic, garde-fous
de veracite, persistance — contre un backend factice alimente de reponses
preenregistrees. Aucun moteur reel.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import yaml

from app.agents.breaker import MAX_GUARDRAIL_RETRIES
from app.core.config import get_settings
from app.db import registry
from app.db.pool import get_pool, reset_pool
from app.db.session import transaction
from app.db.vector import insert_chunk_with_embedding
from app.llm import manager as manager_module
from app.llm.manager import LLMManager
from app.main import create_app
from app.models.plan import PlanStatus, PlanTree
from app.models.section import SectionStatus
from app.rag import retriever
from app.services import plan_service, section_service, task_service
from tests.tests_agents.test_plan_agent import arbre_valide
from tests.tests_agents.test_writer_agent import HYPOTHESE_SOURCEE, MYST, FakeBackend

NOW = datetime.now(UTC).isoformat()
DIM = 768
MOTS_PAR_FEUILLE = 1000
CONTRAT = Path(__file__).resolve().parents[3] / "contracts" / "openapi.yaml"

PROJET = {
    "name": "These microplastiques",
    "subject": "Impact des microplastiques sur la fonction renale",
    "language": "fr",
    "academic_level": "doctorat",
    "target_words": 16000,
}

DOI_CONNU = "10.1016/j.envint.2021.106274"

# (source_id, titre, annee, doi)
SOURCES = (
    (1, "Filtration renale et polymeres", 2021, DOI_CONNU),
    (2, "Exposition chronique urbaine", 2019, None),
)
CLE_1 = "src1_2021_filtration"
CLE_2 = "src2_2019_exposition"

EXTRAITS = (
    (1, "La filtration glomerulaire decroit apres exposition prolongee.", 0.99),
    (1, "Les polymeres s'accumulent dans le tissu cortical.", 0.97),
    (2, "L'exposition urbaine moyenne est estimee par biomarqueurs.", 0.95),
    (2, "Les cohortes suivies depassent rarement cinq annees.", 0.93),
)


def axe(x: float) -> list[float]:
    vecteur = [0.0] * DIM
    vecteur[0] = x
    return vecteur


class ServiceEspion:
    async def embed_query(self, texte: str) -> list[float]:
        return axe(1.0)


def texte_de_longueur(mots: int, debut: str) -> str:
    """Contenu dont la longueur MESUREE atteint `mots`.

    Le controle de longueur compte les mots du texte, et non plus le
    `word_count` declare par le modele : une reponse de dix mots annoncee a
    mille etait acceptee. Le remplissage ne porte ni chiffre, ni URL, ni cle,
    pour que seul le defaut vise par chaque test declenche un rejet.
    """
    phrase = "Le propos se developpe ici de maniere argumentee et prudente.".split()
    tete = debut.split()
    restant = max(0, mots - len(tete))
    remplissage = (phrase * (restant // len(phrase) + 1))[:restant]
    return " ".join(tete + remplissage)


def reponse_valide(chunk_id: int, mots: int = MOTS_PAR_FEUILLE) -> str:
    """Sortie conforme : une affirmation sourcee, une limite, un DOI connu."""
    return json.dumps(
        {
            "content_qmd": texte_de_longueur(
                mots,
                f"La filtration decroit apres exposition prolongee [@{CLE_1}]. "
                f"Les cohortes urbaines confirment la tendance [@{CLE_2}]. "
                f"Voir {DOI_CONNU}.",
            ),
            "claims": [
                {
                    "text": "La filtration glomerulaire decroit apres exposition prolongee.",
                    "kind": "sourced",
                    "citation_keys": [CLE_1],
                    "chunk_ids": [chunk_id],
                },
                {
                    "text": "La duree de suivi limite la portee des conclusions.",
                    "kind": "limitation",
                },
            ],
            "word_count": mots,
        },
        ensure_ascii=False,
    )


def reponse_cle_inventee(chunk_id: int) -> str:
    return json.dumps(
        {
            "content_qmd": texte_de_longueur(
                MOTS_PAR_FEUILLE, "La filtration decroit [@src9_2020_inventee]."
            ),
            "claims": [
                {
                    "text": "La filtration decroit.",
                    "kind": "sourced",
                    "citation_keys": ["src9_2020_inventee"],
                    "chunk_ids": [chunk_id],
                }
            ],
            "word_count": MOTS_PAR_FEUILLE,
        },
        ensure_ascii=False,
    )


def reponse_doi_invente(chunk_id: int) -> str:
    return json.dumps(
        {
            "content_qmd": texte_de_longueur(
                MOTS_PAR_FEUILLE,
                f"La filtration decroit [@{CLE_1}], cf. 10.9999/inexistant.2024.001.",
            ),
            "claims": [
                {
                    "text": "La filtration decroit.",
                    "kind": "sourced",
                    "citation_keys": [CLE_1],
                    "chunk_ids": [chunk_id],
                }
            ],
            "word_count": MOTS_PAR_FEUILLE,
        },
        ensure_ascii=False,
    )


@pytest.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path)
    task_service.reset_queues()
    await reset_pool()
    monkeypatch.setattr(retriever, "get_embedding_service", ServiceEspion)
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
        yield c
    await reset_pool()
    task_service.reset_queues()


def brancher_modele(monkeypatch: pytest.MonkeyPatch, reponses: list[str]) -> FakeBackend:
    """Substitue le moteur reel par un backend alimente de reponses."""
    backend = FakeBackend(reponses)
    monkeypatch.setattr(manager_module, "build_manager", lambda: LLMManager(backend))
    return backend


async def projet_pret(
    client: httpx.AsyncClient, valide: bool = True
) -> tuple[int, object, int, list[int]]:
    """Projet + sources + extraits + plan (valide ou non). Rend le noeud feuille."""
    pid = (await client.post("/api/v1/projects", json=PROJET)).json()["id"]
    async with registry.connect_registry() as reg:
        ref = await registry.get(reg, pid)
    conn = await get_pool().acquire(pid, ref.db_path)

    async with transaction(conn):
        for sid, titre, annee, doi in SOURCES:
            await conn.execute(
                "INSERT INTO source_document (id, project_id, kind, title, year, doi,"
                " is_preprint, imported_at, approved_at) VALUES (?,?,'article',?,?,?,0,?,?)",
                (sid, pid, titre, annee, doi, NOW, NOW),
            )

    chunk_ids = []
    for ordinal, (sid, texte, proximite) in enumerate(EXTRAITS):
        chunk_ids.append(
            await insert_chunk_with_embedding(
                conn,
                source_id=sid,
                ordinal=ordinal,
                text=texte,
                embedding=axe(proximite),
                page_start=ordinal + 1,
                page_end=ordinal + 2,
            )
        )

    await plan_service.save_tree(
        conn,
        pid,
        PlanTree.model_validate(arbre_valide(MOTS_PAR_FEUILLE)),
        PlanStatus.REVIEW,
    )
    if valide:
        await plan_service.validate(conn, pid)

    plan = (await client.get(f"/api/v1/projects/{pid}/plan")).json()
    feuille = plan["nodes"][0]["children"][0]["children"][0]["id"]
    return pid, conn, feuille, chunk_ids


# --- Conformite au contrat ------------------------------------------------


def test_section_status_matches_the_contract() -> None:
    """`SectionStatus` reprend EXACTEMENT l'enumeration DraftSection.status.

    Une seconde enumeration du meme nom existait dans models/plan.py, sans
    GUARDRAIL ni CORRECTING : persister une section dans l'un de ces deux
    etats aurait fait lever la LECTURE du plan. Deux enumerations partielles
    du meme contrat finissent par diverger, ce test les empeche de renaitre.
    """
    schema = yaml.safe_load(CONTRAT.read_text(encoding="utf-8"))
    attendus = schema["components"]["schemas"]["DraftSection"]["properties"]["status"]["enum"]
    assert [e.value for e in SectionStatus] == attendus

    from app.models import plan as modele_plan

    assert modele_plan.SectionStatus is SectionStatus


def test_persisted_status_also_fits_the_plan_node_enum() -> None:
    """Le contrat decrit la MEME colonne par deux enumerations differentes :
    DraftSection.status en compte six, PlanNode.section_status cinq — sans
    GUARDRAIL ni CORRECTING, avec un NONE que rien n'ecrit. `plan_service`
    reporte l'une dans l'autre, donc les etats REELLEMENT persistes doivent
    tenir dans la plus etroite des deux. US-301 n'ecrit que REVIEWING, et
    US-PLAN-001 que ORPHANED."""
    schema = yaml.safe_load(CONTRAT.read_text(encoding="utf-8"))
    noeud = schema["components"]["schemas"]["PlanNode"]["properties"]["section_status"]["enum"]

    for persiste in (SectionStatus.REVIEWING, SectionStatus.ORPHANED):
        assert persiste.value in noeud


# --- Verrou de redaction --------------------------------------------------


async def test_draft_blocked_when_plan_not_validated(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La structure doit etre approuvee avant qu'on ecrive dessus (§5.2)."""
    pid, _, feuille, chunk_ids = await projet_pret(client, valide=False)
    backend = brancher_modele(monkeypatch, [reponse_valide(chunk_ids[0])])

    r = await client.post(f"/api/v1/projects/{pid}/sections/{feuille}/draft")

    assert r.status_code == 409
    corps = r.json()
    assert corps["current_state"] == "REVIEW"
    assert corps["required_state"] == "VALIDATED"
    assert backend.users == [], "aucun appel au modele avant validation du plan"


async def test_draft_refuses_when_context_is_too_thin(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mieux vaut refuser d'ecrire que produire une section non sourcee."""
    pid, conn, feuille, chunk_ids = await projet_pret(client)
    async with transaction(conn):
        await conn.execute("DELETE FROM chunk WHERE id > ?", (chunk_ids[0],))
    backend = brancher_modele(monkeypatch, [reponse_valide(chunk_ids[0])])

    r = await client.post(f"/api/v1/projects/{pid}/sections/{feuille}/draft")

    assert r.status_code == 409
    assert r.json()["code"] == "INSUFFICIENT_CONTEXT"
    assert backend.users == []


# --- Citations ------------------------------------------------------------


async def test_citations_persisted_as_verified(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifiees parce qu'elles ont franchi V1, V2 et V3 — pas parce que le
    modele les a produites."""
    pid, conn, feuille, chunk_ids = await projet_pret(client)
    brancher_modele(monkeypatch, [reponse_valide(chunk_ids[0])])

    r = await client.post(f"/api/v1/projects/{pid}/sections/{feuille}/draft")
    assert r.status_code == 202
    assert r.json()["state"] == "SECTION_REVIEWING"

    section_id = (await section_service.versions_of(conn, feuille))[0]
    section = (await client.get(f"/api/v1/projects/{pid}/sections/{section_id}")).json()

    assert section["status"] == SectionStatus.REVIEWING
    assert section["version"] == 1
    assert len(section["citations"]) == 1

    citation = section["citations"][0]
    assert citation["verified"] is True
    assert citation["bibtex_key"] == CLE_1
    assert citation["source_id"] == 1
    assert citation["chunk_id"] == chunk_ids[0]
    # Sans localisation, une citation n'est pas retrouvable dans le PDF.
    assert citation["locator"] == "p. 1-2"


async def test_version_incremented_per_generation(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Les versions anterieures restent lisibles : une reecriture peut etre
    moins bonne que la precedente."""
    pid, conn, feuille, chunk_ids = await projet_pret(client)
    brancher_modele(monkeypatch, [reponse_valide(chunk_ids[0])])

    await client.post(f"/api/v1/projects/{pid}/sections/{feuille}/draft")
    await client.post(f"/api/v1/projects/{pid}/sections/{feuille}/draft")

    sections = await section_service.versions_of(conn, feuille)
    assert len(sections) == 2

    versions = []
    for identifiant in sections:
        r = await client.get(f"/api/v1/projects/{pid}/sections/{identifiant}")
        assert r.status_code == 200, "une version anterieure reste lisible"
        versions.append(r.json()["version"])
    assert sorted(versions) == [1, 2]


# --- Edition manuelle -----------------------------------------------------


async def test_manual_edit_unverifies_removed_citations(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Une citation verifiee l'a ete contre un contenu qui n'existe plus."""
    pid, conn, feuille, chunk_ids = await projet_pret(client)
    brancher_modele(monkeypatch, [reponse_valide(chunk_ids[0])])
    await client.post(f"/api/v1/projects/{pid}/sections/{feuille}/draft")
    section_id = (await section_service.versions_of(conn, feuille))[0]

    r = await client.put(
        f"/api/v1/projects/{pid}/sections/{section_id}",
        json={"content_qmd": "Texte reecrit par l'auteur, sans aucune reference."},
    )

    assert r.status_code == 200
    assert r.json()["content_qmd"].startswith("Texte reecrit")
    assert [c["verified"] for c in r.json()["citations"]] == [False]


async def test_manual_edit_keeps_citations_still_present(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Une cle toujours citee dans le texte reste verifiee."""
    pid, conn, feuille, chunk_ids = await projet_pret(client)
    brancher_modele(monkeypatch, [reponse_valide(chunk_ids[0])])
    await client.post(f"/api/v1/projects/{pid}/sections/{feuille}/draft")
    section_id = (await section_service.versions_of(conn, feuille))[0]

    r = await client.put(
        f"/api/v1/projects/{pid}/sections/{section_id}",
        json={"content_qmd": f"Phrase remaniee, mais toujours sourcee [@{CLE_1}]."},
    )

    assert [c["verified"] for c in r.json()["citations"]] == [True]


async def test_manual_edit_never_deletes_citation_rows(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La trace qu'une affirmation a ete rattachee a une source est ce qu'un
    jury regarde : elle ne s'efface pas."""
    pid, conn, feuille, chunk_ids = await projet_pret(client)
    brancher_modele(monkeypatch, [reponse_valide(chunk_ids[0])])
    await client.post(f"/api/v1/projects/{pid}/sections/{feuille}/draft")
    section_id = (await section_service.versions_of(conn, feuille))[0]

    avant = await section_service.citations_of(conn, section_id)
    assert avant

    for contenu in ("Texte sans reference.", "Encore un autre texte.", "Et un troisieme."):
        await client.put(
            f"/api/v1/projects/{pid}/sections/{section_id}",
            json={"content_qmd": contenu},
        )

    apres = await section_service.citations_of(conn, section_id)
    assert [c.id for c in apres] == [c.id for c in avant]
    assert all(not c.verified for c in apres)


async def test_manual_edit_rejects_myst(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Du MyST colle ici casserait la compilation des semaines plus tard,
    sans que rien ne relie l'echec a cette edition (ADR-006)."""
    pid, conn, feuille, chunk_ids = await projet_pret(client)
    brancher_modele(monkeypatch, [reponse_valide(chunk_ids[0])])
    await client.post(f"/api/v1/projects/{pid}/sections/{feuille}/draft")
    section_id = (await section_service.versions_of(conn, feuille))[0]

    r = await client.put(
        f"/api/v1/projects/{pid}/sections/{section_id}",
        json={"content_qmd": ":::{note}\nUn encadre MyST.\n:::"},
    )
    assert r.status_code == 422


async def test_get_unknown_section_is_404(client: httpx.AsyncClient) -> None:
    pid, _, _, _ = await projet_pret(client)
    r = await client.get(f"/api/v1/projects/{pid}/sections/9999")
    assert r.status_code == 404


# --- Garde-fous en bout de chaine -----------------------------------------


@pytest.mark.parametrize(
    "fabrique",
    [reponse_cle_inventee, reponse_doi_invente],
    ids=["cle_inventee", "doi_invente"],
)
async def test_fabricated_reference_is_never_persisted(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, fabrique
) -> None:
    """Rendre malgre tout la derniere version reviendrait a livrer un texte
    dont on SAIT qu'il porte une reference inventee."""
    pid, conn, feuille, chunk_ids = await projet_pret(client)
    backend = brancher_modele(monkeypatch, [fabrique(chunk_ids[0])])

    r = await client.post(f"/api/v1/projects/{pid}/sections/{feuille}/draft")

    assert r.status_code == 422
    assert r.json()["code"] == "SECTION_GUARDRAIL_FAILED"
    assert await section_service.versions_of(conn, feuille) == []
    # Le MEME agent est relance, autant de fois que le breaker l'autorise.
    assert len(backend.users) == MAX_GUARDRAIL_RETRIES


@pytest.mark.parametrize("sortie", [MYST, HYPOTHESE_SOURCEE], ids=["myst", "hypothese_sourcee"])
async def test_invalid_output_is_rejected_not_repaired(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, sortie: str
) -> None:
    """ADR-008 : le guardrail valide ou rejette, il ne repare jamais."""
    pid, conn, feuille, _ = await projet_pret(client)
    brancher_modele(monkeypatch, [sortie])

    r = await client.post(f"/api/v1/projects/{pid}/sections/{feuille}/draft")

    assert r.status_code == 422
    assert await section_service.versions_of(conn, feuille) == []


async def test_second_attempt_succeeds_after_a_rejection(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un rejet coute un essai, il n'interrompt pas la redaction."""
    pid, conn, feuille, chunk_ids = await projet_pret(client)
    backend = brancher_modele(
        monkeypatch, [reponse_cle_inventee(chunk_ids[0]), reponse_valide(chunk_ids[0])]
    )

    r = await client.post(f"/api/v1/projects/{pid}/sections/{feuille}/draft")

    assert r.status_code == 202
    assert len(backend.users) == 2
    # Le message de correction nomme le jeton fautif.
    assert "src9_2020_inventee" in backend.users[1]
    assert len(await section_service.versions_of(conn, feuille)) == 1


async def test_rejection_is_audited(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un rejet non journalise est un rejet qu'on ne peut pas expliquer."""
    pid, conn, feuille, chunk_ids = await projet_pret(client)
    brancher_modele(monkeypatch, [reponse_cle_inventee(chunk_ids[0]), reponse_valide(chunk_ids[0])])
    await client.post(f"/api/v1/projects/{pid}/sections/{feuille}/draft")

    async with conn.execute(
        "SELECT count(*) FROM audit_log WHERE event_type = 'GUARDRAIL_TRIGGERED'"
    ) as cur:
        assert int((await cur.fetchone())[0]) == 1


# --- Constats de revue ----------------------------------------------------


async def test_validation_error_matches_contract_shape(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le contrat decrit toute reponse 422 par le schema `Error` : `code` et
    `message` obligatoires. FastAPI repondait `{"detail": [...]}` : le message
    ADR-006 sur MyST n'atteignait aucun client code contre le contrat."""
    pid, conn, feuille, chunk_ids = await projet_pret(client)
    brancher_modele(monkeypatch, [reponse_valide(chunk_ids[0])])
    await client.post(f"/api/v1/projects/{pid}/sections/{feuille}/draft")
    section_id = (await section_service.versions_of(conn, feuille))[0]

    r = await client.put(
        f"/api/v1/projects/{pid}/sections/{section_id}",
        json={"content_qmd": ":::{note}\nUn encadre MyST.\n:::"},
    )

    assert r.status_code == 422
    corps = r.json()
    assert corps["code"] == "VALIDATION_FAILED"
    assert "MyST" in corps["message"]
    assert "detail" not in corps


async def test_concurrent_drafts_get_distinct_versions(client: httpx.AsyncClient) -> None:
    """Des enregistrements simultanes sur la connexion partagee du projet
    produisaient soit un 500 brut, soit plusieurs versions n° 1 du meme noeud
    — dont l'export retenait l'une au hasard. Constat de revue, reproduit."""
    import asyncio

    from app.models.section import Claim, SectionDraft
    from app.rag.context_builder import SectionContext

    pid, conn, feuille, _ = await projet_pret(client)
    contexte = SectionContext(
        node_id=feuille,
        node_title="Noeud",
        node_objective="Objectif",
        target_words=1000,
        query="requete",
        chunks=[],
        allowed_keys=[],
        token_estimate=0,
    )
    brouillon = SectionDraft(
        content_qmd="Texte.",
        claims=[Claim(text="Point de synthese.", kind="synthesis")],
        word_count=1,
    )

    ids = await asyncio.gather(
        *(section_service.save_draft(conn, pid, feuille, brouillon, contexte) for _ in range(4))
    )

    versions = sorted([(await section_service.get(conn, i)).version for i in ids])
    assert versions == [1, 2, 3, 4]
