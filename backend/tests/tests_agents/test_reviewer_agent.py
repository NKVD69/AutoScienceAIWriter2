"""US-302 — agent relecteur et schéma de rapport.

Le relecteur est exercé contre un backend factice alimenté de réponses
préenregistrées. Aucun moteur réel : un modèle de 31B produit du JSON conforme
la plupart du temps, ce qui rend ses écarts rares et donc impossibles à
provoquer autrement qu'en les injectant.

Deux natures de contrôle cohabitent, et le découpage des tests les sépare :
le CONTRAT de l'agent — il rend du texte brut, il ne voit pas le processus —
et le SCHÉMA de son rapport — six catégories, extrait recopié, note recomposée
côté serveur.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.agents.reviewer_agent import MAX_OUTPUT_TOKENS, ReviewerAgent, build_user_message
from app.agents.state import initial_state
from app.core.config import Settings, get_settings
from app.db.vector import ChunkHit
from app.llm.manager import LLMManager
from app.llm.prompts.registry import AgentName, get_system_prompt
from app.models.review import CATEGORIES, CORRECTABLE, Finding, ReviewReport, weighted_overall
from tests.tests_agents.test_writer_agent import FakeBackend


class ReviewerBackend(FakeBackend):
    """`FakeBackend` qui retient aussi température et budget de sortie.

    Le relecteur n'appelle QUE `generate` (jamais `stream`) : la base ne
    conserve ces deux réglages que sur le flux, ce que cette sous-classe complète.
    """

    async def generate(self, system, user, *, max_tokens=1024, temperature=0.2, stop=None):
        self.temperatures.append(temperature)
        self.max_tokens.append(max_tokens)
        return await super().generate(
            system, user, max_tokens=max_tokens, temperature=temperature, stop=stop
        )


def finding(
    *,
    severity: str = "major",
    category: str = "argumentation",
    excerpt: str = "un extrait suffisamment long pour tenir la contrainte",
    message: str = "Le raisonnement saute une étape.",
    suggestion: str | None = None,
) -> dict:
    """Constat au format brut du modèle, tous champs obligatoires présents."""
    corps: dict = {
        "severity": severity,
        "category": category,
        "excerpt": excerpt,
        "message": message,
    }
    if suggestion is not None:
        corps["suggestion"] = suggestion
    return corps


def rapport(
    *,
    verdict: str = "ready",
    scores: dict[str, float] | None = None,
    findings: list[dict] | None = None,
    overall: float = 50.0,
) -> str:
    """Sortie JSON d'un relecteur, réutilisée par la boucle et l'API."""
    return json.dumps(
        {
            "findings": findings if findings is not None else [],
            "scores": scores if scores is not None else {c: 80.0 for c in CATEGORIES},
            "overall_score": overall,
            "verdict": verdict,
        },
        ensure_ascii=False,
    )


def _chunk(titre: str = "Une source citée") -> ChunkHit:
    return ChunkHit(
        chunk_id=1,
        source_id=1,
        text="Un extrait de la source, montré au relecteur pour cadrer son jugement.",
        page_start=3,
        page_end=4,
        distance=0.1,
        source_title=titre,
        source_year=2021,
        source_doi=None,
        is_preprint=False,
    )


CTX = {
    "content_qmd": "La filtration glomérulaire décroît après exposition prolongée [@src1].",
    "node_objective": "Établir le lien entre exposition prolongée et filtration.",
    "target_words": 1000,
    "chunks": [_chunk()],
}


# --- Contrat de l'agent ---------------------------------------------------


def test_reviewer_declares_its_name_and_output_model() -> None:
    agent = ReviewerAgent(LLMManager(ReviewerBackend([rapport()])))
    assert agent.name is AgentName.REVIEWER
    assert agent.output_model is ReviewReport


async def test_reviewer_returns_raw_text_without_validating() -> None:
    """L'agent ne valide pas sa propre sortie : c'est ce qui rend le guardrail
    exerçable seul, sur des rapports fautifs qu'un 7B produit rarement."""
    backend = ReviewerBackend(["ceci n'est pas du JSON"])
    agent = ReviewerAgent(LLMManager(backend))

    brut = await agent.run(initial_state(1), dict(CTX))

    assert brut == "ceci n'est pas du JSON"
    assert isinstance(brut, str)


