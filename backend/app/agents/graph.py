"""Graphe déterministe — US-201, ADR-004.

**Aucun agent ne décide du prochain agent.** Chaque nœud reçoit un état typé
et en retourne un autre ; la table des transitions de `state.py` dit seule ce
qui peut suivre quoi. Une transition non déclarée est refusée — le graphe ne
s'adapte pas.

L'alternative écartée, la discussion libre entre agents, accumule
l'historique dans le contexte : consommation de tokens non bornée, saturation
du cache KV sur 10 Go de VRAM, et surtout audit impossible — on ne pourrait
pas établir *pourquoi* une section a été écrite ainsi.

Le graphe LangGraph est construit **à partir** de la même table, de sorte
qu'il ne puisse pas en diverger : un test compare les deux structures.
"""

from __future__ import annotations

from app.agents.state import (
    TRANSITIONS,
    GraphState,
    WorkflowState,
    allowed_targets,
    is_human_gate,
)
from app.core.errors import InvalidTransitionError
from app.core.logging import get_logger

logger = get_logger(__name__)


def assert_transition_allowed(
    depuis: WorkflowState, vers: WorkflowState, *, human: bool = False
) -> None:
    """Refuse toute transition non déclarée, et toute porte franchie seule.

    `human=True` n'est passé que par `workflow_service.human_validate` :
    aucun nœud, aucun score, aucune configuration ne peut l'obtenir.
    """
    if vers not in allowed_targets(depuis):
        raise InvalidTransitionError.undeclared(depuis, vers, allowed_targets(depuis))
    if is_human_gate(depuis, vers) and not human:
        raise InvalidTransitionError.human_gate(depuis, vers)


def build_langgraph():
    """Construit le `StateGraph` depuis la table de transitions (ADR-004).

    Le graphe est dérivé, jamais recopié : une divergence entre lui et la
    table serait une seconde source de vérité, donc une occasion de les voir
    diverger en silence.
    """
    from langgraph.graph import END, StateGraph

    graphe = StateGraph(GraphState)

    def noeud_identite(state: GraphState) -> GraphState:
        # Les nœuds métier sont branchés par les stories d'agents. Ici, la
        # structure seule est posée : ce qui est vérifiable, c'est la forme
        # du graphe, pas un comportement qui n'existe pas encore.
        return state

    for etat in TRANSITIONS:
        graphe.add_node(etat.value, noeud_identite)

    for depuis, cibles in TRANSITIONS.items():
        if not cibles:
            graphe.add_edge(depuis.value, END)
            continue
        graphe.add_conditional_edges(
            depuis.value,
            lambda state: str(state["state"]),
            {cible.value: cible.value for cible in cibles},
        )

    graphe.set_entry_point(WorkflowState.PROJECT_CREATED.value)
    return graphe


def next_states(depuis: WorkflowState) -> frozenset[WorkflowState]:
    return allowed_targets(depuis)


def describe_graph() -> dict[str, list[str]]:
    """Table lisible, pour le tableau de bord et les tests."""
    return {depuis.value: sorted(c.value for c in cibles) for depuis, cibles in TRANSITIONS.items()}
