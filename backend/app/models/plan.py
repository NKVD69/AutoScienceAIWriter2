"""Plan de recherche — US-PLAN-001, contrat `Plan` et `PlanNode`.

**Les validateurs ne sont pas des contraintes de saisie, ce sont des règles
de structuration doctorale.** Un plan à une seule sous-section n'est pas un
plan « un peu faible » : c'est un chapitre mal découpé, et le rédacteur
produira dessus une section qui n'a pas de place. Les rejeter au guardrail
coûte un essai de génération ; les accepter coûte la structure du mémoire.

**Aucune citation dans un plan.** Le plan est une structure, pas un texte
sourcé. Une clé de citation qui y apparaîtrait viendrait forcément du modèle
et non des sources approuvées — c'est la définition d'une référence inventée.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

MIN_DEPTH = 3
MAX_DEPTH = 5
MIN_ROOT_NODES = 4
MAX_ROOT_NODES = 12
MIN_CHILDREN = 2
WORD_BUDGET_LOW = 0.7
WORD_BUDGET_HIGH = 1.3

# Clés de citation Quarto et LaTeX. Repéré syntaxiquement, donc fiable :
# le contrôle ne dépend pas du jugement d'un modèle (§5.5).
CITATION_PATTERNS = (
    re.compile(r"\[@[A-Za-z0-9_:.-]+\]"),
    re.compile(r"(?<![A-Za-z0-9])@[A-Za-z][A-Za-z0-9_:.-]{2,}"),
    re.compile(r"\\cite[tp]?\{"),
)


class PlanStatus(StrEnum):
    DRAFT = "DRAFT"
    REVIEW = "REVIEW"
    VALIDATED = "VALIDATED"


class SectionStatus(StrEnum):
    NONE = "NONE"
    DRAFTING = "DRAFTING"
    REVIEWING = "REVIEWING"
    VALIDATED = "VALIDATED"
    # Le nœud portant cette section a été supprimé. La section survit :
    # supprimer silencieusement un texte rédigé serait détruire du travail.
    ORPHANED = "ORPHANED"


def contains_citation(texte: str) -> str | None:
    """Première clé de citation trouvée, ou None."""
    for motif in CITATION_PATTERNS:
        trouve = motif.search(texte)
        if trouve:
            return trouve.group()
    return None


class PlanNode(BaseModel):
    """Nœud de l'arbre. Récursif : `model_rebuild()` est appelé plus bas."""

    title: str = Field(min_length=3, max_length=200)
    objective: str = Field(min_length=10)
    target_words: int = Field(ge=150, le=20_000)
    children: list[PlanNode] = []

    @model_validator(mode="after")
    def _structure(self) -> PlanNode:
        if self.children and len(self.children) < MIN_CHILDREN:
            raise ValueError(
                f"« {self.title} » n'a qu'une sous-section. Un nœud non terminal "
                f"en compte au moins {MIN_CHILDREN} : une sous-section unique est "
                "un défaut de découpage, pas une section."
            )
        titres = [c.title.strip().lower() for c in self.children]
        if len(titres) != len(set(titres)):
            raise ValueError(f"« {self.title} » a des sous-sections de titres identiques.")
        for champ, valeur in (("title", self.title), ("objective", self.objective)):
            citation = contains_citation(valeur)
            if citation:
                raise ValueError(
                    f"Citation « {citation} » dans le {champ} de « {self.title} ». "
                    "Un plan est une structure, pas un texte sourcé : aucune "
                    "référence n'y est admise."
                )
        return self

    def depth(self) -> int:
        return 1 if not self.children else 1 + max(c.depth() for c in self.children)

    def leaves(self) -> list[PlanNode]:
        if not self.children:
            return [self]
        return [feuille for enfant in self.children for feuille in enfant.leaves()]


PlanNode.model_rebuild()


class PlanTree(BaseModel):
    """Sortie attendue de l'agent de plan."""

    problematique: str = Field(min_length=40, max_length=1500)
    research_questions: list[str] = Field(min_length=1, max_length=6)
    methodology_note: str
    nodes: list[PlanNode] = Field(min_length=MIN_ROOT_NODES, max_length=MAX_ROOT_NODES)

    @model_validator(mode="after")
    def _structure(self) -> PlanTree:
        profondeur = max(n.depth() for n in self.nodes)
        if not MIN_DEPTH <= profondeur <= MAX_DEPTH:
            raise ValueError(
                f"Profondeur du plan : {profondeur}. Un mémoire se structure sur "
                f"{MIN_DEPTH} à {MAX_DEPTH} niveaux — moins est trop grossier pour "
                "guider la rédaction, plus devient impraticable à relire."
            )

        titres = [n.title.strip().lower() for n in self.nodes]
        if len(titres) != len(set(titres)):
            raise ValueError("Deux chapitres racine portent le même titre.")

        for champ, valeur in (
            ("problematique", self.problematique),
            ("methodology_note", self.methodology_note),
        ):
            citation = contains_citation(valeur)
            if citation:
                raise ValueError(f"Citation « {citation} » dans {champ}. Le plan ne cite pas.")
        return self

    def total_target_words(self) -> int:
        """Somme des feuilles. Les nœuds intermédiaires ne comptent pas :
        leur budget est celui de leurs enfants, l'additionner le doublerait."""
        return sum(feuille.target_words for noeud in self.nodes for feuille in noeud.leaves())

    def check_word_budget(self, target_words: int) -> None:
        """Contrôle le budget contre la cible du projet.

        Séparé des validateurs : la cible appartient au projet, pas au plan,
        et un modèle Pydantic ne doit pas dépendre d'un contexte extérieur.
        """
        total = self.total_target_words()
        bas, haut = int(target_words * WORD_BUDGET_LOW), int(target_words * WORD_BUDGET_HIGH)
        if not bas <= total <= haut:
            raise ValueError(
                f"Budget de mots : {total} pour une cible de {target_words} "
                f"(fourchette admise de {bas} a {haut}). Ajuste les target_words des "
                "sections terminales."
            )


class PlanNodeOut(BaseModel):
    """Nœud tel qu'exposé par l'API — schéma `PlanNode` du contrat."""

    id: int
    parent_id: int | None = None
    title: str
    objective: str
    target_words: int
    level: int
    ordinal: int
    section_status: SectionStatus | None = None
    children: list[PlanNodeOut] = []


PlanNodeOut.model_rebuild()


class PlanOut(BaseModel):
    """Plan tel qu'exposé par l'API — schéma `Plan` du contrat."""

    id: int
    version: int
    status: PlanStatus
    problematique: str
    research_questions: list[str] = []
    methodology_note: str = ""
    nodes: list[PlanNodeOut] = []
    validated_at: str | None = None


class PlanNodeUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=200)
    objective: str | None = None
    target_words: int | None = Field(default=None, ge=150, le=20_000)


class PlanNodeCreate(BaseModel):
    parent_id: int | None = None
    title: str = Field(min_length=3, max_length=200)
    objective: str = Field(min_length=10)
    target_words: int = Field(ge=150, le=20_000)
    ordinal: int | None = None


class ReorderItem(BaseModel):
    node_id: int
    parent_id: int | None = None
    ordinal: int


class ReorderRequest(BaseModel):
    items: list[ReorderItem] = Field(min_length=1)


class PlanVersion(BaseModel):
    version: int
    status: PlanStatus
    created_at: str
    node_count: int
