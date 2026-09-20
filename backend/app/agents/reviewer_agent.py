"""Agent relecteur — US-302, spécifications §5.4.

**Il retourne du texte brut.** La validation appartient au guardrail, la
consolidation du score au service, la transition au graphe (US-201). Un agent
qui jugerait recevable sa propre sortie ne pourrait plus être exercé sur des
sorties fautives — extrait halluciné, catégorie manquante, verdict incohérent.

**Il évalue le TEXTE, pas le PROCESSUS.** Il ne reçoit ni l'historique de
génération, ni les versions antérieures, ni les rapports précédents. Lui
montrer par quoi le texte est passé biaiserait son jugement : une section doit
valoir ce qu'elle vaut, pas ce qu'elle a coûté.
"""

from __future__ import annotations

import json

from app.agents.state import GraphState
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.vector import ChunkHit
from app.llm.manager import LLMManager
from app.llm.prompts.registry import AgentName
from app.models.review import CATEGORIES, ReviewReport

logger = get_logger(__name__)

MAX_OUTPUT_TOKENS = 2048

SCHEMA_EXAMPLE = json.dumps(
    {
        "findings": [
            {
                "severity": "major | blocking | minor | suggestion",
                "category": "coherence | argumentation | sourcing | style | "
                "structure | completeness",
                "excerpt": "extrait recopié mot pour mot du texte, 20 à 300 caractères",
                "message": "ce qui ne va pas, précisément",
                "suggestion": "correction proposée, ou null",
            }
        ],
        "scores": {c: 0 for c in CATEGORIES},
        "overall_score": 0,
        "verdict": "ready | needs_work | insufficient",
    },
    ensure_ascii=False,
    indent=2,
)


def _chunk_line(hit: ChunkHit) -> str:
    pages = "" if hit.page_start is None else f" (p. {hit.page_start})"
    return f"- {hit.source_title}{pages} : {hit.text[:200]}"


def build_user_message(
    content_qmd: str,
    node_objective: str,
    target_words: int,
    chunks: list[ChunkHit],
) -> str:
    """Message utilisateur. Le texte à juger et son cadre, rien du processus."""
    extraits = "\n".join(_chunk_line(h) for h in chunks) or "(aucune source fournie)"
    return f"""Objectif de la section (ce qu'elle doit établir) : {node_objective}
Longueur cible : {target_words} mots

Sources utilisées à la rédaction :
{extraits}

Texte à relire :
---
{content_qmd}
---

Rends un rapport de relecture au format JSON strict suivant. Chaque extrait
d'un constat est recopié MOT POUR MOT depuis le texte ci-dessus :

{SCHEMA_EXAMPLE}"""


class ReviewerAgent:
    """Agent conforme au Protocol `Agent` de US-201."""

    name = AgentName.REVIEWER
    output_model = ReviewReport

    def __init__(self, manager: LLMManager) -> None:
        self._manager = manager

    def build_user_message(self, state: GraphState, ctx: dict) -> str:
        return build_user_message(
            content_qmd=ctx["content_qmd"],
            node_objective=ctx["node_objective"],
            target_words=ctx["target_words"],
            chunks=ctx.get("chunks", []),
        )

    async def run(self, state: GraphState, ctx: dict) -> str:
        """Texte brut du modèle. Aucune validation ici (US-201, point 3)."""
        message = self.build_user_message(state, ctx)
        resultat = await self._manager.generate_for_agent(
            AgentName.REVIEWER,
            message,
            max_tokens=ctx.get("max_tokens", MAX_OUTPUT_TOKENS),
            temperature=get_settings().reviewer_temperature,
        )
        logger.debug("Relecture : %s tokens de sortie", resultat.completion_tokens)
        return resultat.text
