"""Modèles de sources et de tâches — schémas `SourceDocument` et `Task`
de `contracts/openapi.yaml`.

`is_preprint` est propagé jusque dans la bibliographie exportée, sans
exception (§7.3) : un lecteur doit pouvoir distinguer une prépublication d'un
article relu par les pairs.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class SourceKind(StrEnum):
    ARTICLE = "article"
    BOOK = "book"
    THESIS = "thesis"
    REPORT = "report"
    STANDARD = "standard"
    PREPRINT = "preprint"
    OTHER = "other"


class SourceDocument(BaseModel):
    id: int
    kind: SourceKind
    title: str
    authors: str | None = None
    year: int | None = None
    doi: str | None = None
    url: str | None = None
    venue: str | None = None
    is_preprint: bool = False
    file_path: str | None = None
    sha256: str | None = None
    chunk_count: int = 0
    imported_at: str
    approved_at: str | None = None
    # Hors contrat : signale qu'un import a rencontré une source déjà
    # présente, plutôt que d'en créer un doublon silencieux.
    duplicate: bool = False


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    k: int = Field(default=5, ge=1, le=50)
    year_min: int | None = None
    exclude_preprints: bool = False


class TaskState(StrEnum):
    """Sous-ensemble de `TaskState` du contrat utilisé par l'ingestion."""

    SOURCES_INGESTING = "SOURCES_INGESTING"
    SOURCES_READY = "SOURCES_READY"
    ERROR_STATE = "ERROR_STATE"


class Task(BaseModel):
    id: int
    project_id: int
    state: TaskState
    agent: str | None = None
    progress: float = 0.0
    retry_count: int = 0
    last_error: str | None = None
    created_at: str
    updated_at: str


class TaskEvent(BaseModel):
    """Événement de flux SSE. Types imposés par le contrat."""

    type: str  # state | progress | error | done
    task_id: int
    payload: dict = Field(default_factory=dict)
