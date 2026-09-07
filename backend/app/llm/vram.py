"""Lecture de la VRAM et politique du second modèle.

ADR-003 §4. `nvidia-smi` est interrogé en sous-processus : c'est le seul
moyen d'obtenir la mémoire réellement occupée sans dépendance CUDA. Son
absence n'est pas une erreur — le poste peut n'avoir aucun GPU NVIDIA, et le
système doit alors fonctionner en mode dégradé plutôt que refuser de démarrer.
"""

from __future__ import annotations

import shutil
import subprocess

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_QUERY = "--query-gpu=memory.used,memory.total"
_FORMAT = "--format=csv,noheader,nounits"


def _read_memory() -> tuple[int, int] | None:
    """(utilisée, totale) en Mo pour le premier GPU, ou None."""
    binary = shutil.which("nvidia-smi")
    if binary is None:
        return None
    try:
        # Binaire résolu par shutil.which, arguments constants : pas de shell.
        completed = subprocess.run(
            [binary, _QUERY, _FORMAT],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("nvidia-smi injoignable : %s", exc)
        return None
    if completed.returncode != 0:
        logger.warning("nvidia-smi sort en %s : %s", completed.returncode, completed.stderr.strip())
        return None

    first = completed.stdout.strip().splitlines()
    if not first:
        return None
    try:
        used, total = (int(part.strip()) for part in first[0].split(",")[:2])
    except ValueError:
        logger.warning("Sortie nvidia-smi illisible : %r", first[0])
        return None
    return used, total


def read_vram_used_mb() -> int | None:
    memory = _read_memory()
    return memory[0] if memory else None


def read_vram_total_mb() -> int | None:
    memory = _read_memory()
    return memory[1] if memory else None


def policy_allows_code_model(total_mb: int | None) -> bool:
    """Le second résident n'est admis qu'au-delà de 12 Go, option activée.

    Une VRAM inconnue vaut refus : charger un second modèle « au cas où » sur
    une carte non mesurée est le scénario d'éviction du modèle principal en
    plein milieu d'une rédaction.
    """
    settings = get_settings()
    if not settings.code_model_enabled:
        return False
    if total_mb is None:
        return False
    return total_mb >= settings.code_model_min_vram_mb
