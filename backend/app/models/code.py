"""Proposition de code et artefacts — US-401, §8, ADR-006.

**Une figure de thèse doit être reproductible.** Un résultat qui change à chaque
exécution est indéfendable en soutenance : c'est pourquoi la graine aléatoire
devient OBLIGATOIRE dès que le code importe `numpy` ou `random`. Le modèle le
refuse avant même toute exécution.

**Les renvois sont du Quarto, jamais du MyST (ADR-006).** Un label `fig:foo`
(deux points, à la MyST) compilerait mal des semaines plus tard sans que rien ne
relie l'échec à cette proposition. Le format canonique est `fig-foo` / `tbl-foo`.

**Un nom de fichier ne porte pas de chemin.** L'agent n'écrit que dans le
répertoire de sortie ; un séparateur ou un « .. » serait une tentative d'en
sortir, refusée ici plutôt que découverte dans une traceback.
"""

from __future__ import annotations

import ast
import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

ArtifactKind = Literal["figure", "table", "data"]

# Renvois Quarto : préfixe impose par le type, puis un slug en minuscules.
# MyST utilise les deux-points (fig:foo) ; Quarto le trait d'union (fig-foo).
_SLUG = r"[a-z0-9]+(?:-[a-z0-9]+)*"
LABEL_PATTERNS: dict[str, re.Pattern[str]] = {
    "figure": re.compile(rf"^fig-{_SLUG}$"),
    "table": re.compile(rf"^tbl-{_SLUG}$"),
    "data": re.compile(rf"^{_SLUG}$"),
}

# Imports qui rendent le résultat non déterministe sans graine fixée.
_STOCHASTIC_ROOTS = frozenset({"numpy", "random"})


def _imported_roots(code: str) -> set[str]:
    """Modules de premier niveau importés par `code`. Vide si le source est illisible."""
    try:
        arbre = ast.parse(code)
    except SyntaxError:
        # Un source non analysable sera de toute façon rejeté à l'exécution ;
        # ici, on ne peut rien affirmer de ses imports.
        return set()
    racines: set[str] = set()
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Import):
            racines.update(alias.name.split(".")[0] for alias in noeud.names)
        elif isinstance(noeud, ast.ImportFrom) and noeud.module and noeud.level == 0:
            racines.add(noeud.module.split(".")[0])
    return racines


class ExpectedArtifact(BaseModel):
    """Un artefact que le code s'engage à produire."""

    filename: str = Field(min_length=1)
    kind: ArtifactKind
    caption: str = Field(min_length=1)
    label: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valide(self) -> ExpectedArtifact:
        if "/" in self.filename or "\\" in self.filename or ".." in self.filename:
            raise ValueError(
                f"filename « {self.filename} » porte un séparateur de chemin ou « .. ». "
                "L'agent n'écrit que dans le répertoire de sortie : donne un nom simple, "
                "sans dossier."
            )
        motif = LABEL_PATTERNS[self.kind]
        if not motif.fullmatch(self.label):
            attendu = {"figure": "fig-", "table": "tbl-", "data": "un slug"}[self.kind]
            raise ValueError(
                f"label « {self.label} » n'est pas un renvoi Quarto valide pour un artefact "
                f"« {self.kind} » (attendu : {attendu} suivi d'un slug en minuscules, traits "
                "d'union). Le format MyST « fig:… » n'est pas accepté (ADR-006)."
            )
        return self


class CodeProposal(BaseModel):
    """Sortie attendue de l'agent code, avant exécution."""

    code: str = Field(min_length=1)
    intent: str = Field(min_length=1)
    datasets: list[str] = []
    expected_artifacts: list[ExpectedArtifact] = Field(min_length=1)
    random_seed: int | None = None

    @model_validator(mode="after")
    def _graine_si_alea(self) -> CodeProposal:
        if self.random_seed is None:
            stochastiques = _imported_roots(self.code) & _STOCHASTIC_ROOTS
            if stochastiques:
                raise ValueError(
                    f"Le code importe {sorted(stochastiques)} mais ne fixe pas random_seed. "
                    "Une figure de thèse doit être reproductible : renseigne random_seed, et "
                    "fixe la graine dans le code (np.random.seed / random.seed)."
                )
        return self

    def labels(self) -> list[str]:
        return [a.label for a in self.expected_artifacts]


class ArtifactOut(BaseModel):
    """Artefact persisté, tel qu'exposé par l'API. Porte sa trace de reproduction."""

    id: int
    code_execution_id: int
    draft_section_id: int | None = None
    kind: ArtifactKind
    filename: str
    label: str | None = None
    caption: str | None = None
    rel_path: str
    declared: bool
    random_seed: int | None = None
    library_versions: dict[str, str] = {}
    dataset_sha256: dict[str, str] = {}
    duration_ms: int | None = None
    created_at: str | None = None


class CodeExecutionOut(BaseModel):
    """Exécution persistée, telle qu'exposée par l'API (§8, US-004 + US-401)."""

    id: int
    origin: str
    sandbox_level: str
    exit_code: int
    duration_ms: int
    started_at: str
    artifacts: list[ArtifactOut] = []


class ExecutionResultOut(BaseModel):
    """`ExecutionResult` du contrat — exécution directe et historique (US-004).

    Reprend EXACTEMENT le schéma normatif de `contracts/openapi.yaml` : le
    niveau y est un entier (1 ou 2), et `artifacts` une liste de chemins. Les
    champs `timed_out`, `limit_exceeded` et `network_isolation_guaranteed` sont
    relus de la base, jamais réinférés du niveau — au niveau 2 sous Linux, la
    garantie dépend de la réussite d'`unshare -n`.
    """

    id: int | None = None
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    level: Literal[1, 2]
    timed_out: bool = False
    limit_exceeded: Literal["memory", "cpu", "wall"] | None = None
    network_isolation_guaranteed: bool
    # Vrai quand un niveau natif demandé a été ramené au niveau 1 : un agent ne
    # choisit jamais son isolation (ADR-005), et l'abaissement est dit, pas tu.
    downgraded_from_requested_mode: bool = False
    artifacts: list[str] = []
