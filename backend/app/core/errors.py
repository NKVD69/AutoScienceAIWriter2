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


class ProjectNotFoundError(NotFoundError):
    """Projet absent du registre, ou fichier introuvable sur le disque.

    Le second cas est réel : l'utilisateur possède ses fichiers et peut en
    déplacer un. Le message doit alors proposer une action, pas constater
    une absence.
    """

    code = "PROJECT_NOT_FOUND"

    @classmethod
    def unknown(cls, project_id: int) -> ProjectNotFoundError:
        return cls(
            f"Aucun projet d'identifiant {project_id} dans le registre.", project_id=project_id
        )

    @classmethod
    def file_missing(cls, project_id: int, db_path: str) -> ProjectNotFoundError:
        return cls(
            f"Le registre référence le projet {project_id} en « {db_path} », "
            "mais ce fichier est absent du disque. Il a probablement été "
            "déplacé ou supprimé hors de l'application. Le replacer à cet "
            "emplacement, ou retirer l'entrée du registre par DELETE "
            f"/api/v1/projects/{project_id}.",
            project_id=project_id,
            db_path=db_path,
        )


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


class InvalidTransitionError(AppError):
    """Transition refusée par le graphe (ADR-004).

    Le graphe ne s'adapte pas : ce qui n'est pas déclaré n'arrive pas. Le
    message nomme les cibles admises, pour qu'un défaut d'appel se corrige
    sans lire la table.
    """

    code = "INVALID_TRANSITION"
    status_code = 409

    @classmethod
    def undeclared(cls, depuis: object, vers: object, admises: object) -> InvalidTransitionError:
        cibles = ", ".join(sorted(str(c) for c in admises)) or "aucune (état terminal)"
        return cls(
            f"Transition {depuis} → {vers} non déclarée. Cibles admises : {cibles}.",
            current_state=str(depuis),
            attempted_state=str(vers),
        )

    @classmethod
    def human_gate(cls, depuis: object, vers: object) -> InvalidTransitionError:
        return cls(
            f"{depuis} → {vers} est une porte de validation humaine. Aucun nœud, "
            "aucun score de qualité ne la franchit : elle exige un appel "
            "explicite à human_validate.",
            current_state=str(depuis),
            attempted_state=str(vers),
        )


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


class BackendUnavailableError(AppError):
    """Le moteur d'inférence local ne répond pas.

    ADR-003 : le message porte toujours une remédiation. Un « connection
    refused » brut oblige l'utilisateur à deviner qu'un service doit tourner.
    """

    code = "LLM_BACKEND_UNAVAILABLE"
    status_code = 503


class OllamaUnavailableError(BackendUnavailableError):
    code = "OLLAMA_UNAVAILABLE"

    @classmethod
    def actionable(cls, base_url: str, detail: str = "") -> OllamaUnavailableError:
        message = (
            f"Ollama est injoignable sur {base_url}. "
            "Windows : installer depuis https://ollama.com/download puis lancer "
            "« ollama serve » (le service démarre en général automatiquement). "
            "Linux : « curl -fsSL https://ollama.com/install.sh | sh » puis "
            "« systemctl --user start ollama »."
        )
        if detail:
            message = f"{message} Détail : {detail}"
        return cls(message, base_url=base_url)


class LMStudioUnavailableError(BackendUnavailableError):
    code = "LMSTUDIO_UNAVAILABLE"

    @classmethod
    def actionable(cls, base_url: str, detail: str = "") -> LMStudioUnavailableError:
        message = (
            f"LM Studio est injoignable sur {base_url}. Le serveur local n'est pas "
            "démarré par défaut : l'activer dans l'onglet « Developer » de "
            "l'application, ou lancer « lms server start ». Vérification : "
            "« lms status »."
        )
        if detail:
            message = f"{message} Détail : {detail}"
        return cls(message, base_url=base_url)


class ModelDownloadConsentRequiredError(AppError):
    """Poids absents du cache, et consentement `model_download` non accordé.

    ADR-010 : l'application ne télécharge rien d'elle-même. Elle indique quoi
    faire, et l'utilisateur décide.
    """

    code = "CONSENT_REQUIRED"
    status_code = 403

    @classmethod
    def for_embedding(cls, model: str, cache_dir: object) -> ModelDownloadConsentRequiredError:
        return cls(
            f"Le modèle d'embedding « {model} » est absent du cache "
            f"({cache_dir}). Son téléchargement relève du consentement "
            "model_download et n'est pas déclenché automatiquement. "
            "Accorder le consentement par SAW_EMBEDDING_DOWNLOAD_CONSENT=true, "
            "ou déposer le modèle dans le cache hors de l'application.",
            scope="model_download",
            model=model,
        )


class ModelNotFoundError(AppError):
    """Le modèle attendu n'est pas présent localement.

    Aucun téléchargement automatique : les poids sortent du réseau, donc du
    périmètre de consentement `model_download` (ADR-010). La commande est
    proposée, l'utilisateur décide.
    """

    code = "MODEL_NOT_FOUND"
    status_code = 503

    @classmethod
    def with_command(cls, model: str, command: str) -> ModelNotFoundError:
        return cls(
            f"Le modèle « {model} » n'est pas présent localement. "
            f"Le téléchargement relève du consentement model_download et n'est "
            f"pas déclenché automatiquement. Commande : {command}",
            model=model,
            pull_command=command,
        )

    @classmethod
    def with_pull_command(cls, model: str) -> ModelNotFoundError:
        return cls.with_command(model, f"ollama pull {model}")
