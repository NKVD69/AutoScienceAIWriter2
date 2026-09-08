"""Guardrail générique — US-201, ADR-008.

**Il valide ou il rejette. Il ne répare jamais.**

Réparer une sortie invalide — compléter un champ manquant par une valeur par
défaut, tronquer une liste trop longue, recoller un JSON par expression
régulière — produit une donnée *plausible* que plus rien ne signale comme
fausse. Dans un mémoire, cela devient une affirmation sans source, un budget
de mots inventé, un chapitre qui n'existait pas. Un rejet coûte un essai ; une
réparation coûte la confiance dans tout le document.

La seule tolérance admise porte sur le **format** : un modèle qui entoure son
JSON de prose ou de délimiteurs Markdown reste compréhensible. Le premier
objet équilibré est extrait. Le contenu, lui, n'est jamais arrangé.

Le message d'erreur est destiné **au modèle**, pas à l'utilisateur : chemin du
champ, contrainte violée, valeur reçue. C'est ce qui rend l'essai suivant
utile plutôt qu'aléatoire.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, ValidationError

from app.core.logging import get_logger

logger = get_logger(__name__)

# Au-delà, la valeur reçue encombre le prompt de correction sans l'éclairer.
VALUE_EXCERPT_LENGTH = 200


class GuardrailResult(BaseModel):
    ok: bool
    parsed: BaseModel | None = None
    error_message: str | None = None
    error_path: str | None = None

    model_config = {"arbitrary_types_allowed": True}


def extract_json(raw: str) -> str:
    """Premier objet JSON équilibré. Tolérance de FORMAT, pas de CONTENU.

    Le balayage tient compte des chaînes et des échappements : une accolade
    dans un titre ne doit pas fermer l'objet prématurément.
    """
    debut = raw.find("{")
    if debut < 0:
        raise ValueError("aucun objet JSON dans la réponse")

    profondeur = 0
    dans_chaine = False
    echappe = False
    for i, caractere in enumerate(raw[debut:], debut):
        if dans_chaine:
            if echappe:
                echappe = False
            elif caractere == "\\":
                echappe = True
            elif caractere == '"':
                dans_chaine = False
            continue
        if caractere == '"':
            dans_chaine = True
        elif caractere == "{":
            profondeur += 1
        elif caractere == "}":
            profondeur -= 1
            if profondeur == 0:
                return raw[debut : i + 1]
    raise ValueError("objet JSON non refermé")


def _describe(erreur: dict) -> tuple[str, str]:
    """(chemin, description) d'une erreur Pydantic, pour le modèle."""
    chemin = ".".join(str(part) for part in erreur.get("loc", ())) or "(racine)"
    recue = erreur.get("input")
    extrait = str(recue)
    if len(extrait) > VALUE_EXCERPT_LENGTH:
        extrait = extrait[:VALUE_EXCERPT_LENGTH] + "…"
    return chemin, f"{chemin} : {erreur.get('msg', 'invalide')} — reçu {extrait!r}"


async def validate_output(raw: str, model: type[BaseModel]) -> GuardrailResult:
    """Valide une sortie brute contre un modèle Pydantic."""
    try:
        fragment = extract_json(raw)
    except ValueError as exc:
        return GuardrailResult(
            ok=False,
            error_message=(
                f"{exc}. Réponds UNIQUEMENT par un objet JSON valide, sans "
                "préambule, sans commentaire, sans délimiteur Markdown."
            ),
            error_path="(format)",
        )

    try:
        objet = json.loads(fragment)
    except json.JSONDecodeError as exc:
        return GuardrailResult(
            ok=False,
            error_message=(
                f"JSON syntaxiquement invalide à la position {exc.pos} : {exc.msg}. "
                "Renvoie l'objet complet et correctement formé."
            ),
            error_path="(syntaxe)",
        )

    try:
        valide = model.model_validate(objet)
    except ValidationError as exc:
        erreurs = exc.errors()
        chemin, premier = _describe(erreurs[0])
        details = "; ".join(_describe(e)[1] for e in erreurs[:5])
        logger.debug("Guardrail : %s champ(s) invalide(s) — %s", len(erreurs), premier)
        return GuardrailResult(
            ok=False,
            error_message=(
                f"{len(erreurs)} contrainte(s) non respectée(s) : {details}. "
                "Corrige ces champs et renvoie UNIQUEMENT le JSON complet."
            ),
            error_path=chemin,
        )

    return GuardrailResult(ok=True, parsed=valide)
