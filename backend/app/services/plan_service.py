"""Persistance, versionnement et validation du plan — US-PLAN-001.

**L'arbre est aplati dans `plan_node`** : `parent_id`, `ordinal`, `level`.
Une structure hiérarchique stockée en JSON serait illisible aux requêtes qui
en ont besoin — retrouver les sections d'un chapitre, compter les mots d'une
branche, rattacher une citation à un nœud.

**Rien n'est supprimé silencieusement.** Supprimer un nœud portant une
section rédigée détruirait du travail. La section passe en `ORPHANED` et
reste lisible : c'est à l'auteur de décider de son sort.

**Le verrou de rédaction vit ici, pas dans l'API.** Un appel interne au
graphe doit être bloqué exactement comme un appel HTTP : un contrôle qui
n'existe qu'à la frontière n'est pas un contrôle, c'est une politesse.
"""

from __future__ import annotations

from datetime import UTC, datetime

import aiosqlite

from app.core.errors import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.db.session import transaction
from app.models.audit import AuditEventType
from app.models.plan import (
    PlanNode,
    PlanNodeCreate,
    PlanNodeOut,
    PlanNodeUpdate,
    PlanOut,
    PlanStatus,
    PlanTree,
    PlanVersion,
    ReorderRequest,
    SectionStatus,
)
from app.services import audit_service

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


# --- Écriture -------------------------------------------------------------


async def save_tree(
    conn: aiosqlite.Connection, project_id: int, tree: PlanTree, status: PlanStatus
) -> int:
    """Écrit un nouvel arbre, en incrémentant la version. Retourne `plan.id`.

    Les versions antérieures restent lisibles : leurs lignes ne sont pas
    touchées. Un plan est un objet de travail, et revenir sur une version
    précédente est un geste normal.
    """
    async with conn.execute(
        "SELECT COALESCE(MAX(version), 0) FROM plan WHERE project_id = ?", (project_id,)
    ) as cur:
        version = int((await cur.fetchone())[0]) + 1

    async with transaction(conn):
        cur = await conn.execute(
            "INSERT INTO plan (project_id, problematique, status, version, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (project_id, tree.problematique, str(status), version, _now()),
        )
        plan_id = int(cur.lastrowid or 0)
        await _insert_nodes(conn, plan_id, tree.nodes, parent_id=None, level=1)
        await audit_service.append(
            conn,
            project_id,
            AuditEventType.STATE_TRANSITION,
            {"plan_id": plan_id, "version": version, "noeuds": len(tree.nodes)},
            tx=True,
        )
    logger.info("Plan %s enregistré en version %s", plan_id, version)
    return plan_id


async def _insert_nodes(
    conn: aiosqlite.Connection,
    plan_id: int,
    noeuds: list[PlanNode],
    parent_id: int | None,
    level: int,
) -> None:
    for ordinal, noeud in enumerate(noeuds):
        cur = await conn.execute(
            "INSERT INTO plan_node (plan_id, parent_id, ordinal, level, title, objective,"
            " target_words) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (plan_id, parent_id, ordinal, level, noeud.title, noeud.objective, noeud.target_words),
        )
        await _insert_nodes(conn, plan_id, noeud.children, int(cur.lastrowid or 0), level + 1)


# --- Lecture --------------------------------------------------------------


