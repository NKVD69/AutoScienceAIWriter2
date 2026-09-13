"""Section rédigée — US-301, contrat `DraftSection` et `Citation`.

**Chaque affirmation est qualifiée.** Un texte académique mêle des faits
établis par une source, des synthèses de l'auteur, des hypothèses et des
limites. Les confondre est ce qui rend un mémoire attaquable : le lecteur ne
peut plus distinguer ce qui est démontré de ce qui est avancé.

Deux règles portent cette distinction, et elles sont symétriques.

- Une affirmation **sourcée** sans clé ni chunk est une affirmation qui se
  présente comme établie sans l'être.
- Une **hypothèse** portant une citation est une contradiction : elle
  emprunte l'autorité d'une source pour ce que l'auteur avance lui-même.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# Le contenu canonique est du Quarto (ADR-006). MyST y ressemble assez pour
# passer inaperçu à la relecture, et assez peu pour casser la compilation.
MYST_PATTERNS = (
    re.compile(r":::\{[a-zA-Z]+\}"),
    re.compile(r"\{ref\}`"),
    re.compile(r"\{numref\}`"),
    re.compile(r"\{cite\}`"),
)

WORD_COUNT_LOW = 0.6
WORD_COUNT_HIGH = 1.4
# Un mot : une suite de caractères sans blanc. Le balisage Quarto — dièses de
# titre, clés de citation — compte comme des mots : l'écart reste négligeable
# devant une fourchette de plus ou moins 40 %.
WORD = re.compile(r"\S+")


class ClaimKind(StrEnum):
    SOURCED = "sourced"
    SYNTHESIS = "synthesis"
    HYPOTHESIS = "hypothesis"
    LIMITATION = "limitation"


class SectionStatus(StrEnum):
    DRAFTING = "DRAFTING"
    GUARDRAIL = "GUARDRAIL"
    REVIEWING = "REVIEWING"
    CORRECTING = "CORRECTING"
    VALIDATED = "VALIDATED"
    ORPHANED = "ORPHANED"


def find_myst(texte: str) -> str | None:
    """Première syntaxe MyST trouvée, ou None."""
    for motif in MYST_PATTERNS:
        trouve = motif.search(texte)
        if trouve:
            return trouve.group()
    return None


class Claim(BaseModel):
    """Une affirmation du texte, avec son statut épistémique."""

    text: str = Field(min_length=1)
    kind: Literal["sourced", "synthesis", "hypothesis", "limitation"]
    citation_keys: list[str] = []
    chunk_ids: list[int] = []

    @model_validator(mode="after")
    def _coherence(self) -> Claim:
        if self.kind == ClaimKind.SOURCED:
            if not self.citation_keys:
                raise ValueError(
                    f"Affirmation présentée comme sourcée sans clé de citation : "
                    f"« {self.text[:120]} ». Rattache-la à une source fournie, ou "
                    "requalifie-la en synthesis, hypothesis ou limitation."
                )
            if not self.chunk_ids:
                raise ValueError(
                    f"Affirmation sourcée sans extrait d'origine : "
                    f"« {self.text[:120]} ». Indique le ou les chunk_id qui "
                    "l'établissent : sans eux, la citation n'est pas vérifiable."
                )
        elif self.citation_keys:
            raise ValueError(
                f"Une affirmation de type « {self.kind} » ne cite pas : "
                f"« {self.text[:120]} » porte {self.citation_keys}. Une hypothèse "
                "ou une synthèse qui s'appuie sur une source emprunte une "
                "autorité qui n'est pas la sienne — soit elle est sourcée, soit "
                "elle ne cite pas."
            )
        return self


class SectionDraft(BaseModel):
    """Sortie attendue de l'agent rédacteur."""

    content_qmd: str = Field(min_length=1)
    claims: list[Claim] = Field(min_length=1)
    word_count: int = Field(ge=1)

    @model_validator(mode="after")
    def _format(self) -> SectionDraft:
        myst = find_myst(self.content_qmd)
        if myst:
            raise ValueError(
                f"Syntaxe MyST « {myst} » dans le contenu. Le format canonique "
                "est Quarto (ADR-006) : utilise ::: {.callout-note}, @fig-, "
                "@tbl-, @sec- selon le cas."
            )
        return self

    def measured_word_count(self) -> int:
        """Mots réellement présents dans le contenu."""
        return len(WORD.findall(self.content_qmd))

    def check_word_count(self, target_words: int) -> None:
        """Contrôle la longueur MESURÉE contre la cible du nœud de plan.

        Le `word_count` déclaré par le modèle n'est pas consulté. Un contrôle
        qui le croirait sur parole laisserait passer cinq mots annoncés comme
        1 500 — c'est-à-dire un garde-fou qui demande au modèle de juger sa
        propre production, ce que §5.5 exclut (constat de revue).

        Séparé des validateurs : la cible appartient au plan, et un modèle
        Pydantic ne doit pas dépendre d'un contexte extérieur.
        """
        mesure = self.measured_word_count()
        bas = int(target_words * WORD_COUNT_LOW)
        haut = int(target_words * WORD_COUNT_HIGH)
        if not bas <= mesure <= haut:
            raise ValueError(
                f"Longueur : {mesure} mots pour une cible de "
                f"{target_words} (fourchette admise de {bas} a {haut}). "
                "Développe ou resserre la section."
            )

    def sourced_claims(self) -> list[Claim]:
        return [c for c in self.claims if c.kind == ClaimKind.SOURCED]


class CitationOut(BaseModel):
    """Citation telle qu'exposée par l'API — schéma `Citation` du contrat."""

    id: int
    source_id: int
    chunk_id: int | None = None
    bibtex_key: str
    locator: str | None = None
    verified: bool


class DraftSectionOut(BaseModel):
    """Section telle qu'exposée par l'API — schéma `DraftSection`."""

    id: int
    plan_node_id: int | None
    content_qmd: str
    status: SectionStatus
    quality_score: float | None = None
    version: int
    citations: list[CitationOut] = []
    generated_at: str | None = None
    validated_at: str | None = None


class SectionUpdate(BaseModel):
    """Édition manuelle par l'auteur. Le texte reste le sien.

    Le seul contrôle porte sur le format : du MyST collé ici compilerait mal
    à l'export, des semaines plus tard, sans que rien ne relie l'échec à
    cette édition. Le contenu, lui, n'est pas jugé — c'est l'auteur qui écrit.
    """

    content_qmd: str = Field(min_length=1)

    @model_validator(mode="after")
    def _format(self) -> SectionUpdate:
        myst = find_myst(self.content_qmd)
        if myst:
            raise ValueError(
                f"Syntaxe MyST « {myst} » dans le contenu. Le format canonique "
                "est Quarto (ADR-006)."
            )
        return self
