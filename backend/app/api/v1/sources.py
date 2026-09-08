"""Endpoints des sources, de l'ingestion et de la recherche — US-102.

Contrat `contracts/openapi.yaml`, sections `/sources`, `/ingest`, `/search`.

**Les métadonnées extraites d'un PDF sont proposées, jamais vérifiées.** Un
titre lu dans les propriétés d'un fichier est souvent celui du gabarit de
l'éditeur, ou vide. `approved_at` reste nul jusqu'à ce qu'un humain regarde.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
from fastapi import APIRouter, File, Form, Query, Response, UploadFile, status

from app.core.errors import ConflictError, NotFoundError, ProjectNotFoundError
from app.core.logging import get_logger
from app.db import registry
from app.db.pool import get_pool
from app.db.session import transaction
from app.db.vector import ChunkHit
from app.models.audit import AuditEventType
from app.models.source import SearchRequest, SourceDocument, SourceKind, Task, TaskState
from app.rag.extractor import SUPPORTED_SUFFIXES, UnsupportedDocumentError, extract
from app.rag.retriever import retrieve
from app.services import audit_service, ingestion_service, task_service

logger = get_logger(__name__)
router = APIRouter(tags=["sources"])


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def _project(project_id: int) -> tuple[aiosqlite.Connection, registry.ProjectRef]:
    async with registry.connect_registry() as reg:
        ref = await registry.get(reg, project_id)
    if ref is None:
        raise ProjectNotFoundError.unknown(project_id)
    return await get_pool().acquire(project_id, ref.db_path), ref


def _sources_dir(ref: registry.ProjectRef) -> Path:
    return Path(ref.db_path).parent / ref.slug / "sources"


async def _row_to_source(conn: aiosqlite.Connection, row: aiosqlite.Row) -> SourceDocument:
    async with conn.execute(
        "SELECT count(*) FROM chunk WHERE source_id = ?", (int(row[0]),)
    ) as cur:
        chunks = int((await cur.fetchone())[0])
    return SourceDocument(
        id=int(row[0]),
        kind=SourceKind(str(row[1])),
        title=str(row[2]),
        authors=row[3],
        year=row[4],
        doi=row[5],
        url=row[6],
        venue=row[7],
        is_preprint=bool(row[8]),
        file_path=row[9],
        sha256=row[10],
        chunk_count=chunks,
        imported_at=str(row[11]),
        approved_at=row[12],
    )


_SELECT = (
    "SELECT id, kind, title, authors, year, doi, url, venue, is_preprint,"
    " file_path, sha256, imported_at, approved_at FROM source_document"
)


@router.get(
    "/projects/{project_id}/sources",
    response_model=list[SourceDocument],
    summary="Lister les sources du projet",
)
async def list_sources(
    project_id: int,
    approved: bool | None = Query(default=None),
    is_preprint: bool | None = Query(default=None),
) -> list[SourceDocument]:
    conn, _ = await _project(project_id)
    conditions: list[str] = []
    if approved is True:
        conditions.append("approved_at IS NOT NULL")
    elif approved is False:
        conditions.append("approved_at IS NULL")
    if is_preprint is not None:
        conditions.append(f"is_preprint = {1 if is_preprint else 0}")
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""

    # `where` est construit à partir de littéraux, jamais d'entrée utilisateur.
    async with conn.execute(f"{_SELECT}{where} ORDER BY id") as cur:
        rows = await cur.fetchall()
    return [await _row_to_source(conn, r) for r in rows]


@router.post(
    "/projects/{project_id}/sources",
    response_model=SourceDocument,
    status_code=status.HTTP_201_CREATED,
    summary="Importer un document",
)
async def import_source(
    project_id: int,
    response: Response,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    doi: str | None = Form(default=None),
) -> SourceDocument:
    conn, ref = await _project(project_id)

    suffixe = Path(file.filename or "").suffix.lower()
    if suffixe not in SUPPORTED_SUFFIXES:
        raise UnsupportedDocumentError(
            f"Extension « {suffixe or 'aucune'} » non prise en charge. "
            f"Formats acceptés : {', '.join(sorted(SUPPORTED_SUFFIXES))}.",
            filename=file.filename,
        )

    contenu = await file.read()
    empreinte = hashlib.sha256(contenu).hexdigest()

    # Le même fichier importé deux fois est le même document : renvoyer
    # l'existant plutôt qu'un doublon, qui doublerait aussi ses chunks et
    # fausserait toute recherche.
    async with conn.execute(f"{_SELECT} WHERE sha256 = ?", (empreinte,)) as cur:
        existante = await cur.fetchone()
    if existante is not None:
        response.status_code = status.HTTP_200_OK
        source = await _row_to_source(conn, existante)
        return source.model_copy(update={"duplicate": True})

    destination = _sources_dir(ref) / f"{empreinte}{suffixe}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(contenu)

    # Métadonnées PROPOSÉES : `approved_at` reste nul.
    proposees: dict[str, str] = {}
    try:
        proposees = extract(destination).metadata
    except Exception:
        logger.debug("Métadonnées non extractibles de %s", file.filename, exc_info=True)

    async with transaction(conn):
        cur = await conn.execute(
            "INSERT INTO source_document (project_id, kind, title, authors, doi, venue,"
            " is_preprint, file_path, sha256, imported_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                1,
                str(SourceKind.ARTICLE),
                title or proposees.get("title") or Path(file.filename or "source").stem,
                proposees.get("authors"),
                doi,
                proposees.get("venue"),
                0,
                str(destination),
                empreinte,
                _now(),
            ),
        )
        source_id = int(cur.lastrowid or 0)
        await audit_service.append(
            conn,
            project_id,
            AuditEventType.SOURCE_IMPORTED,
            {"source_id": source_id, "sha256": empreinte, "filename": file.filename},
            tx=True,
        )

    async with conn.execute(f"{_SELECT} WHERE id = ?", (source_id,)) as cur:
        row = await cur.fetchone()
    return await _row_to_source(conn, row)  # type: ignore[arg-type]


@router.post(
    "/projects/{project_id}/sources/{source_id}/approve",
    response_model=SourceDocument,
    summary="Approuver une source",
)
async def approve_source(project_id: int, source_id: int) -> SourceDocument:
    """Porte de validation humaine. Seules les sources approuvées sont ingérées."""
    conn, _ = await _project(project_id)
    async with conn.execute(f"{_SELECT} WHERE id = ?", (source_id,)) as cur:
        if await cur.fetchone() is None:
            raise NotFoundError(f"Source {source_id} introuvable.", source_id=source_id)

    async with transaction(conn):
        await conn.execute(
            "UPDATE source_document SET approved_at = ? WHERE id = ?", (_now(), source_id)
        )
        await audit_service.append(
            conn, project_id, AuditEventType.SOURCE_APPROVED, {"source_id": source_id}, tx=True
        )

    async with conn.execute(f"{_SELECT} WHERE id = ?", (source_id,)) as cur:
        row = await cur.fetchone()
    return await _row_to_source(conn, row)  # type: ignore[arg-type]


@router.delete(
    "/projects/{project_id}/sources/{source_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Supprimer une source",
)
async def delete_source(project_id: int, source_id: int) -> Response:
    """Refusée si la source est citée : `citation.source_id` est en RESTRICT."""
    conn, _ = await _project(project_id)
    async with conn.execute(
        "SELECT count(*) FROM citation WHERE source_id = ?", (source_id,)
    ) as cur:
        citations = int((await cur.fetchone())[0])
    if citations:
        raise ConflictError(
            f"La source {source_id} est citée dans {citations} passage(s). "
            "Retirer ces citations avant de la supprimer.",
            current_state="cited",
            required_state="uncited",
        )

    async with transaction(conn):
        await conn.execute("DELETE FROM source_document WHERE id = ?", (source_id,))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/projects/{project_id}/ingest",
    response_model=Task,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Lancer l'ingestion RAG des sources approuvées",
)
async def ingest(project_id: int) -> Task:
    conn, _ = await _project(project_id)

    # Une ingestion laissée en cours par un arrêt du backend reprend à
    # l'état persisté, jamais depuis le début.
    reprise = await task_service.resumable(conn, project_id)
    if reprise is not None:
        logger.info("Reprise de la tâche d'ingestion %s", reprise.id)
        return await ingestion_service.ingest_project(conn, project_id, reprise)

    if not await ingestion_service.approved_pending(conn):
        raise ConflictError(
            "Aucune source approuvée à ingérer. Approuver au moins une source "
            "avant de lancer l'ingestion.",
            current_state="no_approved_source",
            required_state="approved_source",
        )

    tache = await task_service.create(
        conn, project_id, TaskState.SOURCES_INGESTING, agent="ingestion"
    )
    return await ingestion_service.ingest_project(conn, project_id, tache)


@router.post(
    "/projects/{project_id}/search",
    response_model=list[ChunkHit],
    summary="Recherche sémantique dans la base de connaissances",
)
async def search(project_id: int, payload: SearchRequest) -> list[ChunkHit]:
    conn, _ = await _project(project_id)
    return await retrieve(
        conn,
        payload.query,
        k=payload.k,
        year_min=payload.year_min,
        exclude_preprints=payload.exclude_preprints,
    )
