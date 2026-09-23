"""Agent code — US-401, §8.

**Il retourne du texte brut.** La validation appartient au guardrail (US-201),
l'exécution isolée au bac à sable (US-004), le rapprochement des artefacts au
service. Un agent qui jugerait recevable sa propre sortie ne pourrait plus être
exercé sur des propositions fautives — label MyST, graine manquante, nom de
fichier avec chemin.

**La correction passe par le message utilisateur, jamais par le prompt système.**
Le prompt système reste identique octet pour octet d'un essai à l'autre, faute
de quoi le *prefix caching* d'ADR-003 tomberait. Les vingt dernières lignes de
`stderr`, le code numéroté et la nature d'un éventuel dépassement vont dans le
message : c'est ce qui rend l'essai suivant utile plutôt qu'aléatoire.
"""

from __future__ import annotations

import json

from app.agents.state import GraphState
from app.core.config import get_settings
from app.core.logging import get_logger
from app.llm.manager import LLMManager
from app.llm.prompts.registry import AgentName
from app.models.code import CodeProposal

logger = get_logger(__name__)

MAX_OUTPUT_TOKENS = 3072

SCHEMA_EXAMPLE = json.dumps(
    {
        "code": "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n...",
        "intent": "ce que la figure ou le tableau doit montrer",
        "datasets": ["nom-du-jeu-de-donnees"],
        "expected_artifacts": [
            {
                "filename": "filtration.png",
                "kind": "figure | table | data",
                "caption": "légende de l'artefact",
                "label": "fig-slug (ou tbl-slug)",
            }
        ],
        "random_seed": 1234,
    },
    ensure_ascii=False,
    indent=2,
)


def _dataset_lines(datasets: list[dict]) -> str:
    if not datasets:
        return "(aucun jeu de données monté)"
    return "\n".join(f"- {d['name']} : {d['guest_path']}" for d in datasets)


def build_user_message(
    intent: str,
    datasets: list[dict],
    correction: str | None = None,
) -> str:
    """Message utilisateur : l'intention, les jeux montés, le schéma attendu.

    En reprise, `correction` porte le retour d'exécution — il précède la
    consigne, pour que le modèle le lise avant de réécrire.
    """
    entete = ""
    if correction:
        entete = (
            "REPRISE APRÈS ÉCHEC D'EXÉCUTION. Corrige le code au vu du retour "
            "ci-dessous, sans introduire d'autre défaut :\n"
            f"{correction}\n\n"
        )
    return f"""{entete}Intention : {intent}

Jeux de données disponibles (chemins à lire, en lecture seule) :
{_dataset_lines(datasets)}

Rends une proposition de code au format JSON strict suivant. Le code écrit
chaque artefact dans le répertoire courant sous le nom déclaré :

{SCHEMA_EXAMPLE}"""


class CodeAgent:
    """Agent conforme au Protocol `Agent` de US-201."""

    name = AgentName.CODE
    output_model = CodeProposal

    def __init__(self, manager: LLMManager) -> None:
        self._manager = manager

    def build_user_message(self, state: GraphState, ctx: dict) -> str:
        return build_user_message(
            intent=ctx["intent"],
            datasets=ctx.get("datasets", []),
            correction=ctx.get("correction"),
        )

    async def run(self, state: GraphState, ctx: dict) -> str:
        """Texte brut du modèle. Aucune validation ici (US-201, point 3)."""
        message = self.build_user_message(state, ctx)
        resultat = await self._manager.generate_for_agent(
            AgentName.CODE,
            message,
            max_tokens=ctx.get("max_tokens", MAX_OUTPUT_TOKENS),
            temperature=get_settings().llm_temperature_default,
        )
        logger.debug("Agent code : %s tokens de sortie", resultat.completion_tokens)
        return resultat.text
