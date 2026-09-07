"""Endpoints du journal à détection d'altération.

Contrat `contracts/openapi.yaml`, section `/projects/{projectId}/audit`.
ADR-009 : la vérification atteste une **détection d'altération**, elle ne
garantit aucune immutabilité — le fichier appartient à son auteur.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query

from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.models.audit import AuditEntry, ChainVerification
from app.services import audit_service

router = APIRouter(tags=["audit"])


def project_db_path(project_id: int) -> Path:
    """Chemin du fichier projet.

    Résolution provisoire par convention de nommage : le registre de projets
    appartient à US-101, non encore livrée. Elle est isolée dans cette
    fonction pour n'avoir qu'un point à reprendre.
    """
    return get_settings().projects_dir / f"{project_id}.sqlite"


def _require_project(project_id: int) -> Path:
    path = project_db_path(project_id)
    if not path.exists():
        raise NotFoundError(f"Projet {project_id} introuvable.", project_id=project_id)
    return path


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
    from app.db.session import connect

    path = _require_project(project_id)
    async with connect(path) as conn:
        return await audit_service.list_entries(conn, project_id, event_type, limit)


@router.post(
    "/projects/{project_id}/audit/verify",
    response_model=ChainVerification,
    summary="Vérifier la chaîne de hachage",
)
async def verify_audit(project_id: int) -> ChainVerification:
    from app.db.session import connect

    path = _require_project(project_id)
    async with connect(path) as conn:
        return await audit_service.verify_chain(conn, project_id)
