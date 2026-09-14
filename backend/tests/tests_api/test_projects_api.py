"""US-101 — CRUD projets, corbeille, sauvegarde, capacites. ADR-001, ADR-005."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest
import sqlite_vec

from app.core.config import get_settings
from app.db.pool import reset_pool
from app.main import create_app

CREATION = {
    "name": "Thèse microplastiques",
    "subject": "Impact des microplastiques sur la fonction rénale",
    "discipline": "Écotoxicologie",
    "language": "fr",
    "academic_level": "doctorat",
    "target_words": 80000,
}


@pytest.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """API sur un répertoire de données jetable, cache de connexions remis à zéro."""
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path)
    await reset_pool()
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
        yield c
    await reset_pool()


async def create_project(client: httpx.AsyncClient, **overrides) -> dict:
    r = await client.post("/api/v1/projects", json={**CREATION, **overrides})
    assert r.status_code == 201, r.text
    return r.json()


# --- Création -------------------------------------------------------------


@pytest.mark.parametrize(
    "langue",
    ['fr\nfilters:\n  - "evil.cmd"', "francais", "f", "12", "fr_FR", ""],
)
async def test_create_rejects_invalid_language(client: httpx.AsyncClient, langue: str) -> None:
    """Contrat : la langue est une etiquette BCP 47. Une valeur libre finissait
    dans `_quarto.yml` — ou elle a permis d'injecter une cle `filters`, que
    Quarto execute — et dans un chemin de fichier qui faisait echouer l'export."""
    r = await client.post("/api/v1/projects", json={**CREATION, "language": langue})

    assert r.status_code == 422
    assert r.json()["code"] == "VALIDATION_FAILED"
    assert "language" in r.json()["message"]


@pytest.mark.parametrize("langue", ["fr", "en", "en-GB", "pt-BR", "zh-Hans"])
async def test_create_accepts_standard_language_tags(
    client: httpx.AsyncClient, langue: str
) -> None:
    projet = await create_project(client, language=langue)
    assert projet["language"] == langue


def test_language_pattern_matches_the_contract() -> None:
    """Une seule definition de l'etiquette de langue, partagee par le contrat,
    le modele d'entree et la verification faite a l'export."""
    import yaml

    from app.models.project import LANGUAGE_TAG_PATTERN
    from app.services.export_service import LANGUAGE_TAG

    contrat = Path(__file__).resolve().parents[3] / "contracts" / "openapi.yaml"
    schema = yaml.safe_load(contrat.read_text(encoding="utf-8"))
    langue = schema["components"]["schemas"]["ProjectCreate"]["properties"]["language"]

    assert langue["pattern"] == LANGUAGE_TAG_PATTERN
    assert LANGUAGE_TAG.pattern == LANGUAGE_TAG_PATTERN


