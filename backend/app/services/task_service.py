"""Suivi des tâches longues — US-102, contrat `Task` et `/tasks/{id}/stream`.

**L'état vit en base, pas en mémoire.** Une ingestion de 200 PDF survit à un
arrêt du backend : elle reprend à l'état persisté plutôt que de recommencer.
Recommencer coûterait des heures de calcul déjà faites et, sur une machine où
rédaction et ingestion sont sérialisées (ADR-016), du temps pris à
l'utilisateur.

La file d'événements, elle, est volatile : elle alimente le flux SSE d'un
client connecté. Un client absent ne perd rien d'important — la progression
réelle est relisible en base.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import UTC, datetime

import aiosqlite

from app.core.logging import get_logger
from app.models.source import Task, TaskEvent, TaskState

logger = get_logger(__name__)

# Bornée : un client déconnecté ne doit pas faire croître la mémoire du
# service au rythme de l'ingestion.
EVENT_QUEUE_MAXSIZE = 256

_queues: dict[int, asyncio.Queue[TaskEvent]] = defaultdict(
    lambda: asyncio.Queue(maxsize=EVENT_QUEUE_MAXSIZE)
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def create(
    conn: aiosqlite.Connection,
    project_id: int,
    state: TaskState,
    agent: str | None = None,
) -> Task:
    """Crée une tâche. L'appelant ouvre la transaction s'il en a besoin."""
    maintenant = _now()
    cur = await conn.execute(
        "INSERT INTO task (project_id, state, agent, payload_json, retry_count,"
        " created_at, updated_at) VALUES (?, ?, ?, ?, 0, ?, ?)",
        (project_id, str(state), agent, json.dumps({"progress": 0.0}), maintenant, maintenant),
    )
    await conn.commit()
    return Task(
        id=int(cur.lastrowid or 0),
        project_id=project_id,
        state=state,
        agent=agent,
        progress=0.0,
        created_at=maintenant,
        updated_at=maintenant,
    )


async def get(conn: aiosqlite.Connection, task_id: int) -> Task | None:
    async with conn.execute(
        "SELECT id, project_id, state, agent, payload_json, retry_count, updated_at,"
        " created_at FROM task WHERE id = ?",
        (task_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    charge = json.loads(row[4] or "{}")
    return Task(
        id=int(row[0]),
        project_id=int(row[1]),
        state=TaskState(str(row[2])),
        agent=row[3],
        progress=float(charge.get("progress", 0.0)),
        retry_count=int(row[5]),
        last_error=charge.get("last_error"),
        created_at=str(row[7]),
        updated_at=str(row[6]),
    )


async def update(
    conn: aiosqlite.Connection,
    task_id: int,
    *,
    state: TaskState | None = None,
    progress: float | None = None,
    last_error: str | None = None,
) -> Task:
    """Met à jour l'état persisté, puis émet l'événement correspondant."""
    actuelle = await get(conn, task_id)
    if actuelle is None:
        raise ValueError(f"Tâche {task_id} inconnue.")

    charge = {"progress": progress if progress is not None else actuelle.progress}
    if last_error is not None:
        charge["last_error"] = last_error
    elif actuelle.last_error is not None:
        charge["last_error"] = actuelle.last_error

    nouvel_etat = state or actuelle.state
    await conn.execute(
        "UPDATE task SET state = ?, payload_json = ?, updated_at = ? WHERE id = ?",
        (str(nouvel_etat), json.dumps(charge), _now(), task_id),
    )
    await conn.commit()

    if state is not None and state != actuelle.state:
        emit(TaskEvent(type="state", task_id=task_id, payload={"state": str(state)}))
    if progress is not None and progress != actuelle.progress:
        emit(TaskEvent(type="progress", task_id=task_id, payload={"progress": progress}))
    if last_error is not None:
        emit(TaskEvent(type="error", task_id=task_id, payload={"message": last_error}))
    if nouvel_etat in (TaskState.SOURCES_READY, TaskState.ERROR_STATE):
        emit(TaskEvent(type="done", task_id=task_id, payload={"state": str(nouvel_etat)}))

    return await get(conn, task_id) or actuelle


async def resumable(conn: aiosqlite.Connection, project_id: int) -> Task | None:
    """Tâche d'ingestion laissée en cours par un arrêt du backend.

    Reprise à l'état persisté, jamais relancée depuis le début.
    """
    async with conn.execute(
        "SELECT id FROM task WHERE project_id = ? AND state = ? ORDER BY id DESC LIMIT 1",
        (project_id, str(TaskState.SOURCES_INGESTING)),
    ) as cur:
        row = await cur.fetchone()
    return await get(conn, int(row[0])) if row else None


# --- File d'événements ----------------------------------------------------


def emit(event: TaskEvent) -> None:
    """Publie un événement. Une file pleine perd l'événement, jamais la tâche."""
    file = _queues[event.task_id]
    try:
        file.put_nowait(event)
    except asyncio.QueueFull:
        logger.debug("File d'événements pleine pour la tâche %s", event.task_id)


def queue_for(task_id: int) -> asyncio.Queue[TaskEvent]:
    return _queues[task_id]


def drain(task_id: int) -> list[TaskEvent]:
    """Vide la file. Destiné aux tests et au rattrapage d'un client."""
    file = _queues[task_id]
    evenements: list[TaskEvent] = []
    while not file.empty():
        evenements.append(file.get_nowait())
    return evenements


def reset_queues() -> None:
    _queues.clear()
