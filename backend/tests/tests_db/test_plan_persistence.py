"""US-PLAN-001 — arbre aplati, versionnement, sections orphelines."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.errors import ConflictError
from app.db.migrations.runner import run_migrations
from app.db.session import connect, transaction
from app.models.plan import (
    PlanNodeCreate,
    PlanNodeUpdate,
    PlanStatus,
    PlanTree,
    ReorderItem,
    ReorderRequest,
    SectionStatus,
)
from app.services import plan_service
from tests.tests_agents.test_plan_agent import arbre_valide

NOW = datetime.now(UTC).isoformat()


@pytest.fixture
async def conn(project_db: Path):
    async with connect(project_db) as connexion:
        await run_migrations(connexion)
        async with transaction(connexion):
            await connexion.execute(
                "INSERT INTO project (id, name, subject, language, academic_level,"
                " target_words, created_at, updated_at)"
                " VALUES (1,'These','Sujet','fr','doctorat',60000,?,?)",
                (NOW, NOW),
            )
        yield connexion


async def enregistrer(connexion, status: PlanStatus = PlanStatus.REVIEW) -> int:
    arbre = PlanTree.model_validate(arbre_valide())
    return await plan_service.save_tree(connexion, 1, arbre, status)


async def redige_une_section(connexion, node_id: int) -> int:
    async with transaction(connexion):
        cur = await connexion.execute(
            "INSERT INTO draft_section (plan_node_id, content_qmd, status, generated_at)"
            " VALUES (?, 'Du texte redige.', ?, ?)",
            (node_id, str(SectionStatus.VALIDATED), NOW),
        )
    return int(cur.lastrowid)


# --- Aplatissement --------------------------------------------------------


async def test_plan_persisted_as_flat_tree(conn) -> None:
    """`parent_id`, `ordinal`, `level` : une hierarchie en JSON serait
    illisible aux requetes qui en ont besoin."""
    await enregistrer(conn)

    async with conn.execute(
        "SELECT level, count(*) FROM plan_node GROUP BY level ORDER BY level"
    ) as cur:
        par_niveau = {int(r[0]): int(r[1]) for r in await cur.fetchall()}

    assert par_niveau == {1: 4, 2: 8, 3: 16}, "4 chapitres, 8 sections, 16 sous-sections"

    async with conn.execute(
        "SELECT count(*) FROM plan_node WHERE level = 1 AND parent_id IS NOT NULL"
    ) as cur:
        assert int((await cur.fetchone())[0]) == 0, "les racines n'ont pas de parent"


async def test_ordinals_are_contiguous_per_parent(conn) -> None:
    await enregistrer(conn)
    async with conn.execute(
        "SELECT parent_id, group_concat(ordinal) FROM plan_node GROUP BY parent_id"
    ) as cur:
        for _, ordinaux in await cur.fetchall():
            valeurs = sorted(int(x) for x in str(ordinaux).split(","))
            assert valeurs == list(range(len(valeurs)))


async def test_plan_out_rebuilds_the_tree(conn) -> None:
    await enregistrer(conn)
    plan = await plan_service.current_plan(conn, 1)
    assert plan is not None
    assert len(plan.nodes) == 4
    assert len(plan.nodes[0].children) == 2
    assert len(plan.nodes[0].children[0].children) == 2
    assert plan.status is PlanStatus.REVIEW


# --- Versionnement --------------------------------------------------------


async def test_plan_version_incremented_on_edit(conn) -> None:
    await enregistrer(conn)
    avant = (await plan_service.current_plan(conn, 1)).version

    plan = await plan_service.current_plan(conn, 1)
    apres = await plan_service.update_node(
        conn, 1, plan.nodes[0].id, PlanNodeUpdate(title="Chapitre renomme")
    )
    assert apres.version == avant + 1
    assert apres.nodes[0].title == "Chapitre renomme"


async def test_second_generation_creates_a_new_version(conn) -> None:
    """Les versions anterieures restent lisibles : revenir en arriere est un
    geste normal sur un plan."""
    await enregistrer(conn)
    await enregistrer(conn)
    historique = await plan_service.versions(conn, 1)
    assert [v.version for v in historique] == [2, 1]
    assert all(v.node_count == 28 for v in historique)


async def test_validated_plan_returns_to_draft_on_edit(conn) -> None:
    """Sans cela, la redaction s'appuierait sur une validation qui ne porte
    plus sur la structure en base."""
    await enregistrer(conn)
    valide = await plan_service.validate(conn, 1)
    assert valide.status is PlanStatus.VALIDATED

    apres = await plan_service.update_node(
        conn, 1, valide.nodes[0].id, PlanNodeUpdate(target_words=3000)
    )
    assert apres.status is PlanStatus.DRAFT


async def test_add_node_also_demotes_a_validated_plan(conn) -> None:
    await enregistrer(conn)
    await plan_service.validate(conn, 1)
    apres = await plan_service.add_node(
        conn,
        1,
        PlanNodeCreate(
            title="Chapitre ajoute", objective="Etablir un point neuf.", target_words=2000
        ),
    )
    assert apres.status is PlanStatus.DRAFT
    assert len(apres.nodes) == 5


# --- Suppression et sections orphelines -----------------------------------


async def test_delete_node_with_sections_returns_409(conn) -> None:
    await enregistrer(conn)
    plan = await plan_service.current_plan(conn, 1)
    feuille = plan.nodes[0].children[0].children[0]
    section_id = await redige_une_section(conn, feuille.id)

    with pytest.raises(ConflictError) as exc:
        await plan_service.delete_node(conn, 1, plan.nodes[0].id)

    assert str(section_id) in exc.value.message
    assert exc.value.current_state == "has_sections"
    assert "force=true" in exc.value.message


async def test_delete_node_force_marks_sections_orphaned(conn) -> None:
    """Supprimer silencieusement un texte redige serait detruire du travail."""
    await enregistrer(conn)
    plan = await plan_service.current_plan(conn, 1)
    feuille = plan.nodes[0].children[0].children[0]
    section_id = await redige_une_section(conn, feuille.id)

    apres = await plan_service.delete_node(conn, 1, plan.nodes[0].id, force=True)
    assert len(apres.nodes) == 3

    async with conn.execute(
        "SELECT status, content_qmd FROM draft_section WHERE id = ?", (section_id,)
    ) as cur:
        statut, contenu = await cur.fetchone()
    assert statut == SectionStatus.ORPHANED
    assert contenu == "Du texte redige.", "le texte doit survivre à la suppression du nœud"


async def test_delete_node_without_sections_needs_no_force(conn) -> None:
    await enregistrer(conn)
    plan = await plan_service.current_plan(conn, 1)
    apres = await plan_service.delete_node(conn, 1, plan.nodes[3].id)
    assert len(apres.nodes) == 3


async def test_delete_cascades_to_the_whole_subtree(conn) -> None:
    await enregistrer(conn)
    plan = await plan_service.current_plan(conn, 1)
    await plan_service.delete_node(conn, 1, plan.nodes[0].id)

    async with conn.execute("SELECT count(*) FROM plan_node") as cur:
        # 28 nœuds au total, 7 dans le sous-arbre supprimé.
        assert int((await cur.fetchone())[0]) == 21


# --- Reordonnancement -----------------------------------------------------


async def test_reorder_preserves_children(conn) -> None:
    """Les enfants suivent leur parent : les reordonner ne les deplace pas."""
    await enregistrer(conn)
    plan = await plan_service.current_plan(conn, 1)
    premier, dernier = plan.nodes[0], plan.nodes[3]
    enfants_avant = [c.id for c in premier.children]

    apres = await plan_service.reorder(
        conn,
        1,
        ReorderRequest(
            items=[
                ReorderItem(node_id=premier.id, parent_id=None, ordinal=3),
                ReorderItem(node_id=dernier.id, parent_id=None, ordinal=0),
            ]
        ),
    )

    assert apres.nodes[0].id == dernier.id
    assert apres.nodes[3].id == premier.id
    deplace = next(n for n in apres.nodes if n.id == premier.id)
    assert [c.id for c in deplace.children] == enfants_avant


# --- Verrou de redaction --------------------------------------------------


async def test_writing_blocked_before_validation_via_service(conn) -> None:
    """Le controle vit dans le service : un appel interne du graphe doit
    etre bloque comme un appel HTTP."""
    await enregistrer(conn, status=PlanStatus.REVIEW)
    with pytest.raises(ConflictError) as exc:
        await plan_service.assert_writable(conn, 1)

    assert exc.value.current_state == "REVIEW"
    assert exc.value.required_state == "VALIDATED"
    assert exc.value.status_code == 409


async def test_writing_blocked_when_no_plan_exists(conn) -> None:
    with pytest.raises(ConflictError) as exc:
        await plan_service.assert_writable(conn, 1)
    assert exc.value.current_state == "NO_PLAN"


async def test_writing_allowed_after_validation(conn) -> None:
    await enregistrer(conn)
    await plan_service.validate(conn, 1)
    plan = await plan_service.assert_writable(conn, 1)
    assert plan.status is PlanStatus.VALIDATED


async def test_writing_blocked_again_after_an_edit(conn) -> None:
    """Un plan modifie apres validation ne doit plus autoriser la redaction."""
    await enregistrer(conn)
    valide = await plan_service.validate(conn, 1)
    await plan_service.update_node(conn, 1, valide.nodes[0].id, PlanNodeUpdate(target_words=2000))

    with pytest.raises(ConflictError):
        await plan_service.assert_writable(conn, 1)


async def test_validation_is_audited(conn) -> None:
    await enregistrer(conn)
    await plan_service.validate(conn, 1, validated_by="doctorant")
    async with conn.execute(
        "SELECT payload_json FROM audit_log WHERE event_type = 'HUMAN_VALIDATION'"
    ) as cur:
        charges = [str(r[0]) for r in await cur.fetchall()]
    assert any("doctorant" in c for c in charges)
