"""US-201 — transitions declarees, portes humaines, persistance. ADR-004."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from app.agents.graph import build_langgraph, describe_graph
from app.agents.state import (
    HUMAN_GATES,
    TERMINAL_STATES,
    TRANSITIONS,
    GraphState,
    WorkflowState,
    allowed_targets,
    automatic_targets,
    initial_state,
)
from app.core.errors import InvalidTransitionError
from app.services import task_service, workflow_service

from .conftest import count_audit

CONTRAT = Path(__file__).resolve().parents[3] / "contracts" / "openapi.yaml"


# --- Conformite au contrat ------------------------------------------------


def test_workflow_state_matches_the_contract() -> None:
    """`WorkflowState` reprend EXACTEMENT l'enumeration du contrat."""
    schema = yaml.safe_load(CONTRAT.read_text(encoding="utf-8"))
    attendus = schema["components"]["schemas"]["TaskState"]["enum"]
    assert [e.value for e in WorkflowState] == attendus


def test_task_state_alias_is_the_same_enum() -> None:
    """Deux enumerations partielles du meme contrat finiraient par diverger."""
    from app.models.source import TaskState

    assert TaskState is WorkflowState


# --- Transitions ----------------------------------------------------------


async def test_declared_transition_succeeds(projet, etat: GraphState) -> None:
    _, conn = projet
    task_id = await workflow_service.start(conn, project_id=1)
    apres = await workflow_service.transition(conn, task_id, etat, WorkflowState.SOURCES_INGESTING)
    assert apres["state"] is WorkflowState.SOURCES_INGESTING


async def test_undeclared_transition_raises(projet, etat: GraphState) -> None:
    """Le graphe refuse, il ne s'adapte pas."""
    _, conn = projet
    task_id = await workflow_service.start(conn, project_id=1)
    with pytest.raises(InvalidTransitionError) as exc:
        await workflow_service.transition(conn, task_id, etat, WorkflowState.EXPORTED)

    assert "non déclarée" in exc.value.message
    assert "SOURCES_INGESTING" in exc.value.message, "les cibles admises doivent être nommées"
    assert etat["state"] is WorkflowState.PROJECT_CREATED, "l'état ne doit pas avoir bougé"


async def test_transition_persisted_before_returning(projet, etat: GraphState) -> None:
    """Retourner avant l'ecriture laisserait le graphe en avance sur sa trace."""
    _, conn = projet
    task_id = await workflow_service.start(conn, project_id=1)
    await workflow_service.transition(conn, task_id, etat, WorkflowState.SOURCES_INGESTING)

    relu = await workflow_service.load_state(conn, task_id)
    assert relu is not None
    assert relu["state"] is WorkflowState.SOURCES_INGESTING


async def test_transition_writes_audit_entry(projet, etat: GraphState) -> None:
    """Une transition non journalisee est une transition qui n'a pas eu lieu."""
    _, conn = projet
    task_id = await workflow_service.start(conn, project_id=1)
    avant = await count_audit(conn, "STATE_TRANSITION")
    await workflow_service.transition(conn, task_id, etat, WorkflowState.SOURCES_INGESTING)
    assert await count_audit(conn, "STATE_TRANSITION") == avant + 1


async def test_failed_transition_writes_nothing(projet, etat: GraphState) -> None:
    _, conn = projet
    task_id = await workflow_service.start(conn, project_id=1)
    avant = await count_audit(conn, "STATE_TRANSITION")
    with pytest.raises(InvalidTransitionError):
        await workflow_service.transition(conn, task_id, etat, WorkflowState.EXPORTED)
    assert await count_audit(conn, "STATE_TRANSITION") == avant


# --- Portes humaines ------------------------------------------------------


def test_no_automatic_path_reaches_plan_validated() -> None:
    """Aucun chemin automatique ne mene a PLAN_VALIDATED (S5.2)."""
    atteignables = {cible for depuis in WorkflowState for cible in automatic_targets(depuis)}
    assert WorkflowState.PLAN_VALIDATED not in atteignables


