"""Pipeline d'ingestion — US-102.

Extraction → découpage → embeddings → indexation, source par source.

Trois propriétés portent ce module, chacune contre un défaut précis.

**Transaction par source, pas par chunk.** Une source interrompue au
cinquantième chunk laisserait une source à moitié indexée : la recherche y
puiserait des extraits sans que rien ne signale que le reste manque.

**Deux essais, puis on continue.** Une source illisible ne doit jamais bloquer
l'ingestion des deux cents autres. Le circuit breaker d'ADR-008 s'applique
ici à la source, pas au lot.

**Seules les sources approuvées sont ingérées.** L'approbation est une porte
humaine : ce qui n'a pas été relu par l'auteur n'entre pas dans la base sur
laquelle il rédigera.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from app.core.logging import get_logger
from app.db.session import transaction
from app.db.vector import insert_chunk_with_embedding
from app.models.audit import AuditEventType
from app.models.source import Task, TaskState
from app.rag.chunker import Chunk, chunk_document
from app.rag.embeddings import EmbeddingService, get_embedding_service
from app.rag.extractor import extract
from app.services import audit_service, task_service

logger = get_logger(__name__)

# ADR-008 appliqué à la source : deux essais, puis échec marqué et poursuite.
MAX_ATTEMPTS = 2


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def approved_pending(conn: aiosqlite.Connection) -> list[tuple[int, str]]:
    """Sources approuvées qui n'ont pas encore de chunks. (id, chemin)."""
    async with conn.execute(
        "SELECT s.id, s.file_path FROM source_document s"
        " WHERE s.approved_at IS NOT NULL AND s.file_path IS NOT NULL"
        " AND NOT EXISTS (SELECT 1 FROM chunk c WHERE c.source_id = s.id)"
        " ORDER BY s.id"
    ) as cur:
        return [(int(r[0]), str(r[1])) for r in await cur.fetchall()]


async def _index_source(
    conn: aiosqlite.Connection,
    source_id: int,
    chunks: list[Chunk],
    embeddings: EmbeddingService,
    on_batch: object | None = None,
) -> int:
    """Vectorise puis indexe les chunks d'une source, en une transaction."""
    vecteurs = await embeddings.embed_documents(
        [c.text for c in chunks],
        on_progress=on_batch,  # type: ignore[arg-type]
    )
    async with transaction(conn):
        for chunk, vecteur in zip(chunks, vecteurs, strict=True):
            await insert_chunk_with_embedding(
                conn,
                source_id=source_id,
                ordinal=chunk.ordinal,
                text=chunk.text,
                embedding=vecteur,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                section_kind=str(chunk.section_kind),
                tx=True,
            )
    return len(chunks)


async def ingest_project(
    conn: aiosqlite.Connection,
    project_id: int,
    task: Task,
    embeddings: EmbeddingService | None = None,
) -> Task:
    """Ingère les sources approuvées du projet. Poursuit malgré les échecs."""
    service = embeddings or get_embedding_service()
    sources = await approved_pending(conn)
    if not sources:
        return await task_service.update(conn, task.id, state=TaskState.SOURCES_READY, progress=1.0)

    async with transaction(conn):
        await audit_service.append(
            conn, project_id, AuditEventType.INGESTION_STARTED, {"sources": len(sources)}, tx=True
        )

    total_chunks = 0
    echecs: list[int] = []

    for index, (source_id, chemin) in enumerate(sources):
        # L'annulation se manifeste aux points d'attente : entre deux
        # sources ici, entre deux lots dans le service d'embeddings (US-005).
        # Un `current_task().cancelled()` serait inopérant — il ne devient
        # vrai qu'une fois la tâche terminée.
        await asyncio.sleep(0)

        derniere_erreur: Exception | None = None
        for essai in range(1, MAX_ATTEMPTS + 1):
            try:
                document = extract(Path(chemin))
                chunks = chunk_document(document)
                if not chunks:
                    raise ValueError("aucun chunk produit : document vide après extraction")
                total_chunks += await _index_source(conn, source_id, chunks, service)
                derniere_erreur = None
                break
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # une source illisible n'arrête pas le lot
                derniere_erreur = exc
                logger.warning("Source %s, essai %s/%s : %s", source_id, essai, MAX_ATTEMPTS, exc)

        if derniere_erreur is not None:
            echecs.append(source_id)
            async with transaction(conn):
                await audit_service.append(
                    conn,
                    project_id,
                    AuditEventType.INGESTION_FAILED,
                    {"source_id": source_id, "erreur": str(derniere_erreur)[:500]},
                    tx=True,
                )

        await task_service.update(conn, task.id, progress=(index + 1) / len(sources))

    async with transaction(conn):
        await audit_service.append(
            conn,
            project_id,
            AuditEventType.INGESTION_COMPLETED,
            {"chunks": total_chunks, "sources_en_echec": len(echecs)},
            tx=True,
        )

    return await task_service.update(
        conn,
        task.id,
        state=TaskState.SOURCES_READY,
        progress=1.0,
        last_error=(f"{len(echecs)} source(s) non ingérée(s) : {echecs}" if echecs else None),
    )
