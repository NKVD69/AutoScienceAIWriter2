"""US-101 — registre global : slug, unicite, stabilite. ADR-001."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import get_settings
from app.db import registry


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isole le registre et les fichiers projet dans un repertoire jetable."""
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path)
    return tmp_path


@pytest.mark.parametrize(
    ("nom", "attendu"),
    [
        ("Thèse de doctorat", "these-de-doctorat"),
        ("Étude   des   microplastiques", "etude-des-microplastiques"),
        ("C:\\chemin/invalide?", "c-chemin-invalide"),
        ("---", "projet"),
        ("", "projet"),
        ("Œuvre", "uvre"),
    ],
)
def test_slugify_produces_portable_filenames(nom: str, attendu: str) -> None:
    """Le slug sert de nom de fichier sur Windows et Linux."""
    assert registry.slugify(nom) == attendu


def test_slugify_truncates_and_trims() -> None:
    slug = registry.slugify("a" * 200)
    assert len(slug) <= registry.SLUG_MAX_LENGTH
    assert not slug.startswith("-") and not slug.endswith("-")


async def test_registry_schema_is_created_on_connect(data_dir: Path) -> None:
    async with registry.connect_registry() as reg:
        async with reg.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='project_ref'"
        ) as cur:
            assert await cur.fetchone() is not None
    assert (data_dir / "registry.sqlite").exists()


async def test_registry_connect_is_idempotent(data_dir: Path) -> None:
    async with registry.connect_registry():
        pass
    async with registry.connect_registry() as reg:
        assert await registry.list_all(reg) == []


async def test_slug_collision_gets_numeric_suffix(data_dir: Path) -> None:
    async with registry.connect_registry() as reg:
        premier = await registry.unique_slug(reg, "Ma thèse")
        await registry.register(reg, premier, "Ma thèse", data_dir / f"{premier}.sqlite")

        second = await registry.unique_slug(reg, "Ma thèse")
        await registry.register(reg, second, "Ma thèse", data_dir / f"{second}.sqlite")

        troisieme = await registry.unique_slug(reg, "Ma thèse")

    assert premier == "ma-these"
    assert second == "ma-these-2"
    assert troisieme == "ma-these-3"


async def test_slug_stable_after_rename(data_dir: Path) -> None:
    """Le chemin du fichier depend du slug : le renommer romprait les
    sauvegardes deja faites par l'utilisateur."""
    async with registry.connect_registry() as reg:
        slug = await registry.unique_slug(reg, "Nom initial")
        ref = await registry.register(reg, slug, "Nom initial", data_dir / f"{slug}.sqlite")

        await registry.rename(reg, ref.id, "Tout autre nom")
        apres = await registry.get(reg, ref.id)

    assert apres is not None
    assert apres.name == "Tout autre nom"
    assert apres.slug == slug == "nom-initial"
    assert apres.db_path == ref.db_path


async def test_list_projects_from_registry(data_dir: Path) -> None:
    async with registry.connect_registry() as reg:
        for nom in ("Alpha", "Beta", "Gamma"):
            slug = await registry.unique_slug(reg, nom)
            await registry.register(reg, slug, nom, data_dir / f"{slug}.sqlite")
        refs = await registry.list_all(reg)

    assert [r.name for r in refs] == ["Alpha", "Beta", "Gamma"]
    assert [r.slug for r in refs] == ["alpha", "beta", "gamma"]


async def test_unregister_removes_entry_only(data_dir: Path) -> None:
    fichier = data_dir / "alpha.sqlite"
    fichier.parent.mkdir(parents=True, exist_ok=True)
    fichier.write_text("contenu", encoding="utf-8")

    async with registry.connect_registry() as reg:
        ref = await registry.register(reg, "alpha", "Alpha", fichier)
        await registry.unregister(reg, ref.id)
        assert await registry.get(reg, ref.id) is None

    # Le registre ne touche jamais au fichier : c'est le service qui decide.
    assert fichier.exists()


async def test_touch_records_last_opened(data_dir: Path) -> None:
    async with registry.connect_registry() as reg:
        ref = await registry.register(reg, "alpha", "Alpha", data_dir / "alpha.sqlite")
        assert ref.last_opened is None
        await registry.touch(reg, ref.id)
        apres = await registry.get(reg, ref.id)
    assert apres is not None and apres.last_opened is not None
