"""Liste blanche des paquets du niveau 1 et contrôle des imports — US-004.

**Le niveau 1 n'expose qu'un jeu fermé de paquets scientifiques.** Ce n'est pas
la barrière de sécurité — celle-là est le bac WebAssembly lui-même —, mais la
frontière de ce qui est *disponible*. Un import hors liste est signalé AVANT
l'exécution, avec une proposition de passage au niveau 2 : mieux vaut le dire au
lancement qu'au détour d'un `ModuleNotFoundError` noyé dans une trace.

Le contrôle est statique (analyse du source), donc franchissable par un
`__import__` calculé. C'est assumé : la liste blanche gouverne la *disponibilité*
des paquets, pas l'isolement, qui tient au runtime. Un import dynamique d'un
paquet non chargé échouerait de toute façon dans Pyodide.
"""

from __future__ import annotations

import ast
import sys

from app.core.errors import PackageUnavailableInWasmError

# Paquets scientifiques admis au niveau 1, sous leur nom de distribution.
WASM_ALLOWED_PACKAGES: frozenset[str] = frozenset(
    {"numpy", "pandas", "scipy", "matplotlib", "sympy", "scikit-learn"}
)

# Les mêmes, sous leur nom d'IMPORT : scikit-learn s'importe « sklearn ». C'est
# ce jeu-là que l'analyse du source compare aux imports rencontrés.
WASM_ALLOWED_IMPORTS: frozenset[str] = frozenset(
    {"numpy", "pandas", "scipy", "matplotlib", "sympy", "sklearn"}
)


def _imported_roots(code: str) -> list[str]:
    """Modules de premier niveau importés par `code`, dans l'ordre d'apparition.

    `import a.b` et `from a.b import c` comptent tous deux pour « a » : c'est la
    racine qui décide de la disponibilité d'un paquet.
    """
    arbre = ast.parse(code)
    racines: list[str] = []
    vues: set[str] = set()
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Import):
            noms = [alias.name.split(".")[0] for alias in noeud.names]
        elif isinstance(noeud, ast.ImportFrom):
            # `from . import x` (niveau > 0, module None) est relatif : pas un paquet.
            noms = [noeud.module.split(".")[0]] if noeud.module and noeud.level == 0 else []
        else:
            continue
        for nom in noms:
            if nom not in vues:
                vues.add(nom)
                racines.append(nom)
    return racines


def check_wasm_imports(code: str) -> None:
    """Refuse un import hors liste blanche ET hors bibliothèque standard.

    La bibliothèque standard de Pyodide est celle de CPython : `sys.stdlib_module_names`
    de l'hôte en est un fidèle indicateur. Tout le reste — un `requests`, un
    `torch` — n'est pas au niveau 1 et provoque une proposition de niveau 2.
    """
    for racine in _imported_roots(code):
        if racine in WASM_ALLOWED_IMPORTS or racine in sys.stdlib_module_names:
            continue
        raise PackageUnavailableInWasmError.suggest_level2(racine)
