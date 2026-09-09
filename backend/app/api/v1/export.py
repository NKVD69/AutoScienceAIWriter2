"""Endpoint d'export — US-501, US-502.

Une citation non vérifiée répond 409 avec `blocking_items` : la section, la
clé et le passage. Le frontend peut donc mener l'utilisateur au texte fautif
plutôt que lui annoncer un échec sans suite.
"""

from __future__ import annotations

import aiosqlite
from fastapi import APIRouter, status

from app.core.errors import ProjectNotFoundError
from app.core.logging import get_logger
from app.db import registry
from app.db.pool import get_pool
from app.models.source import Task, TaskState
from app.services import export_service, task_service
from app.services.export_service import ExportRequest

logger = get_logger(__name__)
router = APIRouter(tags=["export"])


async def _project_conn(project_id: int) -> aiosqlite.Connection:
    async with registry.connect_registry() as reg:
        ref = await registry.get(reg, project_id)
    if ref is None:
        raise ProjectNotFoundError.unknown(project_id)
    return await get_pool().acquire(project_id, ref.db_path)


@router.post(
    "/projects/{project_id}/export",
    response_model=Task,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Exporter le document",
)
async def export(project_id: int, payload: ExportRequest) -> Task:
    """Compile la bibliographie puis le document.

    La bibliographie est regénérée depuis les seules citations vérifiées
    (ADR-007) ; une citation invalide bloque l'export avant toute écriture.
    """
    conn = await _project_conn(project_id)
    tache = await task_service.create(conn, project_id, TaskState.EXPORTING, agent="export")

    try:
        rapport = await export_service.run_export(conn, project_id, payload)
    except Exception:
        await task_service.update(
            conn, tache.id, state=TaskState.ERROR_STATE, last_error="export interrompu"
        )
        raise

    etat = TaskState.ERROR_STATE if rapport.status == "ECHEC" else TaskState.EXPORTED
    return await task_service.update(conn, tache.id, state=etat, progress=1.0)
