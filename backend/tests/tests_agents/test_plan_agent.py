"""US-PLAN-001 — validateurs, agent, guardrail. Aucun moteur reel requis."""

from __future__ import annotations

import json

import pytest

from app.agents.breaker import register_guardrail_failure
from app.agents.graph import PLAN_SUBGRAPH, plan_guardrail_target
from app.agents.guardrails import validate_output
from app.agents.plan_agent import PlanAgent, build_user_message, generate_plan
from app.agents.state import TRANSITIONS, WorkflowState, initial_state
from app.llm.base import LLMResult
from app.llm.manager import LLMManager
from app.llm.prompts.registry import PLAN_PROMPT, AgentName
from app.models.plan import PlanTree
from app.models.project import AcademicLevel, Project
from app.models.source import SourceDocument, SourceKind

CIBLE = 60_000


def noeud(titre: str, mots: int = 5000, enfants: list | None = None) -> dict:
    """Nœud du fixture. `target_words` >= 150 sur TOUT nœud, y compris
    intermediaire : le modele l'exige, et un fixture qui l'ignorerait ferait
    echouer les tests pour une raison sans rapport avec ce qu'ils visent."""
    enfants = enfants or []
    # Un nœud intermediaire porte la somme de ses enfants, PLAFONNEE a
    # 20 000 : le modele borne `target_words` sur tout nœud, feuille ou non.
    # Seules les feuilles entrent dans le budget, la valeur d'un
    # intermediaire n'est donc qu'indicative.
    if enfants:
        mots = min(20_000, sum(e["target_words"] for e in enfants))
    return {
        "title": titre,
        "objective": f"Etablir ce que {titre} demontre, de facon verifiable.",
        "target_words": max(150, mots),
        "children": enfants,
    }


def feuilles(prefixe: str, n: int, mots: int) -> list[dict]:
    return [noeud(f"{prefixe} {i + 1}", mots) for i in range(n)]


# 16 feuilles x 3750 = 60 000 mots, soit exactement la cible du projet de
# test. Un defaut hors fourchette ferait echouer la moitie des tests pour
# une raison sans rapport avec ce qu'ils visent.
def arbre_valide(mots_par_feuille: int = 3750) -> dict:
    """4 chapitres x 2 sections x 2 sous-sections = 16 feuilles, profondeur 3."""
    chapitres = []
    for c in range(4):
        sections = [
            noeud(
                f"Section {c}.{s}",
                enfants=feuilles(f"Sous-section {c}.{s}", 2, mots_par_feuille),
            )
            for s in range(2)
        ]
        chapitres.append(noeud(f"Chapitre {c + 1}", enfants=sections))
    return {
        "problematique": "Comment les microplastiques alterent-ils la fonction renale "
        "des mammiferes marins en milieu tempere ?",
        "research_questions": ["Quels mecanismes de transport ?", "Quels marqueurs ?"],
        "methodology_note": "Le perimetre temporel demande un arbitrage du directeur.",
        "nodes": chapitres,
    }


def arbre_profond(niveaux: int, mots: int = 500) -> dict:
    """Chapitre d'une profondeur donnee, chaque nœud ayant deux enfants."""

    def batir(niveau: int, prefixe: str) -> dict:
        if niveau >= niveaux:
            return noeud(f"{prefixe} feuille", mots)
        return noeud(
            f"{prefixe} N{niveau}",
            enfants=[batir(niveau + 1, f"{prefixe}.{i}") for i in range(2)],
        )

    return batir(1, "C")


def projet() -> Project:
    return Project(
        id=1,
        name="These",
        subject="Impact des microplastiques sur la fonction renale des mammiferes marins",
        discipline="Ecotoxicologie",
        language="fr",
        academic_level=AcademicLevel.DOCTORAT,
        target_words=CIBLE,
        created_at="2026-01-01T00:00:00+00:00",
    )


class FakeLLMBackend:
    """Moteur alimente par des reponses preenregistrees."""

    name = "fake"

    def __init__(self, reponses: list[str]) -> None:
        self.reponses = list(reponses)
        self.messages: list[str] = []

    async def ensure_loaded(self) -> None: ...

    async def generate(self, system, user, **kwargs) -> LLMResult:
        self.messages.append(user)
        texte = self.reponses.pop(0) if len(self.reponses) > 1 else self.reponses[0]
        return LLMResult(text=texte, model="fake", backend="fake")

    async def stream(self, system, user, **kwargs):
        yield ""

    async def health(self):
        from app.llm.base import BackendHealth

        return BackendHealth(available=True, expected_model_present=True)


def manager_pour(*reponses: str) -> tuple[LLMManager, FakeLLMBackend]:
    backend = FakeLLMBackend(list(reponses))
    return LLMManager(backend), backend


# --- Validateurs de structure ---------------------------------------------


