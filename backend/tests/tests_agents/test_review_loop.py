"""US-302 — mesures déterministes, boucle de correction et pause sur la meilleure.

Les mesures Python (longueur, proportion sourcée, diversité des sources) sont
testées pures ; la boucle est exercée de bout en bout contre un backend factice,
la rédaction de reprise étant substituée pour n'observer que l'orchestration de
la relecture — le rédacteur réel a sa propre story (US-301).

**Aucun chemin ne valide une section.** La relecture conseille ; la validation
est une porte humaine (ADR-004). Les tests le vérifient à trois niveaux : la
fonction de transition du graphe, la boucle de service, et la table des portes.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.agents import breaker
from app.agents.graph import review_target
from app.agents.state import HUMAN_GATES, WorkflowState
from app.core.config import get_settings
from app.db.session import transaction
from app.llm.manager import LLMManager
from app.models.plan import PlanStatus, PlanTree
from app.models.review import CATEGORIES, weighted_overall
from app.services import plan_service, review_service, section_service
from app.services.review_service import (
    DETERMINISTIC_SHARE,
    DeterministicMeasures,
    best_scored_section,
    blend,
    distinct_sources_score,
    length_ratio_score,
    sourced_ratio_score,
)
from tests.tests_agents.test_plan_agent import arbre_valide
from tests.tests_agents.test_reviewer_agent import finding, rapport
from tests.tests_agents.test_writer_agent import FakeBackend

NOW = datetime.now(UTC).isoformat()


# --- Mesures déterministes (pures) ----------------------------------------


def test_length_ratio_score_peaks_at_target_and_falls_off_both_sides() -> None:
    assert length_ratio_score(1000, 1000) == 100.0
    # Pente linéaire sur l'écart de ratio : le zéro n'est atteint qu'aux écarts
    # d'au moins 1,0 — texte vide ou double de la cible.
    assert length_ratio_score(0, 1000) == 0.0
    assert length_ratio_score(2000, 1000) == 0.0
    # À mi-cible, l'écart n'est que de 0,5 : la moitié du score.
    assert length_ratio_score(500, 1000) == 50.0
    # Écarts symétriques → même score : trop court et trop long, deux défauts.
    assert length_ratio_score(600, 1000) == length_ratio_score(1400, 1000) == 60.0
    # Cible absente : la longueur ne pénalise pas.
    assert length_ratio_score(10, 0) == 100.0


def test_sourced_ratio_score_is_a_plain_proportion() -> None:
    assert sourced_ratio_score(3, 4) == 75.0
    assert sourced_ratio_score(5, 5) == 100.0
    # Aucune affirmation : rien n'est sourcé.
    assert sourced_ratio_score(0, 0) == 0.0


def test_distinct_sources_score_saturates_at_three() -> None:
    assert distinct_sources_score(0) == 0.0
    assert distinct_sources_score(3) == 100.0
    assert distinct_sources_score(5) == 100.0
    assert distinct_sources_score(1) == round(100 / 3, 1)


def test_deterministic_measures_report_on_completeness_and_sourcing() -> None:
    m = DeterministicMeasures(
        measured_words=1000, target_words=1000, sourced=3, total=4, distinct=3
    )
    assert m.length == 100.0
    assert m.distinct == 100.0
    assert m.sourced_ratio == 75.0
    assert m.completeness == round((100.0 + 100.0) / 2, 1)
    assert m.sourcing == round((75.0 + 100.0) / 2, 1)


def test_blend_mixes_only_completeness_and_sourcing_half_and_half() -> None:
    """La part mesurée vaut la moitié de chacune de ces deux catégories ; les
    quatre autres — cohérence, argumentation, style, structure — relèvent du
    jugement et restent au modèle."""
    modele = {c: 100.0 for c in CATEGORIES}
    # Mesures au plancher : longueur, proportion et diversité toutes nulles.
    mesures = DeterministicMeasures(
        measured_words=0, target_words=1000, sourced=0, total=0, distinct=0
    )
    fondus = blend(modele, mesures)

    assert DETERMINISTIC_SHARE == 0.5
    assert fondus["completeness"] == 50.0  # 0.5 fois 100 + 0.5 fois 0
    assert fondus["sourcing"] == 50.0
    for intacte in ("coherence", "argumentation", "style", "structure"):
        assert fondus[intacte] == 100.0


# --- Transition de graphe : la relecture ne valide jamais -----------------


def test_review_never_targets_section_validated() -> None:
    """Aucune combinaison ne mène à SECTION_VALIDATED : la seule sortie vers cet
    état est la porte humaine (ADR-004)."""
    for verdict in ("ready", "needs_work", "insufficient"):
        for auto in (True, False):
            for tripped in (True, False):
                assert review_target(verdict, auto, tripped) is not WorkflowState.SECTION_VALIDATED


def test_review_target_routes_corrections_only_when_asked() -> None:
    # « prêt » : rien à corriger, on attend l'humain.
    assert review_target("ready", True, False) is None
    # Correction demandée et perfectible : retour au rédacteur.
    assert review_target("needs_work", True, False) is WorkflowState.SECTION_CORRECTING
    assert review_target("insufficient", True, False) is WorkflowState.SECTION_CORRECTING
    # Sans correction demandée : pause, même perfectible.
    assert review_target("needs_work", False, False) is None
    # Plafond atteint : pause quoi qu'il arrive.
    assert review_target("needs_work", True, True) is None


def test_section_validation_is_reserved_to_the_human_gate() -> None:
    assert (WorkflowState.SECTION_REVIEWING, WorkflowState.SECTION_VALIDATED) in HUMAN_GATES


# --- Échafaudage base : plan validé, section, citations -------------------


async def _sources(conn, nombre: int = 2) -> None:
    async with transaction(conn):
        for sid in range(1, nombre + 1):
            await conn.execute(
                "INSERT INTO source_document (id, project_id, kind, title, year, doi,"
                " is_preprint, imported_at, approved_at) VALUES (?,1,'article',?,?,NULL,0,?,?)",
                (sid, f"Source {sid}", 2020 + sid, NOW, NOW),
            )


def _premiere_feuille(plan):
    def descendre(noeuds):
        for noeud in noeuds:
            if not noeud.children:
                return noeud
            trouve = descendre(noeud.children)
            if trouve is not None:
                return trouve
        return None

    return descendre(plan.nodes)


async def _plan_feuille(conn) -> int:
    await plan_service.save_tree(
        conn, 1, PlanTree.model_validate(arbre_valide(1000)), PlanStatus.REVIEW
    )
    await plan_service.validate(conn, 1)
    plan = await plan_service._require_plan(conn, 1)
    return _premiere_feuille(plan).id


async def _section(conn, node_id, *, content, version, claims=2, sourced=1) -> int:
    async with transaction(conn):
        cur = await conn.execute(
            "INSERT INTO draft_section (plan_node_id, content_qmd, status, version,"
            " generated_at, claim_count, sourced_claim_count) VALUES (?,?,?,?,?,?,?)",
            (node_id, content, "REVIEWING", version, NOW, claims, sourced),
        )
        return int(cur.lastrowid or 0)


async def _citation(conn, section_id, source_id, key) -> None:
    async with transaction(conn):
        await conn.execute(
            "INSERT INTO citation (draft_section_id, source_id, chunk_id, bibtex_key,"
            " locator, verified) VALUES (?,?,NULL,?,NULL,1)",
            (section_id, source_id, key),
        )


async def _report_row(conn, section_id, overall, verdict="needs_work") -> None:
    async with transaction(conn):
        await conn.execute(
            "INSERT INTO review_report (draft_section_id, overall_score, verdict,"
            " scores_json, findings_json, auto_correct, created_at) VALUES (?,?,?,?,?,0,?)",
            (section_id, overall, verdict, json.dumps({c: overall for c in CATEGORIES}), "[]", NOW),
        )


# --- Meilleure version, relecture unitaire, boucle ------------------------


async def test_best_scored_section_returns_the_highest_not_the_latest(projet) -> None:
    """La pause propose la MEILLEURE version, pas la dernière : un 7B dégrade
    souvent sa sortie en corrigeant."""
    _, conn = projet
    feuille = await _plan_feuille(conn)
    v1 = await _section(conn, feuille, content="Version une.", version=1)
    v2 = await _section(conn, feuille, content="Version deux.", version=2)
    v3 = await _section(conn, feuille, content="Version trois.", version=3)
    await _report_row(conn, v1, 70.0)
    await _report_row(conn, v2, 40.0)
    await _report_row(conn, v3, 55.0)  # la plus récente n'est pas la meilleure

    assert await best_scored_section(conn, feuille) == v1


async def test_review_once_blends_scores_and_persists_a_report(projet) -> None:
    """La relecture fond les scores du modèle avec les mesures Python, recompose
    la note côté serveur, et la reporte sur la section."""
    _, conn = projet
    await _sources(conn, 2)
    feuille = await _plan_feuille(conn)
    contenu = "La filtration glomérulaire décroît nettement après exposition prolongée au polluant."
    section_id = await _section(conn, feuille, content=contenu, version=1, claims=4, sourced=3)
    await _citation(conn, section_id, 1, "src1")
    await _citation(conn, section_id, 2, "src2")

    constat = finding(excerpt="La filtration glomérulaire décroît nettement", category="sourcing")
    backend = FakeBackend([rapport(verdict="needs_work", findings=[constat])])

    resultat = await review_service.review_once(
        conn, 1, section_id, LLMManager(backend), auto_correct=False
    )

    assert resultat.verdict == "needs_work"
    # Note recomposée à partir des scores fondus et des poids de configuration.
    assert resultat.overall_score == weighted_overall(
        resultat.scores, get_settings().review_weights
    )
    # Persistée, et reportée sur la section pour une lecture sans rejouer la relecture.
    assert (await review_service.last_report(conn, section_id)).id == resultat.id
    assert (await section_service.get(conn, section_id)).quality_score == resultat.overall_score


async def test_run_review_without_auto_correct_renders_one_report(projet) -> None:
    """La correction n'est jamais implicite : sans `auto_correct`, la relecture
    rend un rapport et s'arrête, même quand le verdict appelle une reprise."""
    _, conn = projet
    await _sources(conn, 1)
    feuille = await _plan_feuille(conn)
    section_id = await _section(
        conn, feuille, content="Un texte à relire, assez long pour compter.", version=1
    )
    await _citation(conn, section_id, 1, "src1")

    backend = FakeBackend([rapport(verdict="needs_work")])
    resultat = await review_service.run_review(
        conn, 1, section_id, LLMManager(backend), auto_correct=False
    )

    assert len(backend.users) == 1  # une seule relecture, aucune reprise
    assert len(await section_service.versions_of(conn, feuille)) == 1  # aucune nouvelle version
    assert resultat.verdict == "needs_work"


