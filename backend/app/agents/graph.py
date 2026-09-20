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


# --- Nœuds du plan (US-PLAN-001) -----------------------------------------

# Sous-graphe du plan, extrait de la table pour être vérifiable seul. Il
# n'ajoute aucune arête : il nomme celles qui existent déjà, et un test
# compare les deux — un sous-graphe qui inventerait une transition
# contournerait le refus du graphe principal.
PLAN_SUBGRAPH: dict[WorkflowState, frozenset[WorkflowState]] = {
    WorkflowState.PLAN_DRAFTING: frozenset({WorkflowState.PLAN_GUARDRAIL}),
    WorkflowState.PLAN_GUARDRAIL: frozenset(
        {WorkflowState.PLAN_REVIEW, WorkflowState.PLAN_DRAFTING, WorkflowState.ERROR_STATE}
    ),
    WorkflowState.PLAN_REVIEW: frozenset({WorkflowState.PLAN_VALIDATED}),
    WorkflowState.PLAN_VALIDATED: frozenset({WorkflowState.SECTION_DRAFTING}),
}


def plan_guardrail_target(ok: bool, breaker_tripped: bool) -> WorkflowState:
    """Cible après le guardrail du plan.

    Trois issues seulement, et aucune ne mène à `PLAN_VALIDATED` : la porte
    humaine reste entière quelle que soit la qualité du plan produit.
    """
    if ok:
        return WorkflowState.PLAN_REVIEW
    if breaker_tripped:
        return WorkflowState.ERROR_STATE
    # Le MÊME agent est relancé : une erreur de format n'est pas un
    # problème de fond, le relecteur n'a rien à y faire.
    return WorkflowState.PLAN_DRAFTING


# --- Nœuds de section (US-301) -------------------------------------------

# Même construction que pour le plan : extrait de la table, jamais ajouté à
# elle. `SECTION_VALIDATED` boucle vers `SECTION_DRAFTING` — c'est la section
# suivante du plan, pas une réécriture de la même.
SECTION_SUBGRAPH: dict[WorkflowState, frozenset[WorkflowState]] = {
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
}


def section_guardrail_target(ok: bool, breaker_tripped: bool) -> WorkflowState:
    """Cible après les garde-fous de véracité d'une section (§5.5).

    Un rejet renvoie au RÉDACTEUR, jamais au relecteur. Une clé inconnue, un
    chiffre non rattaché ou un DOI hors base sont des défauts de production,
    pas des défauts de fond : le relecteur n'a rien à en dire, et lui passer
    un texte porteur d'une référence inventée lui ferait juger un contenu que
    le produit refuse d'écrire.
    """
    if ok:
        return WorkflowState.SECTION_REVIEWING
    if breaker_tripped:
        return WorkflowState.ERROR_STATE
    return WorkflowState.SECTION_DRAFTING


def review_target(verdict: str, auto_correct: bool, breaker_tripped: bool) -> WorkflowState | None:
    """Cible après une relecture (US-302). Ne mène JAMAIS à SECTION_VALIDATED.

    La relecture conseille, elle ne valide pas : la seule sortie vers
    `SECTION_VALIDATED` est la porte humaine (ADR-004). Une relecture qui
    demande une correction, et dont l'auteur a demandé la correction
    automatique, repart vers le rédacteur ; tout le reste s'arrête et attend
    l'humain — `None`.

    - plafond de boucles atteint : pause, la meilleure version est proposée ;
    - verdict « ready » : rien à corriger, on attend la validation humaine ;
    - « needs_work »/« insufficient » sans correction demandée : idem ;
    - avec correction demandée : retour à `SECTION_CORRECTING`.
    """
    if breaker_tripped or verdict == "ready":
        return None
    if auto_correct and verdict in ("needs_work", "insufficient"):
        return WorkflowState.SECTION_CORRECTING
    return None
