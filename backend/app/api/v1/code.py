"""Endpoints du code d'analyse et des artefacts — US-401, §8.

Proposer, exécuter, lister, rattacher, supprimer. L'exécution passe toujours par
le niveau 1 (US-004) : ces routes n'exposent aucun moyen pour un agent de
demander le niveau natif.

Les jeux de données déposés (US-DATA-001) n'existent pas encore : la résolution
part donc d'un registre vide, et tout jeu cité est refusé. C'est explicite et
sans surprise, en attendant la story du dépôt.
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
from app.models.code import CodeExecutionOut, CodeProposal
from app.models.section import DraftSectionOut
from app.services import artifact_service, code_service

logger = get_logger(__name__)
router = APIRouter(tags=["code"])


class ProposeRequest(BaseModel):
    """Demande de génération de code : ce que l'analyse doit montrer, sur quoi."""

    intent: str = Field(min_length=1)
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
    response_model=CodeProposal,
    summary="Proposer du code d'analyse",
)
async def propose(project_id: int, payload: ProposeRequest) -> CodeProposal:
    """L'agent code génère une proposition, validée par le guardrail (US-201)."""
    from app.llm.manager import build_manager

    conn = await _project_conn(project_id)
    manager = build_manager()
    await manager.startup()
    return await code_service.propose(
        conn, project_id, manager, payload.intent, payload.datasets, _known_datasets(project_id)
    )


@router.post(
    "/projects/{project_id}/code/execute",
    response_model=CodeExecutionOut,
    summary="Exécuter une proposition de code",
)
async def execute(project_id: int, payload: CodeProposal) -> CodeExecutionOut:
    """Exécution au niveau 1, correction en boucle, rapprochement des artefacts."""
    from app.llm.manager import build_manager

    conn = await _project_conn(project_id)
    manager = build_manager()
    await manager.startup()
    return await code_service.execute(
        conn, project_id, manager, payload, _known_datasets(project_id)
    )


@router.get(
    "/projects/{project_id}/code/executions",
    response_model=list[CodeExecutionOut],
    summary="Historique des exécutions de code",
)
async def executions(project_id: int) -> list[CodeExecutionOut]:
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
