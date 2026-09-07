# ADR-008 — Guardrails Pydantic et circuit breaker dans le graphe

- **Statut :** Accepté
- **Date :** 2026-09-05
- **Lié à :** ADR-003, ADR-004

## Contexte

Un modèle 7B quantifié échoue régulièrement à respecter un format de sortie
strict : JSON tronqué, champs manquants, prose parasite autour du bloc. Il peut
aussi boucler indéfiniment entre relecture et correction sans converger — sur une
machine locale, cette boucle mobilise le GPU pendant des heures sans produire de
résultat.

## Décision

**1. Guardrail structurel avant toute transition.**
La sortie de chaque agent est désérialisée dans un modèle Pydantic dédié
(`PlanTree`, `SectionDraft`, `ReviewReport`, `CodeProposal`). En cas d'échec, un
nœud `*_GUARDRAIL` reformule un message d'erreur **technique et précis** (chemin
du champ, contrainte violée) et relance le même agent — **sans** passer par
l'agent de relecture, dont ce n'est pas le rôle.

**2. Circuit breaker.**
Un compteur d'itérations est porté par l'état.

| Boucle | Limite | Action au dépassement |
|---|---|---|
| Guardrail → agent | 3 | `ERROR_STATE` + intervention humaine |
| `SECTION_REVIEWING` ⇄ `SECTION_CORRECTING` | 3 | Pause, meilleure version proposée à l'utilisateur |
| Ingestion d'une source | 2 | Source marquée en échec, pipeline poursuivi |

`ERROR_STATE` est **terminal sans action humaine** : aucune reprise automatique.

**3. Journalisation.** Chaque déclenchement de guardrail ou de breaker produit une
entrée d'audit avec le contenu fautif tronqué, afin d'alimenter l'amélioration des
prompts.

## Options écartées

| Option | Motif |
|---|---|
| Réessais illimités | Mobilise la machine sans borne |
| Correction par un second LLM | Double le coût, ne garantit pas la structure |
| Réparation JSON par regex | Masque le défaut, produit des données silencieusement fausses |

## Conséquences

Le système peut s'arrêter et demander l'aide de l'utilisateur — comportement voulu
pour un outil dont la promesse est la rigueur, non l'automatisme. L'interface doit
présenter `ERROR_STATE` de manière actionnable : ce qui a échoué, ce qui a été
tenté, ce que l'utilisateur peut faire.

## Vérification

`test_invalid_output_triggers_guardrail` · `test_guardrail_retries_same_agent` ·
`test_max_retry_leads_to_error_state` ·
`test_circuit_breaker_pauses_review_loop` · `test_error_state_requires_human`