async def current_plan(conn: aiosqlite.Connection, project_id: int) -> PlanOut | None:
    async with conn.execute(
        "SELECT id, problematique, status, version, created_at FROM plan"
        " WHERE project_id = ? ORDER BY version DESC LIMIT 1",
        (project_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    return await _build_plan_out(conn, row)


async def _build_plan_out(conn: aiosqlite.Connection, row: aiosqlite.Row) -> PlanOut:
    plan_id = int(row[0])
    async with conn.execute(
        "SELECT n.id, n.parent_id, n.ordinal, n.level, n.title, n.objective, n.target_words,"
        " (SELECT s.status FROM draft_section s WHERE s.plan_node_id = n.id"
        "  ORDER BY s.version DESC LIMIT 1)"
        " FROM plan_node n WHERE n.plan_id = ? ORDER BY n.level, n.parent_id, n.ordinal",
        (plan_id,),
    ) as cur:
        lignes = await cur.fetchall()

    par_id: dict[int, PlanNodeOut] = {}
    racines: list[PlanNodeOut] = []
    for ligne in lignes:
        noeud = PlanNodeOut(
            id=int(ligne[0]),
            parent_id=ligne[1],
            ordinal=int(ligne[2]),
            level=int(ligne[3]),
            title=str(ligne[4]),
            objective=str(ligne[5] or ""),
            target_words=int(ligne[6] or 0),
            section_status=SectionStatus(str(ligne[7])) if ligne[7] else None,
        )
        par_id[noeud.id] = noeud
        if noeud.parent_id is None:
            racines.append(noeud)
        elif noeud.parent_id in par_id:
            par_id[noeud.parent_id].children.append(noeud)

    validated_at = None
    if str(row[2]) == PlanStatus.VALIDATED:
        validated_at = str(row[4])

    return PlanOut(
        id=plan_id,
        version=int(row[3]),
        status=PlanStatus(str(row[2])),
        problematique=str(row[1]),
        nodes=racines,
        validated_at=validated_at,
    )


async def versions(conn: aiosqlite.Connection, project_id: int) -> list[PlanVersion]:
    async with conn.execute(
        "SELECT p.version, p.status, p.created_at,"
        " (SELECT count(*) FROM plan_node n WHERE n.plan_id = p.id)"
        " FROM plan p WHERE p.project_id = ? ORDER BY p.version DESC",
        (project_id,),
    ) as cur:
        return [
            PlanVersion(
                version=int(r[0]),
                status=PlanStatus(str(r[1])),
                created_at=str(r[2]),
                node_count=int(r[3]),
            )
            for r in await cur.fetchall()
        ]


# --- Édition --------------------------------------------------------------


async def _require_plan(conn: aiosqlite.Connection, project_id: int) -> PlanOut:
    plan = await current_plan(conn, project_id)
    if plan is None:
        raise NotFoundError(
            f"Aucun plan pour le projet {project_id}. Le générer avant de l'éditer.",
            project_id=project_id,
        )
    return plan


async def _demote_if_validated(conn: aiosqlite.Connection, plan: PlanOut) -> None:
    """Une modification structurelle fait repasser un plan validé en DRAFT.

    Sans cela, la rédaction s'appuierait sur une validation qui ne porte
    plus sur la structure réellement en base.
    """
    if plan.status is PlanStatus.VALIDATED:
        await conn.execute(
            "UPDATE plan SET status = ? WHERE id = ?", (str(PlanStatus.DRAFT), plan.id)
        )
        logger.info("Plan %s repassé en DRAFT après modification", plan.id)


async def update_node(
    conn: aiosqlite.Connection, project_id: int, node_id: int, payload: PlanNodeUpdate
) -> PlanOut:
    plan = await _require_plan(conn, project_id)
    champs = payload.model_dump(exclude_none=True)
    if not champs:
        return plan

    assignations = ", ".join(f"{k} = ?" for k in champs)
    async with transaction(conn):
        cur = await conn.execute(
            f"UPDATE plan_node SET {assignations} WHERE id = ? AND plan_id = ?",  # champs clos
            (*champs.values(), node_id, plan.id),
        )
        if cur.rowcount == 0:
            raise NotFoundError(f"Nœud {node_id} absent du plan.", node_id=node_id)
        await _demote_if_validated(conn, plan)
        await _bump_version(conn, plan.id)
    return await _require_plan(conn, project_id)


async def add_node(conn: aiosqlite.Connection, project_id: int, payload: PlanNodeCreate) -> PlanOut:
    plan = await _require_plan(conn, project_id)
    level = 1
    if payload.parent_id is not None:
        async with conn.execute(
            "SELECT level FROM plan_node WHERE id = ? AND plan_id = ?",
            (payload.parent_id, plan.id),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise NotFoundError(
                f"Nœud parent {payload.parent_id} absent.", node_id=payload.parent_id
            )
        level = int(row[0]) + 1

    async with conn.execute(
        "SELECT COALESCE(MAX(ordinal), -1) + 1 FROM plan_node WHERE plan_id = ? AND parent_id IS ?",
        (plan.id, payload.parent_id),
    ) as cur:
        ordinal = payload.ordinal if payload.ordinal is not None else int((await cur.fetchone())[0])

    async with transaction(conn):
        await conn.execute(
            "INSERT INTO plan_node (plan_id, parent_id, ordinal, level, title, objective,"
            " target_words) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                plan.id,
                payload.parent_id,
                ordinal,
                level,
                payload.title,
                payload.objective,
                payload.target_words,
            ),
        )
        await _demote_if_validated(conn, plan)
        await _bump_version(conn, plan.id)
    return await _require_plan(conn, project_id)


async def sections_under(conn: aiosqlite.Connection, node_id: int) -> list[int]:
    """Sections rédigées portées par un nœud ou l'un de ses descendants."""
    async with conn.execute(
        """
        WITH RECURSIVE sous_arbre(id) AS (
            SELECT ? UNION ALL
            SELECT n.id FROM plan_node n JOIN sous_arbre s ON n.parent_id = s.id
        )
        SELECT d.id FROM draft_section d JOIN sous_arbre s ON d.plan_node_id = s.id
        WHERE d.status <> ?
        """,
        (node_id, str(SectionStatus.ORPHANED)),
    ) as cur:
        return [int(r[0]) for r in await cur.fetchall()]


async def delete_node(
    conn: aiosqlite.Connection, project_id: int, node_id: int, force: bool = False
) -> PlanOut:
    """Supprime un sous-arbre. Refuse si des sections y sont rédigées."""
    plan = await _require_plan(conn, project_id)
    concernees = await sections_under(conn, node_id)

    if concernees and not force:
        raise ConflictError(
            f"Le nœud {node_id} porte {len(concernees)} section(s) rédigée(s) : "
            f"{concernees}. Relancer avec force=true pour les marquer orphelines — "
            "elles ne seront pas supprimées.",
            current_state="has_sections",
            required_state="no_sections",
        )

    async with transaction(conn):
        if concernees:
            # Marquées, jamais supprimées : c'est du travail de l'auteur.
            marques = ",".join("?" * len(concernees))
            await conn.execute(
                # `marques` ne contient que des points d'interrogation.
                f"UPDATE draft_section SET status = ? WHERE id IN ({marques})",
                (str(SectionStatus.ORPHANED), *concernees),
            )
            await audit_service.append(
                conn,
                project_id,
                AuditEventType.STATE_TRANSITION,
                {"sections_orphelines": concernees, "noeud_supprime": node_id},
                tx=True,
            )
        # `plan_node.parent_id` est en ON DELETE CASCADE : le sous-arbre suit.
        await conn.execute("DELETE FROM plan_node WHERE id = ? AND plan_id = ?", (node_id, plan.id))
        await _demote_if_validated(conn, plan)
        await _bump_version(conn, plan.id)
    return await _require_plan(conn, project_id)


async def reorder(conn: aiosqlite.Connection, project_id: int, payload: ReorderRequest) -> PlanOut:
    """Réordonne par lot. Les enfants suivent leur parent, jamais déplacés."""
    plan = await _require_plan(conn, project_id)
    async with transaction(conn):
        for item in payload.items:
            await conn.execute(
                "UPDATE plan_node SET parent_id = ?, ordinal = ? WHERE id = ? AND plan_id = ?",
                (item.parent_id, item.ordinal, item.node_id, plan.id),
            )
        await _demote_if_validated(conn, plan)
        await _bump_version(conn, plan.id)
    return await _require_plan(conn, project_id)


async def _bump_version(conn: aiosqlite.Connection, plan_id: int) -> None:
    await conn.execute("UPDATE plan SET version = version + 1 WHERE id = ?", (plan_id,))


# --- Validation et verrou -------------------------------------------------


async def validate(
    conn: aiosqlite.Connection, project_id: int, validated_by: str = "utilisateur"
) -> PlanOut:
    """Porte humaine. Aucun score, aucune condition automatique (§5.2)."""
    plan = await _require_plan(conn, project_id)
    async with transaction(conn):
        await conn.execute(
            "UPDATE plan SET status = ? WHERE id = ?", (str(PlanStatus.VALIDATED), plan.id)
        )
        await audit_service.append(
            conn,
            project_id,
            AuditEventType.HUMAN_VALIDATION,
            {"plan_id": plan.id, "version": plan.version, "par": validated_by},
            tx=True,
        )
    logger.info("Plan %s validé par %s", plan.id, validated_by)
    return await _require_plan(conn, project_id)


async def assert_writable(conn: aiosqlite.Connection, project_id: int) -> PlanOut:
    """Refuse la rédaction tant que le plan n'est pas validé.

    Appelé par le service, donc par tout chemin — API comme graphe. Un
    contrôle qui n'existerait qu'à la frontière HTTP laisserait passer un
    appel interne, et c'est exactement le chemin que prendra le graphe.
    """
    plan = await current_plan(conn, project_id)
    if plan is None:
        raise ConflictError(
            "Aucun plan n'existe pour ce projet. La rédaction exige un plan validé.",
            current_state="NO_PLAN",
            required_state=str(PlanStatus.VALIDATED),
        )
    if plan.status is not PlanStatus.VALIDATED:
        raise ConflictError(
            f"Le plan est en {plan.status}. La rédaction exige un plan validé : "
            "la structure doit être approuvée avant d'écrire dessus.",
            current_state=str(plan.status),
            required_state=str(PlanStatus.VALIDATED),
        )
    return plan
