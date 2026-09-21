"""Bac à sable d'exécution à deux niveaux — US-004, ADR-005.

Niveau 1 (WebAssembly/Pyodide) pour tout code d'agent : isolation réseau et
disque garantie par le runtime, identique sur tout OS. Niveau 2 (natif, sous
consentement) pour le code que l'utilisateur assume, avec des capacités plus
larges et des garanties d'isolation moindres — honnêtement déclarées.
"""

from __future__ import annotations

from app.sandbox.base import (
    ExecutionResult,
    MountSpec,
    ResourceLimits,
    SandboxExecutor,
    SandboxLevel,
    SandboxMode,
    SandboxOrigin,
)
from app.sandbox.factory import (
    native_consent_granted,
    persist_execution,
    run_sandboxed,
    select,
)

__all__ = [
    "ExecutionResult",
    "MountSpec",
    "ResourceLimits",
    "SandboxExecutor",
    "SandboxLevel",
    "SandboxMode",
    "SandboxOrigin",
    "native_consent_granted",
    "persist_execution",
    "run_sandboxed",
    "select",
]
