"""US-701 — analyse statique et controle lexical. ADR-009, D-07.

Deux controles que le code seul ne garantit pas :

- aucune ecriture destructrice sur `audit_log` ailleurs que dans les tests ;
- aucune promesse d'immuabilite dans le code, la documentation ou l'interface.

Une convention non verifiee n'est pas une convention.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

RACINE = Path(__file__).resolve().parents[3]
APP = RACINE / "backend" / "app"

MOTS_PROSCRITS = ("immuable", "immutable", "infalsifiable")

# L'interdit porte sur la PROMESSE, pas sur son explication : un document qui
# expose pourquoi ces mots sont proscrits doit pouvoir les citer. Une simple
# recherche de sous-chaine confondrait les deux et rendrait le controle
# inexploitable — donc, en pratique, desactive.
MARQUEURS_EXPLICATIFS = (
    "jamais",
    "proscri",
    "interdi",
    "ne pas",
    "plutot que",
    "plutôt que",
    "au lieu de",
    "inexact",
    "faux",
    "d-07",
    "adr-009",
    "remplac",
)

# Le controle repose sur la distinction usage / mention. « immuable » entre
# guillemets est un mot CITE — c'est ainsi que les ADR enoncent l'interdit.
# `journal immuable` sans guillemets est une PROMESSE — c'est cela qui est
# proscrit. Sans cette distinction, le controle signale les documents qui
# fondent la regle et devient inexploitable.
OCCURRENCE = re.compile("|".join(MOTS_PROSCRITS), re.IGNORECASE)

# Identifiants et parametres techniques : `test_..._immutable`,
# `immutable=1` (URI SQLite en lecture seule, US-ZOTERO-001).
IDENTIFIANT = re.compile(r"[A-Za-z0-9_]*_(?:immutable|immuable)|immutable\s*=", re.IGNORECASE)

# Delimiteurs de citation reconnus.
CITATIONS = re.compile(r"«[^»]*»|\"[^\"]*\"|`[^`]*`|“[^”]*”")

SQL_DESTRUCTIF = re.compile(r"\b(update\s+audit_log|delete\s+from\s+audit_log)\b", re.IGNORECASE)


def _chaines_du_module(path: Path) -> list[str]:
    arbre = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    valeurs: list[str] = []
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Constant) and isinstance(noeud.value, str):
            valeurs.append(noeud.value)
        elif isinstance(noeud, ast.JoinedStr):
            valeurs.extend(
                p.value
                for p in noeud.values
                if isinstance(p, ast.Constant) and isinstance(p.value, str)
            )
    return valeurs


def test_no_update_or_delete_on_audit_log() -> None:
    """Le journal est append-only par convention applicative — verifiee ici."""
    fautes: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        for chaine in _chaines_du_module(path):
            if SQL_DESTRUCTIF.search(chaine):
                fautes.append(f"{path.relative_to(RACINE)} : {chaine[:80]!r}")
    assert not fautes, "ecriture destructrice sur audit_log :\n" + "\n".join(fautes)


def test_single_insert_path_into_audit_log() -> None:
    """Une seule fonction ecrit dans `audit_log` : `audit_service.append`."""
    fautes: list[str] = []
    motif = re.compile(r"insert\s+into\s+audit_log", re.IGNORECASE)
    for path in sorted(APP.rglob("*.py")):
        if path.name == "audit_service.py":
            continue
        for chaine in _chaines_du_module(path):
            if motif.search(chaine):
                fautes.append(str(path.relative_to(RACINE)))
    assert not fautes, "insertion dans audit_log hors de audit_service :\n" + "\n".join(fautes)


def _fichiers_a_controler() -> list[Path]:
    cibles: list[Path] = []
    for base, motifs in (
        (RACINE / "backend" / "app", ("*.py", "*.sql")),
        (RACINE / "docs", ("*.md",)),
        (RACINE / "frontend" / "src", ("*.ts", "*.html")),
        (RACINE, ("README.md",)),
    ):
        if not base.exists():
            continue
        for motif in motifs:
            cibles.extend(p for p in base.rglob(motif) if p.is_file())
    return cibles


def test_wording_no_immutable_claim() -> None:
    """« detection d'alteration », jamais « immuable » (D-07).

    L'ecart entre les deux notions est decisif pour un outil vendu sur
    l'integrite : l'utilisateur possede le fichier .sqlite et peut recalculer
    la chaine entiere. La chaine detecte, elle n'empeche pas.
    """
    fautes: list[str] = []
    for path in _fichiers_a_controler():
        lignes = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        for numero, ligne in enumerate(lignes, start=1):
            if not _est_une_promesse(ligne):
                continue
            contexte = " ".join(lignes[max(0, numero - 3) : numero + 1]).lower()
            if any(marqueur in contexte for marqueur in MARQUEURS_EXPLICATIFS):
                continue
            fautes.append(f"{path.relative_to(RACINE)}:{numero} — {ligne.strip()[:90]}")
    assert not fautes, "promesse d'immutabilite (ADR-009, D-07) :\n" + "\n".join(fautes)


def _est_une_promesse(ligne: str) -> bool:
    """Vrai si la ligne emploie un mot proscrit, plutot que de le citer."""
    reste = CITATIONS.sub(" ", ligne)
    reste = IDENTIFIANT.sub(" ", reste)
    return bool(OCCURRENCE.search(reste))


def test_wording_check_still_bites() -> None:
    """Un test lexical trop permissif est pire qu'absent : il donne
    l'illusion d'une garantie que rien ne verifie plus."""
    assert _est_une_promesse("Le journal d'audit est immuable et garantit l'origine.")
    assert _est_une_promesse("Ce registre infalsifiable prouve la provenance.")
    # Mentions legitimes : citation, identifiant, parametre d'URI SQLite.
    assert not _est_une_promesse("Employer « immuable » ou « infalsifiable » est proscrit.")
    assert not _est_une_promesse("    test_wording_no_immutable_claim")
    assert not _est_une_promesse("Ouverture en mode `file:...?mode=ro&immutable=1`.")


def test_audit_module_uses_tamper_evident_wording() -> None:
    """Le vocabulaire correct est effectivement present, pas seulement absent."""
    source = (APP / "services" / "audit_service.py").read_text(encoding="utf-8").lower()
    assert "détection d'altération" in source