async def test_reviewer_uses_the_reviewer_system_prompt() -> None:
    backend = ReviewerBackend([rapport()])
    await ReviewerAgent(LLMManager(backend)).run(initial_state(1), dict(CTX))
    assert backend.systems == [get_system_prompt(AgentName.REVIEWER)]


async def test_reviewer_temperature_comes_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Basse et configurable : deux relectures du même texte ne devraient pas
    rendre deux verdicts."""
    monkeypatch.setattr(get_settings(), "reviewer_temperature", 0.07)
    backend = ReviewerBackend([rapport()])
    await ReviewerAgent(LLMManager(backend)).run(initial_state(1), dict(CTX))
    assert backend.temperatures == [0.07]


def test_default_reviewer_temperature_is_low_for_reproducibility() -> None:
    settings = get_settings()
    assert settings.reviewer_temperature == 0.1
    assert settings.reviewer_temperature < settings.writer_temperature


async def test_reviewer_output_budget_is_transmitted() -> None:
    backend = ReviewerBackend([rapport()])
    await ReviewerAgent(LLMManager(backend)).run(initial_state(1), dict(CTX))
    assert backend.max_tokens == [MAX_OUTPUT_TOKENS]


def test_reviewer_message_carries_the_text_objective_target_and_sources() -> None:
    message = build_user_message(
        content_qmd=CTX["content_qmd"],
        node_objective=CTX["node_objective"],
        target_words=CTX["target_words"],
        chunks=CTX["chunks"],
    )
    assert CTX["content_qmd"] in message
    assert CTX["node_objective"] in message
    assert "1000" in message
    assert "Une source citée" in message


def test_reviewer_message_excludes_the_generation_history() -> None:
    """Il évalue le TEXTE, pas le PROCESSUS. Ni la correction en cours, ni la
    meilleure version retenue, ni le compteur d'essais ne transparaissent dans
    son invite : le lui montrer biaiserait son jugement — une section vaut ce
    qu'elle vaut, pas ce qu'elle a coûté."""
    etat = initial_state(1)
    etat["retry_count"] = 2
    etat["last_error"] = "SENTINELLE_CORRECTION_a1b2c3"
    etat["payload_json"]["best_version"] = {"content": "SENTINELLE_VERSION_d4e5f6"}

    message = ReviewerAgent(LLMManager(ReviewerBackend([rapport()]))).build_user_message(
        etat, dict(CTX)
    )

    assert "SENTINELLE_CORRECTION_a1b2c3" not in message
    assert "SENTINELLE_VERSION_d4e5f6" not in message
    # Contrôle positif : le texte à juger, lui, est bien là.
    assert CTX["content_qmd"] in message


# --- Schéma du rapport ----------------------------------------------------


def _report(**kw) -> ReviewReport:
    base: dict = {
        "findings": [],
        "scores": {c: 70.0 for c in CATEGORIES},
        "overall_score": 0.0,
        "verdict": "needs_work",
    }
    base.update(kw)
    return ReviewReport.model_validate(base)


def test_scores_must_carry_exactly_the_six_categories() -> None:
    manquante = {c: 70.0 for c in CATEGORIES if c != "style"}
    with pytest.raises(ValidationError):
        _report(scores=manquante)

    superflue = {**{c: 70.0 for c in CATEGORIES}, "originality": 90.0}
    with pytest.raises(ValidationError):
        _report(scores=superflue)

    # Exactement les six : accepté.
    assert _report().verdict == "needs_work"


def test_ready_verdict_with_a_blocking_finding_is_rejected() -> None:
    """« prêt » et « défaut bloquant » sont contradictoires : un texte porteur
    d'un blocage n'est pas prêt."""
    bloquant = [finding(severity="blocking", category="sourcing", message="Non sourcé.")]
    with pytest.raises(ValidationError):
        _report(verdict="ready", findings=bloquant)

    # Le même constat sous « needs_work » est cohérent.
    assert _report(verdict="needs_work", findings=bloquant).verdict == "needs_work"


def test_finding_excerpt_length_is_bounded() -> None:
    with pytest.raises(ValidationError):
        Finding(severity="minor", category="style", excerpt="trop court", message="x")
    with pytest.raises(ValidationError):
        Finding(severity="minor", category="style", excerpt="x" * 301, message="y")
    ok = Finding(severity="minor", category="style", excerpt="x" * 30, message="y")
    assert ok.severity == "minor"


def test_excerpt_must_be_an_exact_substring_of_the_reviewed_text() -> None:
    contenu = "La filtration glomérulaire décroît après exposition prolongée au polluant."
    present = finding(excerpt="La filtration glomérulaire décroît")
    _report(findings=[present]).check_excerpts_present(contenu)  # ne lève pas


def test_a_hallucinated_excerpt_is_rejected() -> None:
    """Un relecteur qui cite un passage inexistant a halluciné : sa sortie est
    rejetée comme n'importe quelle sortie mal formée."""
    contenu = "La filtration glomérulaire décroît après exposition prolongée au polluant."
    invente = finding(excerpt="un passage que le texte ne contient pas du tout")
    with pytest.raises(ValueError, match="introuvable"):
        _report(findings=[invente]).check_excerpts_present(contenu)