async def test_create_project_creates_dedicated_sqlite_file(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    projet = await create_project(client)
    fichier = tmp_path / "projects" / "these-microplastiques.sqlite"
    assert fichier.exists(), "un projet = un fichier (ADR-001)"
    assert projet["name"] == CREATION["name"]
    assert projet["academic_level"] == "doctorat"
    assert projet["target_words"] == 80000
    assert projet["created_at"]


async def test_create_project_applies_all_migrations(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    await create_project(client)
    con = sqlite3.connect(tmp_path / "projects" / "these-microplastiques.sqlite")
    try:
        noms = {r[0] for r in con.execute("SELECT name FROM sqlite_master").fetchall()}
    finally:
        con.close()
    for objet in ("project", "chunk", "vec_chunk", "chunk_after_delete", "idx_audit_prev"):
        assert objet in noms, f"{objet} absent du fichier créé"


async def test_create_project_inserts_model_config(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    """La dimension d'embedding est verrouillée par projet (§4.3)."""
    await create_project(client)
    settings = get_settings()
    con = sqlite3.connect(tmp_path / "projects" / "these-microplastiques.sqlite")
    try:
        row = con.execute(
            "SELECT llm_model, embedding_model, embedding_dim FROM model_config"
        ).fetchone()
    finally:
        con.close()
    assert row == (settings.llm_model, settings.embedding_model, settings.embedding_dim)


async def test_create_project_writes_audit_entry(client: httpx.AsyncClient) -> None:
    projet = await create_project(client)
    r = await client.get(f"/api/v1/projects/{projet['id']}/audit")
    assert r.status_code == 200
    entrees = r.json()
    assert [e["event_type"] for e in entrees] == ["PROJECT_CREATED"]
    assert entrees[0]["prev_hash"] == ""

    verif = await client.post(f"/api/v1/projects/{projet['id']}/audit/verify")
    assert verif.json()["status"] == "VALIDE"


async def test_create_project_rolls_back_file_on_failure(
    client: httpx.AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Aucun projet fantôme : un fichier écrit puis abandonné serait invisible
    de l'application et indéchiffrable pour l'utilisateur."""
    from app.db import registry

    async def refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("écriture du registre impossible")

    monkeypatch.setattr(registry, "register", refuse)

    # Le transport ASGI relaie l'exception telle quelle ; ce que le test
    # etablit n'est pas le code HTTP mais l'invariant : rien ne reste sur
    # le disque.
    with pytest.raises(RuntimeError, match="registre"):
        await client.post("/api/v1/projects", json=CREATION)

    restes = list((tmp_path / "projects").glob("*.sqlite*"))
    assert restes == [], f"fichier orphelin laisse sur le disque : {restes}"


async def test_slug_collision_gets_numeric_suffix(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    await create_project(client)
    await create_project(client)
    fichiers = sorted(p.name for p in (tmp_path / "projects").glob("*.sqlite"))
    assert fichiers == ["these-microplastiques-2.sqlite", "these-microplastiques.sqlite"]


async def test_create_rejects_invalid_body(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/v1/projects", json={**CREATION, "academic_level": "licence"})
    assert r.status_code == 422
    r = await client.post("/api/v1/projects", json={**CREATION, "subject": "court"})
    assert r.status_code == 422


# --- Lecture et modification ---------------------------------------------


async def test_list_projects_from_registry(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/v1/projects")).json() == []
    await create_project(client, name="Alpha")
    await create_project(client, name="Beta")
    noms = [p["name"] for p in (await client.get("/api/v1/projects")).json()]
    assert noms == ["Alpha", "Beta"]


async def test_get_project_404_when_absent(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/projects/4242")
    assert r.status_code == 404
    assert r.json()["code"] == "PROJECT_NOT_FOUND"


async def test_patch_updates_name_but_not_slug(client: httpx.AsyncClient, tmp_path: Path) -> None:
    projet = await create_project(client)
    r = await client.patch(
        f"/api/v1/projects/{projet['id']}", json={"name": "Titre entièrement différent"}
    )
    assert r.status_code == 200
    assert r.json()["name"] == "Titre entièrement différent"
    # Le fichier ne bouge pas : renommer romprait les sauvegardes existantes.
    assert (tmp_path / "projects" / "these-microplastiques.sqlite").exists()


async def test_patch_is_partial(client: httpx.AsyncClient) -> None:
    projet = await create_project(client)
    r = await client.patch(f"/api/v1/projects/{projet['id']}", json={"target_words": 120000})
    assert r.status_code == 200
    assert r.json()["target_words"] == 120000
    assert r.json()["subject"] == CREATION["subject"]


# --- Suppression ----------------------------------------------------------


async def test_delete_moves_file_to_trash_not_unlink(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    """Un mémoire représente des mois de travail : rien n'est effacé."""
    projet = await create_project(client)
    r = await client.delete(f"/api/v1/projects/{projet['id']}")
    assert r.status_code == 204

    assert not (tmp_path / "projects" / "these-microplastiques.sqlite").exists()
    corbeille = list((tmp_path / "trash").glob("these-microplastiques-*.sqlite"))
    assert len(corbeille) == 1
    assert corbeille[0].stat().st_size > 0
    assert (await client.get(f"/api/v1/projects/{projet['id']}")).status_code == 404


async def test_delete_closes_connection(client: httpx.AsyncClient) -> None:
    """Sous Windows, un fichier encore ouvert ne se renomme pas."""
    from app.db.pool import get_pool

    projet = await create_project(client)
    await client.get(f"/api/v1/projects/{projet['id']}")
    assert get_pool().is_open(projet["id"])

    assert (await client.delete(f"/api/v1/projects/{projet['id']}")).status_code == 204
    assert not get_pool().is_open(projet["id"])


async def test_delete_unknown_project_is_404(client: httpx.AsyncClient) -> None:
    assert (await client.delete("/api/v1/projects/4242")).status_code == 404


# --- Sauvegarde -----------------------------------------------------------


async def test_backup_checkpoints_wal_before_copy(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    projet = await create_project(client)
    r = await client.post(f"/api/v1/projects/{projet['id']}/backup")
    assert r.status_code == 200
    corps = r.json()
    assert set(corps) == {"path", "size_bytes", "created_at"}
    copie = Path(corps["path"])
    assert copie.exists() and corps["size_bytes"] > 0  # noqa: ASYNC240 - stat local
    # Le checkpoint a replié le WAL : la copie se lit sans ses annexes.
    assert not copie.with_name(copie.name + "-wal").exists()
    assert not list((tmp_path / "backups").glob("*.part"))


async def test_backup_copy_contains_vectors(client: httpx.AsyncClient) -> None:
    """ADR-001 : un seul fichier transporte le relationnel ET le vectoriel."""
    from app.db.pool import get_pool
    from app.db.session import transaction
    from app.db.vector import insert_chunk_with_embedding

    projet = await create_project(client)
    conn = await get_pool().acquire(projet["id"], _db_path(projet["id"]))
    async with transaction(conn):
        await conn.execute(
            "INSERT INTO source_document (id, project_id, kind, title, imported_at)"
            " VALUES (1, 1, 'article', 'Source A', '2026-01-01T00:00:00+00:00')"
        )
    dim = get_settings().embedding_dim
    for i in range(5):
        await insert_chunk_with_embedding(
            conn, source_id=1, ordinal=i, text=f"chunk {i}", embedding=[0.01 * i] * dim
        )

    corps = (await client.post(f"/api/v1/projects/{projet['id']}/backup")).json()

    con = sqlite3.connect(corps["path"])
    try:
        con.enable_load_extension(True)
        sqlite_vec.load(con)
        con.enable_load_extension(False)
        assert con.execute("SELECT count(*) FROM chunk").fetchone()[0] == 5
        assert con.execute("SELECT count(*) FROM vec_chunk").fetchone()[0] == 5
        joint = con.execute(
            "SELECT count(*) FROM chunk c JOIN vec_chunk v ON c.id = v.rowid"
        ).fetchone()[0]
        assert joint == 5
    finally:
        con.close()


def _db_path(project_id: int) -> Path:
    return get_settings().projects_dir / "these-microplastiques.sqlite"


# --- Capacités ------------------------------------------------------------


async def test_capabilities_never_500_when_dependency_absent(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ce endpoint permet de désactiver une fonctionnalité plutôt que de la
    laisser échouer : un 500 priverait le frontend de ce moyen."""
    import shutil

    from app.llm import vram

    monkeypatch.setattr(shutil, "which", lambda _: None)
    monkeypatch.setattr(vram, "_read_memory", lambda: None)

    r = await client.get("/api/v1/system/capabilities")
    assert r.status_code == 200
    corps = r.json()
    assert corps["gpu_available"] is False
    assert corps["vram_total_mb"] is None
    assert corps["quarto_version"] is None
    assert corps["wasm_sandbox_available"] is False


async def test_capabilities_survives_a_probe_that_raises(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.llm import vram

    def explose() -> None:
        raise OSError("pilote absent")

    monkeypatch.setattr(vram, "_read_memory", explose)
    r = await client.get("/api/v1/system/capabilities")
    assert r.status_code == 200
    assert r.json()["vram_total_mb"] is None


async def test_capabilities_windows_network_isolation_false(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-005 : faux sous Windows, sans condition — les Job Objects
    contraignent la mémoire et le processeur, jamais le réseau."""
    import app.api.v1.system as system

    monkeypatch.setattr(system.sys, "platform", "win32")
    corps = (await client.get("/api/v1/system/capabilities")).json()
    assert corps["platform"] == "windows"
    assert corps["native_network_isolation_guaranteed"] is False


async def test_capabilities_reports_sqlite_vec(client: httpx.AsyncClient) -> None:
    corps = (await client.get("/api/v1/system/capabilities")).json()
    assert corps["sqlite_vec_version"]
