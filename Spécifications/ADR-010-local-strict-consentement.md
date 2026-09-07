# ADR-010 — Local strict par défaut, sortie réseau sous consentement explicite

- **Statut :** Accepté · amendé le 2026-09-06 (point 5)
- **Date :** 2026-09-05
- **Lié à :** ADR-009

## Contexte

Une thèse en cours contient des résultats non publiés, parfois des données
personnelles ou sous accord de confidentialité industriel. L'envoi involontaire
d'un extrait à un service tiers peut compromettre une publication, voire violer
un contrat. Simultanément, l'outil a besoin du réseau pour trois usages légitimes :
recherche bibliographique, téléchargement initial des modèles, vérification
anti-plagiat.

## Décision

**Mode « local strict » par défaut.** Aucune donnée de projet ne sort sans
consentement explicite, par périmètre, enregistré dans `consent` et journalisé.

| Périmètre | Ce qui sort | Défaut |
|---|---|---|
| `biblio_search` | Requêtes de recherche uniquement — jamais de texte du mémoire | Refusé |
| `model_download` | Rien du projet ; téléchargement initial des poids | Refusé |
| `plagiarism_check` | Texte du mémoire, **à l'étendue choisie par l'utilisateur**, à un service tiers configuré par lui | Refusé |
| `remote_llm` | Prompts et extraits de sources | Refusé |

Règles :

- consentement **par périmètre et par projet**, révocable, jamais global ;
- liste blanche de domaines par périmètre, codée en dur ;
- pour `plagiarism_check` et `remote_llm`, l'interface affiche **ce qui sera
  transmis** avant l'envoi ;
- `scripts/check_no_cloud_calls.py` en CI : toute sortie réseau hors liste blanche
  fait échouer la construction.

**Amendement du 2026-09-06 — étendue de `plagiarism_check`.** Le consentement
autorise la sortie ; il n'appartient pas à l'outil de décider *combien* de texte
sort. L'utilisateur choisit à chaque requête entre `passages`, `section` et
`document`. La rédaction initiale de cet ADR limitait implicitement la
transmission aux seuls passages signalés localement : cette restriction est
levée.

Ce que le consentement continue de garantir n'est pas une limite de volume mais
la **qualité de l'information** sur laquelle la décision est prise : le texte
exact et son volume sont affichés avant l'envoi, le choix n'est jamais
mémorisé implicitement, et l'étendue retenue est journalisée. Un consentement
éclairé sur un document entier vaut mieux qu'une restriction technique que
l'utilisateur contournerait en collant son texte dans un navigateur.

## Options écartées

| Option | Motif |
|---|---|
| Consentement global unique | L'utilisateur perd le contrôle du périmètre |
| Réseau ouvert par défaut | Contredit la promesse produit |
| Aucun réseau | Prive l'outil de la bibliographie, sa fonction la plus utile |

## Conséquences

Chaque appel sortant traverse un client HTTP unique qui vérifie le consentement et
la liste blanche. Refuser un consentement doit dégrader proprement, jamais
provoquer une erreur technique : recherche bibliographique refusée → import manuel
de PDF proposé.

## Vérification

`scripts/check_no_cloud_calls.py` · `test_no_consent_blocks_biblio_search` ·
`test_consent_recorded_and_revocable` · `test_whitelist_enforced` ·
`test_graceful_degradation_without_consent`
