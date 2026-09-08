"""Endpoints des projets — contrat `contracts/openapi.yaml`, section /projects.

Les codes de statut et les noms de champs viennent du contrat : une
divergence est un défaut de ce module, pas du contrat.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.models.project import BackupResult, Project, ProjectCreate, ProjectUpdate
from app.services import project_service

router = APIRouter(tags=["projects"])


@router.get("/projects", response_model=list[Project], summary="Lister les projets")
async def list_projects() -> list[Project]:
    return await project_service.list_projects()


@router.post(
    "/projects",
    response_model=Project,
    status_code=status.HTTP_201_CREATED,
    summary="Créer un projet",
)
async def create_project(payload: ProjectCreate) -> Project:
    return await project_service.create(payload)


@router.get("/projects/{project_id}", response_model=Project, summary="Détail d'un projet")
async def get_project(project_id: int) -> Project:
    return await project_service.get(project_id)


@router.patch("/projects/{project_id}", response_model=Project, summary="Modifier un projet")
async def update_project(project_id: int, payload: ProjectUpdate) -> Project:
    return await project_service.update(project_id, payload)


@router.delete(
    "/projects/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Supprimer un projet",
)
async def delete_project(project_id: int) -> Response:
    """Déplace le projet en corbeille. Le fichier n'est jamais effacé."""
    await project_service.delete(project_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/projects/{project_id}/backup",
    response_model=BackupResult,
    summary="Sauvegarder le projet dans un fichier unique",
)
async def backup_project(project_id: int) -> BackupResult:
    return await project_service.backup(project_id)
