"""Rédaction et persistance des sections — US-301.

**Chaque génération crée une ligne, jamais une mise à jour.** Une section
réécrite après relecture peut être moins bonne que la précédente ; écraser
rendrait le retour en arrière impossible, sur un travail qui se compte en
semaines.

**Les citations sont écrites `verified = 1` parce qu'elles ont franchi V1, V2
et V3** — pas parce que le modèle les a produites. La différence est tout le
sujet de cette story.

**Une édition manuelle dévérifie, elle ne supprime pas.** L'auteur qui
retouche son texte peut faire disparaître une clé du contenu ; la ligne de
citation reste, marquée non vérifiée. La supprimer effacerait la trace qu'une
affirmation a un jour été rattachée à une source, et cette trace est ce que
l'audit d'un jury regarde.

La reprise après rejet appartient au graphe : le MÊME agent est relancé, avec
le message d'erreur en correction, et le compteur de US-202 arrête la boucle
au troisième essai.
"""

from __future__ import annotations

from datetime import UTC, datetime

import aiosqlite

from app.agents.breaker import MAX_GUARDRAIL_RETRIES, register_guardrail_failure
from app.agents.guardrails import validate_output
from app.agents.state import GraphState, WorkflowState, initial_state
from app.agents.veracity import check_veracity, extract_inline_keys
from app.agents.writer_agent import WriterAgent
from app.core.errors import AppError, NotFoundError
from app.core.logging import get_logger
from app.db.session import transaction
from app.llm.manager import LLMManager
from app.models.audit import AuditEventType
from app.models.plan import PlanNodeOut, PlanOut
from app.models.section import (
    CitationOut,
    DraftSectionOut,
    SectionDraft,
    SectionStatus,
    SectionUpdate,
)
from app.rag.context_builder import SectionContext, bibtex_key, build_section_context
from app.services import audit_service, plan_service, task_service

logger = get_logger(__name__)


class SectionGuardrailError(AppError):
    """Trois essais rejetés par les garde-fous : la section n'est pas produite.

    Rendre malgré tout la dernière version serait rendre un texte dont on
    sait qu'il contient une référence inventée, un chiffre non rattaché ou un
    DOI hors base. C'est exactement ce que le produit existe pour empêcher.
    """

    code = "SECTION_GUARDRAIL_FAILED"
    status_code = 422


def _now() -> str:
    return datetime.now(UTC).isoformat()


# --- Lecture --------------------------------------------------------------


