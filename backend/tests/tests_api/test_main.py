"""US-001 — point d'entree : demarrage sans moteur, erreurs projetees sur le contrat."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest
from fastapi.testclient import TestClient

from app import main as module_main
from app.core.config import get_settings
from app.core.errors import LMStudioUnavailableError
from app.main import create_app, describe_validation_error


class ManagerSansMoteur:
    """Manager dont le demarrage echoue, comme lorsque LM Studio n'est pas lance."""

    def __init__(self) -> None:
        self.demarrages = 0

    async def startup(self) -> None:
        self.demarrages += 1
        raise LMStudioUnavailableError.actionable("http://127.0.0.1:1234")


@pytest.fixture
def donnees(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path)
    return tmp_path


def test_service_starts_even_when_the_llm_backend_is_down(
    donnees: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-003 : un moteur absent n'empeche pas le demarrage. L'import de
    sources, la bibliographie et l'export n'en dependent pas."""
    manager = ManagerSansMoteur()
    monkeypatch.setattr(module_main, "build_manager", lambda: manager)

    with TestClient(create_app()) as client:
        assert client.app.state.llm is manager

    assert manager.demarrages == 1
    assert (donnees / "projects").is_dir()


def test_database_error_is_structured_and_does_not_leak_engine_text(donnees: Path) -> None:
    """Une erreur SQLite imprevue sortait en trace brute. Elle est projetee sur
    `{code, message}`, sans le texte du moteur : il devoilerait la structure de
    la base sans rien apprendre d'actionnable au client."""
    app = create_app()

    @app.get("/sonde-erreur-base")
    async def _sonde() -> None:
        raise aiosqlite.OperationalError("no such table: draft_section_secrete")

    # Sans `with` : le cycle de vie, qui chargerait le vrai modele, ne tourne pas.
    reponse = TestClient(app).get("/sonde-erreur-base")

    assert reponse.status_code == 500
    assert reponse.json()["code"] == "DATABASE_ERROR"
    assert "draft_section_secrete" not in reponse.text


def test_validation_error_summary_names_field_and_reason() -> None:
    erreur = {"loc": ("body", "formats", 0), "msg": "Input should be 'pdf', 'docx' or 'html'"}
    assert describe_validation_error(erreur) == (
        "formats.0 : Input should be 'pdf', 'docx' or 'html'"
    )
    assert describe_validation_error({"loc": ("body",), "msg": "Field required"}) == (
        "Field required"
    )
