"""Cycle de vie d'un projet — création, lecture, modification, corbeille,
sauvegarde. US-101, ADR-001.

Deux invariants portent ce module.

**Aucun projet fantôme.** La création écrit un fichier avant d'écrire au
registre. Si une étape échoue après la création du fichier, celui-ci est
supprimé : un `.sqlite` orphelin sur le disque serait invisible de
l'application et indéchiffrable pour l'utilisateur.

**Aucune suppression définitive.** Un mémoire représente des mois de travail.
La suppression déplace le fichier vers une corbeille ; la purger est une
action explicite de l'utilisateur, hors de ce périmètre.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from app.core.config import get_settings
from app.core.errors import ProjectNotFoundError
from app.core.logging import get_logger
from app.db import registry
from app.db.migrations.runner import run_migrations
from app.db.pool import get_pool
from app.db.session import apply_pragmas, checkpoint, load_vec_extension, transaction
from app.models.audit import AuditEventType
from app.models.project import BackupResult, Project, ProjectCreate, ProjectUpdate
from app.services import audit_service

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _stamp() -> str:
    """Horodatage sûr comme composant de nom de fichier."""
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


async def create(payload: ProjectCreate) -> Project:
    settings = get_settings()
    settings.projects_dir.mkdir(parents=True, exist_ok=True)

    async with registry.connect_registry() as reg:
        slug = await registry.unique_slug(reg, payload.name)
        db_path = settings.projects_dir / f"{slug}.sqlite"

        created_file = False
        try:
            # 3. Fichier projet + schéma complet.
            conn = await aiosqlite.connect(str(db_path))
            created_file = True
            try:
                conn.row_factory = aiosqlite.Row
                await apply_pragmas(conn)
                await load_vec_extension(conn)
                await run_migrations(conn)

                # 4. Ligne de projet et configuration de modèles, puis
                # 6. journalisation — dans la même transaction que l'écriture
                # métier qu'elle décrit (ADR-009).
                async with transaction(conn):
                    cur = await conn.execute(
                        "INSERT INTO project (name, subject, discipline, language,"
                        " academic_level, target_words, created_at, updated_at)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            payload.name,
                            payload.subject,
                            payload.discipline,
                            payload.language,
                            str(payload.academic_level),
                            payload.target_words,
                            _now(),
                            _now(),
                        ),
                    )
                    project_id = int(cur.lastrowid or 0)
                    await conn.execute(
                        "INSERT INTO model_config (project_id, llm_model, embedding_model,"
                        " embedding_dim, created_at) VALUES (?, ?, ?, ?, ?)",
                        (
                            project_id,
                            settings.llm_model,
                            settings.embedding_model,
                            settings.embedding_dim,
                            _now(),
                        ),
                    )
                    await audit_service.append(
                        conn,
                        project_id,
                        AuditEventType.PROJECT_CREATED,
                        {"name": payload.name, "slug": slug, "level": str(payload.academic_level)},
                        tx=True,
                    )
            finally:
                await conn.close()

            # 5. Entrée du registre, une fois le fichier valide.
            ref = await registry.register(reg, slug, payload.name, db_path)
        except Exception:
            if created_file:
                _discard(db_path)
            raise

    logger.info("Projet %s créé (%s)", ref.id, db_path.name)
    return await get(ref.id)


def _discard(db_path: Path) -> None:
    """Retire un fichier de projet incomplet, et ses annexes WAL."""
    for suffix in ("", "-wal", "-shm"):
        candidate = db_path.with_name(db_path.name + suffix)
        if candidate.exists():
            candidate.unlink()
    logger.warning("Création interrompue : %s retiré du disque", db_path.name)


async def _conn_for(project_id: int) -> tuple[aiosqlite.Connection, registry.ProjectRef]:
    async with registry.connect_registry() as reg:
        ref = await registry.get(reg, project_id)
        if ref is None:
            raise ProjectNotFoundError.unknown(project_id)
        await registry.touch(reg, project_id)
    conn = await get_pool().acquire(project_id, ref.db_path)
    return conn, ref


async def get(project_id: int) -> Project:
    conn, ref = await _conn_for(project_id)
    async with conn.execute(
        "SELECT id, name, subject, discipline, language, academic_level, target_words,"
        " created_at FROM project ORDER BY id LIMIT 1"
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        raise ProjectNotFoundError.unknown(project_id)
    # L'identifiant exposé est celui du REGISTRE : la ligne `project` vaut 1
    # dans chaque fichier, elle n'identifie rien globalement.
    return Project(
        id=ref.id,
        name=str(row[1]),
        subject=str(row[2]),
        discipline=row[3],
        language=str(row[4]),
        academic_level=str(row[5]),
        target_words=row[6],
        created_at=str(row[7]),
    )


async def list_projects() -> list[Project]:
    async with registry.connect_registry() as reg:
        refs = await registry.list_all(reg)
    projets: list[Project] = []
    for ref in refs:
        try:
            projets.append(await get(ref.id))
        except ProjectNotFoundError:
            # Un fichier déplacé à la main ne doit pas rendre la liste
            # entière inutilisable : l'entrée est omise et signalée.
            logger.warning("Projet %s référencé mais fichier absent : %s", ref.id, ref.db_path)
    return projets


async def update(project_id: int, payload: ProjectUpdate) -> Project:
    conn, _ = await _conn_for(project_id)
    champs = payload.model_dump(exclude_none=True)
    if champs:
        assignations = ", ".join(f"{k} = ?" for k in champs)
        async with transaction(conn):
            await conn.execute(
                f"UPDATE project SET {assignations}, updated_at = ?"  # clés issues du modèle
                " WHERE id = (SELECT id FROM project ORDER BY id LIMIT 1)",
                (*champs.values(), _now()),
            )
        if "name" in champs:
            # Le nom affiché suit ; le slug et le chemin ne bougent jamais,
            # sous peine de rompre les sauvegardes déjà faites.
            async with registry.connect_registry() as reg:
                await registry.rename(reg, project_id, champs["name"])
    return await get(project_id)


async def delete(project_id: int) -> Path:
    """Déplace le projet vers la corbeille. Jamais de suppression définitive."""
    settings = get_settings()
    async with registry.connect_registry() as reg:
        ref = await registry.get(reg, project_id)
        if ref is None:
            raise ProjectNotFoundError.unknown(project_id)

        # Fermer avant de déplacer : sous Windows, un fichier ouvert ne se
        # renomme pas.
        await get_pool().release(project_id)

        settings.trash_dir.mkdir(parents=True, exist_ok=True)
        destination = settings.trash_dir / f"{ref.slug}-{_stamp()}.sqlite"
        source = Path(ref.db_path)
        # Un `stat` local sur un poste mono-utilisateur : le coût est celui
        # d'un appel système, sans commune mesure avec la copie qui suit.
        if source.exists():  # noqa: ASYNC240
            shutil.move(str(source), str(destination))
        await registry.unregister(reg, project_id)

    logger.info("Projet %s déplacé en corbeille : %s", project_id, destination.name)
    return destination


async def backup(project_id: int) -> BackupResult:
    """Checkpoint WAL puis copie atomique du fichier unique (ADR-001)."""
    settings = get_settings()
    conn, ref = await _conn_for(project_id)

    await checkpoint(conn)

    settings.backups_dir.mkdir(parents=True, exist_ok=True)
    destination = settings.backups_dir / f"{ref.slug}-{_stamp()}.sqlite"
    # Écriture sous nom temporaire puis renommage : une sauvegarde
    # interrompue ne doit pas laisser un fichier au nom d'une sauvegarde
    # valide.
    temporaire = destination.with_suffix(".sqlite.part")
    shutil.copy2(ref.db_path, temporaire)
    temporaire.replace(destination)

    return BackupResult(
        path=str(destination),
        size_bytes=destination.stat().st_size,
        created_at=_now(),
    )
