"""Endpoints du code d'analyse et des artefacts — US-401, US-004, §8.

Deux portes distinctes, et la distinction est au contrat :

- `/code/execute` est l'exécution DIRECTE d'US-004 : un code fourni, une
  origine, un mode, des bornes. Le serveur décide seul du niveau — une origine
  agent est ramenée au niveau 1 quel que soit le mode demandé (ADR-005), et
  l'abaissement est rapporté dans `downgraded_from_requested_mode`. Le niveau
  natif exige le consentement `native_execution` (403 sans lui).
- `/code/propose` est le pipeline d'US-401 : l'agent écrit le code, le serveur
  l'exécute au niveau 1, rapproche les artefacts attendus des artefacts
  produits, corrige en boucle plafonnée, et persiste la trace de reproduction.

Les jeux de données déposés (US-DATA-001) n'existent pas encore : la résolution
part d'un registre vide, et tout jeu cité est refusé — explicitement, avant
l'exécution, plutôt que par une traceback.
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite
from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from app.core.errors import ProjectNotFoundError
from app.core.logging import get_logger
from app.db import registry
from app.db.pool import get_pool
from app.models.code import CodeExecutionOut, ExecutionResultOut
from app.models.section import DraftSectionOut
from app.sandbox.base import ResourceLimits, SandboxMode, SandboxOrigin
from app.services import artifact_service, code_service

logger = get_logger(__name__)
router = APIRouter(tags=["code"])


class ProposeRequest(BaseModel):
    """Demande d'analyse : ce qu'elle doit montrer, sur quelles données."""

    intent: str = Field(min_length=1)
    datasets: list[str] = []


class ExecuteRequest(BaseModel):
    """Exécution directe — schéma du contrat pour `/code/execute`."""

    code: str = Field(min_length=1)
    origin: SandboxOrigin
    mode: SandboxMode = SandboxMode.WASM
    limits: ResourceLimits = Field(default_factory=ResourceLimits)
    datasets: list[str] = []


def _known_datasets(project_id: int) -> dict[str, Path]:
    """Registre des jeux de données du projet. Vide tant qu'US-DATA-001 n'existe pas."""
    return {}


async def _project_conn(project_id: int) -> aiosqlite.Connection:
    async with registry.connect_registry() as reg:
        ref = await registry.get(reg, project_id)
    if ref is None:
        raise ProjectNotFoundError.unknown(project_id)
    return await get_pool().acquire(project_id, ref.db_path)


@router.post(
    "/projects/{project_id}/code/propose",
    response_model=CodeExecutionOut,
    summary="Faire produire une analyse par l'agent",
)
async def propose(project_id: int, payload: ProposeRequest) -> CodeExecutionOut:
    """L'agent écrit le code, le serveur l'exécute au niveau 1 et rapproche.

    Rend l'exécution retenue et ses artefacts, chacun porteur de sa trace de
    reproduction : code exact, graine, versions, SHA-256 des jeux.
    """
    from app.llm.manager import build_manager

    conn = await _project_conn(project_id)
    manager = build_manager()
    await manager.startup()
    connus = _known_datasets(project_id)
    proposition = await code_service.propose(
        conn, project_id, manager, payload.intent, payload.datasets, connus
    )
    return await code_service.execute(conn, project_id, manager, proposition, connus)


@router.post(
    "/projects/{project_id}/code/execute",
    response_model=ExecutionResultOut,
    summary="Exécuter un script Python en environnement isolé",
)
async def execute(project_id: int, payload: ExecuteRequest) -> ExecutionResultOut:
    """Exécution directe (US-004). Le niveau est décidé par le serveur."""
    conn = await _project_conn(project_id)
    return await code_service.execute_direct(
        conn,
        project_id,
        payload.origin,
        payload.mode,
        payload.code,
        payload.datasets,
        payload.limits,
        _known_datasets(project_id),
    )


@router.get(
    "/projects/{project_id}/code/executions",
    response_model=list[ExecutionResultOut],
    summary="Historique des exécutions",
)
async def executions(project_id: int) -> list[ExecutionResultOut]:
    conn = await _project_conn(project_id)
    return await code_service.list_executions(conn, project_id)


@router.post(
    "/projects/{project_id}/sections/{section_id}/artifacts/{artifact_id}/attach",
    response_model=DraftSectionOut,
    summary="Rattacher un artefact à une section",
)
async def attach(project_id: int, section_id: int, artifact_id: int) -> DraftSectionOut:
    """Insère le renvoi Quarto dans le texte. Idempotent."""
    conn = await _project_conn(project_id)
    return await artifact_service.attach(conn, project_id, section_id, artifact_id)


@router.delete(
    "/projects/{project_id}/artifacts/{artifact_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Supprimer un artefact",
)
async def delete_artifact(project_id: int, artifact_id: int) -> None:
    """Refusée (409) si l'artefact est référencé dans une section."""
    conn = await _project_conn(project_id)
    await artifact_service.delete(conn, project_id, artifact_id)
