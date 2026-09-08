"""Couche RAG — découpage, embeddings, recherche.

Les embeddings sont calculés sur CPU, hors du serveur d'inférence (ADR-013).
"""

from app.rag.embeddings import (
    DOCUMENT_PREFIX,
    QUERY_PREFIX,
    EmbeddingService,
    get_embedding_service,
    reset_embedding_service,
)

__all__ = [
    "DOCUMENT_PREFIX",
    "QUERY_PREFIX",
    "EmbeddingService",
    "get_embedding_service",
    "reset_embedding_service",
]
