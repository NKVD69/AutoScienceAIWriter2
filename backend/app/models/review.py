"""Rapport de relecture — US-302, spécifications §5.4.

**La relecture est un conseil, pas une autorisation.** Elle n'a jamais le
pouvoir de valider une section : seule la porte humaine le fait (ADR-004). Un
score de 95 ne franchit rien. C'est la différence de nature avec le garde-fou
de US-301, et elle est portée jusque dans les types.

- le **garde-fou** contrôle la forme et la véracité *vérifiable* — clé de
  citation inconnue, chiffre non rattaché, DOI hors base. Syntaxique, fiable,
  bloquant.
- la **relecture** apprécie le *fond* — cohérence, argumentation, style,
  complétude. Jugement d'un modèle, faillible, jamais bloquant.

Un seul contrôle de véracité s'applique à la relecture, et il est syntaxique :
**tout extrait cité doit être une sous-chaîne exacte du texte relu.** Un
relecteur qui cite un passage inexistant a halluciné ; sa sortie est rejetée
comme n'importe quelle sortie mal formée.

**Le score global n'est jamais repris du modèle.** Le modèle propose des
scores par catégorie ; le serveur recompose la note d'ensemble à partir des
poids de configuration. Laisser le modèle fixer sa propre note globale
reviendrait à lui laisser décider de ce qui compte.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Severity = Literal["blocking", "major", "minor", "suggestion"]
Category = Literal["coherence", "argumentation", "sourcing", "style", "structure", "completeness"]
Verdict = Literal["ready", "needs_work", "insufficient"]

CATEGORIES: tuple[Category, ...] = (
    "coherence",
    "argumentation",
    "sourcing",
    "style",
    "structure",
    "completeness",
)

EXCERPT_MIN = 20
EXCERPT_MAX = 300

# Sévérités que la correction automatique prend en charge. Les mineurs et les
# suggestions relèvent de l'auteur : les faire corriger par un 7B risquerait de
# dégrader un texte déjà acceptable pour un gain cosmétique.
CORRECTABLE: frozenset[Severity] = frozenset({"blocking", "major"})


class Finding(BaseModel):
    """Un constat localisé. Jamais un avis global : celui-ci se lit dans le score."""

    severity: Severity
    category: Category
    excerpt: str = Field(min_length=EXCERPT_MIN, max_length=EXCERPT_MAX)
    message: str = Field(min_length=1)
    suggestion: str | None = None


class ReviewReport(BaseModel):
    """Sortie attendue de l'agent relecteur, avant consolidation serveur.

    `overall_score` est présent dans le schéma pour que le modèle propose une
    note, mais il est IGNORÉ à la persistance : le serveur le recalcule (voir
    `weighted_overall`). Le garder ici sert seulement à ne pas faire échouer la
    validation d'une sortie qui le fournit.
    """

    findings: list[Finding] = []
    scores: dict[str, float]
    overall_score: float = Field(ge=0, le=100)
    verdict: Verdict

    @model_validator(mode="after")
    def _coherence(self) -> ReviewReport:
        manquantes = set(CATEGORIES) - set(self.scores)
        superflues = set(self.scores) - set(CATEGORIES)
        if manquantes or superflues:
            raise ValueError(
                f"scores doit porter exactement les six catégories. Manquantes : "
                f"{sorted(manquantes)} ; en trop : {sorted(superflues)}."
            )
        for categorie, valeur in self.scores.items():
            if not 0 <= valeur <= 100:
                raise ValueError(f"Score de « {categorie} » hors bornes 0-100 : {valeur}.")

        # Un verdict « ready » avec un constat bloquant est contradictoire : le
        # relecteur dit à la fois « prêt » et « il reste un défaut bloquant ».
        if self.verdict == "ready" and any(f.severity == "blocking" for f in self.findings):
            raise ValueError(
                "Verdict « ready » incohérent : au moins un constat est de sévérité "
                "« blocking ». Un texte porteur d'un défaut bloquant n'est pas prêt."
            )
        return self

    def check_excerpts_present(self, content_qmd: str) -> None:
        """Tout extrait cité doit exister TEL QUEL dans le texte relu.

        Séparé des validateurs Pydantic : le contrôle dépend d'un contexte
        extérieur — le contenu de la section — qu'un modèle ne doit pas
        embarquer. C'est l'unique contrôle de véracité de la relecture.
        """
        for finding in self.findings:
            if finding.excerpt not in content_qmd:
                raise ValueError(
                    f"Extrait introuvable dans le texte relu : « {finding.excerpt[:80]} ». "
                    "Un relecteur qui cite un passage inexistant a halluciné : cite un "
                    "extrait présent mot pour mot, ou retire le constat."
                )

    def blocking_and_major(self) -> list[Finding]:
        """Constats que la correction automatique traite. Les autres sont à l'auteur."""
        return [f for f in self.findings if f.severity in CORRECTABLE]


class ReviewReportOut(BaseModel):
    """Rapport tel qu'exposé par l'API et persisté, note globale consolidée."""

    id: int | None = None
    draft_section_id: int
    section_version: int
    findings: list[Finding]
    scores: dict[str, float]
    overall_score: float
    verdict: Verdict
    auto_correct: bool = False
    created_at: str | None = None


def weighted_overall(scores: dict[str, float], weights: dict[str, float]) -> float:
    """Note globale, recomposée côté serveur à partir des poids de configuration.

    Jamais reprise de la sortie du modèle : il propose les scores par
    catégorie, il ne décide pas de ce qui pèse. Arrondie à un dixième, elle
    reste comparable d'une version à l'autre sans faux écarts de flottant.
    """
    return round(sum(scores[c] * weights[c] for c in CATEGORIES), 1)
