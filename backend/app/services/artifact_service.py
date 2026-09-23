"""Artefacts : persistance, reproductibilité, rattachement — US-401, §8, ADR-006.

**Un artefact conservé porte de quoi le reproduire.** Code exact, graine,
versions des bibliothèques relevées dans la sandbox, SHA-256 des jeux de données,
durée : c'est ce qui permet de répondre en soutenance à « comment cette figure
a-t-elle été obtenue ». Sans le SHA-256 des données, la réponse est incomplète.

**L'inattendu est conservé, jamais rattaché seul.** Un fichier produit mais non
déclaré (`declared = 0`) est gardé et signalé : il a pu coûter du calcul, on ne
le jette pas, mais on ne l'insère pas dans le document sans décision.

**Rattacher insère le renvoi Quarto, une seule fois.** `![légende](chemin){#label}`
pour une figure. L'opération est idempotente : un artefact déjà référencé n'ajoute
pas un second renvoi. Et supprimer un artefact référencé est refusé (409) — cela
casserait un renvoi à l'export (US-502).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import aiosqlite

from app.core.errors import AppError, NotFoundError
from app.core.logging import get_logger
from app.db.session import transaction
from app.models.code import ArtifactOut
from app.models.section import DraftSectionOut
from app.services import section_service

logger = get_logger(__name__)

# Types d'artefact rattachables à une section. Un « data » (jeu produit) ne porte
# pas de renvoi dans le texte : il alimente d'autres artefacts, il ne s'y affiche pas.
ATTACHABLE_KINDS = frozenset({"figure", "table"})


class ArtifactReferencedError(AppError):
    """Suppression refusée : l'artefact est cité dans une section.

    Le supprimer laisserait un renvoi Quarto sans cible, et l'export (US-502)
    échouerait des semaines plus tard sans que rien ne relie la panne à cette
    suppression. On refuse, en nommant la section.
    """

    code = "ARTIFACT_REFERENCED"
    status_code = 409


class ArtifactNotAttachableError(AppError):
    """Seules une figure et un tableau se rattachent à une section (§8)."""

    code = "ARTIFACT_NOT_ATTACHABLE"
    status_code = 422


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _quarto_reference(artifact: ArtifactOut) -> str:
    """Renvoi Quarto d'un artefact rattachable. La légende et le label en font foi."""
    return f"![{artifact.caption or ''}]({artifact.rel_path}){{#{artifact.label}}}"


# --- Persistance ----------------------------------------------------------


async def save_artifacts(
    conn: aiosqlite.Connection,
    code_execution_id: int,
    matched: list,
    undeclared: list[tuple[str, str]],
    code: str,
    random_seed: int | None,
    library_versions: dict[str, str],
    dataset_sha256: dict[str, str],
    duration_ms: int,
) -> list[ArtifactOut]:
    """Persiste artefacts attendus (declared=1) et inattendus (declared=0).

    `matched` : (ExpectedArtifact, rel_path) réellement produits. `undeclared` :
    (filename, rel_path) produits sans avoir été déclarés — conservés, signalés.
    """
    versions_json = json.dumps(library_versions, ensure_ascii=False)
    sha_json = json.dumps(dataset_sha256, ensure_ascii=False)
    ids: list[int] = []
    async with transaction(conn):
        for expected, rel_path in matched:
            cur = await conn.execute(
                "INSERT INTO artifact (code_execution_id, kind, filename, label, caption,"
                " rel_path, declared, code, random_seed, library_versions_json,"
                " dataset_sha256_json, duration_ms, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)",
                (
                    code_execution_id,
                    expected.kind,
                    expected.filename,
                    expected.label,
                    expected.caption,
                    rel_path,
                    code,
                    random_seed,
                    versions_json,
                    sha_json,
                    duration_ms,
                    _now(),
                ),
            )
            ids.append(int(cur.lastrowid or 0))
        for filename, rel_path in undeclared:
            cur = await conn.execute(
                "INSERT INTO artifact (code_execution_id, kind, filename, label, caption,"
                " rel_path, declared, code, random_seed, library_versions_json,"
                " dataset_sha256_json, duration_ms, created_at)"
                " VALUES (?, 'data', ?, NULL, NULL, ?, 0, ?, ?, ?, ?, ?, ?)",
                (
                    code_execution_id,
                    filename,
                    rel_path,
                    code,
                    random_seed,
                    versions_json,
                    sha_json,
                    duration_ms,
                    _now(),
                ),
            )
            ids.append(int(cur.lastrowid or 0))
    return [await get(conn, i) for i in ids]


