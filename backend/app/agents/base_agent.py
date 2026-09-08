"""Contrat d'agent — US-201.

**Un agent retourne du texte brut.** Il ne désérialise pas, ne valide pas, ne
décide pas de la suite. La validation appartient au guardrail, la transition
au graphe.

Cette séparation n'est pas un goût d'architecture : elle rend les trois
testables séparément. Un agent qui validerait sa propre sortie ne pourrait
plus être exercé sur des sorties fautives, et c'est précisément ce qu'il faut
exercer — un modèle de 31B produit du JSON conforme la plupart du temps, ce
qui rend ses échecs rares, donc difficiles à provoquer autrement qu'en les
injectant.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from app.agents.state import GraphState
from app.llm.prompts.registry import AgentName


@runtime_checkable
class Agent(Protocol):
    """Ce qu'un nœud du graphe attend d'un agent."""

    name: AgentName
    output_model: type[BaseModel]

    def build_user_message(self, state: GraphState, ctx: dict) -> str:
        """Message utilisateur. Le prompt système vient du registre (ADR-003).

        Ne reçoit que le strict nécessaire : jamais l'historique complet de
        la conversation, dont le coût en tokens n'est pas borné et dont la
        présence saturerait le cache KV sur 10 Go de VRAM.
        """
        ...

    async def run(self, state: GraphState, ctx: dict) -> str:
        """Texte brut du modèle. Aucune validation ici."""
        ...
