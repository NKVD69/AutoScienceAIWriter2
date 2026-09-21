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


# --- Bac à sable d'exécution (US-004, ADR-005) ---------------------------


class UnsupportedPlatformError(AppError):
    """Aucun exécuteur natif pour cette plateforme.

    Le niveau 2 s'appuie sur des primitives d'OS — Job Objects, setrlimit —
    qui n'existent pas partout. Plutôt qu'un exécuteur factice donnant une
    fausse impression d'isolation, on refuse explicitement.
    """

    code = "SANDBOX_UNSUPPORTED_PLATFORM"
    status_code = 500

    @classmethod
    def for_platform(cls, platform: str) -> UnsupportedPlatformError:
        return cls(
            f"Aucun bac à sable natif pour la plateforme « {platform} ». "
            "Le niveau natif est fourni pour win32 et linux ; le niveau Wasm, "
            "lui, est disponible partout.",
            platform=platform,
        )


class PackageUnavailableInWasmError(AppError):
    """Import d'un paquet hors de la liste blanche du niveau 1.

    Jamais un échec sec : le message NOMME le paquet manquant et PROPOSE le
    passage au niveau 2 sous consentement. C'est une décision humaine — un
    paquet absent du Wasm n'est pas une erreur de code (US-401 ne relance pas
    l'agent là-dessus), c'est une limite du bac à sable le plus étanche.
    """

    code = "WASM_PACKAGE_UNAVAILABLE"
    status_code = 422

    @classmethod
    def suggest_level2(cls, package: str) -> PackageUnavailableInWasmError:
        return cls(
            f"Le paquet « {package} » n'est pas disponible au niveau 1 (WebAssembly). "
            f"Les paquets admis y sont numpy, pandas, scipy, matplotlib, sympy et "
            f"scikit-learn. Pour utiliser « {package} », l'exécution doit passer au "
            "niveau 2 (natif), sous consentement native_execution : c'est une "
            "décision de l'auteur, pas une correction de code.",
            package=package,
            suggested_level=2,
        )


class ConsentRequiredError(AppError):
    """Niveau 2 demandé sans consentement native_execution accordé.

    Le niveau natif ouvre des capacités que le runtime ne borne plus : réseau
    sous Windows, disque hors montage. On ne le lance pas sans un accord de
    périmètre explicite, vérifié avant le démarrage et journalisé.
    """

    code = "CONSENT_REQUIRED"
    status_code = 403

    @classmethod
    def native_execution(cls) -> ConsentRequiredError:
        return cls(
            "L'exécution native (niveau 2) exige le consentement de périmètre "
            "« native_execution », absent pour ce projet. Le niveau natif n'isole "
            "ni le réseau sous Windows ni le disque hors des montages déclarés : "
            "l'accorder est une décision de l'auteur. Le niveau 1 (WebAssembly) "
            "ne demande aucun consentement.",
            scope="native_execution",
        )


class WasmRuntimeUnavailableError(AppError):
    """Le runtime Pyodide/Node ne peut pas être démarré.

    ADR-010 : la distribution Pyodide est vendorisée hors-ligne. Son absence
    est un défaut d'installation, rapporté avec la remédiation, jamais un repli
    silencieux vers une exécution non isolée.
    """

    code = "SANDBOX_RUNTIME_UNAVAILABLE"
    status_code = 503

    @classmethod
    def actionable(cls, detail: str = "") -> WasmRuntimeUnavailableError:
        message = (
            "Le runtime WebAssembly (Pyodide via Node) est indisponible. "
            "Node.js doit être installé et la distribution Pyodide vendorisée "
            "sous backend/app/sandbox/runtime (npm install). "
            "L'exécution isolée de niveau 1 en dépend."
        )
        if detail:
            message = f"{message} Détail : {detail}"
        return cls(message)
