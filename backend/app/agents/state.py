"""États du graphe et état partagé — US-201, ADR-004, spécifications §5.

`WorkflowState` reprend **exactement** l'énumération `TaskState` du contrat :
aucun état ajouté, aucun renommage. Un test compare les deux à
`contracts/openapi.yaml` lui-même, qui est normatif.

`GraphState` est **intégralement sérialisable en JSON** : aucun objet vivant,
aucune connexion, aucun callable. C'est la condition de la reprise après
arrêt — un état qui ne se relit pas depuis la base est un état perdu.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TypedDict


class WorkflowState(StrEnum):
    PROJECT_CREATED = "PROJECT_CREATED"
    SOURCES_INGESTING = "SOURCES_INGESTING"
    SOURCES_READY = "SOURCES_READY"
    PLAN_DRAFTING = "PLAN_DRAFTING"
    PLAN_GUARDRAIL = "PLAN_GUARDRAIL"
    PLAN_REVIEW = "PLAN_REVIEW"
    PLAN_VALIDATED = "PLAN_VALIDATED"
    SECTION_DRAFTING = "SECTION_DRAFTING"
    SECTION_GUARDRAIL = "SECTION_GUARDRAIL"
    SECTION_REVIEWING = "SECTION_REVIEWING"
    SECTION_CORRECTING = "SECTION_CORRECTING"
    SECTION_VALIDATED = "SECTION_VALIDATED"
    DOCUMENT_READY = "DOCUMENT_READY"
    EXPORTING = "EXPORTING"
    EXPORTED = "EXPORTED"
    ERROR_STATE = "ERROR_STATE"


class GraphState(TypedDict):
    """État partagé, persisté dans `task` à chaque transition."""

    project_id: int
    plan_id: int | None
    current_node_id: int | None
    state: WorkflowState
    retry_count: int
    review_loop_count: int
    ingest_retry_count: int
    last_error: str | None
    payload_json: dict


# Transitions déclarées (§5.2). Le graphe refuse ce qui n'y figure pas ; il
# ne s'adapte pas. C'est la propriété qui rend le système auditable : on peut
# établir a priori tout ce qui peut arriver.
TRANSITIONS: dict[WorkflowState, frozenset[WorkflowState]] = {
    WorkflowState.PROJECT_CREATED: frozenset({WorkflowState.SOURCES_INGESTING}),
    WorkflowState.SOURCES_INGESTING: frozenset(
        {WorkflowState.SOURCES_READY, WorkflowState.ERROR_STATE}
    ),
    WorkflowState.SOURCES_READY: frozenset({WorkflowState.PLAN_DRAFTING}),
    WorkflowState.PLAN_DRAFTING: frozenset({WorkflowState.PLAN_GUARDRAIL}),
    WorkflowState.PLAN_GUARDRAIL: frozenset(
        {WorkflowState.PLAN_REVIEW, WorkflowState.PLAN_DRAFTING, WorkflowState.ERROR_STATE}
    ),
    WorkflowState.PLAN_REVIEW: frozenset({WorkflowState.PLAN_VALIDATED}),
    WorkflowState.PLAN_VALIDATED: frozenset({WorkflowState.SECTION_DRAFTING}),
    WorkflowState.SECTION_DRAFTING: frozenset({WorkflowState.SECTION_GUARDRAIL}),
    WorkflowState.SECTION_GUARDRAIL: frozenset(
        {
            WorkflowState.SECTION_REVIEWING,
            WorkflowState.SECTION_DRAFTING,
            WorkflowState.ERROR_STATE,
        }
    ),
    WorkflowState.SECTION_REVIEWING: frozenset(
        {WorkflowState.SECTION_CORRECTING, WorkflowState.SECTION_VALIDATED}
    ),
    WorkflowState.SECTION_CORRECTING: frozenset(
        {WorkflowState.SECTION_REVIEWING, WorkflowState.ERROR_STATE}
    ),
    WorkflowState.SECTION_VALIDATED: frozenset(
        {WorkflowState.SECTION_DRAFTING, WorkflowState.DOCUMENT_READY}
    ),
    WorkflowState.DOCUMENT_READY: frozenset({WorkflowState.EXPORTING}),
    WorkflowState.EXPORTING: frozenset({WorkflowState.EXPORTED, WorkflowState.ERROR_STATE}),
    # Terminaux : aucune transition sortante automatique.
    WorkflowState.EXPORTED: frozenset(),
    WorkflowState.ERROR_STATE: frozenset(),
}

# Portes humaines. Ces arêtes existent dans la table ci-dessus, mais seul un
# appel explicite à `workflow_service.human_validate` les franchit : aucun
# nœud, aucun score de qualité, aucune configuration.
HUMAN_GATES: frozenset[tuple[WorkflowState, WorkflowState]] = frozenset(
    {
        (WorkflowState.PLAN_REVIEW, WorkflowState.PLAN_VALIDATED),
        (WorkflowState.SECTION_REVIEWING, WorkflowState.SECTION_VALIDATED),
    }
)

# `ERROR_STATE` n'est pas un échec du graphe, c'est son refus de continuer
# sans intervention. `EXPORTED` est la fin nominale.
TERMINAL_STATES: frozenset[WorkflowState] = frozenset(
    {WorkflowState.EXPORTED, WorkflowState.ERROR_STATE}
)


def initial_state(project_id: int) -> GraphState:
    return GraphState(
        project_id=project_id,
        plan_id=None,
        current_node_id=None,
        state=WorkflowState.PROJECT_CREATED,
        retry_count=0,
        review_loop_count=0,
        ingest_retry_count=0,
        last_error=None,
        payload_json={},
    )


def is_human_gate(depuis: WorkflowState, vers: WorkflowState) -> bool:
    return (depuis, vers) in HUMAN_GATES


def allowed_targets(depuis: WorkflowState) -> frozenset[WorkflowState]:
    return TRANSITIONS.get(depuis, frozenset())


def automatic_targets(depuis: WorkflowState) -> frozenset[WorkflowState]:
    """Cibles atteignables **sans** intervention humaine."""
    return frozenset(t for t in allowed_targets(depuis) if not is_human_gate(depuis, t))