def _row_to_out(row: aiosqlite.Row) -> ArtifactOut:
    return ArtifactOut(
        id=int(row[0]),
        code_execution_id=int(row[1]),
        draft_section_id=row[2],
        kind=str(row[3]),
        filename=str(row[4]),
        label=row[5],
        caption=row[6],
        rel_path=str(row[7]),
        declared=bool(row[8]),
        random_seed=row[9],
        library_versions=json.loads(row[10]) if row[10] else {},
        dataset_sha256=json.loads(row[11]) if row[11] else {},
        duration_ms=row[12],
        created_at=str(row[13]),
    )


_SELECT = (
    "SELECT id, code_execution_id, draft_section_id, kind, filename, label, caption,"
    " rel_path, declared, random_seed, library_versions_json, dataset_sha256_json,"
    " duration_ms, created_at FROM artifact"
)


async def get(conn: aiosqlite.Connection, artifact_id: int) -> ArtifactOut:
    async with conn.execute(f"{_SELECT} WHERE id = ?", (artifact_id,)) as cur:
        row = await cur.fetchone()
    if row is None:
        raise NotFoundError(f"Artefact {artifact_id} inconnu.", artifact_id=artifact_id)
    return _row_to_out(row)


async def list_for_execution(
    conn: aiosqlite.Connection, code_execution_id: int
) -> list[ArtifactOut]:
    async with conn.execute(
        f"{_SELECT} WHERE code_execution_id = ? ORDER BY id", (code_execution_id,)
    ) as cur:
        return [_row_to_out(r) for r in await cur.fetchall()]


# --- Rattachement et suppression ------------------------------------------


async def attach(
    conn: aiosqlite.Connection, project_id: int, section_id: int, artifact_id: int
) -> DraftSectionOut:
    """Rattache une figure ou un tableau à une section, renvoi Quarto inséré.

    Idempotent : si le renvoi (`{#label}`) est déjà dans le texte, il n'est pas
    dupliqué ; seul le lien artefact -> section est (ré)affirmé.
    """
    artifact = await get(conn, artifact_id)
    if artifact.kind not in ATTACHABLE_KINDS:
        raise ArtifactNotAttachableError(
            f"Un artefact « {artifact.kind} » ne se rattache pas à une section : "
            "seules une figure et un tableau portent un renvoi dans le texte.",
            artifact_id=artifact_id,
            kind=artifact.kind,
        )
    section = await section_service.get(conn, section_id)  # 404 si absente
    marqueur = f"{{#{artifact.label}}}"
    contenu = section.content_qmd
    if marqueur not in contenu:
        separateur = "" if contenu.endswith("\n") or not contenu else "\n\n"
        contenu = f"{contenu}{separateur}{_quarto_reference(artifact)}\n"

    async with transaction(conn):
        await conn.execute(
            "UPDATE artifact SET draft_section_id = ? WHERE id = ?", (section_id, artifact_id)
        )
        await conn.execute(
            "UPDATE draft_section SET content_qmd = ? WHERE id = ?", (contenu, section_id)
        )
    logger.info("Artefact %s rattaché à la section %s", artifact_id, section_id)
    return await section_service.get(conn, section_id)


async def delete(conn: aiosqlite.Connection, project_id: int, artifact_id: int) -> None:
    """Supprime un artefact. Refusée (409) s'il est référencé dans sa section."""
    artifact = await get(conn, artifact_id)
    if artifact.draft_section_id is not None and artifact.label:
        section = await section_service.get(conn, artifact.draft_section_id)
        if f"{{#{artifact.label}}}" in section.content_qmd:
            raise ArtifactReferencedError(
                f"L'artefact {artifact_id} est cité dans la section "
                f"{artifact.draft_section_id} (renvoi {{#{artifact.label}}}). Le supprimer "
                "casserait ce renvoi à l'export. Retire d'abord le renvoi du texte, ou "
                "détache l'artefact.",
                artifact_id=artifact_id,
                draft_section_id=artifact.draft_section_id,
            )
    async with transaction(conn):
        await conn.execute("DELETE FROM artifact WHERE id = ?", (artifact_id,))
    logger.info("Artefact %s supprimé", artifact_id)
