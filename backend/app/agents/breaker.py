"""Compteurs et circuit breaker — US-202, ADR-008, spécifications §5.3.

**Les compteurs vivent dans `GraphState`, jamais dans une variable de
module.** Un compteur en mémoire disparaît au redémarrage : une boucle qui
avait déjà consommé ses trois essais repartirait à zéro, et la machine
tournerait indéfiniment sans que rien ne le signale — exactement ce que
US-202 existe pour empêcher.

**La pause de relecture propose la MEILLEURE version, pas la dernière.** Une
boucle de correction ne converge pas nécessairement : la troisième version
peut être moins bonne que la première. Rendre la dernière serait rendre le
résultat d'un abandon, pas d'un travail.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.agents.state import GraphState, WorkflowState
from app.core.logging import get_logger

logger = get_logger(__name__)

MAX_GUARDRAIL_RETRIES = 3
MAX_REVIEW_LOOPS = 3
MAX_INGEST_RETRIES = 2

BEST_VERSION_KEY = "best_version"


@dataclass(frozen=True)
class BreakerDecision:
    """Ce que le graphe doit faire, et pourquoi."""

    tripped: bool
    next_state: WorkflowState | None = None
    reason: str | None = None


def register_guardrail_failure(state: GraphState, error_message: str) -> BreakerDecision:
    """Comptabilise un rejet de guardrail. Au troisième, `ERROR_STATE`."""
    state["retry_count"] += 1
    state["last_error"] = error_message

    if state["retry_count"] >= MAX_GUARDRAIL_RETRIES:
        logger.warning("Circuit breaker : %s essais de guardrail épuisés", state["retry_count"])
        return BreakerDecision(
            tripped=True,
            next_state=WorkflowState.ERROR_STATE,
            reason=(
                f"{MAX_GUARDRAIL_RETRIES} essais consécutifs rejetés par le "
                f"guardrail. Dernière erreur : {error_message}"
            ),
        )
    return BreakerDecision(tripped=False)


def record_review_version(state: GraphState, content: str, score: float) -> None:
    """Retient la meilleure version rencontrée, au fil des itérations."""
    meilleure = state["payload_json"].get(BEST_VERSION_KEY)
    if meilleure is None or score > float(meilleure.get("score", float("-inf"))):
        state["payload_json"][BEST_VERSION_KEY] = {
            "content": content,
            "score": score,
            "iteration": state["review_loop_count"],
        }


def best_version(state: GraphState) -> dict | None:
    return state["payload_json"].get(BEST_VERSION_KEY)


def register_review_loop(state: GraphState) -> BreakerDecision:
    """Comptabilise un aller-retour relecture ⇄ correction.

    Au dépassement, le graphe s'arrête sur `SECTION_REVIEWING` et attend
    l'humain : la porte de validation reste la sienne, et la meilleure
    version lui est proposée.
    """
    state["review_loop_count"] += 1
    if state["review_loop_count"] >= MAX_REVIEW_LOOPS:
        meilleure = best_version(state)
        logger.warning(
            "Circuit breaker : %s boucles de relecture, pause", state["review_loop_count"]
        )
        return BreakerDecision(
            tripped=True,
            # Pas d'ERROR_STATE : le travail existe, il attend un arbitrage.
            next_state=None,
            reason=(
                f"{MAX_REVIEW_LOOPS} boucles de relecture sans convergence. "
                "La meilleure version rencontrée est proposée"
                + (
                    f" (itération {meilleure['iteration']}, score {meilleure['score']})."
                    if meilleure
                    else "."
                )
            ),
        )
    return BreakerDecision(tripped=False)


def register_ingest_failure(state: GraphState) -> BreakerDecision:
    """Comptabilise un échec d'ingestion de source.

    Au dépassement, la source est marquée en échec et le pipeline continue :
    une source illisible ne doit pas bloquer les deux cents autres.
    """
    state["ingest_retry_count"] += 1
    if state["ingest_retry_count"] >= MAX_INGEST_RETRIES:
        return BreakerDecision(
            tripped=True,
            next_state=None,
            reason=f"{MAX_INGEST_RETRIES} essais épuisés : source marquée en échec.",
        )
    return BreakerDecision(tripped=False)


def reset_node_counters(state: GraphState) -> None:
    """Remise à zéro à l'entrée dans un nouveau nœud de plan ou de section.

    Jamais globale : les compteurs d'un nœud n'ont rien à voir avec ceux du
    suivant, et les confondre ferait déclencher le breaker sur un nœud sain
    à cause de l'historique du précédent.
    """
    state["retry_count"] = 0
    state["review_loop_count"] = 0
    state["ingest_retry_count"] = 0
    state["last_error"] = None
    state["payload_json"].pop(BEST_VERSION_KEY, None)
