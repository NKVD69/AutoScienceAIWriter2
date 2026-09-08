"""Endpoints du plan — US-PLAN-001.

L'édition est exposée pour l'interface de US-UI-002 ; c'est l'API qu'elle
consommera, pas le glisser-déposer lui-même.
"""

from __future__ import annotations

import aiosqlite
from fastapi import APIRouter, Query, status

from app.agents.plan_agent import generate_plan
from app.core.errors import ProjectNotFoundError
from app.core.logging import get_logger
from app.db import registry
from app.db.pool import get_pool
from app.models.plan import (
    PlanNodeCreate,
    PlanNodeUpdate,
    PlanOut,
    PlanStatus,
    PlanVersion,
    ReorderRequest,
)
from app.models.project import Project
from app.models.source import SourceDocument, Task, TaskState
from app.services import plan_service, project_service, task_service

logger = get_logger(__name__)
router = APIRouter(tags=["plan"])


async def _project_conn(project_id: int) -> aiosqlite.Connection:
    async with registry.connect_registry() as reg:
        ref = await registry.get(reg, project_id)
    if ref is None:
        raise ProjectNotFoundError.unknown(project_id)
    return await get_pool().acquire(project_id, ref.db_path)


async def _approved_sources(conn: aiosqlite.Connection) -> list[SourceDocument]:
    async with conn.execute(
        "SELECT id, kind, title, authors, year, doi, url, venue, is_preprint,"
        " imported_at, approved_at FROM source_document WHERE approved_at IS NOT NULL"
        " ORDER BY id"
    ) as cur:
        rows = await cur.fetchall()
    return [
        SourceDocument(
            id=int(r[0]),
            kind=str(r[1]),
            title=str(r[2]),
            authors=r[3],
            year=r[4],
            doi=r[5],
            url=r[6],
            venue=r[7],
            is_preprint=bool(r[8]),
            imported_at=str(r[9]),
            approved_at=r[10],
        )
        for r in rows
    ]


@router.post(
    "/projects/{project_id}/plan/generate",
    response_model=Task,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Générer le plan de recherche",
)
async def generate(project_id: int, problematique: str | None = Query(default=None)) -> Task:
    """Opération longue : une génération de plan se compte en minutes."""
    from app.llm.manager import build_manager

    conn = await _project_conn(project_id)
    projet: Project = await project_service.get(project_id)

    tache = await task_service.create(conn, project_id, TaskState.PLAN_DRAFTING, agent="plan")
    manager = build_manager()
    await manager.startup()

    arbre = await generate_plan(
        manager=manager,
        project=projet,
        approved_sources=await _approved_sources(conn),
        user_problematique=problematique,
        target_words=projet.target_words or 60_000,
    )
    await plan_service.save_tree(conn, project_id, arbre, PlanStatus.REVIEW)
    return await task_service.update(conn, tache.id, state=TaskState.PLAN_REVIEW, progress=1.0)


@router.get("/projects/{project_id}/plan", response_model=PlanOut, summary="Plan courant")
async def read_plan(project_id: int) -> PlanOut:
    conn = await _project_conn(project_id)
    return await plan_service._require_plan(conn, project_id)


@router.get(
    "/projects/{project_id}/plan/versions",
    response_model=list[PlanVersion],
    summary="Historique des versions du plan",
)
async def read_versions(project_id: int) -> list[PlanVersion]:
    conn = await _project_conn(project_id)
    return await plan_service.versions(conn, project_id)


@router.put(
    "/projects/{project_id}/plan/nodes/{node_id}",
    response_model=PlanOut,
    summary="Modifier un nœud du plan",
)
async def update_node(project_id: int, node_id: int, payload: PlanNodeUpdate) -> PlanOut:
    conn = await _project_conn(project_id)
    return await plan_service.update_node(conn, project_id, node_id, payload)


@router.post(
    "/projects/{project_id}/plan/nodes",
    response_model=PlanOut,
    status_code=status.HTTP_201_CREATED,
    summary="Ajouter un nœud au plan",
)
async def add_node(project_id: int, payload: PlanNodeCreate) -> PlanOut:
    conn = await _project_conn(project_id)
    return await plan_service.add_node(conn, project_id, payload)


@router.delete(
    "/projects/{project_id}/plan/nodes/{node_id}",
    response_model=PlanOut,
    summary="Supprimer un sous-arbre du plan",
)
async def delete_node(project_id: int, node_id: int, force: bool = Query(default=False)) -> PlanOut:
    """Refusée si des sections y sont rédigées, sauf `force=true` — auquel
    cas elles passent en ORPHANED, jamais supprimées."""
    conn = await _project_conn(project_id)
    return await plan_service.delete_node(conn, project_id, node_id, force=force)


@router.post(
    "/projects/{project_id}/plan/reorder",
    response_model=PlanOut,
    summary="Réordonner les nœuds du plan",
)
async def reorder(project_id: int, payload: ReorderRequest) -> PlanOut:
    conn = await _project_conn(project_id)
    return await plan_service.reorder(conn, project_id, payload)


@router.post(
    "/projects/{project_id}/plan/validate",
    response_model=PlanOut,
    summary="Valider le plan",
)
async def validate(project_id: int) -> PlanOut:
    """Porte de validation humaine. Aucun score ne la franchit (§5.2)."""
    conn = await _project_conn(project_id)
    return await plan_service.validate(conn, project_id)