def test_no_automatic_path_reaches_section_validated() -> None:
    atteignables = {cible for depuis in WorkflowState for cible in automatic_targets(depuis)}
    assert WorkflowState.SECTION_VALIDATED not in atteignables


async def test_human_gate_refuses_automatic_crossing(projet) -> None:
    _, conn = projet
    task_id = await workflow_service.start(conn, project_id=1)
    etat = initial_state(1)
    etat["state"] = WorkflowState.PLAN_REVIEW

    with pytest.raises(InvalidTransitionError) as exc:
        await workflow_service.transition(conn, task_id, etat, WorkflowState.PLAN_VALIDATED)
    assert "validation humaine" in exc.value.message
    assert "human_validate" in exc.value.message


async def test_human_validate_crosses_gate(projet) -> None:
    _, conn = projet
    task_id = await workflow_service.start(conn, project_id=1)
    etat = initial_state(1)
    etat["state"] = WorkflowState.PLAN_REVIEW

    apres = await workflow_service.human_validate(conn, task_id, etat, WorkflowState.PLAN_VALIDATED)
    assert apres["state"] is WorkflowState.PLAN_VALIDATED
    assert await count_audit(conn, "HUMAN_VALIDATION") == 1


def test_human_validate_takes_no_quality_score() -> None:
    """Une validation qui dependrait d'un score serait automatisable."""
    import inspect

    parametres = set(inspect.signature(workflow_service.human_validate).parameters)
    assert not {"score", "quality_score", "quality"} & parametres


# --- ERROR_STATE ----------------------------------------------------------


def test_error_state_has_no_automatic_outgoing_transition() -> None:
    assert allowed_targets(WorkflowState.ERROR_STATE) == frozenset()
    assert WorkflowState.ERROR_STATE in TERMINAL_STATES


def test_exported_is_terminal_too() -> None:
    assert allowed_targets(WorkflowState.EXPORTED) == frozenset()


async def test_fail_records_breaker_and_reaches_error_state(projet) -> None:
    _, conn = projet
    task_id = await workflow_service.start(conn, project_id=1)
    etat = initial_state(1)
    etat["state"] = WorkflowState.SOURCES_INGESTING

    apres = await workflow_service.fail(conn, task_id, etat, "source illisible")
    assert apres["state"] is WorkflowState.ERROR_STATE
    assert await count_audit(conn, "BREAKER_TRIGGERED") == 1
    types = [e.type for e in task_service.drain(task_id)]
    assert "error" in types and "done" in types


# --- Serialisation --------------------------------------------------------


def test_graph_state_is_json_serializable() -> None:
    """Aucun objet vivant : c'est la condition de la reprise."""
    etat = initial_state(project_id=7)
    etat["payload_json"] = {"best_version": {"content": "x", "score": 0.8}}
    texte = json.dumps({**etat, "state": str(etat["state"])}, allow_nan=False)
    assert json.loads(texte)["project_id"] == 7


def test_every_state_appears_in_the_transition_table() -> None:
    assert set(TRANSITIONS) == set(WorkflowState)


def test_all_targets_are_known_states() -> None:
    for cibles in TRANSITIONS.values():
        assert all(c in WorkflowState for c in cibles)


# --- Graphe LangGraph -----------------------------------------------------


def test_langgraph_is_derived_from_the_transition_table() -> None:
    """Le graphe est derive, jamais recopie : une seconde source de verite
    serait une occasion de les voir diverger en silence (ADR-004)."""
    graphe = build_langgraph()
    assert set(graphe.nodes) >= {e.value for e in WorkflowState}
    graphe.compile()


def test_describe_graph_matches_the_table() -> None:
    description = describe_graph()
    assert set(description) == {e.value for e in WorkflowState}
    assert description["PLAN_GUARDRAIL"] == ["ERROR_STATE", "PLAN_DRAFTING", "PLAN_REVIEW"]


def test_human_gates_are_declared_transitions() -> None:
    """Une porte doit exister dans la table, sinon elle serait infranchissable."""
    for depuis, vers in HUMAN_GATES:
        assert vers in allowed_targets(depuis)
