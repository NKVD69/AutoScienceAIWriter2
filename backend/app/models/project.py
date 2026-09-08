"""Modèles de projet — conformes aux schémas `Project`, `ProjectCreate`,
`ProjectUpdate` de `contracts/openapi.yaml`.

Le contrat est normatif : une divergence entre ces modèles et lui est un
défaut de ces modèles.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class AcademicLevel(StrEnum):
    MASTER = "master"
    INGENIEUR = "ingenieur"
    DOCTORAT = "doctorat"
    HDR = "hdr"


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    subject: str = Field(min_length=10)
    discipline: str | None = None
    language: str = "fr"
    academic_level: AcademicLevel
    target_words: int = Field(default=60_000, ge=5_000)


class ProjectUpdate(BaseModel):
    """Tous les champs sont facultatifs : un PATCH ne remplace pas un objet.

    `academic_level` et `language` en sont absents, conformément au contrat :
    changer le niveau académique d'un mémoire en cours invaliderait le plan
    déjà validé.
    """

    name: str | None = None
    subject: str | None = None
    discipline: str | None = None
    target_words: int | None = None


class Project(BaseModel):
    id: int
    name: str
    subject: str
    discipline: str | None = None
    language: str
    academic_level: AcademicLevel
    target_words: int | None = None
    created_at: str


class BackupResult(BaseModel):
    path: str
    size_bytes: int
    created_at: str
