"""Relecture, score et boucle de correction — US-302, spécifications §5.4.

**La relecture conseille, elle ne valide jamais.** Aucun chemin de ce module
ne fait passer une section en VALIDATED : cette transition est une porte
humaine (ADR-004). Le meilleur score possible s'arrête à la porte.

**Le score global est recomposé ici, jamais repris du modèle.** Le modèle
propose des scores par catégorie ; le serveur les pondère. Et deux d'entre eux
— sourçage et complétude — sont pour moitié MESURÉS en Python : longueur réelle
contre cible, proportion d'affirmations sourcées, nombre de sources distinctes.
Un modèle estime mal les proportions ; une requête SQL ne se trompe pas.

**La pause propose la MEILLEURE version, pas la dernière.** Un 7B dégrade
souvent sa sortie en corrigeant : au bout de trois boucles sans convergence,
c'est la version au plus haut score qui est mise en avant, et toutes les
versions comme tous les rapports restent lisibles pour la comparaison.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import aiosqlite

from app.agents.guardrails import validate_output
from app.agents.reviewer_agent import ReviewerAgent
from app.agents.state import GraphState, WorkflowState, initial_state
from app.core.config import get_settings
from app.core.errors import AppError, NotFoundError
from app.core.logging import get_logger
from app.db.session import transaction
from app.llm.manager import LLMManager
from app.models.audit import AuditEventType
from app.models.review import (
    CORRECTABLE,
    Finding,
    ReviewReport,
    ReviewReportOut,
    weighted_overall,
)
from app.rag.context_builder import build_section_context
from app.services import audit_service, plan_service, section_service, task_service

logger = get_logger(__name__)

# Nombre de sources distinctes au-delà duquel le sous-score « diversité » est
# plein. Aligné sur le plancher de matière de US-301 (au moins trois extraits) :
# une section sourcée sur une seule référence est une paraphrase.
DISTINCT_SOURCES_FULL = 3
# Part mesurée en Python dans le score de sourçage et de complétude. « à
# hauteur de la moitié de chaque » (prompt) : l'autre moitié reste au modèle.
DETERMINISTIC_SHARE = 0.5


class SectionReviewError(AppError):
    code = "SECTION_REVIEW_FAILED"
    status_code = 422


def _now() -> str:
    return datetime.now(UTC).isoformat()


# --- Mesures déterministes ------------------------------------------------


def length_ratio_score(measured_words: int, target_words: int) -> float:
    """Score de longueur : plein à la cible, nul à mi-cible ou au double.

    Triangulaire autour de 1,0. Un texte à 60 % de la cible et un à 140 %
    tombent au même 60 : trop court et trop long sont deux défauts, pas un.
    """
    if target_words <= 0:
        return 100.0
    ratio = measured_words / target_words
    return round(100.0 * max(0.0, 1.0 - abs(ratio - 1.0)), 1)


def sourced_ratio_score(sourced_claims: int, total_claims: int) -> float:
    """Proportion d'affirmations rattachées à une source, sur 100."""
    if total_claims <= 0:
        return 0.0
    return round(100.0 * sourced_claims / total_claims, 1)


def distinct_sources_score(distinct_sources: int) -> float:
    """Diversité des sources : pleine dès trois références distinctes."""
    return round(100.0 * min(distinct_sources, DISTINCT_SOURCES_FULL) / DISTINCT_SOURCES_FULL, 1)


class DeterministicMeasures:
    """Les trois mesures Python et leur report sur deux catégories."""

    def __init__(
        self,
        measured_words: int,
        target_words: int,
        sourced: int,
        total: int,
        distinct: int,
    ) -> None:
        self.length = length_ratio_score(measured_words, target_words)
        self.sourced_ratio = sourced_ratio_score(sourced, total)
        self.distinct = distinct_sources_score(distinct)

    @property
    def completeness(self) -> float:
        """Complétude mesurée : longueur et diversité des sources, à parts égales."""
        return round((self.length + self.distinct) / 2, 1)

    @property
    def sourcing(self) -> float:
        """Sourçage mesuré : proportion sourcée et diversité, à parts égales."""
        return round((self.sourced_ratio + self.distinct) / 2, 1)


def blend(model_scores: dict[str, float], mesures: DeterministicMeasures) -> dict[str, float]:
    """Fusionne scores du modèle et mesures Python.

    Seuls le sourçage et la complétude sont mélangés, moitié-moitié. Les quatre
    autres catégories — cohérence, argumentation, style, structure — relèvent du
    jugement, qu'aucune requête ne remplace : elles restent celles du modèle.
    """
    part = DETERMINISTIC_SHARE
    fondus = dict(model_scores)
    fondus["completeness"] = round(
        (1 - part) * model_scores["completeness"] + part * mesures.completeness, 1
    )
    fondus["sourcing"] = round((1 - part) * model_scores["sourcing"] + part * mesures.sourcing, 1)
    return fondus


