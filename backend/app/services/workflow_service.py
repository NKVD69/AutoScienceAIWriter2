"""Persistance, transitions et reprise du graphe — US-201, US-202.

**Une transition non journalisée est une transition qui n'a pas eu lieu.**
L'écriture dans `task` et l'entrée d'audit se font dans la même transaction
que la transition elle-même (ADR-009) : séparées, on pourrait avoir un état
avancé sans trace, ou une trace sans état — les deux ruinent l'auditabilité
que le graphe déterministe existe pour fournir.

**`ERROR_STATE` est terminal sans action humaine.** Aucune transition
sortante automatique n'en part. La seule sortie est `resume_from_error`, où
l'utilisateur choisit : reprendre, passer, abandonner.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum

import aiosqlite

from app.agents.graph import assert_transition_allowed
from app.agents.state import (
    TERMINAL_STATES,
    GraphState,
    WorkflowState,
    initial_state,
)
from app.core.errors import AppError
from app.core.logging import get_logger
from app.db.session import transaction
from app.models.audit import AuditEventType
from app.models.source import TaskEvent
from app.services import audit_service, task_service

logger = get_logger(__name__)


class ResumeAction(StrEnum):
    RETRY = "retry"
    SKIP = "skip"
    ABORT = "abort"


class NotInErrorStateError(AppError):
    """Reprise demandée sur une tâche qui n'est pas en erreur."""

    code = "NOT_IN_ERROR_STATE"
    status_code = 409


# --- Persistance ----------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def save_state(conn: aiosqlite.Connection, task_id: int, state: GraphState) -> None:
    """Écrit l'état complet. L'appelant détient la transaction."""
    await conn.execute(
        "UPDATE task SET state = ?, payload_json = ?, retry_count = ?, updated_at = ? WHERE id = ?",
        (
            str(state["state"]),
            json.dumps(_serialisable(state), ensure_ascii=False, allow_nan=False),
            state["retry_count"],
            _now(),
            task_id,
        ),
    )


def _serialisable(state: GraphState) -> dict:
    """Vue JSON de l'état. Aucun objet vivant n'y survivrait."""
    return {
        "project_id": state["project_id"],
        "plan_id": state["plan_id"],
        "current_node_id": state["current_node_id"],
        "state": str(state["state"]),
        "retry_count": state["retry_count"],
        "review_loop_count": state["review_loop_count"],
        "ingest_retry_count": state["ingest_retry_count"],
        "last_error": state["last_error"],
        "payload_json": state["payload_json"],
    }


