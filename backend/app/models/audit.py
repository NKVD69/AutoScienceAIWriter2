"""Modèles du journal à détection d'altération.

ADR-009. Vocabulaire imposé : « détection d'altération », « tamper-evident ».
Le propriétaire du fichier `.sqlite` peut réécrire la table et recalculer
toute la chaîne ; celle-ci **détecte** une altération, elle ne l'empêche pas.
Les deux termes que D-07 proscrit sont énumérés dans le test lexical de
US-701, pas ici : les répéter dans le code applicatif reviendrait à écrire
la promesse qu'ils portent.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class AuditEventType(StrEnum):
    """Événements obligatoires (spécifications §11.2)."""

    PROJECT_CREATED = "PROJECT_CREATED"
    SOURCE_IMPORTED = "SOURCE_IMPORTED"
    SOURCE_APPROVED = "SOURCE_APPROVED"
    INGESTION_STARTED = "INGESTION_STARTED"
    INGESTION_COMPLETED = "INGESTION_COMPLETED"
    INGESTION_FAILED = "INGESTION_FAILED"
    STATE_TRANSITION = "STATE_TRANSITION"
    LLM_CALL = "LLM_CALL"
    GUARDRAIL_TRIGGERED = "GUARDRAIL_TRIGGERED"
    BREAKER_TRIGGERED = "BREAKER_TRIGGERED"
    HUMAN_VALIDATION = "HUMAN_VALIDATION"
    CODE_EXECUTED = "CODE_EXECUTED"
    CONSENT_GRANTED = "CONSENT_GRANTED"
    CONSENT_REVOKED = "CONSENT_REVOKED"
    EXPORT_STARTED = "EXPORT_STARTED"
    EXPORT_COMPLETED = "EXPORT_COMPLETED"
    AI_DECLARATION_DISABLED = "AI_DECLARATION_DISABLED"


class ChainStatus(StrEnum):
    VALIDE = "VALIDE"
    ALTERE = "ALTERE"


class AuditEntry(BaseModel):
    """Une entrée du journal, telle qu'inscrite."""

    id: int
    project_id: int | None
    event_type: str
    payload: dict
    prev_hash: str
    hash: str
    created_at: str


class ChainVerification(BaseModel):
    """Résultat d'une vérification de chaîne.

    `first_invalid_index` est l'index de parcours (0 pour la première entrée
    du projet), `first_invalid_id` la clé primaire correspondante : l'un
    situe le défaut dans la lecture, l'autre permet de le retrouver en base.
    """

    status: ChainStatus
    entries_checked: int
    first_invalid_index: int | None = None
    first_invalid_id: int | None = None