# --- Lecture du contexte de section ---------------------------------------


async def _section_row(conn: aiosqlite.Connection, section_id: int) -> aiosqlite.Row:
    async with conn.execute(
        "SELECT id, plan_node_id, content_qmd, version, claim_count, sourced_claim_count"
        " FROM draft_section WHERE id = ?",
        (section_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        raise NotFoundError(f"Section {section_id} inconnue.", section_id=section_id)
    return row


async def _distinct_sources(conn: aiosqlite.Connection, section_id: int) -> int:
    async with conn.execute(
        "SELECT COUNT(DISTINCT source_id) FROM citation"
        " WHERE draft_section_id = ? AND verified = 1",
        (section_id,),
    ) as cur:
        return int((await cur.fetchone())[0])


# --- Persistance des rapports ---------------------------------------------


async def save_report(
    conn: aiosqlite.Connection,
    project_id: int,
    section_id: int,
    version: int,
    report: ReviewReport,
    scores: dict[str, float],
    overall: float,
    auto_correct: bool,
) -> ReviewReportOut:
    """Écrit un rapport et met à jour le score de qualité de la section.

    Un rapport par relecture, jamais écrasé : l'historique d'une section se lit
    en additionnant ses rapports. Le score courant est aussi recopié sur
    `draft_section.quality_score`, pour qu'une lecture de section le porte sans
    rejouer la relecture.
    """
    async with transaction(conn):
        cur = await conn.execute(
            "INSERT INTO review_report (draft_section_id, overall_score, verdict, scores_json,"
            " findings_json, auto_correct, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                section_id,
                overall,
                report.verdict,
                json.dumps(scores, ensure_ascii=False),
                json.dumps([f.model_dump() for f in report.findings], ensure_ascii=False),
                1 if auto_correct else 0,
                _now(),
            ),
        )
        report_id = int(cur.lastrowid or 0)
        await conn.execute(
            "UPDATE draft_section SET quality_score = ? WHERE id = ?", (overall, section_id)
        )
        await audit_service.append(
            conn,
            project_id,
            AuditEventType.LLM_CALL,
            {
                "review_report_id": report_id,
                "section_id": section_id,
                "verdict": report.verdict,
                "overall_score": overall,
                "findings": len(report.findings),
            },
            tx=True,
        )
    return ReviewReportOut(
        id=report_id,
        draft_section_id=section_id,
        section_version=version,
        findings=report.findings,
        scores=scores,
        overall_score=overall,
        verdict=report.verdict,
        auto_correct=auto_correct,
        created_at=_now(),
    )


def _row_to_report_out(row: aiosqlite.Row) -> ReviewReportOut:
    return ReviewReportOut(
        id=int(row[0]),
        draft_section_id=int(row[1]),
        section_version=int(row[2]),
        overall_score=float(row[3]),
        verdict=str(row[4]),
        scores=json.loads(row[5]),
        findings=[Finding.model_validate(f) for f in json.loads(row[6])],
        auto_correct=bool(row[7]),
        created_at=str(row[8]),
    )


_REPORT_SELECT = (
    "SELECT r.id, r.draft_section_id, d.version, r.overall_score, r.verdict, r.scores_json,"
    " r.findings_json, r.auto_correct, r.created_at FROM review_report r"
    " JOIN draft_section d ON d.id = r.draft_section_id"
)


async def last_report(conn: aiosqlite.Connection, section_id: int) -> ReviewReportOut:
    async with conn.execute(
        f"{_REPORT_SELECT} WHERE r.draft_section_id = ? ORDER BY r.id DESC LIMIT 1",
        (section_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        raise NotFoundError(
            f"Aucune relecture pour la section {section_id}.", section_id=section_id
        )
    return _row_to_report_out(row)


async def reports_for_node(conn: aiosqlite.Connection, node_id: int) -> list[ReviewReportOut]:
    """Historique de tous les rapports du nœud, versions confondues, récents d'abord."""
    async with conn.execute(
        f"{_REPORT_SELECT} WHERE d.plan_node_id = ? ORDER BY r.id DESC",
        (node_id,),
    ) as cur:
        return [_row_to_report_out(r) for r in await cur.fetchall()]


async def best_scored_section(conn: aiosqlite.Connection, node_id: int) -> int | None:
    """Version de section au plus haut score, d'après son dernier rapport.

    « Meilleure », pas « dernière » : c'est ce qui est proposé à la pause.
    """
    async with conn.execute(
        """
        SELECT d.id, r.overall_score FROM draft_section d
        JOIN review_report r ON r.id = (
            SELECT r2.id FROM review_report r2 WHERE r2.draft_section_id = d.id
            ORDER BY r2.id DESC LIMIT 1
        )
        WHERE d.plan_node_id = ?
        ORDER BY r.overall_score DESC, d.version DESC LIMIT 1
        """,
        (node_id,),
    ) as cur:
        row = await cur.fetchone()
    return int(row[0]) if row else None


# --- Orchestration --------------------------------------------------------


def format_corrections(findings: list[Finding]) -> str:
    """Message de correction : uniquement les constats bloquants et majeurs.

    Les mineurs et les suggestions relèvent de l'auteur — les faire corriger
    par un 7B risquerait de dégrader un texte déjà acceptable.
    """
    lignes = [
        f"- [{f.severity}/{f.category}] « {f.excerpt[:80]} » : {f.message}"
        + (f" Proposition : {f.suggestion}" if f.suggestion else "")
        for f in findings
    ]
    return (
        "La relecture a relevé les points suivants, à corriger sans introduire "
        "d'autre défaut :\n" + "\n".join(lignes)
    )


async def review_once(
    conn: aiosqlite.Connection,
    project_id: int,
    section_id: int,
    manager: LLMManager,
    auto_correct: bool,
) -> ReviewReportOut:
    """Relit UNE version de section et persiste son rapport. Aucune correction."""
    row = await _section_row(conn, section_id)
    node_id = row[1]
    content_qmd = str(row[2])
    version = int(row[3])

    plan = await plan_service._require_plan(conn, project_id)
    node = section_service.find_node(plan, int(node_id)) if node_id is not None else None
    objective = node.objective if node else ""
    target = node.target_words if node else 0

    chunks = []
    if node is not None:
        try:
            contexte = await build_section_context(conn, node, plan)
            chunks = contexte.chunks
        except AppError:
            # La matière a pu changer depuis la rédaction : le relecteur juge
            # alors le seul texte, ce qui reste utile.
            logger.info("Relecture sans contexte RAG pour la section %s", section_id)

    agent = ReviewerAgent(manager)
    state: GraphState = initial_state(project_id)
    state["current_node_id"] = int(node_id) if node_id is not None else None
    ctx = {
        "content_qmd": content_qmd,
        "node_objective": objective,
        "target_words": target,
        "chunks": chunks,
    }

    brut = await agent.run(state, ctx)
    resultat = await validate_output(brut, ReviewReport)
    if not resultat.ok:
        raise SectionReviewError(resultat.error_message or "rapport de relecture invalide")
    report: ReviewReport = resultat.parsed  # type: ignore[assignment]
    # Unique contrôle de véracité de la relecture : un extrait halluciné est un
    # rejet, au même titre qu'un JSON mal formé.
    try:
        report.check_excerpts_present(content_qmd)
    except ValueError as exc:
        raise SectionReviewError(str(exc)) from exc

    mesures = DeterministicMeasures(
        measured_words=len(content_qmd.split()),
        target_words=target,
        sourced=int(row[5]),
        total=int(row[4]),
        distinct=await _distinct_sources(conn, section_id),
    )
    scores = blend(report.scores, mesures)
    overall = weighted_overall(scores, get_settings().review_weights)
    return await save_report(
        conn, project_id, section_id, version, report, scores, overall, auto_correct
    )


async def run_review(
    conn: aiosqlite.Connection,
    project_id: int,
    section_id: int,
    manager: LLMManager,
    auto_correct: bool = False,
    task_id: int | None = None,
) -> ReviewReportOut:
    """Relit une section, corrige en boucle si demandé, et s'arrête à la porte.

    La correction n'est jamais implicite : sans `auto_correct`, la relecture
    rend un rapport et s'arrête là. Avec, le rédacteur est relancé sur les
    seuls constats bloquants et majeurs, jusqu'à convergence ou plafond.
    """
    from app.agents.breaker import MAX_REVIEW_LOOPS

    node_id = int((await _section_row(conn, section_id))[1])
    courant = section_id
    dernier = await review_once(conn, project_id, section_id, manager, auto_correct)

    boucle = 0
    while (
        auto_correct
        and dernier.verdict in ("needs_work", "insufficient")
        and boucle < MAX_REVIEW_LOOPS
    ):
        boucle += 1
        a_corriger = [f for f in dernier.findings if f.severity in CORRECTABLE]
        correction = format_corrections(a_corriger)
        if task_id is not None:
            await task_service.update(conn, task_id, state=WorkflowState.SECTION_CORRECTING)
        courant = await section_service.draft_section(
            conn, project_id, node_id, manager, task_id=task_id, correction=correction
        )
        dernier = await review_once(conn, project_id, courant.id, manager, auto_correct)

    if boucle >= MAX_REVIEW_LOOPS and dernier.verdict in ("needs_work", "insufficient"):
        meilleure = await best_scored_section(conn, node_id)
        logger.warning(
            "Relecture : %s boucles sans convergence, pause sur la meilleure version %s",
            boucle,
            meilleure,
        )
        if meilleure is not None and meilleure != courant:
            return await last_report(conn, meilleure)

    if task_id is not None:
        await task_service.update(
            conn, task_id, state=WorkflowState.SECTION_REVIEWING, progress=1.0
        )
    return dernier