def test_overall_score_is_recomputed_from_the_weights() -> None:
    poids = get_settings().review_weights
    # Tous à 80 : la somme pondérée vaut 80, quels que soient les poids (Σ = 1).
    assert weighted_overall({c: 80.0 for c in CATEGORIES}, poids) == 80.0
    # Seul le sourçage à 100 : la note vaut son poids fois 100.
    isole = {c: 0.0 for c in CATEGORIES}
    isole["sourcing"] = 100.0
    assert weighted_overall(isole, poids) == round(poids["sourcing"] * 100, 1)


def test_the_model_supplied_overall_score_is_ignored() -> None:
    """Le modèle propose une note ; le serveur la recompose. Le laisser fixer sa
    propre note reviendrait à lui laisser décider de ce qui compte."""
    poids = get_settings().review_weights
    rap = _report(scores={c: 80.0 for c in CATEGORIES}, overall_score=12.3)
    # La note serveur ne dépend QUE des scores et des poids, pas du champ fourni.
    assert weighted_overall(rap.scores, poids) == 80.0
    assert rap.overall_score == 12.3  # conservé au schéma, mais jamais consulté en aval


def test_correction_targets_only_blocking_and_major_findings() -> None:
    """Les mineurs et les suggestions relèvent de l'auteur : les faire corriger
    par un 7B risquerait de dégrader un texte déjà acceptable."""
    findings = [
        finding(severity="blocking", category="sourcing", message="a"),
        finding(severity="major", category="coherence", message="b"),
        finding(severity="minor", category="style", message="c"),
        finding(severity="suggestion", category="style", message="d"),
    ]
    retenus = _report(verdict="needs_work", findings=findings).blocking_and_major()
    assert {f.severity for f in retenus} == {"blocking", "major"}
    assert CORRECTABLE == frozenset({"blocking", "major"})


def test_review_weights_are_validated_at_startup() -> None:
    """Un poids mal réglé produirait un score faux à chaque relecture, sans
    jamais lever : échouer au démarrage le rend visible."""
    get_settings().assert_review_weights()  # défaut : ne lève pas

    with pytest.raises(ValueError, match=r"doit valoir 1\.0"):
        Settings(
            review_weights={
                "sourcing": 0.30,
                "coherence": 0.25,
                "argumentation": 0.20,
                "completeness": 0.15,
                "structure": 0.05,
                "style": 0.15,  # somme = 1.10
            }
        ).assert_review_weights()

    with pytest.raises(ValueError, match="six catégories"):
        Settings(review_weights={"sourcing": 1.0}).assert_review_weights()