def test_valid_tree_is_accepted() -> None:
    arbre = PlanTree.model_validate(arbre_valide())
    assert len(arbre.nodes) == 4
    arbre.check_word_budget(CIBLE)


def test_plan_tree_rejects_depth_below_3() -> None:
    """Deux niveaux sont trop grossiers pour guider une redaction."""
    plat = {**arbre_valide(), "nodes": [noeud(f"Chapitre {i}", 15000) for i in range(4)]}
    with pytest.raises(ValueError, match="Profondeur"):
        PlanTree.model_validate(plat)


def test_plan_tree_rejects_depth_above_5() -> None:
    """Au-dela de cinq niveaux, un plan devient impraticable a relire."""
    trop_profond = {
        **arbre_valide(),
        "nodes": [arbre_profond(niveaux=6) for _ in range(1)]
        + [noeud(f"Chapitre {i}", enfants=feuilles(f"S{i}", 2, 500)) for i in range(3)],
    }
    trop_profond["nodes"][0]["title"] = "Chapitre profond"
    with pytest.raises(ValueError, match="Profondeur"):
        PlanTree.model_validate(trop_profond)


def test_plan_tree_rejects_single_child_node() -> None:
    """Une sous-section unique est un defaut de decoupage."""
    arbre = arbre_valide()
    arbre["nodes"][0]["children"] = [arbre["nodes"][0]["children"][0]]
    with pytest.raises(ValueError, match="sous-section"):
        PlanTree.model_validate(arbre)


def test_plan_tree_rejects_duplicate_sibling_titles() -> None:
    arbre = arbre_valide()
    arbre["nodes"][1]["title"] = arbre["nodes"][0]["title"]
    with pytest.raises(ValueError, match="même titre"):
        PlanTree.model_validate(arbre)


def test_plan_tree_rejects_too_few_root_nodes() -> None:
    arbre = arbre_valide()
    arbre["nodes"] = arbre["nodes"][:3]
    with pytest.raises(ValueError):
        PlanTree.model_validate(arbre)


def test_plan_tree_rejects_word_budget_out_of_range() -> None:
    trop_court = PlanTree.model_validate(arbre_valide(mots_par_feuille=200))
    with pytest.raises(ValueError, match="Budget de mots"):
        trop_court.check_word_budget(CIBLE)

    trop_long = PlanTree.model_validate(arbre_valide(mots_par_feuille=9000))
    with pytest.raises(ValueError, match="Budget de mots"):
        trop_long.check_word_budget(CIBLE)


def test_word_budget_counts_leaves_only() -> None:
    """Additionner les nœuds intermediaires doublerait le budget."""
    arbre = PlanTree.model_validate(arbre_valide(mots_par_feuille=3750))
    assert arbre.total_target_words() == 16 * 3750


# --- Interdiction de citer ------------------------------------------------


@pytest.mark.parametrize(
    "citation",
    ["[@dupont2021]", "@dupont2021", "\\citep{dupont}", "voir [@martin_2019_renal]"],
)
def test_agent_output_with_citation_key_rejected(citation: str) -> None:
    """Une citation dans un plan vient du modele, pas des sources : c'est
    la definition d'une reference inventee."""
    arbre = arbre_valide()
    arbre["nodes"][0]["objective"] = f"Etablir le mecanisme decrit dans {citation}."
    with pytest.raises(ValueError, match=r"[Cc]itation"):
        PlanTree.model_validate(arbre)


def test_citation_rejected_in_problematique() -> None:
    arbre = {**arbre_valide(), "problematique": "Question fondee sur [@dupont2021] et son corpus."}
    with pytest.raises(ValueError, match=r"[Cc]itation"):
        PlanTree.model_validate(arbre)


def test_ordinary_email_like_text_is_not_a_citation() -> None:
    """Le controle ne doit pas mordre sur du texte legitime."""
    arbre = arbre_valide()
    arbre["nodes"][0]["objective"] = "Etablir le role du transport a 25 @ 30 degres Celsius."
    PlanTree.model_validate(arbre)


# --- Message utilisateur --------------------------------------------------


def test_user_problematique_preserved_verbatim() -> None:
    """Reformuler remplacerait la question du doctorant par celle du modele."""
    question = "Dans quelle mesure la charge en microplastiques module-t-elle la clairance ?"
    message = build_user_message(projet(), [], question, CIBLE)
    assert question in message
    assert "TELLE QUELLE" in message


async def test_user_problematique_survives_a_model_that_reformulates() -> None:
    question = "Dans quelle mesure la charge en microplastiques module-t-elle la clairance ?"
    reformule = {**arbre_valide(), "problematique": "Le modele a reformule la question a sa guise."}
    manager, _ = manager_pour(json.dumps(reformule))

    plan = await generate_plan(manager, projet(), [], question, CIBLE)
    assert plan.problematique == question


