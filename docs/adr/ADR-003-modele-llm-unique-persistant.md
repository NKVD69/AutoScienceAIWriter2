# ADR-003 — Modèle LLM unique et persistant en VRAM

- **Statut :** Accepté
- **Date :** 2026-09-05
- **Lié à :** ADR-004, ADR-013

## Contexte

Le poste cible dispose de **10 Go de VRAM**. La V0.1 prévoyait un modèle
spécialisé par agent (rédaction, code, relecture) avec chargement et déchargement
à chaque transition. Charger 5 à 6 Go depuis un SSD prend de plusieurs secondes à
plusieurs dizaines de secondes : une thèse compte des centaines de transitions
d'agents, soit des heures d'attente cumulée.

## Décision

1. **Un seul modèle généraliste résident** : `qwen2.5-7b-instruct-q4_k_m`
   (~5,2 Go), chargé au démarrage avec `keep_alive=-1`.
2. **Aucun `load`/`unload` par agent.** La spécialisation passe par le prompt
   système, pas par les poids.
3. **Prompts système figés par agent**, définis comme constantes, afin que le
   *prefix caching* soit effectif.
4. **Option `code_model_enabled`** (défaut `false`) : si activée et VRAM ≥ 12 Go,
   `qwen2.5-coder-7b` est chargé en second résident, réservé à l'agent code.
5. Interface `LLMBackend` permettant un backend `llama-cpp-python` ultérieur ;
   non implémenté au MVP.

## Arbitrage assumé

Le modèle généraliste est **moins bon en génération de code** que `qwen2.5-coder`.
C'est un échange délibéré : latence contre qualité de code. La porte de sortie est
l'option du point 4, pour les postes mieux dotés.

## Options écartées

| Option | Motif du rejet |
|---|---|
| Un modèle par agent avec swap | Latence rédhibitoire, cause première de la décision |
| Modèle 30B quantifié | ~18-20 Go hors cache KV : OOM immédiat sur 10 Go |
| MoE local | Écosystème local encore immature à cette taille ; VRAM non réduite en pratique |
| Modèle distant par défaut | Contraire au mode local strict |

## Conséquences

Le budget VRAM devient : modèle (5,2 Go) + cache KV (jusqu'à 2 Go selon contexte)
+ marge système. Les embeddings sont donc **exclus du GPU** (ADR-013). Toute
fonctionnalité nouvelle consommant de la VRAM doit être arbitrée contre ce budget,
vérifié par `scripts/check_vram_budget.py`.

## Vérification

`test_no_weights_reload_between_requests` (`load_duration < 50 ms`) ·
`test_system_prompt_byte_stable` · `scripts/check_llm_latency.py` ·
`scripts/check_vram_budget.py`

> **Note.** Ne pas écrire de test affirmant que « le cache KV est réutilisé » :
> deux requêtes indépendantes aux prompts différents ne partagent pas de cache KV,
> et l'API Ollama n'expose pas cette information. Le critère observable est
> `load_duration`.
