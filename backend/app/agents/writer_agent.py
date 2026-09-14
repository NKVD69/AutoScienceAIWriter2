"""Agent rédacteur — US-301, spécifications §5.4.

**Il retourne du texte brut.** La validation appartient au guardrail, les
garde-fous de véracité au nœud `SECTION_GUARDRAIL`, la transition au graphe
(US-201). Un agent qui contrôlerait sa propre sortie ne pourrait plus être
exercé sur des sorties fautives.

**Les tokens sont émis au fil de l'eau, avant toute validation.** À ~2,3
tokens/s (ADR-015), une section de 1 500 mots demande un quart d'heure :
attendre la fin pour montrer quoi que ce soit laisserait l'utilisateur devant
une barre d'attente sans savoir si le texte prend la bonne direction. Ce qui
est diffusé n'est pas encore validé — c'est un aperçu, et l'interface le
présente comme tel jusqu'au verdict des garde-fous.

**Chaque extrait est présenté avec son identifiant, sa clé et ses pages.**
Le modèle ne peut rattacher une affirmation à un `chunk_id` qu'il n'a pas vu ;
sans ce format stable, V2 rejetterait des affirmations correctes que le
modèle n'avait aucun moyen de sourcer.
"""

from __future__ import annotations

import json

from app.agents.state import GraphState
from app.core.config import get_settings
from app.core.logging import get_logger
from app.llm.manager import LLMManager
from app.llm.prompts.registry import AgentName
from app.models.section import SectionDraft
from app.models.source import TaskEvent
from app.rag.context_builder import SectionContext
from app.services import task_service

logger = get_logger(__name__)

# Une section de 1 500 mots pèse ~2 000 tokens ; le JSON qui l'enveloppe et
# les affirmations qualifiées en ajoutent autant. En deçà, la génération se
# fait couper en fin de course et le JSON reste inachevé.
MAX_OUTPUT_TOKENS = 4096

SCHEMA_EXAMPLE = json.dumps(
    {
        "content_qmd": "Le texte de la section, en Quarto Markdown.",
        "claims": [
            {
                "text": "affirmation telle qu'elle apparaît dans le texte",
                "kind": "sourced | synthesis | hypothesis | limitation",
                "citation_keys": ["clé prise dans la liste autorisée"],
                "chunk_ids": [12],
            }
        ],
        "word_count": 1500,
    },
    ensure_ascii=False,
    indent=2,
)


def format_chunk(index: int, chunk_id: int, cle: str, titre: str, pages: str, texte: str) -> str:
    """Présentation d'un extrait. Format stable — le modèle s'y appuie."""
    return f"[{index}] chunk_id={chunk_id} · clé={cle} · {titre} · {pages}\n{texte}"


def _pages(page_start: int | None, page_end: int | None) -> str:
    if page_start is None:
        return "pages inconnues"
    if page_end is None or page_end == page_start:
        return f"p. {page_start}"
    return f"p. {page_start}-{page_end}"


def build_user_message(context: SectionContext, correction: str | None = None) -> str:
    """Message utilisateur. Toutes les données variables sont ici (ADR-003)."""
    from app.rag.context_builder import bibtex_key

    extraits = "\n\n".join(
        format_chunk(
            index=i,
            chunk_id=hit.chunk_id,
            cle=bibtex_key(hit),
            titre=hit.source_title,
            pages=_pages(hit.page_start, hit.page_end),
            texte=hit.text,
        )
        for i, hit in enumerate(context.chunks, start=1)
    )

    message = f"""Section à rédiger : {context.node_title}
Objectif de la section : {context.node_objective}
Longueur cible : {context.target_words} mots

Clés de citation AUTORISÉES, liste close — aucune autre n'est admise :
{", ".join(context.allowed_keys)}

Extraits disponibles, et eux seuls :

{extraits}

Rédige la section et renvoie un objet JSON strict de la forme :

{SCHEMA_EXAMPLE}

Chaque affirmation du texte figure dans claims avec sa nature. Une
affirmation sourced porte au moins une clé de la liste ci-dessus et au moins
un chunk_id pris parmi ceux fournis. Chacune de ses clés apparaît dans content_qmd
sous la forme [@clé], à l'endroit de l'affirmation : une clé déclarée mais
absente du texte fait rejeter la section. Une affirmation synthesis,
hypothesis ou limitation ne porte aucune clé. word_count est le nombre de mots
réel de content_qmd."""

    if correction:
        # Ajouté au message utilisateur, jamais au prompt système : celui-ci
        # doit rester stable octet pour octet d'un essai à l'autre (ADR-003).
        message += f"\n\nTa réponse précédente a été rejetée : {correction}"
    return message


class WriterAgent:
    """Agent conforme au Protocol `Agent` de US-201."""

    name = AgentName.WRITER
    output_model = SectionDraft

    def __init__(self, manager: LLMManager) -> None:
        self._manager = manager

    def build_user_message(self, state: GraphState, ctx: dict) -> str:
        return build_user_message(ctx["context"], correction=state.get("last_error"))

    async def run(self, state: GraphState, ctx: dict) -> str:
        """Texte brut du modèle, diffusé au fil de l'eau. Aucune validation ici."""
        message = self.build_user_message(state, ctx)
        task_id = ctx.get("task_id")

        fragments: list[str] = []
        async for fragment in self._manager.stream_for_agent(
            AgentName.WRITER,
            message,
            max_tokens=ctx.get("max_tokens", MAX_OUTPUT_TOKENS),
            temperature=get_settings().writer_temperature,
        ):
            fragments.append(fragment)
            if task_id is not None:
                # Émis avant toute validation : l'utilisateur voit le texte
                # se former, le verdict vient à la fin.
                task_service.emit(
                    TaskEvent(type="token", task_id=int(task_id), payload={"text": fragment})
                )

        brut = "".join(fragments)
        logger.debug("Section « %s » : %s caractères bruts", ctx["context"].node_title, len(brut))
        return brut
