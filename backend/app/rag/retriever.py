"""Recherche sémantique — US-102, spécifications §7.2.

Deux responsabilités, et une seule ligne de conduite : ne rien décider que
les couches inférieures décident déjà mieux.

- La requête est vectorisée par le service d'embeddings, qui applique le
  préfixe `search_query:` (US-005). Le retriever ne le fait pas lui-même :
  un préfixe appliqué à deux endroits serait appliqué deux fois.
- Le KNN et l'élargissement de `k` en présence de filtres appartiennent à
  `search_similar_chunks` (US-002).

Ce qui est propre au retriever : **les chunks de bibliographie ne sortent
pas.** Rendre à l'agent rédacteur une liste de titres qu'il n'a pas lus, c'est
lui fournir la matière première d'une citation inventée.
"""

from __future__ import annotations

import aiosqlite

from app.core.logging import get_logger
from app.db.vector import ChunkHit, search_similar_chunks
from app.rag.embeddings import EmbeddingService, get_embedding_service

logger = get_logger(__name__)


async def retrieve(
    conn: aiosqlite.Connection,
    query: str,
    k: int = 5,
    year_min: int | None = None,
    exclude_preprints: bool = False,
    include_references: bool = False,
    project_id: int | None = None,
    service: EmbeddingService | None = None,
) -> list[ChunkHit]:
    """Extraits les plus proches de la requête, avec leur provenance."""
    embeddings = service or get_embedding_service()
    vecteur = await embeddings.embed_query(query)

    resultats = await search_similar_chunks(
        conn,
        vecteur,
        k=k,
        project_id=project_id,
        year_min=year_min,
        exclude_preprints=exclude_preprints,
        include_references=include_references,
    )
    logger.debug(
        "recherche k=%s filtres(annee=%s, preprints=%s, refs=%s) → %s résultats",
        k,
        year_min,
        exclude_preprints,
        include_references,
        len(resultats),
    )
    return resultats