def test_agent_receives_abstracts_not_full_text() -> None:
    """Le contexte fait 8192 tokens : y verser le texte integral le sature."""
    sources = [
        SourceDocument(
            id=i,
            kind=SourceKind.ARTICLE,
            title=f"Article {i}",
            year=2020 + i,
            venue="Un resume court de la source.",
            imported_at="2026-01-01T00:00:00+00:00",
        )
        for i in range(3)
    ]
    message = build_user_message(projet(), sources, None, CIBLE)

    assert "Article 0" in message and "2022" in message
    assert "titres et résumés seulement" in message
    assert len(message) < 6000, "le message ne doit pas enfler avec les sources"


def test_source_list_is_capped() -> None:
    sources = [
        SourceDocument(
            id=i,
            kind=SourceKind.ARTICLE,
            title=f"Article {i}",
            imported_at="2026-01-01T00:00:00+00:00",
        )
        for i in range(120)
    ]
    message = build_user_message(projet(), sources, None, CIBLE)
    assert "autres sources" in message
    assert "Article 119" not in message


def test_correction_goes_to_the_user_message_not_the_system_prompt() -> None:
    """Le prompt systeme doit rester stable octet pour octet (ADR-003)."""
    message = build_user_message(projet(), [], None, CIBLE, correction="champ « poids » invalide")
    assert "rejetée" in message
    assert "champ « poids » invalide" in message
    assert "{" not in PLAN_PROMPT and "rejetée" not in PLAN_PROMPT


def test_plan_prompt_has_no_interpolation() -> None:
    assert "{" not in PLAN_PROMPT
    assert "%s" not in PLAN_PROMPT
    for exigence in ("ne cites RIEN", "VÉRIFIABLE", "methodology_note", "TELLE QUELLE"):
        assert exigence in PLAN_PROMPT, f"le prompt doit poser : {exigence}"


# --- Agent et guardrail ---------------------------------------------------


async def test_agent_returns_raw_text_without_validating() -> None:
    """La validation appartient au guardrail (US-201, point 3)."""
    manager, _ = manager_pour("ceci n'est pas du JSON")
    agent = PlanAgent(manager)
    brut = await agent.run(initial_state(1), {"project": projet(), "target_words": CIBLE})
    assert brut == "ceci n'est pas du JSON"


async def test_generate_plan_rejects_invalid_output() -> None:
    manager, _ = manager_pour('{"problematique": "trop court"}')
    with pytest.raises(ValueError, match="contrainte"):
        await generate_plan(manager, projet(), [], None, CIBLE)


async def test_guardrail_retries_same_agent_with_field_path() -> None:
    """Le message de correction porte le chemin du champ fautif."""
    invalide = arbre_valide()
    invalide["nodes"][0]["children"] = [invalide["nodes"][0]["children"][0]]

    resultat = await validate_output(json.dumps(invalide), PlanTree)
    assert not resultat.ok
    assert "nodes" in resultat.error_path
    assert "sous-section" in resultat.error_message


async def test_guardrail_error_state_after_three_retries() -> None:
    etat = initial_state(1)
    for _ in range(2):
        assert not register_guardrail_failure(etat, "plan invalide").tripped
    decision = register_guardrail_failure(etat, "plan invalide")
    assert decision.tripped
    assert decision.next_state is WorkflowState.ERROR_STATE
    assert plan_guardrail_target(ok=False, breaker_tripped=True) is WorkflowState.ERROR_STATE


def test_guardrail_target_reruns_the_plan_agent() -> None:
    """Une erreur de format n'est pas un probleme de fond : jamais le relecteur."""
    assert plan_guardrail_target(ok=False, breaker_tripped=False) is WorkflowState.PLAN_DRAFTING
    assert plan_guardrail_target(ok=True, breaker_tripped=False) is WorkflowState.PLAN_REVIEW


def test_guardrail_never_targets_plan_validated() -> None:
    """La porte humaine reste entiere quelle que soit la qualite du plan."""
    for ok in (True, False):
        for tripped in (True, False):
            assert plan_guardrail_target(ok, tripped) is not WorkflowState.PLAN_VALIDATED


# --- Sous-graphe ----------------------------------------------------------


def test_plan_subgraph_adds_no_transition() -> None:
    """Un sous-graphe qui inventerait une arete contournerait le refus du
    graphe principal."""
    for depuis, cibles in PLAN_SUBGRAPH.items():
        assert cibles <= TRANSITIONS[depuis], f"{depuis} : arête absente de la table"


def test_agent_is_registered_under_plan_name() -> None:
    manager, _ = manager_pour("{}")
    assert PlanAgent(manager).name is AgentName.PLAN
    assert PlanAgent(manager).output_model is PlanTree
