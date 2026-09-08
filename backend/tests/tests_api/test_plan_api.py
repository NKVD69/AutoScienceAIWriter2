"""US-PLAN-001 — API du plan : edition, porte humaine, verrou de redaction."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.agents.state import WorkflowState, initial_state
from app.core.config import get_settings
from app.core.errors import InvalidTransitionError
from app.db import registry
from app.db.pool import get_pool, reset_pool
from app.main import create_app
from app.models.plan import PlanStatus, PlanTree, SectionStatus
from app.services import plan_service, workflow_service
from tests.tests_agents.test_plan_agent import arbre_valide

PROJET = {
    "name": "Thèse microplastiques",
    "subject": "Impact des microplastiques sur la fonction rénale",
    "language": "fr",
    "academic_level": "doctorat",
    "target_words": 60000,
}


@pytest.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path)
    await reset_pool()
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
        yield c
    await reset_pool()


async def projet_avec_plan(client: httpx.AsyncClient) -> tuple[int, object]:
    """Projet + plan enregistre directement : la generation passe par le
    modele, ce qui n'a pas sa place dans un test d'API."""
    pid = (await client.post("/api/v1/projects", json=PROJET)).json()["id"]
    async with registry.connect_registry() as reg:
        ref = await registry.get(reg, pid)
    conn = await get_pool().acquire(pid, ref.db_path)
    await plan_service.save_tree(
        conn, pid, PlanTree.model_validate(arbre_valide()), PlanStatus.REVIEW
    )
    return pid, conn


# --- Lecture --------------------------------------------------------------


async def test_get_plan_returns_the_tree(client: httpx.AsyncClient) -> None:
    pid, _ = await projet_avec_plan(client)
    r = await client.get(f"/api/v1/projects/{pid}/plan")
    assert r.status_code == 200
    plan = r.json()
    assert plan["status"] == "REVIEW"
    assert len(plan["nodes"]) == 4
    assert len(plan["nodes"][0]["children"]) == 2
    assert plan["nodes"][0]["level"] == 1


async def test_get_plan_404_when_absent(client: httpx.AsyncClient) -> None:
    pid = (await client.post("/api/v1/projects", json=PROJET)).json()["id"]
    r = await client.get(f"/api/v1/projects/{pid}/plan")
    assert r.status_code == 404
    assert "générer" in r.json()["message"]


async def test_versions_history(client: httpx.AsyncClient) -> None:
    pid, conn = await projet_avec_plan(client)
    await plan_service.save_tree(
        conn, pid, PlanTree.model_validate(arbre_valide()), PlanStatus.REVIEW
    )
    versions = (await client.get(f"/api/v1/projects/{pid}/plan/versions")).json()
    assert [v["version"] for v in versions] == [2, 1]


# --- Edition --------------------------------------------------------------


async def test_update_node_renames_and_bumps_version(client: httpx.AsyncClient) -> None:
    pid, _ = await projet_avec_plan(client)
    plan = (await client.get(f"/api/v1/projects/{pid}/plan")).json()
    node_id = plan["nodes"][0]["id"]

    r = await client.put(
        f"/api/v1/projects/{pid}/plan/nodes/{node_id}",
        json={"title": "Titre revu par l'auteur"},
    )
    assert r.status_code == 200
    assert r.json()["nodes"][0]["title"] == "Titre revu par l'auteur"
    assert r.json()["version"] == plan["version"] + 1


async def test_add_node(client: httpx.AsyncClient) -> None:
    pid, _ = await projet_avec_plan(client)
    r = await client.post(
        f"/api/v1/projects/{pid}/plan/nodes",
        json={
            "title": "Chapitre ajouté",
            "objective": "Établir un point que le modèle avait omis.",
            "target_words": 3000,
        },
    )
    assert r.status_code == 201
    assert len(r.json()["nodes"]) == 5


async def test_reorder(client: httpx.AsyncClient) -> None:
    pid, _ = await projet_avec_plan(client)
    plan = (await client.get(f"/api/v1/projects/{pid}/plan")).json()
    premier, dernier = plan["nodes"][0]["id"], plan["nodes"][3]["id"]

    r = await client.post(
        f"/api/v1/projects/{pid}/plan/reorder",
        json={
            "items": [
                {"node_id": premier, "parent_id": None, "ordinal": 3},
                {"node_id": dernier, "parent_id": None, "ordinal": 0},
            ]
        },
    )
    assert r.status_code == 200
    assert r.json()["nodes"][0]["id"] == dernier


# --- Suppression ----------------------------------------------------------


async def test_delete_node_with_sections_returns_409(client: httpx.AsyncClient) -> None:
    pid, conn = await projet_avec_plan(client)
    plan = (await client.get(f"/api/v1/projects/{pid}/plan")).json()
    feuille = plan["nodes"][0]["children"][0]["children"][0]["id"]

    from app.db.session import transaction

    async with transaction(conn):
        await conn.execute(
            "INSERT INTO draft_section (plan_node_id, content_qmd, status)"
            " VALUES (?, 'Texte rédigé.', ?)",
            (feuille, str(SectionStatus.VALIDATED)),
        )

    r = await client.delete(f"/api/v1/projects/{pid}/plan/nodes/{plan['nodes'][0]['id']}")
    assert r.status_code == 409
    assert r.json()["current_state"] == "has_sections"


async def test_delete_node_force_keeps_the_text(client: httpx.AsyncClient) -> None:
    pid, conn = await projet_avec_plan(client)
    plan = (await client.get(f"/api/v1/projects/{pid}/plan")).json()
    feuille = plan["nodes"][0]["children"][0]["children"][0]["id"]

    from app.db.session import transaction

    async with transaction(conn):
        await conn.execute(
            "INSERT INTO draft_section (plan_node_id, content_qmd, status)"
            " VALUES (?, 'Texte rédigé.', ?)",
            (feuille, str(SectionStatus.VALIDATED)),
        )

    r = await client.delete(
        f"/api/v1/projects/{pid}/plan/nodes/{plan['nodes'][0]['id']}?force=true"
    )
    assert r.status_code == 200
    assert len(r.json()["nodes"]) == 3

    async with conn.execute("SELECT status, content_qmd FROM draft_section") as cur:
        statut, contenu = await cur.fetchone()
    assert statut == SectionStatus.ORPHANED
    assert contenu == "Texte rédigé.", "le texte doit survivre à la suppression du nœud"


# --- Porte humaine et verrou ----------------------------------------------


async def test_validate_crosses_the_human_gate(client: httpx.AsyncClient) -> None:
    pid, conn = await projet_avec_plan(client)
    r = await client.post(f"/api/v1/projects/{pid}/plan/validate")
    assert r.status_code == 200
    assert r.json()["status"] == "VALIDATED"
    assert r.json()["validated_at"] is not None

    plan = await plan_service.assert_writable(conn, pid)
    assert plan.status is PlanStatus.VALIDATED


async def test_writing_blocked_before_validation_via_service(client: httpx.AsyncClient) -> None:
    """Le controle vit dans le service : un appel interne est bloque aussi."""
    from app.core.errors import ConflictError

    pid, conn = await projet_avec_plan(client)
    with pytest.raises(ConflictError) as exc:
        await plan_service.assert_writable(conn, pid)
    assert exc.value.required_state == "VALIDATED"


async def test_human_gate_cannot_be_crossed_automatically(client: httpx.AsyncClient) -> None:
    """Aucun score, aucune configuration ne franchit PLAN_REVIEW (S5.2)."""
    pid, conn = await projet_avec_plan(client)
    task_id = await workflow_service.start(conn, project_id=pid)
    etat = initial_state(pid)
    etat["state"] = WorkflowState.PLAN_REVIEW

    with pytest.raises(InvalidTransitionError):
        await workflow_service.transition(conn, task_id, etat, WorkflowState.PLAN_VALIDATED)

    # Et la validation du plan ne franchit pas non plus la porte du graphe :
    # les deux mecanismes sont distincts et le restent.
    await client.post(f"/api/v1/projects/{pid}/plan/validate")
    relu = await workflow_service.load_state(conn, task_id)
    assert relu["state"] is not WorkflowState.PLAN_VALIDATED


async def test_edit_after_validation_blocks_writing_again(client: httpx.AsyncClient) -> None:
    from app.core.errors import ConflictError

    pid, conn = await projet_avec_plan(client)
    await client.post(f"/api/v1/projects/{pid}/plan/validate")
    plan = (await client.get(f"/api/v1/projects/{pid}/plan")).json()

    r = await client.put(
        f"/api/v1/projects/{pid}/plan/nodes/{plan['nodes'][0]['id']}",
        json={"target_words": 2500},
    )
    assert r.json()["status"] == "DRAFT"

    with pytest.raises(ConflictError):
        await plan_service.assert_writable(conn, pid)


async def test_validation_is_recorded_in_the_audit_log(client: httpx.AsyncClient) -> None:
    pid, _ = await projet_avec_plan(client)
    await client.post(f"/api/v1/projects/{pid}/plan/validate")
    evenements = [
        e["event_type"] for e in (await client.get(f"/api/v1/projects/{pid}/audit")).json()
    ]
    assert "HUMAN_VALIDATION" in evenements