async def get(conn: aiosqlite.Connection, section_id: int) -> DraftSectionOut:
    async with conn.execute(
        "SELECT id, plan_node_id, content_qmd, status, quality_score, version,"
        " generated_at, validated_at FROM draft_section WHERE id = ?",
        (section_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        raise NotFoundError(f"Section {section_id} inconnue.", section_id=section_id)

    return DraftSectionOut(
        id=int(row[0]),
        plan_node_id=row[1],
        content_qmd=str(row[2]),
        status=SectionStatus(str(row[3])),
        quality_score=row[4],
        version=int(row[5]),
        citations=await citations_of(conn, section_id),
        generated_at=row[6],
        validated_at=row[7],
    )


async def citations_of(conn: aiosqlite.Connection, section_id: int) -> list[CitationOut]:
    async with conn.execute(
        "SELECT id, source_id, chunk_id, bibtex_key, locator, verified FROM citation"
        " WHERE draft_section_id = ? ORDER BY id",
        (section_id,),
    ) as cur:
        return [
            CitationOut(
                id=int(r[0]),
                source_id=int(r[1]),
                chunk_id=r[2],
                bibtex_key=str(r[3]),
                locator=r[4],
                verified=bool(r[5]),
            )
            for r in await cur.fetchall()
        ]


async def versions_of(conn: aiosqlite.Connection, node_id: int) -> list[int]:
    """Identifiants des versions d'un nœud, de la plus récente à la plus ancienne."""
    async with conn.execute(
        "SELECT id FROM draft_section WHERE plan_node_id = ? ORDER BY version DESC",
        (node_id,),
    ) as cur:
        return [int(r[0]) for r in await cur.fetchall()]


# --- Écriture -------------------------------------------------------------


def _locator(page_start: int | None, page_end: int | None) -> str | None:
    """Localisation dans le PDF. Sans elle, une citation n'est pas retrouvable."""
    if page_start is None:
        return None
    if page_end is None or page_end == page_start:
        return f"p. {page_start}"
    return f"p. {page_start}-{page_end}"


def citation_rows(draft: SectionDraft, context: SectionContext) -> list[tuple]:
    """Lignes de citation déduites des affirmations sourcées.

    Une clé est rapprochée des extraits de SA source : un extrait déclaré
    sous une clé qui n'est pas la sienne produirait une localisation fausse,
    c'est-à-dire une citation qu'on ne retrouve pas dans le PDF.
    """
    par_chunk = {hit.chunk_id: hit for hit in context.chunks}
    source_de_cle = {bibtex_key(hit): hit.source_id for hit in context.chunks}

    lignes: dict[tuple[str, int | None], tuple] = {}
    for claim in draft.claims:
        if claim.kind != "sourced":
            continue
        for cle in claim.citation_keys:
            source_id = source_de_cle.get(cle)
            if source_id is None:
                # Impossible après V1 ; ne pas écrire une ligne dont la
                # source est inventée reste la bonne conduite si V1 saute.
                continue
            propres = [
                c for c in claim.chunk_ids if c in par_chunk and par_chunk[c].source_id == source_id
            ]
            for chunk_id in propres or [None]:
                hit = par_chunk.get(chunk_id) if chunk_id is not None else None
                lignes[(cle, chunk_id)] = (
                    source_id,
                    chunk_id,
                    cle,
                    _locator(hit.page_start, hit.page_end) if hit else None,
                )
    return list(lignes.values())


async def save_draft(
    conn: aiosqlite.Connection,
    project_id: int,
    node_id: int,
    draft: SectionDraft,
    context: SectionContext,
    status: SectionStatus = SectionStatus.REVIEWING,
) -> int:
    """Écrit une nouvelle version de la section et ses citations vérifiées."""
    async with conn.execute(
        "SELECT COALESCE(MAX(version), 0) FROM draft_section WHERE plan_node_id = ?",
        (node_id,),
    ) as cur:
        version = int((await cur.fetchone())[0]) + 1

    async with transaction(conn):
        cur = await conn.execute(
            "INSERT INTO draft_section (plan_node_id, content_qmd, status, version,"
            " generated_at) VALUES (?, ?, ?, ?, ?)",
            (node_id, draft.content_qmd, str(status), version, _now()),
        )
        section_id = int(cur.lastrowid or 0)

        for source_id, chunk_id, cle, locator in citation_rows(draft, context):
            await conn.execute(
                "INSERT INTO citation (draft_section_id, source_id, chunk_id, bibtex_key,"
                " locator, verified) VALUES (?, ?, ?, ?, ?, 1)",
                (section_id, source_id, chunk_id, cle, locator),
            )

        await audit_service.append(
            conn,
            project_id,
            AuditEventType.LLM_CALL,
            {
                "section_id": section_id,
                "plan_node_id": node_id,
                "version": version,
                "mots": draft.word_count,
                "affirmations": len(draft.claims),
                "sourcees": len(draft.sourced_claims()),
            },
            tx=True,
        )
    logger.info(
        "Section %s enregistrée en version %s (%s mots)", section_id, version, draft.word_count
    )
    return section_id


async def update_content(
    conn: aiosqlite.Connection, project_id: int, section_id: int, payload: SectionUpdate
) -> DraftSectionOut:
    """Édition manuelle. Le texte devient celui de l'auteur.

    Les citations dont la clé a disparu du texte passent à `verified = 0` :
    elles ont été vérifiées contre un contenu qui n'existe plus. Aucune ligne
    n'est supprimée, et une clé qui réapparaît ne se revérifie pas d'elle-même
    — cela exigerait de rejouer V1, V2 et V3 contre un contexte RAG qui n'est
    plus celui de la génération.
    """
    await get(conn, section_id)  # 404 si absente
    presentes = sorted(extract_inline_keys(payload.content_qmd))

    # UPDATE, jamais DELETE. Le WHERE ne porte que sur `verified` : une ligne
    # déjà dévérifiée le reste, et aucune n'est retirée.
    condition = ""
    if presentes:
        marques = ",".join("?" * len(presentes))
        condition = f" AND bibtex_key NOT IN ({marques})"  # marques : que des « ? »

    async with transaction(conn):
        await conn.execute(
            "UPDATE draft_section SET content_qmd = ? WHERE id = ?",
            (payload.content_qmd, section_id),
        )
        cur = await conn.execute(
            "UPDATE citation SET verified = 0 WHERE draft_section_id = ? AND verified = 1"
            + condition,
            (section_id, *presentes),
        )
        deverifiees = cur.rowcount
        await audit_service.append(
            conn,
            project_id,
            AuditEventType.HUMAN_VALIDATION,
            {
                "section_id": section_id,
                "edition": "manuelle",
                "citations_deverifiees": deverifiees,
            },
            tx=True,
        )
    if deverifiees:
        logger.info("Section %s : %s citation(s) dévérifiée(s)", section_id, deverifiees)
    return await get(conn, section_id)


# --- Rédaction ------------------------------------------------------------


def find_node(plan: PlanOut, node_id: int) -> PlanNodeOut:
    """Nœud du plan par identifiant, à n'importe quelle profondeur."""

    def descendre(noeuds: list[PlanNodeOut]) -> PlanNodeOut | None:
        for noeud in noeuds:
            if noeud.id == node_id:
                return noeud
            trouve = descendre(noeud.children)
            if trouve is not None:
                return trouve
        return None

    noeud = descendre(plan.nodes)
    if noeud is None:
        raise NotFoundError(f"Nœud {node_id} absent du plan courant.", node_id=node_id)
    return noeud


async def _validate_draft(
    conn: aiosqlite.Connection, brut: str, context: SectionContext
) -> tuple[SectionDraft | None, str | None]:
    """Guardrail Pydantic, longueur, puis véracité. Rend (section, rejet)."""
    resultat = await validate_output(brut, SectionDraft)
    if not resultat.ok:
        return None, resultat.error_message

    draft: SectionDraft = resultat.parsed  # type: ignore[assignment]
    try:
        draft.check_word_count(context.target_words)
    except ValueError as exc:
        return None, str(exc)

    veracite = await check_veracity(conn, draft, context)
    if not veracite.ok:
        return None, veracite.to_correction()
    return draft, None


async def draft_section(
    conn: aiosqlite.Connection,
    project_id: int,
    node_id: int,
    manager: LLMManager,
    task_id: int | None = None,
) -> DraftSectionOut:
    """Rédige une section. Refuse si le plan n'est pas validé ou le RAG trop maigre.

    Le contrôle de plan validé vient de US-PLAN-001 : il est consommé, pas
    réimplémenté — un second contrôle finirait par diverger du premier.
    """
    plan = await plan_service.assert_writable(conn, project_id)
    noeud = find_node(plan, node_id)
    context = await build_section_context(conn, noeud, plan)

    agent = WriterAgent(manager)
    state: GraphState = initial_state(project_id)
    state["current_node_id"] = node_id
    state["plan_id"] = plan.id
    state["state"] = WorkflowState.SECTION_DRAFTING
    ctx = {"context": context, "task_id": task_id}

    while True:
        brut = await agent.run(state, ctx)
        draft, rejet = await _validate_draft(conn, brut, context)
        if draft is not None:
            section_id = await save_draft(conn, project_id, node_id, draft, context)
            if task_id is not None:
                await task_service.update(
                    conn, task_id, state=WorkflowState.SECTION_REVIEWING, progress=1.0
                )
            return await get(conn, section_id)

        essai = state["retry_count"] + 1
        if task_id is not None:
            task_service.emit_guardrail(task_id, agent.name.value, essai, rejet)
        decision = register_guardrail_failure(state, rejet or "rejet sans message")
        async with transaction(conn):
            await audit_service.append(
                conn,
                project_id,
                AuditEventType.GUARDRAIL_TRIGGERED,
                {"plan_node_id": node_id, "essai": essai, "motif": (rejet or "")[:500]},
                tx=True,
            )
        if decision.tripped:
            if task_id is not None:
                await task_service.update(
                    conn,
                    task_id,
                    state=WorkflowState.ERROR_STATE,
                    last_error=decision.reason,
                )
            raise SectionGuardrailError(
                f"La section « {noeud.title} » n'a pas été produite : "
                f"{MAX_GUARDRAIL_RETRIES} essais rejetés par les garde-fous de "
                f"véracité. Dernier motif : {rejet}",
                node_id=node_id,
                retry_count=state["retry_count"],
            )


__all__ = [
    "SectionGuardrailError",
    "citation_rows",
    "citations_of",
    "draft_section",
    "find_node",
    "get",
    "save_draft",
    "update_content",
    "versions_of",
]
