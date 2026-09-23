"""US-401 — API du code et des artefacts, et persistance de reproductibilité.

Deux niveaux : le SERVICE (persistance, rattachement, suppression), éprouvé
contre une base migrée sans HTTP ; et l'API, éprouvée de bout en bout contre un
bac à sable factice — aucun runtime Pyodide, aucun moteur réel.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.core.config import get_settings
from app.db.migrations.runner import run_migrations
from app.db.pool import reset_pool
from app.db.session import connect, transaction
from app.main import create_app
from app.models.code import ExpectedArtifact
from app.rag import retriever
from app.services import artifact_service, code_service, task_service
from app.services.artifact_service import ArtifactReferencedError
from tests.tests_agents.test_code_agent import FakeSandbox, _proposal_obj, _proposal_reply
from tests.tests_api.test_sections_api import brancher_modele, projet_pret

NOW = datetime.now(UTC).isoformat()
DIM = 768


# --- Fixtures -------------------------------------------------------------


@pytest.fixture
async def conn(tmp_path: Path):
    """Base projet migrée, une ligne project. Rend la connexion."""
    async with connect(tmp_path / "projet.sqlite") as c:
        await run_migrations(c)
        async with transaction(c):
            await c.execute(
                "INSERT INTO project (id, name, subject, language, academic_level,"
                " created_at, updated_at) VALUES (1, 'P', 'Sujet', 'fr', 'doctorat', ?, ?)",
                (NOW, NOW),
            )
        yield c


class ServiceEspion:
    async def embed_query(self, texte: str) -> list[float]:
        return [1.0] + [0.0] * (DIM - 1)


@pytest.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path)
    task_service.reset_queues()
    await reset_pool()
    monkeypatch.setattr(retriever, "get_embedding_service", ServiceEspion)
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
        yield c
    await reset_pool()
    task_service.reset_queues()


# --- Aides service --------------------------------------------------------


async def _execution(c, project_id: int = 1) -> int:
    async with transaction(c):
        cur = await c.execute(
            "INSERT INTO code_execution (project_id, origin, sandbox_level, code, exit_code,"
            " duration_ms, started_at) VALUES (?, 'agent', 'wasm', 'print(1)', 0, 5, ?)",
            (project_id, NOW),
        )
        return int(cur.lastrowid or 0)


async def _section(c, content: str = "Introduction.") -> int:
    async with transaction(c):
        cur = await c.execute(
            "INSERT INTO draft_section (plan_node_id, content_qmd, status, version, generated_at)"
            " VALUES (NULL, ?, 'REVIEWING', 1, ?)",
            (content, NOW),
        )
        return int(cur.lastrowid or 0)


def _fig(label: str = "fig-filtration", filename: str = "filtration.png") -> ExpectedArtifact:
    return ExpectedArtifact(filename=filename, kind="figure", caption="Filtration", label=label)


# --- Reproductibilité (persistance) ---------------------------------------


async def test_artifact_records_seed_and_library_versions(conn) -> None:
    exec_id = await _execution(conn)
    [artefact] = await artifact_service.save_artifacts(
        conn,
        exec_id,
        matched=[(_fig(), "artifacts/1/fig.png")],
        undeclared=[],
        code="import numpy as np\nnp.random.seed(7)",
        random_seed=7,
        library_versions={"numpy": "1.26.4", "matplotlib": "3.9.0"},
        dataset_sha256={},
        duration_ms=42,
    )
    relu = await artifact_service.get(conn, artefact.id)
    assert relu.random_seed == 7
    assert relu.library_versions == {"numpy": "1.26.4", "matplotlib": "3.9.0"}
    assert relu.duration_ms == 42


async def test_dataset_sha256_computed_and_recorded(conn, tmp_path) -> None:
    jeu = tmp_path / "donnees.csv"
    jeu.write_bytes(b"a,b\n1,2\n")
    attendu = hashlib.sha256(jeu.read_bytes()).hexdigest()

    resolved = code_service.resolve_datasets(1, ["donnees"], {"donnees": jeu})
    empreintes = code_service.dataset_sha256(resolved)
    assert empreintes == {"donnees": attendu}

    exec_id = await _execution(conn)
    [artefact] = await artifact_service.save_artifacts(
        conn,
        exec_id,
        matched=[(_fig(), "artifacts/1/fig.png")],
        undeclared=[],
        code="...",
        random_seed=None,
        library_versions={},
        dataset_sha256=empreintes,
        duration_ms=1,
    )
    assert (await artifact_service.get(conn, artefact.id)).dataset_sha256 == {"donnees": attendu}


# --- Rattachement et suppression ------------------------------------------


async def _artifact(conn, section_or_none: int | None = None) -> int:
    exec_id = await _execution(conn)
    [artefact] = await artifact_service.save_artifacts(
        conn,
        exec_id,
        matched=[(_fig(), "artifacts/1/filtration.png")],
        undeclared=[],
        code="...",
        random_seed=None,
        library_versions={},
        dataset_sha256={},
        duration_ms=1,
    )
    return artefact.id


async def test_attach_inserts_quarto_reference(conn) -> None:
    section_id = await _section(conn, "Le texte de la section.")
    artifact_id = await _artifact(conn)

    section = await artifact_service.attach(conn, 1, section_id, artifact_id)

    assert "![Filtration](artifacts/1/filtration.png){#fig-filtration}" in section.content_qmd
    assert (await artifact_service.get(conn, artifact_id)).draft_section_id == section_id


async def test_attach_is_idempotent(conn) -> None:
    section_id = await _section(conn)
    artifact_id = await _artifact(conn)

    await artifact_service.attach(conn, 1, section_id, artifact_id)
    section = await artifact_service.attach(conn, 1, section_id, artifact_id)

    assert section.content_qmd.count("{#fig-filtration}") == 1, "un seul renvoi, jamais dupliqué"


async def test_delete_referenced_artifact_returns_409(conn) -> None:
    section_id = await _section(conn)
    artifact_id = await _artifact(conn)
    await artifact_service.attach(conn, 1, section_id, artifact_id)

    with pytest.raises(ArtifactReferencedError):
        await artifact_service.delete(conn, 1, artifact_id)
    # L'artefact est toujours là.
    assert (await artifact_service.get(conn, artifact_id)).id == artifact_id


async def test_delete_unreferenced_artifact_succeeds(conn) -> None:
    from app.core.errors import NotFoundError

    artifact_id = await _artifact(conn)  # jamais rattaché
    await artifact_service.delete(conn, 1, artifact_id)
    with pytest.raises(NotFoundError):
        await artifact_service.get(conn, artifact_id)


# --- API HTTP -------------------------------------------------------------


def _brancher_sandbox(monkeypatch, sandbox: FakeSandbox) -> None:
    monkeypatch.setattr(code_service.factory, "select", lambda *a, **k: sandbox)


async def test_execute_endpoint_persists_execution_with_level(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid, _, _, _ = await projet_pret(client)
    brancher_modele(monkeypatch, [_proposal_reply()])
    _brancher_sandbox(monkeypatch, FakeSandbox([{"files": ["filtration.png"]}]))

    payload = _proposal_obj().model_dump()
    r = await client.post(f"/api/v1/projects/{pid}/code/execute", json=payload)

    assert r.status_code == 200
    corps = r.json()
    assert corps["sandbox_level"] == "wasm"  # exécution tracée avec son niveau
    assert [a["filename"] for a in corps["artifacts"]] == ["filtration.png"]

    liste = (await client.get(f"/api/v1/projects/{pid}/code/executions")).json()
    assert len(liste) == 1
    assert liste[0]["sandbox_level"] == "wasm"


async def test_propose_endpoint_returns_a_validated_proposal(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid, _, _, _ = await projet_pret(client)
    brancher_modele(monkeypatch, [_proposal_reply(intent="tracer la filtration")])

    r = await client.post(
        f"/api/v1/projects/{pid}/code/propose", json={"intent": "tracer", "datasets": []}
    )

    assert r.status_code == 200
    assert r.json()["expected_artifacts"][0]["label"] == "fig-filtration"


async def test_execute_endpoint_rejects_unknown_dataset(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid, _, _, _ = await projet_pret(client)
    brancher_modele(monkeypatch, [_proposal_reply()])
    _brancher_sandbox(monkeypatch, FakeSandbox([{"files": ["filtration.png"]}]))

    payload = _proposal_obj(datasets=["inexistant"]).model_dump()
    r = await client.post(f"/api/v1/projects/{pid}/code/execute", json=payload)

    assert r.status_code == 422
    assert r.json()["code"] == "UNKNOWN_DATASET"
