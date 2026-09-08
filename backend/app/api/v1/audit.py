"""Endpoints du journal à détection d'altération.

Contrat `contracts/openapi.yaml`, section `/projects/{projectId}/audit`.
ADR-009 : la vérification atteste une **détection d'altération**, elle ne
garantit aucune immutabilité — le fichier appartient à son auteur.
"""

from __future__ import annotations

import aiosqlite
from fastapi import APIRouter, Query

from app.core.errors import ProjectNotFoundError
from app.db import registry
from app.db.pool import get_pool
from app.models.audit import AuditEntry, ChainVerification
from app.services import audit_service

router = APIRouter(tags=["audit"])


async def _project_conn(project_id: int) -> aiosqlite.Connection:
    """Connexion du projet, résolue par le registre global (US-101).

    US-701 résolvait provisoirement le chemin par convention de nommage, le
    registre n'existant pas encore. Cette dette est ici soldée : le nom de
    fichier vient du slug, que la convention ignorait.
    """
    async with registry.connect_registry() as reg:
        ref = await registry.get(reg, project_id)
    if ref is None:
        raise ProjectNotFoundError.unknown(project_id)
    return await get_pool().acquire(project_id, ref.db_path)


@router.get(
    "/projects/{project_id}/audit",
    response_model=list[AuditEntry],
    summary="Journal d'audit",
)
async def read_audit(
    project_id: int,
    event_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[AuditEntry]:
    conn = await _project_conn(project_id)
    return await audit_service.list_entries(conn, project_id, event_type, limit)


@router.post(
    "/projects/{project_id}/audit/verify",
    response_model=ChainVerification,
    summary="Vérifier la chaîne de hachage",
)
async def verify_audit(project_id: int) -> ChainVerification:
    conn = await _project_conn(project_id)
    return await audit_service.verify_chain(conn, project_id)
