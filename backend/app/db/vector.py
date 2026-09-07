"""Couche d'accès vectorielle — insertion et recherche KNN avec provenance.

ADR-002. L'invariant `vec_chunk.rowid == chunk.id` est maintenu ici et
nulle part ailleurs : toute écriture dans `vec_chunk` passe par
`insert_chunk_with_embedding`, qui écrit d'abord dans `chunk` et réutilise
l'identifiant obtenu.
"""

from __future__ import annotations

import aiosqlite
import sqlite_vec
from pydantic import BaseModel

from app.core.config import get_settings
from app.db.session import transaction

# `sqlite-vec` applique le KNN AVANT toute jointure : la table virtuelle ne
# connaît ni la source ni l'année. Un filtre relationnel appliqué après coup
# peut donc vider un résultat de k lignes. On élargit k en interne, puis on
# tronque à k après filtrage.
_FILTER_EXPANSION = 4
_FILTER_EXPANSION_CAP = 200


class ChunkHit(BaseModel):
    """Résultat de recherche, porteur de sa provenance.

    `page_start` et `page_end` sont transportés jusqu'ici parce qu'une
    citation qu'on ne peut pas retrouver dans le PDF d'origine n'est pas
    vérifiable (spécifications §7.1).
    """

    chunk_id: int
    source_id: int
    text: str
    page_start: int | None
    page_end: int | None
    distance: float
    source_title: str
    source_year: int | None
    source_doi: str | None
    is_preprint: bool


async def insert_chunk_with_embedding(
    conn: aiosqlite.Connection,
    source_id: int,
    ordinal: int,
    text: str,
    embedding: list[float],
    page_start: int | None = None,
    page_end: int | None = None,
) -> int:
    """Insère un chunk et son vecteur dans une seule transaction.

    Les deux écritures sont indissociables : un `chunk` sans vecteur est
    invisible à la recherche, un vecteur sans `chunk` est un résultat sans
    provenance — donc inutilisable pour citer.
    """
    settings = get_settings()
    if len(embedding) != settings.embedding_dim:
        raise ValueError(
            f"Dimension d'embedding attendue {settings.embedding_dim}, reçue {len(embedding)}."
        )

    async with transaction(conn):
        cur = await conn.execute(
            "INSERT INTO chunk (source_id, ordinal, text, page_start, page_end, token_count) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (source_id, ordinal, text, page_start, page_end, len(text.split())),
        )
        chunk_id = cur.lastrowid
        if chunk_id is None:  # pragma: no cover - le moteur renseigne toujours lastrowid
            raise RuntimeError("SQLite n'a pas retourné d'identifiant pour le chunk inséré.")
        await conn.execute(
            "INSERT INTO vec_chunk (rowid, embedding) VALUES (?, ?)",
            (chunk_id, sqlite_vec.serialize_float32(embedding)),
        )
    return int(chunk_id)


async def search_similar_chunks(
    conn: aiosqlite.Connection,
    query_embedding: list[float],
    k: int = 5,
    project_id: int | None = None,
    year_min: int | None = None,
    exclude_preprints: bool = False,
) -> list[ChunkHit]:
    """Recherche KNN, jointe à la provenance relationnelle."""
    settings = get_settings()
    if len(query_embedding) != settings.embedding_dim:
        raise ValueError(
            f"Dimension d'embedding attendue {settings.embedding_dim}, "
            f"reçue {len(query_embedding)}."
        )

    filters: list[str] = []
    params: list[object] = []
    if project_id is not None:
        filters.append("s.project_id = ?")
        params.append(project_id)
    if year_min is not None:
        filters.append("s.year >= ?")
        params.append(year_min)
    if exclude_preprints:
        filters.append("s.is_preprint = 0")

    inner_k = min(k * _FILTER_EXPANSION, _FILTER_EXPANSION_CAP) if filters else k
    where = f"WHERE {' AND '.join(filters)}" if filters else ""

    sql = f"""
        WITH knn AS (
            SELECT rowid AS chunk_id, distance
            FROM vec_chunk
            WHERE embedding MATCH ? AND k = ?
        )
        SELECT c.id, c.source_id, c.text, c.page_start, c.page_end, knn.distance,
               s.title, s.year, s.doi, s.is_preprint
        FROM knn
        JOIN chunk c ON c.id = knn.chunk_id
        JOIN source_document s ON s.id = c.source_id
        {where}
        ORDER BY knn.distance
        LIMIT ?
    """
    args = [sqlite_vec.serialize_float32(query_embedding), inner_k, *params, k]

    async with conn.execute(sql, args) as cur:
        rows = await cur.fetchall()

    return [
        ChunkHit(
            chunk_id=int(r[0]),
            source_id=int(r[1]),
            text=str(r[2]),
            page_start=r[3],
            page_end=r[4],
            distance=float(r[5]),
            source_title=str(r[6]),
            source_year=r[7],
            source_doi=r[8],
            is_preprint=bool(r[9]),
        )
        for r in rows
    ]
