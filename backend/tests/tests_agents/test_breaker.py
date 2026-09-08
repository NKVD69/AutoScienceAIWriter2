"""US-202 — compteurs dans l'etat, pause sur la meilleure version. ADR-008."""

from __future__ import annotations

import inspect

import pytest

from app.agents import breaker
from app.agents.breaker import (
    BEST_VERSION_KEY,
    MAX_GUARDRAIL_RETRIES,
    MAX_INGEST_RETRIES,
    MAX_REVIEW_LOOPS,
    best_version,
    record_review_version,
    register_guardrail_failure,
    register_ingest_failure,
    register_review_loop,
    reset_node_counters,
)
from app.agents.state import GraphState, WorkflowState, initial_state
from app.services import workflow_service

# --- Ou vivent les compteurs ----------------------------------------------


def test_breaker_counters_live_in_state_not_module() -> None:
    """Un compteur en memoire disparait au redemarrage : la boucle
    repartirait a zero et tournerait indefiniment.

    Le controle porte sur l'AST, non sur le texte : une ligne comme
    `@dataclass(frozen=True)` contient un signe egal sans rien affecter, et
    un test qui s'y tromperait serait desactive a la premiere correction.
    """
    import ast

    arbre = ast.parse(inspect.getsource(breaker))
    noms: list[str] = []
    for noeud in arbre.body:
        if isinstance(noeud, ast.Assign):
            noms += [t.id for t in noeud.targets if isinstance(t, ast.Name)]
        elif isinstance(noeud, ast.AnnAssign) and isinstance(noeud.target, ast.Name):
            noms.append(noeud.target.id)

    assert noms, "le module devrait déclarer au moins ses limites"
    for nom in noms:
        assert nom.isupper() or nom == "logger", f"état mutable au niveau du module : {nom}"


def test_counters_are_fields_of_graph_state(etat: GraphState) -> None:
    for champ in ("retry_count", "review_loop_count", "ingest_retry_count"):
        assert champ in etat


# --- Guardrail ------------------------------------------------------------


def test_guardrail_error_state_after_three_retries(etat: GraphState) -> None:
    for essai in range(1, MAX_GUARDRAIL_RETRIES):
        decision = register_guardrail_failure(etat, f"erreur {essai}")
        assert not decision.tripped, f"le breaker s'est déclenché à l'essai {essai}"

    decision = register_guardrail_failure(etat, "erreur finale")
    assert decision.tripped
    assert decision.next_state is WorkflowState.ERROR_STATE
    assert "erreur finale" in decision.reason, "la dernière erreur doit être exposée"
    assert etat["retry_count"] == MAX_GUARDRAIL_RETRIES


def test_guardrail_records_last_error(etat: GraphState) -> None:
    register_guardrail_failure(etat, "champ « poids » hors bornes")
    assert etat["last_error"] == "champ « poids » hors bornes"


# --- Boucle de relecture --------------------------------------------------


def test_review_loop_pauses_after_three_iterations(etat: GraphState) -> None:
    for _ in range(MAX_REVIEW_LOOPS - 1):
        assert not register_review_loop(etat).tripped

    decision = register_review_loop(etat)
    assert decision.tripped
    # Pause, pas ERROR_STATE : le travail existe, il attend un arbitrage.
    assert decision.next_state is None
    assert "sans convergence" in decision.reason


def test_review_pause_returns_best_version_not_last(etat: GraphState) -> None:
    """Une boucle de correction ne converge pas necessairement : la
    troisieme version peut etre moins bonne que la premiere."""
    record_review_version(etat, "version 1, plutot bonne", score=0.82)
    etat["review_loop_count"] = 1
    record_review_version(etat, "version 2, moins bonne", score=0.55)
    etat["review_loop_count"] = 2
    record_review_version(etat, "version 3, la pire", score=0.31)

    meilleure = best_version(etat)
    assert meilleure["content"] == "version 1, plutot bonne"
    assert meilleure["score"] == 0.82
    assert meilleure["iteration"] == 0


def test_best_version_survives_serialization(etat: GraphState) -> None:
    import json

    record_review_version(etat, "contenu", score=0.9)
    relu = json.loads(json.dumps(etat["payload_json"]))
    assert relu[BEST_VERSION_KEY]["score"] == 0.9


# --- Ingestion ------------------------------------------------------------


def test_ingest_retries_twice_then_marks_failed(etat: GraphState) -> None:
    assert not register_ingest_failure(etat).tripped
    decision = register_ingest_failure(etat)
    assert decision.tripped
    # Pas d'ERROR_STATE : une source illisible ne bloque pas les autres.
    assert decision.next_state is None
    assert etat["ingest_retry_count"] == MAX_INGEST_RETRIES


# --- Remise a zero --------------------------------------------------------


def test_counters_reset_on_new_node(etat: GraphState) -> None:
    etat["retry_count"] = 2
    etat["review_loop_count"] = 2
    etat["ingest_retry_count"] = 1
    etat["last_error"] = "quelque chose"
    record_review_version(etat, "ancien contenu", score=0.7)

    reset_node_counters(etat)

    assert etat["retry_count"] == 0
    assert etat["review_loop_count"] == 0
    assert etat["ingest_retry_count"] == 0
    assert etat["last_error"] is None
    assert best_version(etat) is None, "la meilleure version du nœud précédent doit partir"


def test_reset_keeps_the_rest_of_the_payload(etat: GraphState) -> None:
    """La remise a zero est locale au nœud, jamais globale."""
    etat["payload_json"]["plan_id"] = 12
    etat["payload_json"]["autre_donnee"] = "à conserver"
    reset_node_counters(etat)
    assert etat["payload_json"]["plan_id"] == 12
    assert etat["payload_json"]["autre_donnee"] == "à conserver"


# --- Survie au redemarrage ------------------------------------------------


async def test_counters_survive_restart(projet) -> None:
    """C'est tout l'objet de US-202 : sans persistance, la boucle repart."""
    _, conn = projet
    task_id = await workflow_service.start(conn, project_id=1)

    etat = initial_state(1)
    register_guardrail_failure(etat, "premier rejet")
    register_guardrail_failure(etat, "deuxieme rejet")
    record_review_version(etat, "meilleure version", score=0.91)
    etat["review_loop_count"] = 1
    async with __import__("app.db.session", fromlist=["transaction"]).transaction(conn):
        await workflow_service.save_state(conn, task_id, etat)

    # Redemarrage : plus rien en memoire, tout vient de la base.
    relu = await workflow_service.load_state(conn, task_id)
    assert relu is not None
    assert relu["retry_count"] == 2
    assert relu["review_loop_count"] == 1
    assert relu["last_error"] == "deuxieme rejet"
    assert best_version(relu)["score"] == 0.91

    # Un troisieme rejet apres redemarrage doit declencher, pas repartir.
    decision = register_guardrail_failure(relu, "troisieme rejet")
    assert decision.tripped, "les compteurs sont repartis de zéro après redémarrage"


def test_limits_match_the_specification() -> None:
    """§5.3 : 3 essais de guardrail, 3 boucles de relecture, 2 essais d'ingestion."""
    assert (MAX_GUARDRAIL_RETRIES, MAX_REVIEW_LOOPS, MAX_INGEST_RETRIES) == (3, 3, 2)


@pytest.mark.parametrize("compteur", ["retry_count", "review_loop_count", "ingest_retry_count"])
def test_counters_start_at_zero(compteur: str) -> None:
    assert initial_state(1)[compteur] == 0
