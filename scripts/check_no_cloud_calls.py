#!/usr/bin/env python3
"""Analyse statique du code applicatif : aucune sortie reseau hors liste blanche.

ADR-010. Le controle est syntaxique, donc fiable : il ne depend d'aucune
execution ni d'aucun jugement. Il repond a deux questions.

1. **Qui peut ouvrir une connexion sortante ?** Seuls les modules declares
   dans `MODULES_RESEAU` importent un client HTTP ou une primitive socket.
   Ailleurs, un tel import est un defaut : c'est ainsi qu'une sortie reseau
   apparait par inadvertance dans un agent ou un service.

2. **Vers quels hotes ?** Toute URL absolue litterale presente dans le code
   applicatif doit viser la boucle locale ou un domaine de la liste blanche
   (specifications S10, ADR-010).

Sortie : 0 conforme - 1 non conforme.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from urllib.parse import urlparse

RACINE = Path(__file__).resolve().parents[1]
APP = RACINE / "backend" / "app"

# Modules autorises a parler au reseau. Tout le reste passe par eux.
MODULES_RESEAU = {
    "biblio",  # requetes de recherche, sous consentement biblio_search
    "llm",  # Ollama, sur la boucle locale
}

MODULES_INTERDITS = {
    "httpx",
    "requests",
    "aiohttp",
    "urllib",
    "urllib3",
    "urllib.request",
    "socket",
    "ftplib",
    "smtplib",
    "telnetlib",
    "http",
    "http.client",
    "websockets",
}

# Liste blanche codee en dur (S10). La boucle locale n'est pas une sortie.
HOTES_LOCAUX = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}
HOTES_AUTORISES = {
    "api.openalex.org",
    "api.crossref.org",
    "eutils.ncbi.nlm.nih.gov",
    "api.semanticscholar.org",
    "export.arxiv.org",
}

# Espaces de noms XML et identifiants de schema : des URL qui ne sont jamais
# dereferencees. Les exclure evite de transformer un namespace BibTeX ou
# Dublin Core en faux positif.
PREFIXES_NON_RESEAU = (
    "http://www.w3.org/",
    "http://purl.org/",
    "http://schema.org/",
    "https://schema.org/",
    "http://xmlns.com/",
)

defauts: list[str] = []


def module_de(path: Path) -> str:
    rel = path.relative_to(APP)
    return rel.parts[0] if len(rel.parts) > 1 else rel.stem


def controler_imports(path: Path, arbre: ast.AST) -> None:
    if module_de(path) in MODULES_RESEAU:
        return
    for noeud in ast.walk(arbre):
        noms: list[tuple[str, int]] = []
        if isinstance(noeud, ast.Import):
            noms = [(alias.name, noeud.lineno) for alias in noeud.names]
        elif isinstance(noeud, ast.ImportFrom) and noeud.module:
            noms = [(noeud.module, noeud.lineno)]
        for nom, ligne in noms:
            racine = nom.split(".")[0]
            if racine in MODULES_INTERDITS or nom in MODULES_INTERDITS:
                defauts.append(
                    f"{path.relative_to(RACINE)}:{ligne} — import reseau '{nom}' hors "
                    f"des modules autorises ({', '.join(sorted(MODULES_RESEAU))})"
                )


def controler_urls(path: Path, arbre: ast.AST) -> None:
    for noeud in ast.walk(arbre):
        if not isinstance(noeud, ast.Constant) or not isinstance(noeud.value, str):
            continue
        valeur = noeud.value
        if not valeur.startswith(("http://", "https://")):
            continue
        if valeur.startswith(PREFIXES_NON_RESEAU):
            continue
        hote = (urlparse(valeur).hostname or "").lower()
        if hote in HOTES_LOCAUX or hote in HOTES_AUTORISES:
            continue
        defauts.append(
            f"{path.relative_to(RACINE)}:{noeud.lineno} — URL hors liste blanche : {valeur}"
        )


def main() -> int:
    print("check_no_cloud_calls - ADR-010\n")
    if not APP.exists():
        print(f"  Repertoire applicatif absent : {APP}")
        return 1

    fichiers = sorted(APP.rglob("*.py"))
    for path in fichiers:
        arbre = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        controler_imports(path, arbre)
        controler_urls(path, arbre)

    print(f"  {len(fichiers)} fichiers analyses dans backend/app/")
    print(f"  modules autorises au reseau : {', '.join(sorted(MODULES_RESEAU))}")
    print(f"  domaines en liste blanche   : {len(HOTES_AUTORISES)}\n")

    if defauts:
        for d in defauts:
            print(f"  [ECHEC] {d}")
        print(
            f"\n{'=' * 60}\ncheck_no_cloud_calls : {len(defauts)} sortie(s) reseau non autorisee(s)"
        )
        return 1

    print(f"{'=' * 60}\ncheck_no_cloud_calls : aucune sortie reseau hors liste blanche")
    return 0


if __name__ == "__main__":
    sys.exit(main())
