"""Endpoints de section — US-301.

La demande de rédaction sur un plan non validé répond 409 en nommant l'état
courant et l'état requis. Ce contrôle vient de `plan_service` : il s'applique
donc aussi bien à cet appel HTTP qu'à un appel interne du graphe. Le
réimplémenter ici en ferait une politesse de frontière plutôt qu'une règle.
"""

from __future__ import annotations

import aiosqlite
from fastapi import APIRouter, status

from app.core.errors import ProjectNotFoundError
from app.core.logging import get_logger
from app.db import registry
from app.db.pool import get_pool
from app.models.section import DraftSectionOut, SectionUpdate
from app.models.source import Task, TaskState
from app.services import section_service, task_service

logger = get_logger(__name__)
router = APIRouter(tags=["sections"])


async def _project_conn(project_id: int) -> aiosqlite.Connection:
    async with registry.connect_registry() as reg:
        ref = await registry.get(reg, project_id)
    if ref is None:
        raise ProjectNotFoundError.unknown(project_id)
    return await get_pool().acquire(project_id, ref.db_path)


@router.post(
    "/projects/{project_id}/sections/{node_id}/draft",
    response_model=Task,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Rédiger une section",
)
async def draft(project_id: int, node_id: int) -> Task:
    """Opération longue : ~15 minutes pour 1 500 mots (ADR-015).

    Les tokens sont diffusés sur `/tasks/id/stream` pendant la génération ;
    le verdict des garde-fous n'arrive qu'à la fin.
    """
    from app.llm.manager import build_manager

    conn = await _project_conn(project_id)
    tache = await task_service.create(conn, project_id, TaskState.SECTION_DRAFTING, agent="writer")

    manager = build_manager()
    await manager.startup()
    await section_service.draft_section(conn, project_id, node_id, manager, task_id=tache.id)
    return await task_service.get(conn, tache.id) or tache


@router.get(
    "/projects/{project_id}/sections/{section_id}",
    response_model=DraftSectionOut,
    summary="Contenu d'une section",
)
async def read_section(project_id: int, section_id: int) -> DraftSectionOut:
    conn = await _project_conn(project_id)
    return await section_service.get(conn, section_id)


@router.put(
    "/projects/{project_id}/sections/{section_id}",
    response_model=DraftSectionOut,
    summary="Remplacer le contenu d'une section",
)
async def update_section(
    project_id: int, section_id: int, payload: SectionUpdate
) -> DraftSectionOut:
    """Édition manuelle. Dévérifie les citations disparues, n'en supprime aucune."""
    conn = await _project_conn(project_id)
    return await section_service.update_content(conn, project_id, section_id, payload)
