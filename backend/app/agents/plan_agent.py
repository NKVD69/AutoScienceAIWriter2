"""Agent de plan — US-PLAN-001.

Trois règles, chacune contre un défaut précis.

**La problématique fournie est reprise telle quelle.** La reformuler, même en
mieux, remplacerait la question du doctorant par celle du modèle. C'est une
exigence explicite du cahier des charges, et elle est vérifiée par un test
sur le message envoyé, non sur la sortie.

**L'agent ne reçoit que titres, années et résumés.** Le contexte est de
8 192 tokens ; y verser le texte intégral de cinquante sources le saturerait
avant même la première phrase du plan.

**L'agent ne cite pas.** Le plan est une structure. Une clé de citation dans
sa sortie viendrait du modèle et non des sources approuvées — c'est la
définition d'une référence inventée, et le guardrail la rejette.
"""

from __future__ import annotations

import json

from app.agents.state import GraphState
from app.core.logging import get_logger
from app.llm.manager import LLMManager
from app.llm.prompts.registry import AgentName
from app.models.plan import PlanTree
from app.models.project import Project
from app.models.source import SourceDocument

logger = get_logger(__name__)

# Au-delà, un résumé de source encombre le contexte sans mieux guider la
# structuration : le plan a besoin du sujet d'un article, pas de sa méthode.
ABSTRACT_EXCERPT = 400
MAX_SOURCES_IN_PROMPT = 40

SCHEMA_EXAMPLE = json.dumps(
    {
        "problematique": "chaîne de 40 à 1500 caractères",
        "research_questions": ["1 à 6 questions de recherche"],
        "methodology_note": "points exigeant un arbitrage du directeur",
        "nodes": [
            {
                "title": "titre du chapitre",
                "objective": "ce que le chapitre établit, de façon vérifiable",
                "target_words": 5000,
                "children": [
                    {
                        "title": "titre de section",
                        "objective": "...",
                        "target_words": 1500,
                        "children": [],
                    }
                ],
            }
        ],
    },
    ensure_ascii=False,
    indent=2,
)


def _describe_sources(sources: list[SourceDocument]) -> str:
    if not sources:
        return "Aucune source approuvée : construis le plan sur le seul sujet."
    lignes = []
    for source in sources[:MAX_SOURCES_IN_PROMPT]:
        annee = source.year or "s. d."
        resume = (source.venue or "")[:ABSTRACT_EXCERPT]
        lignes.append(f"- [{annee}] {source.title}" + (f" — {resume}" if resume else ""))
    if len(sources) > MAX_SOURCES_IN_PROMPT:
        lignes.append(f"- (… {len(sources) - MAX_SOURCES_IN_PROMPT} autres sources)")
    return "\n".join(lignes)


def build_user_message(
    project: Project,
    approved_sources: list[SourceDocument],
    user_problematique: str | None,
    target_words: int,
    correction: str | None = None,
) -> str:
    """Message utilisateur. Toutes les données variables sont ici (ADR-003)."""
    consigne_problematique = (
        f"Problématique imposée, à reprendre TELLE QUELLE dans le champ "
        f"problematique, sans reformulation :\n{user_problematique}"
        if user_problematique
        else "Aucune problématique fournie : formule-la à partir du sujet."
    )

    message = f"""Sujet : {project.subject}
Discipline : {project.discipline or "non précisée"}
Langue : {project.language}
Niveau : {project.academic_level}
Longueur cible totale : {target_words} mots

{consigne_problematique}

Sources approuvées (titres et résumés seulement) :
{_describe_sources(approved_sources)}

Produis un plan de recherche au format JSON strict suivant :

{SCHEMA_EXAMPLE}

Contraintes de structure : 4 à 12 chapitres racine ; profondeur de 3 à 5
niveaux ; tout nœud non terminal a au moins 2 enfants ; titres uniques entre
frères ; la somme des target_words des sections TERMINALES est comprise entre
{int(target_words * 0.7)} et {int(target_words * 1.3)}."""

    if correction:
        # Le message de correction est ajouté au message utilisateur, jamais
        # au prompt système : celui-ci doit rester stable octet pour octet
        # d'un essai à l'autre (ADR-003, prefix caching).
        message += (
            f"\n\nTa réponse précédente a été rejetée : {correction}\n"
            "Corrige et renvoie UNIQUEMENT le JSON complet."
        )
    return message


class PlanAgent:
    """Agent conforme au Protocol `Agent` de US-201."""

    name = AgentName.PLAN
    output_model = PlanTree

    def __init__(self, manager: LLMManager) -> None:
        self._manager = manager

    def build_user_message(self, state: GraphState, ctx: dict) -> str:
        return build_user_message(
            project=ctx["project"],
            approved_sources=ctx.get("sources", []),
            user_problematique=ctx.get("user_problematique"),
            target_words=ctx["target_words"],
            correction=state.get("last_error"),
        )

    async def run(self, state: GraphState, ctx: dict) -> str:
        """Texte brut du modèle. Aucune validation ici (US-201, point 3)."""
        message = self.build_user_message(state, ctx)
        resultat = await self._manager.generate_for_agent(
            AgentName.PLAN, message, max_tokens=ctx.get("max_tokens", 4096)
        )
        logger.debug("Plan généré : %s tokens de sortie", resultat.completion_tokens)
        return resultat.text


async def generate_plan(
    manager: LLMManager,
    project: Project,
    approved_sources: list[SourceDocument],
    user_problematique: str | None,
    target_words: int,
) -> PlanTree:
    """Génère et valide un plan. Un seul essai — la boucle appartient au graphe.

    Séparer l'appel de la boucle de reprise est ce qui rend les deux
    testables : le circuit breaker de US-202 s'exerce sur des sorties
    injectées, sans jamais solliciter un modèle.
    """
    from app.agents.guardrails import validate_output

    agent = PlanAgent(manager)
    state: GraphState = {  # type: ignore[typeddict-item]
        "project_id": project.id,
        "last_error": None,
        "retry_count": 0,
        "payload_json": {},
    }
    ctx = {
        "project": project,
        "sources": approved_sources,
        "user_problematique": user_problematique,
        "target_words": target_words,
    }
    brut = await agent.run(state, ctx)
    resultat = await validate_output(brut, PlanTree)
    if not resultat.ok:
        raise ValueError(resultat.error_message)

    plan: PlanTree = resultat.parsed  # type: ignore[assignment]
    plan.check_word_budget(target_words)
    if user_problematique:
        # Ceinture et bretelles : le prompt le demande, le contrôle l'impose.
        plan = plan.model_copy(update={"problematique": user_problematique})
    return plan
