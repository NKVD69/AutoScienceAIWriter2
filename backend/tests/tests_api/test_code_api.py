"""US-401 — API du code et des artefacts, et persistance de reproductibilité.

Deux niveaux : le SERVICE (persistance, rattachement, suppression), éprouvé
contre une base migrée sans HTTP ; et l'API, éprouvée de bout en bout contre un
bac à sable factice — aucun runtime Pyodide, aucun moteur réel.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import yaml

from app.core.config import get_settings
from app.db.migrations.runner import run_migrations
from app.db.pool import reset_pool
from app.db.session import connect, transaction
from app.main import create_app
from app.models.code import ExpectedArtifact
from app.rag import retriever
from app.sandbox.base import SandboxLevel
from app.services import artifact_service, code_service, task_service
from app.services.artifact_service import ArtifactReferencedError
from tests.tests_agents.test_code_agent import FakeSandbox, _proposal_reply
from tests.tests_api.test_sections_api import brancher_modele, projet_pret

NOW = datetime.now(UTC).isoformat()
DIM = 768
CONTRAT = Path(__file__).resolve().parents[3] / "contracts" / "openapi.yaml"


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


def _brancher_sandbox(monkeypatch, sandbox) -> None:
    monkeypatch.setattr(code_service.factory, "select", lambda *a, **k: sandbox)


class FakeNativeSandbox(FakeSandbox):
    """Même bac factice, mais de niveau 2 : sert à éprouver le consentement."""

    level = SandboxLevel.NATIVE


async def test_propose_endpoint_runs_pipeline_and_returns_artifacts(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`propose` : l'agent écrit, le serveur exécute et rapproche."""
    pid, _, _, _ = await projet_pret(client)
    brancher_modele(monkeypatch, [_proposal_reply()])
    _brancher_sandbox(monkeypatch, FakeSandbox([{"files": ["filtration.png"]}]))

    r = await client.post(
        f"/api/v1/projects/{pid}/code/propose", json={"intent": "tracer", "datasets": []}
    )

    assert r.status_code == 200
    corps = r.json()
    assert corps["sandbox_level"] == "wasm"
    assert [a["filename"] for a in corps["artifacts"]] == ["filtration.png"]
    assert corps["artifacts"][0]["declared"] is True


async def test_execute_endpoint_runs_directly_and_matches_contract(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`execute` est l'exécution DIRECTE d'US-004 : un code, une origine, un mode."""
    pid, _, _, _ = await projet_pret(client)
    _brancher_sandbox(monkeypatch, FakeSandbox([{"files": ["sortie.txt"]}]))

    r = await client.post(
        f"/api/v1/projects/{pid}/code/execute",
        json={"code": "print('ok')", "origin": "user", "mode": "wasm"},
    )

    assert r.status_code == 200
    corps = r.json()
    for champ in ("exit_code", "stdout", "stderr", "duration_ms", "level"):
        assert champ in corps
    assert corps["level"] == 1  # entier, comme au contrat
    assert corps["network_isolation_guaranteed"] is True
    assert corps["downgraded_from_requested_mode"] is False
    assert corps["artifacts"] == [a for a in corps["artifacts"] if isinstance(a, str)]


async def test_execute_agent_origin_downgraded_to_level1_and_reported(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un agent qui demande le natif obtient le niveau 1, et on le DIT."""
    pid, _, _, _ = await projet_pret(client)
    _brancher_sandbox(monkeypatch, FakeSandbox([{"files": []}]))

    r = await client.post(
        f"/api/v1/projects/{pid}/code/execute",
        json={"code": "print('ok')", "origin": "agent", "mode": "native"},
    )

    assert r.status_code == 200
    assert r.json()["level"] == 1
    assert r.json()["downgraded_from_requested_mode"] is True


async def test_execute_native_without_consent_is_403(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le niveau 2 sans consentement native_execution ne démarre pas."""
    pid, _, _, _ = await projet_pret(client)
    natif = FakeNativeSandbox([{"files": []}])
    _brancher_sandbox(monkeypatch, natif)

    r = await client.post(
        f"/api/v1/projects/{pid}/code/execute",
        json={"code": "print('ok')", "origin": "user", "mode": "native"},
    )

    assert r.status_code == 403
    assert r.json()["code"] == "CONSENT_REQUIRED"
    assert natif.calls == [], "rien ne démarre avant le consentement"


async def test_executions_history_matches_contract(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid, _, _, _ = await projet_pret(client)
    _brancher_sandbox(monkeypatch, FakeSandbox([{"files": ["sortie.txt"]}]))
    await client.post(
        f"/api/v1/projects/{pid}/code/execute",
        json={"code": "print('ok')", "origin": "user", "mode": "wasm"},
    )

    liste = (await client.get(f"/api/v1/projects/{pid}/code/executions")).json()

    assert len(liste) == 1
    requis = [
        "exit_code",
        "stdout",
        "stderr",
        "duration_ms",
        "level",
        "network_isolation_guaranteed",
    ]
    for champ in requis:
        assert champ in liste[0], f"{champ} est obligatoire au contrat"
    assert liste[0]["level"] in (1, 2)
    # La garantie est RELUE de la base, pas réinférée du niveau.
    assert liste[0]["network_isolation_guaranteed"] is True


async def test_execute_endpoint_rejects_unknown_dataset(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid, _, _, _ = await projet_pret(client)
    sandbox = FakeSandbox([{"files": []}])
    _brancher_sandbox(monkeypatch, sandbox)

    r = await client.post(
        f"/api/v1/projects/{pid}/code/execute",
        json={"code": "print('ok')", "origin": "user", "datasets": ["inexistant"]},
    )

    assert r.status_code == 422
    assert r.json()["code"] == "UNKNOWN_DATASET"
    assert sandbox.calls == [], "refus AVANT toute exécution"


# --- Conformité au contrat ------------------------------------------------


def _chemin_contrat(route: str) -> str:
    """`/api/v1/projects/{project_id}/...` -> `/projects/{projectId}/...`."""
    sans_prefixe = route.removeprefix("/api/v1")

    def camel(m: re.Match[str]) -> str:
        tete, *reste = m.group(1).split("_")
        return "{" + tete + "".join(p.title() for p in reste) + "}"

    return re.sub(r"\{(\w+)\}", camel, sans_prefixe)


def test_implemented_code_routes_are_declared_in_the_contract() -> None:
    """Le contrat est normatif : une route servie qu'il ignore est un défaut."""
    contrat = yaml.safe_load(CONTRAT.read_text(encoding="utf-8"))["paths"]
    # L'OpenAPI que FastAPI génère est la liste de ce qui est RÉELLEMENT servi.
    servies = {
        _chemin_contrat(c)
        for c in create_app().openapi()["paths"]
        if "/code/" in c or "/artifacts/" in c
    }
    assert servies, "aucune route de code trouvée"
    absentes = sorted(p for p in servies if p not in contrat)
    assert absentes == [], f"routes servies mais absentes du contrat : {absentes}"
