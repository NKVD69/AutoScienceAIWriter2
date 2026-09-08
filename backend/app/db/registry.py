"""Registre global des projets — US-101.

Deux niveaux, et le découpage n'est pas cosmétique (ADR-001) :

- **ce registre** ne contient que des références : quel projet existe, sous
  quel nom, dans quel fichier ;
- **un fichier `.sqlite` par projet** contient tout le reste.

C'est ce qui rend la sauvegarde triviale : copier un fichier transporte les
sources, les chunks, les vecteurs, le plan, les sections et l'audit. Une base
unique regroupant plusieurs projets ferait perdre exactement cette propriété.

Le registre lui-même n'a pas besoin de `sqlite-vec` : il ne stocke aucun
vecteur, et charger l'extension pour rien ferait échouer son ouverture sur un
interpréteur qui ne la supporte pas.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
from pydantic import BaseModel

from app.core.config import get_settings
from app.db.session import apply_pragmas

SCHEMA_PATH = Path(__file__).resolve().parent / "registry_schema.sql"
SLUG_MAX_LENGTH = 60


class ProjectRef(BaseModel):
    """Une entrée du registre. Ne porte aucune donnée de projet."""

    id: int
    slug: str
    name: str
    db_path: str
    created_at: str
    last_opened: str | None = None


def slugify(name: str) -> str:
    """Nom lisible → identifiant de fichier : minuscules, ASCII, tirets.

    Le résultat sert de nom de fichier sur deux systèmes d'exploitation :
    tout ce qui n'est pas alphanumérique ASCII est écarté plutôt que
    transcrit, un accent ou un deux-points suffisant à rendre un chemin
    incopiable d'une machine à l'autre.
    """
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_only).strip("-")
    slug = slug[:SLUG_MAX_LENGTH].strip("-")
    return slug or "projet"


@asynccontextmanager
async def connect_registry(path: Path | None = None) -> AsyncIterator[aiosqlite.Connection]:
    """Ouvre le registre, schéma appliqué. Sans extension vectorielle."""
    settings = get_settings()
    registry_path = path or settings.registry_path
    registry_path.parent.mkdir(parents=True, exist_ok=True)

    conn = await aiosqlite.connect(str(registry_path))
    try:
        conn.row_factory = aiosqlite.Row
        await apply_pragmas(conn)
        await conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        await conn.commit()
        yield conn
    finally:
        await conn.close()


async def unique_slug(conn: aiosqlite.Connection, name: str) -> str:
    """Slug disponible, suffixé d'un numéro en cas de collision."""
    base = slugify(name)
    async with conn.execute(
        "SELECT slug FROM project_ref WHERE slug = ? OR slug LIKE ?", (base, f"{base}-%")
    ) as cur:
        pris = {str(r[0]) for r in await cur.fetchall()}
    if base not in pris:
        return base
    n = 2
    while f"{base}-{n}" in pris:
        n += 1
    return f"{base}-{n}"


async def register(conn: aiosqlite.Connection, slug: str, name: str, db_path: Path) -> ProjectRef:
    now = datetime.now(UTC).isoformat()
    cur = await conn.execute(
        "INSERT INTO project_ref (slug, name, db_path, created_at) VALUES (?, ?, ?, ?)",
        (slug, name, str(db_path), now),
    )
    await conn.commit()
    return ProjectRef(
        id=int(cur.lastrowid or 0), slug=slug, name=name, db_path=str(db_path), created_at=now
    )


async def get(conn: aiosqlite.Connection, project_id: int) -> ProjectRef | None:
    async with conn.execute(
        "SELECT id, slug, name, db_path, created_at, last_opened FROM project_ref WHERE id = ?",
        (project_id,),
    ) as cur:
        row = await cur.fetchone()
    return _to_ref(row) if row else None


async def list_all(conn: aiosqlite.Connection) -> list[ProjectRef]:
    async with conn.execute(
        "SELECT id, slug, name, db_path, created_at, last_opened FROM project_ref ORDER BY id"
    ) as cur:
        rows = await cur.fetchall()
    return [_to_ref(r) for r in rows]


async def rename(conn: aiosqlite.Connection, project_id: int, name: str) -> None:
    """Change le nom affiché. Le slug et le chemin restent inchangés."""
    await conn.execute("UPDATE project_ref SET name = ? WHERE id = ?", (name, project_id))
    await conn.commit()


async def touch(conn: aiosqlite.Connection, project_id: int) -> None:
    await conn.execute(
        "UPDATE project_ref SET last_opened = ? WHERE id = ?",
        (datetime.now(UTC).isoformat(), project_id),
    )
    await conn.commit()


async def unregister(conn: aiosqlite.Connection, project_id: int) -> None:
    """Retire la référence. Le fichier du projet n'est pas touché ici."""
    await conn.execute("DELETE FROM project_ref WHERE id = ?", (project_id,))
    await conn.commit()


def _to_ref(row: aiosqlite.Row) -> ProjectRef:
    return ProjectRef(
        id=int(row[0]),
        slug=str(row[1]),
        name=str(row[2]),
        db_path=str(row[3]),
        created_at=str(row[4]),
        last_opened=row[5],
    )
