# ADR-015 — Modèle 31B avec déversement CPU/RAM assumé

- **Statut :** Accepté
- **Date :** 2026-09-07
- **Décideurs :** porteur de projet
- **Amende :** ADR-003 (point 1 et sa table « Options écartées »)
- **Lié à :** ADR-013, ADR-014

## Contexte

ADR-003 écarte explicitement les modèles de 30B : *« Modèle 30B quantifié —
~18-20 Go hors cache KV : OOM immédiat sur 10 Go »*. Le raisonnement supposait
que le modèle devait tenir **entièrement** en VRAM.

Cette hypothèse ne tient plus. LM Studio, sur `llama.cpp`, répartit les couches
entre GPU et CPU : un modèle plus grand que la VRAM ne provoque pas d'OOM, il
s'exécute plus lentement. Le poste dispose de **64 Go de RAM** en plus de ses
10 Go de VRAM.

Le porteur de projet arbitre : pour la rédaction d'un mémoire, la qualité du
modèle prime sur le temps de génération, qui n'est pas une contrainte du
produit. Un document se rédige sur des semaines ; une section produite en une
heure au lieu d'une minute reste utilisable, une section médiocre ne l'est pas.

## Décision

1. **`google/gemma-4-31b` (Q8_0, 33,8 Go) est le modèle par défaut.**
2. **Le déversement GPU → CPU/RAM est un mode nominal**, pas une dégradation.
   La répartition des couches est laissée à LM Studio.
3. **Le budget de latence de §12.2 est levé** pour la génération. Le temps au
   premier token et le débit ne sont plus des critères d'acceptation.
4. **Le plafond de VRAM de §12.1 (< 9,0 Go) ne gouverne plus.** Le critère
   devient : le modèle reste résident, aucune éviction, aucun OOM.
5. **Le seuil `llm_max_ttft_ms` est conservé mais requalifié.** Il ne mesure
   plus le confort d'usage : il détecte un **rechargement de poids**. Porté à
   15 s, il sépare sans ambiguïté le régime établi (3,6–3,8 s mesurés) d'un
   rechargement (plusieurs minutes). Le supprimer ne détecterait plus rien.

## Mesures qui fondent la décision

Relevé du 7 septembre 2026, `google/gemma-4-31b` Q8_0 résident sous LM Studio.

| Grandeur | Valeur | Budget d'origine |
|---|---|---|
| VRAM occupée | 9 713 / 10 240 Mo | < 9 216 Mo (§12.1) |
| RAM occupée | 59,7 / 63,9 Go | non budgété |
| Temps au premier token | 3,6 à 3,8 s | < 2 s (§12.2) |
| Débit | 0,6 token/s | non budgété |
| Contexte maximal | 262 144 tokens | 8 192 configurés |

**Ordre de grandeur à connaître.** À 0,6 token/s, une section de 1 500 mots
(~2 000 tokens) demande environ **55 minutes**. Un mémoire de quarante sections
représente de l'ordre de **36 heures de génération cumulée**, hors relecture et
reprises. C'est le prix accepté ; il est consigné ici pour qu'il ne soit
redécouvert par personne.

## Options écartées

| Option | Motif du rejet |
|---|---|
| `google/gemma-4-e4b` (tient en VRAM) | Modèle nettement plus petit ; la qualité de rédaction prime sur la latence |
| `qwen/qwen3.8-27b` Q4_K_M | Non évalué à ce stade ; réévaluable si la qualité de `gemma-4-31b` déçoit |
| Quantification plus agressive du 31B | `gemma-4-31b-qat` (Q4_0) est installé et reste une porte de sortie si la RAM devient contraignante |
| Supprimer le seuil de TTFT | Un seuil supprimé ne détecte plus un rechargement de poids, seul défaut que ce contrôle sait voir |

## Conséquences

**Positives.** Un modèle de 31B au lieu d'un 4B, sur un poste à 10 Go de VRAM.
Le contexte disponible (262 144 tokens) dépasse largement les 8 192 configurés.

**Négatives, et elles sont réelles.**

- **La RAM devient la ressource critique.** 59,7 Go des 63,9 Go sont occupés,
  soit **4,2 Go libres**. Or ADR-013 place les embeddings sur CPU, donc en RAM,
  et US-102 prévoit l'ingestion de 50 PDF. Les deux charges se disputeront la
  même ressource. À vérifier avant US-005 et US-102 : le débit d'ingestion visé
  (500 chunks en moins de 180 s) a été budgété sans modèle de 34 Go en mémoire.
- **`check_vram_budget.py` (US-006) est à respécifier.** Mesurer une marge de
  VRAM n'a plus de sens quand la saturation est voulue. Le critère utile devient
  l'absence d'éviction et d'OOM sur un cycle complet.
- **L'expérience d'interface change de nature.** Une section qui met une heure
  à se produire impose un suivi de progression et une reprise après arrêt, non
  une attente synchrone. US-801 et US-DASH-001 doivent en tenir compte : le
  pont SSE n'est plus un confort.
- **Le second modèle de code reste refusé.** `code_model_enabled` exige 12 Go de
  VRAM ; le poste en a 10, déjà saturés. La décision d'ADR-003 point 4 tient,
  pour une raison renforcée.

**Sur le code.** `llm_max_ttft_ms` et `vram_ceiling_mb` ne sont plus des budgets
de performance mais des détecteurs de panne. Tout nouveau seuil de latence doit
être introduit avec cette lecture, ou pas du tout.

## Vérification

`scripts/check_llm_latency.py` — doit sortir en 0 avec le modèle par défaut :
résidence constatée, aucun rechargement. `test_default_model_is_documented` ·
`test_model_stays_resident_across_requests` (intégration).

---
> **Règle pour l'IA de codage :** un ADR au statut *Accepté* n'est pas rediscutable
> pendant l'implémentation. Une objection se consigne en section « Réserves » du
> rapport de story, jamais par une déviation silencieuse du code.
