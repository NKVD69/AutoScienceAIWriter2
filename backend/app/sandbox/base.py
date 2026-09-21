"""Interface d'exécution isolée et modèles associés — US-004, ADR-005.

**Le niveau d'isolation est une propriété du RUNTIME, jamais un choix d'agent.**
Un agent qui pourrait demander le niveau natif contournerait la seule garantie
que le produit offre sur du code écrit par une IA. La règle est donc portée par
les types : `SandboxExecutor.level` est fixé par la classe, et la fabrique
(US-004, `factory.py`) refuse à une origine agent tout autre niveau que le Wasm.

**`network_isolation_guaranteed` est un champ HONNÊTE.** Il vaut `True` au
niveau 1 sur tout OS — le runtime WebAssembly n'a pas de pile réseau —, `True`
au niveau 2 sous Linux seulement si `unshare -n` a réussi, et `False` au
niveau 2 sous Windows, sans exception : les Job Objects n'isolent pas le réseau
(ADR-005, D-03). Ce champ est persisté et affiché ; le mémoire sur lequel une
figure a été produite doit pouvoir dire sous quelle garantie elle l'a été.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field, field_validator


class SandboxOrigin(StrEnum):
    """Qui assume le code. Détermine le niveau d'isolation minimal, pas l'inverse."""

    AGENT = "agent"
    USER = "user"


class SandboxMode(StrEnum):
    """Niveau DEMANDÉ. Pour une origine agent, il est ignoré au profit du Wasm."""

    WASM = "wasm"
    NATIVE = "native"


class SandboxLevel(IntEnum):
    """Niveau EFFECTIF. Croissant en capacités, décroissant en garanties."""

    WASM = 1
    NATIVE = 2


class ResourceLimits(BaseModel):
    """Bornes appliquées par le superviseur, jamais par le code invité.

    Les valeurs par défaut visent une analyse scientifique modeste : de quoi
    tracer une figure sur quelques milliers de points sans laisser une boucle
    infinie ou une allocation débridée emporter le poste.
    """

    memory_mb: int = Field(default=2048, ge=64)
    cpu_seconds: int = Field(default=60, ge=1)
    wall_seconds: int = Field(default=120, ge=1)
    max_file_mb: int = Field(default=512, ge=1)
    max_processes: int = Field(default=8, ge=1)


class MountSpec(BaseModel):
    """Un chemin hôte exposé au bac à sable. En lecture seule sauf mention.

    `guest_path` est POSIX : le système de fichiers virtuel du Wasm ignore les
    lettres de lecteur Windows, et un chemin natif sous Windows y serait illisible.
    """

    host_path: Path
    guest_path: PurePosixPath
    writable: bool = False

    @field_validator("guest_path")
    @classmethod
    def _guest_absolute(cls, valeur: PurePosixPath) -> PurePosixPath:
        if not valeur.is_absolute():
            raise ValueError(
                f"guest_path doit être absolu dans le système virtuel : « {valeur} » "
                "est relatif. Un montage relatif n'a pas d'ancrage connu du runtime."
            )
        return valeur


class ExecutionResult(BaseModel):
    """Ce qu'une exécution a produit, mesures et garanties comprises."""

    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    level: SandboxLevel
    timed_out: bool = False
    # « memory » | « cpu » | « wall » | None : quelle borne a été franchie, s'il
    # y en a une. Distinguer un dépassement d'une erreur de code permet à
    # l'appelant (US-401) de savoir s'il faut réécrire ou alléger.
    limit_exceeded: Literal["memory", "cpu", "wall"] | None = None
    # Fichiers réellement produits dans le répertoire de sortie.
    artifacts: list[Path] = []
    network_isolation_guaranteed: bool


def collect_artifacts(output_dir: Path) -> list[Path]:
    """Fichiers présents dans le répertoire de sortie, triés.

    Opération de disque bloquante : les exécuteurs asynchrones l'appellent via
    un thread pour ne pas immobiliser l'event loop.
    """
    return sorted(p for p in output_dir.rglob("*") if p.is_file())


@runtime_checkable
class SandboxExecutor(Protocol):
    """Contrat minimal d'un exécuteur isolé.

    `level` est une propriété de CLASSE : c'est ce qui rend impossible qu'un
    exécuteur Wasm se présente comme natif. `run` ne reçoit ni connexion ni
    projet : la persistance et le consentement sont l'affaire de l'orchestration
    appelante, pas de l'exécuteur, qui ne fait qu'exécuter.
    """

    level: SandboxLevel

    async def run(
        self,
        code: str,
        mounts: list[MountSpec],
        limits: ResourceLimits,
        output_dir: Path,
    ) -> ExecutionResult: ...
