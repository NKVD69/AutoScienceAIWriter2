"""Erreurs applicatives et leur projection sur le contrat HTTP.

Le contrat `contracts/openapi.yaml` impose la forme `{code, message, details}`.
Chaque erreur porte donc un code stable, destiné au frontend, distinct du
message destiné à l'humain.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Racine des erreurs métier. `status_code` est la projection HTTP."""

    code: str = "INTERNAL_ERROR"
    status_code: int = 500

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = details

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return payload


class NotFoundError(AppError):
    code = "NOT_FOUND"
    status_code = 404


class ValidationFailedError(AppError):
    code = "VALIDATION_FAILED"
    status_code = 422


class ConflictError(AppError):
    """Transition refusée : l'état courant n'autorise pas l'opération.

    Spécifications §5.2 — une demande de rédaction avant `PLAN_VALIDATED`
    répond 409 en nommant l'état requis, jamais un 500 opaque.
    """

    code = "STATE_CONFLICT"
    status_code = 409

    def __init__(self, message: str, current_state: str, required_state: str) -> None:
        super().__init__(message)
        self.current_state = current_state
        self.required_state = required_state

    def to_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "current_state": self.current_state,
            "required_state": self.required_state,
        }


class ExtensionLoadError(AppError):
    """Le binaire SQLite de l'interpréteur refuse les extensions chargeables.

    ADR-002 et §14 : défaut bloquant, message actionnable obligatoire. On ne
    contourne pas ce cas par une recherche vectorielle en Python pur — un
    repli silencieux transformerait une panne d'installation en lenteur
    inexplicable à 50 000 chunks.
    """

    code = "SQLITE_EXTENSION_UNAVAILABLE"
    status_code = 500

    REMEDIATION = (
        "Le module sqlite3 de l'interpréteur courant est compilé sans support "
        "des extensions chargeables. Remédiation : installer Python depuis "
        "python.org, ou ajouter le paquet pysqlite3-binary."
    )

    @classmethod
    def unsupported(cls) -> ExtensionLoadError:
        return cls(cls.REMEDIATION)


class DimensionMismatchError(AppError):
    """La dimension d'embedding du projet diffère de celle configurée.

    §4.3 : changer de modèle d'embedding impose une réindexation. On refuse
    d'ouvrir le projet plutôt que de mélanger deux espaces vectoriels.
    """

    code = "EMBEDDING_DIMENSION_MISMATCH"
    status_code = 500

    def __init__(self, expected: int, found: int) -> None:
        super().__init__(
            f"Dimension d'embedding du projet ({found}) différente de la "
            f"configuration ({expected}). Une réindexation complète est requise.",
            expected=expected,
            found=found,
        )