async def load_state(conn: aiosqlite.Connection, task_id: int) -> GraphState | None:
    """Relit l'état persisté. C'est le seul point de reprise."""
    async with conn.execute(
        "SELECT project_id, state, payload_json, retry_count FROM task WHERE id = ?",
        (task_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None

    charge = json.loads(row[2] or "{}")
    return GraphState(
        project_id=int(charge.get("project_id", row[0])),
        plan_id=charge.get("plan_id"),
        current_node_id=charge.get("current_node_id"),
        state=WorkflowState(str(row[1])),
        retry_count=int(charge.get("retry_count", row[3])),
        review_loop_count=int(charge.get("review_loop_count", 0)),
        ingest_retry_count=int(charge.get("ingest_retry_count", 0)),
        last_error=charge.get("last_error"),
        payload_json=charge.get("payload_json", {}),
    )


async def start(conn: aiosqlite.Connection, project_id: int, agent: str | None = None) -> int:
    """Crée une tâche à `PROJECT_CREATED` et retourne son identifiant."""
    tache = await task_service.create(conn, project_id, WorkflowState.PROJECT_CREATED, agent=agent)
    async with transaction(conn):
        await save_state(conn, tache.id, initial_state(project_id))
    return tache.id


# --- Transitions ----------------------------------------------------------


async def transition(
    conn: aiosqlite.Connection,
    task_id: int,
    state: GraphState,
    vers: WorkflowState,
    *,
    human: bool = False,
    reason: str | None = None,
) -> GraphState:
    """Applique une transition déclarée, la persiste et la journalise.

    L'ordre importe : on valide, puis on écrit état **et** audit ensemble.
    Retourner avant l'écriture laisserait le graphe en avance sur sa trace.
    """
    depuis = state["state"]
    assert_transition_allowed(depuis, vers, human=human)

    state["state"] = vers
    async with transaction(conn):
        await save_state(conn, task_id, state)
        await audit_service.append(
            conn,
            state["project_id"],
            AuditEventType.STATE_TRANSITION,
            {
                "task_id": task_id,
                "de": str(depuis),
                "vers": str(vers),
                "humaine": human,
                "motif": reason,
            },
            tx=True,
        )

    task_service.emit(
        TaskEvent(type="state", task_id=task_id, payload={"state": str(vers), "from": str(depuis)})
    )
    if vers in TERMINAL_STATES:
        task_service.emit(TaskEvent(type="done", task_id=task_id, payload={"state": str(vers)}))
    logger.info("Tâche %s : %s → %s%s", task_id, depuis, vers, " (humaine)" if human else "")
    return state


async def human_validate(
    conn: aiosqlite.Connection,
    task_id: int,
    state: GraphState,
    vers: WorkflowState,
    validated_by: str = "utilisateur",
) -> GraphState:
    """Franchit une porte humaine. Seul chemin possible (§5.2).

    Aucun score de qualité n'apparaît dans cette signature, et c'est
    délibéré : rendre la validation dépendante d'un score reviendrait à la
    rendre automatisable, ce que le cahier des charges exclut.
    """
    depuis = state["state"]
    etat = await transition(conn, task_id, state, vers, human=True, reason="validation humaine")
    async with transaction(conn):
        await audit_service.append(
            conn,
            state["project_id"],
            AuditEventType.HUMAN_VALIDATION,
            {"task_id": task_id, "porte": f"{depuis}→{vers}", "par": validated_by},
            tx=True,
        )
    return etat


async def fail(
    conn: aiosqlite.Connection, task_id: int, state: GraphState, reason: str
) -> GraphState:
    """Conduit à `ERROR_STATE` et journalise le déclenchement du breaker."""
    state["last_error"] = reason
    async with transaction(conn):
        await audit_service.append(
            conn,
            state["project_id"],
            AuditEventType.BREAKER_TRIGGERED,
            {"task_id": task_id, "depuis": str(state["state"]), "motif": reason[:500]},
            tx=True,
        )
    etat = await transition(conn, task_id, state, WorkflowState.ERROR_STATE, reason=reason)
    task_service.emit(TaskEvent(type="error", task_id=task_id, payload={"message": reason}))
    return etat


# --- Reprise --------------------------------------------------------------


async def resumable_tasks(conn: aiosqlite.Connection) -> list[int]:
    """Tâches dans un état non terminal, à reprendre au démarrage."""
    terminaux = ",".join(f"'{e.value}'" for e in TERMINAL_STATES)
    async with conn.execute(
        # `terminaux` vient de l'énumération, jamais d'une entrée utilisateur.
        f"SELECT id FROM task WHERE state NOT IN ({terminaux}) ORDER BY id"
    ) as cur:
        return [int(r[0]) for r in await cur.fetchall()]


async def resume_from_error(
    conn: aiosqlite.Connection, task_id: int, action: ResumeAction
) -> GraphState:
    """Seule sortie d'`ERROR_STATE`, et elle vient de l'utilisateur.

    - `retry` : compteurs remis à zéro, retour à l'état d'avant l'erreur ;
    - `skip`  : l'étape est abandonnée, le graphe reprend à l'étape suivante ;
    - `abort` : la tâche reste en erreur, l'utilisateur a décidé d'arrêter.
    """
    state = await load_state(conn, task_id)
    if state is None:
        raise NotInErrorStateError(f"Tâche {task_id} inconnue.")
    if state["state"] is not WorkflowState.ERROR_STATE:
        raise NotInErrorStateError(
            f"La tâche {task_id} est en {state['state']}, pas en ERROR_STATE : "
            "il n'y a rien à reprendre."
        )

    precedent = state["payload_json"].get("state_before_error")
    async with transaction(conn):
        await audit_service.append(
            conn,
            state["project_id"],
            AuditEventType.HUMAN_VALIDATION,
            {"task_id": task_id, "reprise": str(action)},
            tx=True,
        )

    if action is ResumeAction.ABORT:
        logger.info("Tâche %s : abandon confirmé par l'utilisateur", task_id)
        return state

    from app.agents.breaker import reset_node_counters

    reset_node_counters(state)
    cible = WorkflowState(precedent) if precedent else WorkflowState.PROJECT_CREATED

    # `ERROR_STATE` n'a pas d'arête sortante déclarée : la reprise réécrit
    # l'état plutôt que de « transitionner », et le dit dans l'audit.
    state["state"] = cible
    async with transaction(conn):
        await save_state(conn, task_id, state)
        await audit_service.append(
            conn,
            state["project_id"],
            AuditEventType.STATE_TRANSITION,
            {
                "task_id": task_id,
                "de": str(WorkflowState.ERROR_STATE),
                "vers": str(cible),
                "humaine": True,
                "motif": f"reprise après erreur ({action})",
            },
            tx=True,
        )
    task_service.emit(
        TaskEvent(
            type="state", task_id=task_id, payload={"state": str(cible), "resume": str(action)}
        )
    )
    return state


def remember_state_before_error(state: GraphState) -> None:
    """Retient l'état courant, pour qu'une reprise sache où revenir."""
    state["payload_json"]["state_before_error"] = str(state["state"])
