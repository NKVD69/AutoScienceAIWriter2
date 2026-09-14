"""Modèles de projet — conformes aux schémas `Project`, `ProjectCreate`,
`ProjectUpdate` de `contracts/openapi.yaml`.

Le contrat est normatif : une divergence entre ces modèles et lui est un
défaut de ces modèles.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

# Étiquette de langue BCP 47 simplifiée : `fr`, `en-GB`, `zh-Hans`. Définition
# unique, reprise par le contrat et par la vérification faite à l'export.
LANGUAGE_TAG_PATTERN = r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$"


class AcademicLevel(StrEnum):
    MASTER = "master"
    INGENIEUR = "ingenieur"
    DOCTORAT = "doctorat"
    HDR = "hdr"


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    subject: str = Field(min_length=10)
    discipline: str | None = None
    # Contraint dès la création, conformément au contrat. Une valeur libre
    # finissait dans `_quarto.yml`, où elle a permis d'injecter une clé que
    # Quarto exécute, et dans un chemin de fichier qui faisait échouer l'export.
    language: str = Field(default="fr", pattern=LANGUAGE_TAG_PATTERN)
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
