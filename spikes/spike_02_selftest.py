#!/usr/bin/env python3
"""
Auto-test de l'instrument du spike 02 — à exécuter AVANT le spike lui-même.

Le spike 02 mesure un taux de conformité. Si son extracteur JSON ou ses
validateurs sont fautifs, il mesurera la qualité de son propre code plutôt que
celle du modèle, et un mauvais résultat sera imputé au 7B à tort.

Cet auto-test soumet aux mêmes fonctions des sorties dont la conformité est
connue d'avance. Il ne requiert ni Ollama ni GPU et doit passer partout.

Usage : python spike_02_selftest.py
Sortie : 0 instrument fiable · 1 instrument fautif
"""
from __future__ import annotations
import json, sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from spike_02_structured_output import PlanTree, extract_json  # noqa: E402


def plan(nodes=None, **kw):
    base = {
        "problematique": "Dans quelle mesure les particules de polyéthylène franchissent-elles la barrière glomérulaire des mammifères marins ?",
        "research_questions": ["Quels seuils dimensionnels ?", "Quelles conséquences fonctionnelles ?"],
        "methodology_note": "Revue systématique puis analyse comparative des protocoles.",
        "nodes": nodes if nodes is not None else [
            {"title": f"Chapitre {i}", "objective": "Établir un point précis du raisonnement",
             "target_words": 5000,
             "children": [
                 {"title": f"Section {i}.1", "objective": "Premier volet argumentatif", "target_words": 1500, "children": []},
                 {"title": f"Section {i}.2", "objective": "Second volet argumentatif", "target_words": 1500, "children": []},
             ]} for i in range(1, 5)
        ],
    }
    base.update(kw)
    return base


CAS = [
    # (nom, texte brut, conformité attendue, motif attendu si non conforme)
    ("JSON nu conforme", json.dumps(plan()), True, None),
    ("JSON entouré de prose",
     "Voici le plan demandé :\n\n" + json.dumps(plan()) + "\n\nJ'espère qu'il conviendra.",
     True, None),
    ("JSON en bloc Markdown",
     "```json\n" + json.dumps(plan()) + "\n```", True, None),
    ("accolade dans une chaîne",
     json.dumps(plan(methodology_note="Protocole { avec accolade } dans le texte")),
     True, None),
    ("guillemet échappé",
     json.dumps(plan(methodology_note='Le terme dit « seuil \\" critique » est discuté')),
     True, None),

    ("aucun JSON", "Je ne peux pas produire ce plan.", False, "json"),
    ("objet non refermé", "{\"problematique\": \"trop court", False, "json"),
    ("problématique trop courte", json.dumps(plan(problematique="Trop court.")), False, "schema"),
    ("moins de 4 chapitres", json.dumps(plan(nodes=plan()["nodes"][:2])), False, "schema"),
    ("titres frères dupliqués",
     json.dumps(plan(nodes=[
         {"title": "Chapitre A", "objective": "Objectif suffisamment long", "target_words": 5000,
          "children": [
              {"title": "Même titre", "objective": "Premier volet argumentatif", "target_words": 1500, "children": []},
              {"title": "Même titre", "objective": "Second volet argumentatif", "target_words": 1500, "children": []},
          ]}] * 4)),
     False, "schema"),
    ("sous-section unique",
     json.dumps(plan(nodes=[
         {"title": f"Chapitre {i}", "objective": "Objectif suffisamment long", "target_words": 5000,
          "children": [{"title": f"Unique {i}", "objective": "Volet isolé", "target_words": 1500, "children": []}]}
         for i in range(1, 5)])),
     False, "schema"),
    ("target_words hors bornes",
     json.dumps(plan(nodes=[
         {"title": f"Chapitre {i}", "objective": "Objectif suffisamment long", "target_words": 99999,
          "children": [
              {"title": f"S{i}.1", "objective": "Premier volet", "target_words": 1500, "children": []},
              {"title": f"S{i}.2", "objective": "Second volet", "target_words": 1500, "children": []},
          ]} for i in range(1, 5)])),
     False, "schema"),
    ("plan plat sans profondeur",
     json.dumps(plan(nodes=[
         {"title": f"Chapitre {i}", "objective": "Objectif suffisamment long",
          "target_words": 5000, "children": []} for i in range(1, 5)])),
     False, "schema"),
    ("aucune question de recherche", json.dumps(plan(research_questions=[])), False, "schema"),
]


def evaluer(raw: str):
    try:
        obj = json.loads(extract_json(raw))
    except Exception:  # noqa: BLE001
        return False, "json"
    try:
        PlanTree.model_validate(obj)
    except Exception:  # noqa: BLE001
        return False, "schema"
    return True, None


def main() -> int:
    print("Auto-test de l'instrument du spike 02\n")
    echecs = []
    for nom, raw, attendu_ok, attendu_motif in CAS:
        ok, motif = evaluer(raw)
        conforme = (ok == attendu_ok) and (ok or motif == attendu_motif)
        etat = "OK " if conforme else "ÉCHEC"
        detail = "" if conforme else (
            f" — attendu {'conforme' if attendu_ok else attendu_motif}, "
            f"obtenu {'conforme' if ok else motif}")
        print(f"  [{etat}] {nom}{detail}")
        if not conforme:
            echecs.append(nom)

    print("\n" + "=" * 62)
    print(f"Instrument : {len(CAS) - len(echecs)}/{len(CAS)} cas correctement classés")
    if echecs:
        print("\nL'instrument est fautif sur : " + ", ".join(echecs))
        print("Corriger extract_json ou les validateurs AVANT de mesurer le modèle :")
        print("un taux de conformité mesuré avec un instrument faux est sans valeur.")
        return 1
    print("\nL'instrument classe correctement les cas connus. Un échec observé")
    print("pendant le spike 02 sera imputable au modèle, pas au harnais.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
