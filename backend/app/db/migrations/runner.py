"""Application ordonnée et idempotente des migrations de schéma.

Trois garanties :

1. **Ordre.** Les fichiers `NNN_*.sql` sont appliqués par numéro croissant.
2. **Idempotence.** Un second appel n'applique rien.
3. **Détection de dérive.** Le SHA-256 de chaque fichier appliqué est
   enregistré. Si un fichier déjà appliqué change, le démarrage est refusé :
   deux bases nées de la même version porteraient sinon des schémas
   différents, et rien ne le signalerait avant une requête cassée.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import get_logger

logger = get_logger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent

_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migration (
  version    INTEGER PRIMARY KEY,
  filename   TEXT NOT NULL,
  sha256     TEXT NOT NULL,
  applied_at TEXT NOT NULL
)
"""


class MigrationChecksumError(AppError):
    """Un fichier de migration déjà appliqué a changé de contenu."""

    code = "MIGRATION_CHECKSUM_MISMATCH"
    status_code = 500


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def discover(directory: Path | None = None) -> list[tuple[int, Path]]:
    """Migrations disponibles, triées par numéro de version."""
    directory = directory or MIGRATIONS_DIR
    found: list[tuple[int, Path]] = []
    for path in sorted(directory.glob("[0-9][0-9][0-9]_*.sql")):
        found.append((int(path.name[:3]), path))
    return sorted(found, key=lambda item: item[0])


def render(sql: str, embedding_dim: int | None = None) -> str:
    """Substitue la dimension d'embedding dans le DDL.

    C'est le seul endroit où la dimension entre dans le SQL : la valeur vient
    de `settings.embedding_dim` et n'est codée en dur nulle part ailleurs
    (US-002, exigence 3).
    """
    dim = embedding_dim if embedding_dim is not None else get_settings().embedding_dim
    return sql.replace("{embedding_dim}", str(dim))


async def applied_versions(conn: aiosqlite.Connection) -> dict[int, str]:
    """Versions déjà appliquées, associées au SHA-256 de leur fichier."""
    await conn.execute(_TABLE)
    await conn.commit()
    async with conn.execute("SELECT version, sha256 FROM schema_migration") as cur:
        rows = await cur.fetchall()
    return {int(r[0]): str(r[1]) for r in rows}


async def run_migrations(
    conn: aiosqlite.Connection,
    directory: Path | None = None,
    embedding_dim: int | None = None,
) -> list[int]:
    """Applique les migrations manquantes. Retourne les versions appliquées."""
    applied = await applied_versions(conn)
    newly: list[int] = []

    for version, path in discover(directory):
        raw = path.read_text(encoding="utf-8")
        digest = _sha256(raw)

        if version in applied:
            if applied[version] != digest:
                raise MigrationChecksumError(
                    f"La migration {path.name} a changé depuis son application "
                    f"(attendu {applied[version][:12]}…, trouvé {digest[:12]}…). "
                    "Créer une nouvelle migration plutôt que de modifier une "
                    "migration déjà appliquée."
                )
            continue

        # `executescript` valide toute transaction en cours avant de démarrer :
        # la transaction est donc portée par le script lui-même, ce qui rend
        # l'insertion dans `schema_migration` atomique avec le DDL.
        script = (
            "BEGIN;\n"
            + render(raw, embedding_dim)
            + "\nINSERT INTO schema_migration (version, filename, sha256, applied_at) "
            f"VALUES ({version}, '{path.name}', '{digest}', "
            f"'{datetime.now(UTC).isoformat()}');\nCOMMIT;"
        )
        try:
            await conn.executescript(script)
        except Exception:
            await conn.rollback()
            raise
        logger.info("Migration %s appliquée", path.name)
        newly.append(version)

    return newly