async def test_run_review_caps_corrections_and_pauses_on_the_best_version(
    projet, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Trois allers-retours au plus (US-202), puis pause sur la meilleure version
    — pas la dernière —, toutes les versions conservées pour la comparaison.

    La rédaction de reprise est substituée : on n'observe ici que la boucle de
    relecture, pas le rédacteur réel, qui a sa propre story.
    """
    _, conn = projet
    await _sources(conn, 1)
    feuille = await _plan_feuille(conn)
    contenu = "La filtration décroît après exposition, selon plusieurs cohortes urbaines."
    origine = await _section(conn, feuille, content=contenu, version=1)
    await _citation(conn, origine, 1, "src1")

    appels_draft: list[int] = []

    async def faux_draft(conn, project_id, node_id, manager, task_id=None, correction=None):
        appels_draft.append(1)
        async with conn.execute(
            "SELECT COALESCE(MAX(version),0)+1 FROM draft_section WHERE plan_node_id=?",
            (node_id,),
        ) as cur:
            version = int((await cur.fetchone())[0])
        sid = await _section(
            conn, node_id, content=f"{contenu} (reprise {version})", version=version
        )
        return await section_service.get(conn, sid)

    monkeypatch.setattr(section_service, "draft_section", faux_draft)

    # v1 nettement la mieux notée : la pause doit revenir sur elle. Verdict
    # « needs_work » à chaque tour pour épuiser la boucle jusqu'au plafond.
    haut = rapport(verdict="needs_work", scores={c: 90.0 for c in CATEGORIES})
    bas = rapport(verdict="needs_work", scores={c: 20.0 for c in CATEGORIES})
    backend = FakeBackend([haut, bas, bas, bas])

    resultat = await review_service.run_review(
        conn, 1, origine, LLMManager(backend), auto_correct=True
    )

    # Une relecture initiale + trois reprises relues : quatre appels au modèle.
    assert len(backend.users) == 4
    # Boucle plafonnée à trois corrections.
    assert len(appels_draft) == breaker.MAX_REVIEW_LOOPS == 3
    # Toutes les versions conservées : origine + trois reprises.
    assert len(await section_service.versions_of(conn, feuille)) == 4
    # Pause sur la meilleure version rencontrée — l'origine —, pas la dernière.
    assert resultat.draft_section_id == origine
