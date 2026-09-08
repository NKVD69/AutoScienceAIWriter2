"""Contexte RAG d'une section — US-301, spécifications §5.4.

**La liste des clés autorisées est dérivée des chunks retenus, et d'eux
seuls.** C'est ce qui rend le garde-fou V1 utilisable : sans liste close, une
clé « plausible » serait indistinguable d'une clé réelle, et la vérification
retomberait sur le jugement d'un modèle — exactement ce que §5.5 exclut.

**Trois chunks par source au maximum.** Sans plafond, un article long et bien
indexé rafle les douze extraits et la section devient sa paraphrase. Le
plafond force la confrontation de sources, qui est le propre d'un travail
académique.

**Sous trois chunks, on refuse d'écrire.** Une section produite sans matière
serait remplie par le modèle depuis sa mémoire — c'est-à-dire par des
affirmations sans source, présentées comme si elles en avaient une.
"""

from __future__ import annotations

import re

import aiosqlite
from pydantic import BaseModel

from app.core.errors import AppError
from app.core.logging import get_logger
from app.db.vector import ChunkHit
from app.models.plan import PlanNodeOut, PlanOut
from app.rag.retriever import retrieve

logger = get_logger(__name__)

# Approximation usuelle pour du texte académique latin, cohérente avec le
# découpage de US-102.
CHARS_PER_TOKEN = 4
CANDIDATES = 12
MAX_CHUNKS_PER_SOURCE = 3
MIN_CHUNKS = 3


class InsufficientContextError(AppError):
    """Trop peu d'extraits pour rédiger une section sourcée."""

    code = "INSUFFICIENT_CONTEXT"
    status_code = 409

    @classmethod
    def for_node(cls, titre: str, trouves: int) -> InsufficientContextError:
        return cls(
            f"Seulement {trouves} extrait(s) pertinent(s) pour « {titre} », il en "
            f"faut au moins {MIN_CHUNKS}. Rédiger sur cette base produirait un "
            "texte que le modèle remplirait de mémoire, sans source. Importer et "
            "approuver davantage de sources sur ce point, ou élargir l'objectif "
            "du nœud.",
            node_title=titre,
            chunks_found=trouves,
        )


class SectionContext(BaseModel):
    """Matière d'une section, et la liste close de ce qu'elle autorise."""

    node_id: int
    node_title: str
    node_objective: str
    target_words: int
    query: str
    chunks: list[ChunkHit]
    # Liste CLOSE : toute clé absente d'ici est un rejet (V1).
    allowed_keys: list[str]
    token_estimate: int

    def chunk_ids(self) -> set[int]:
        return {c.chunk_id for c in self.chunks}


def bibtex_key(hit: ChunkHit) -> str:
    """Clé `auteur_année_motclé`, dérivée de la source (ADR-007, §9.3).

    Déterministe : la même source produit toujours la même clé, sinon le
    `.bib` généré à l'export ne correspondrait plus au texte.
    """
    titre = re.sub(r"[^a-z0-9 ]", "", hit.source_title.lower())
    mots = [m for m in titre.split() if len(m) > 3]
    motcle = mots[0] if mots else "source"
    annee = hit.source_year or "nd"
    return f"src{hit.source_id}_{annee}_{motcle}"


def _tokens(texte: str) -> int:
    return max(1, len(texte) // CHARS_PER_TOKEN)


def build_query(node: PlanNodeOut, problematique: str) -> str:
    """Requête de recherche : titre, objectif et problématique.

    Le titre seul est trop pauvre pour un KNN utile — « Méthodes » ne
    ressemble à rien dans un espace vectoriel. L'objectif porte le sens, la
    problématique le contexte du mémoire.
    """
    return f"{node.title}. {node.objective} {problematique}".strip()


def select_under_budget(hits: list[ChunkHit], budget_tokens: int) -> list[ChunkHit]:
    """Retient les extraits les plus proches, sous budget et plafond par source."""
    par_source: dict[int, int] = {}
    retenus: list[ChunkHit] = []
    total = 0

    for hit in sorted(hits, key=lambda h: h.distance):
        if par_source.get(hit.source_id, 0) >= MAX_CHUNKS_PER_SOURCE:
            continue
        cout = _tokens(hit.text)
        if total + cout > budget_tokens and retenus:
            break
        retenus.append(hit)
        par_source[hit.source_id] = par_source.get(hit.source_id, 0) + 1
        total += cout

    return retenus


async def build_section_context(
    conn: aiosqlite.Connection,
    node: PlanNodeOut,
    plan: PlanOut,
    budget_tokens: int | None = None,
) -> SectionContext:
    """Rassemble la matière d'une section. Refuse si elle est trop maigre."""
    from app.core.config import get_settings

    budget = budget_tokens or get_settings().writer_context_tokens
    requete = build_query(node, plan.problematique)

    # `include_references` reste faux : la bibliographie d'un article donnerait
    # au rédacteur des titres qu'il n'a pas lus (US-102).
    candidats = await retrieve(conn, requete, k=CANDIDATES)
    retenus = select_under_budget(candidats, budget)

    if len(retenus) < MIN_CHUNKS:
        raise InsufficientContextError.for_node(node.title, len(retenus))

    cles = sorted({bibtex_key(h) for h in retenus})
    logger.info(
        "Contexte de « %s » : %s extraits, %s sources, %s clés autorisées",
        node.title,
        len(retenus),
        len({h.source_id for h in retenus}),
        len(cles),
    )
    return SectionContext(
        node_id=node.id,
        node_title=node.title,
        node_objective=node.objective,
        target_words=node.target_words,
        query=requete,
        chunks=retenus,
        allowed_keys=cles,
        token_estimate=sum(_tokens(h.text) for h in retenus),
    )
