from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Le paquet applicatif est `backend/app` ; il est importable comme `app`.
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


@pytest.fixture
def project_db(tmp_path: Path) -> Path:
    """Chemin d'un fichier projet .sqlite, non créé (ADR-001 : un fichier par projet)."""
    return tmp_path / "projet.sqlite"
