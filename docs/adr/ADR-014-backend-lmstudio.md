# ADR-014 — LM Studio comme moteur d'inférence local

- **Statut :** Accepté
- **Date :** 2026-09-07
- **Décideurs :** porteur de projet
- **Amende :** ADR-003 (points 1 et 4, et sa note de vérification)
- **Lié à :** ADR-010, ADR-013

## Contexte

ADR-003 fige le principe d'**un modèle généraliste unique et résident**, et
retient Ollama comme moteur, avec `keep_alive=-1` et `qwen2.5-7b-instruct-q4_k_m`.
Son point 5 prévoyait explicitement un second backend derrière l'interface
`LLMBackend`.

Le poste de développement exécute LM Studio, dont les modèles sont déjà
installés et gérés. Maintenir deux gestionnaires de poids en parallèle sur une
machine à 10 Go de VRAM revient à les faire s'évincer mutuellement — c'est
précisément le coût que ADR-003 cherche à supprimer, réintroduit par le haut.

La mesure du 7 septembre 2026 rend l'arbitrage sensible : un chargement à froid
coûte **7 min 44 s** sur ce poste (`spikes/RESULTATS.md`). Un moteur de trop
n'est pas une gêne, c'est une interruption de service.

## Décision

1. **LM Studio est le moteur par défaut** (`llm_backend = "lmstudio"`), via son
   API native `/api/v0` — seule à publier `stats` et l'état de chargement des
   modèles. L'API OpenAI `/v1` ne le fait pas.
2. **Ollama reste implémenté** derrière la même interface `LLMBackend` et
   sélectionnable par `llm_backend = "ollama"`. Le principe d'ADR-003 est
   inchangé ; seul le moteur qui l'applique devient un réglage.
3. **La persistance s'exprime par `ttl`**, transmis à chaque requête comme
   l'était `keep_alive`. LM Studio n'accepte pas de valeur infinie : on demande
   une durée que le service ne dépassera pas en usage (24 h par défaut).
4. **Le critère de vérification de la persistance devient l'état du modèle.**
   LM Studio ne publie pas `load_duration`. `/api/v0/models` publie en revanche
   `state: loaded | not-loaded` : la résidence s'observe **directement**, avant
   et après une série de requêtes.

   Corollaire pour l'outillage : une métrique *remplacée* n'est pas une métrique
   *manquante*. `check_llm_latency.py` signale l'absence de `load_duration` sous
   LM Studio comme une information, non comme une mesure impossible — la
   traiter en code 2 rendrait le contrôle définitivement non concluant sur le
   moteur par défaut, donc sans usage.
5. **Une métrique absente vaut `None`, jamais `0`.** `LLMResult.load_duration_ms`
   est optionnel. Un zéro se lirait comme « poids restés résidents », c'est-à-dire
   comme la preuve de ce que l'ADR cherche à établir, alors qu'il ne signifierait
   que l'absence de mesure.
6. **Les embeddings ne passent pas par LM Studio**, bien que
   `text-embedding-nomic-embed-text-v1.5` y soit installé. ADR-013 tient : un
   modèle d'embedding chargé par le serveur d'inférence occupe le GPU et évince
   le modèle principal pendant l'ingestion.

## Ce qui change dans ADR-003

| Point d'ADR-003 | Devient |
|---|---|
| `qwen2.5-7b-instruct-q4_k_m`, `keep_alive=-1` | Modèle configurable, `ttl` long sous LM Studio |
| `qwen2.5-coder-7b` en second résident | `qwen/qwen3-coder-next`, mêmes conditions (VRAM ≥ 12 Go) |
| Vérification par `load_duration < 50 ms` | Vérification par `load_duration` **si le moteur la publie**, sinon par l'état du modèle |
| Backend alternatif « non implémenté au MVP » | Implémenté : c'est celui-ci |

Les points 2 et 3 d'ADR-003 — aucun `load`/`unload` par agent, prompts système
figés — sont **inchangés** et restent vérifiés par les mêmes tests.

## Options écartées

| Option | Motif du rejet |
|---|---|
| Remplacer Ollama plutôt que l'ajouter | L'interface existe pour porter les deux ; supprimer du code vérifié ne rend service à personne |
| Utiliser l'API OpenAI `/v1` de LM Studio | Ne publie ni `stats` ni l'état de chargement : la persistance deviendrait invérifiable |
| Conserver `load_duration` comme critère unique | Rendrait ADR-003 invérifiable sous LM Studio, donc de fait abandonné |
| Router les embeddings par LM Studio | Contredit ADR-013 ; le modèle est pourtant installé, d'où la mention explicite au point 6 |

## Conséquences

**Positives.** Un seul gestionnaire de poids sur le poste. La résidence est
*observée* au lieu d'être inférée. Le temps au premier token est publié par le
moteur, donc mesuré sans chronomètre client.

**Négatives.** Le modèle d'ADR-003 n'est plus disponible tel quel : les
identifiants LM Studio (`google/gemma-4-e4b`) ne sont pas ceux d'Ollama, et
basculer de moteur impose de changer aussi `llm_model`. Le seuil
`llm_max_load_duration_ms` devient inapplicable sous LM Studio.

**Sur le code.** `resolve_backend_factory()` est le seul point de sélection.
Toute métrique de latence nouvelle doit être optionnelle par défaut : un moteur
peut ne pas la publier.

**Réserve consignée.** Aucun des modèles installés ne correspond au profil
d'ADR-003 (~5,2 Go). `google/gemma-4-e4b` est retenu par défaut comme le seul
compatible avec le budget VRAM de §12.1.

`google/gemma-4-31b` a d'abord été mesuré par `check_llm_latency.py` — sur un
budget de 24 tokens, donc un débit apparent de 0,6 token/s et un temps au
premier token de 3,6 à 3,8 s — ce qui plaçait le modèle hors du budget de 2 s
de §12.2 et faisait sortir le script en code 1.

**Cette réserve est levée** : ADR-015 a retenu ce modèle et supprimé le budget
de latence, et le spike 02 a mesuré le débit réel sur une génération complète
à **2,3 tokens/s**. Le chiffre de 0,6 n'était pas faux, il n'était pas
extrapolable : sur 24 tokens, le temps d'amorce du prompt écrase le débit.

## Vérification

`scripts/check_llm_latency.py` (agnostique du moteur) ·
`test_ttl_sent_on_every_request` · `test_load_duration_is_none_not_zero` ·
`test_model_stays_resident_across_requests` (intégration) ·
`test_factory_resolves_both_engines` · `test_no_unload_route_is_called`

---
> **Règle pour l'IA de codage :** un ADR au statut *Accepté* n'est pas rediscutable
> pendant l'implémentation. Une objection se consigne en section « Réserves » du
> rapport de story, jamais par une déviation silencieuse du code.
