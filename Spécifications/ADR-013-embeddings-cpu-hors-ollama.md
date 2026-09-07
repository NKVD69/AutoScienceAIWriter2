# ADR-013 — Embeddings sur CPU, hors d'Ollama

- **Statut :** Accepté
- **Date :** 2026-09-05
- **Lié à :** ADR-002, ADR-003
- **Corrige :** défaut D-05

## Contexte

Les spécifications posent deux exigences qui, telles qu'écrites, se contredisent :
« embeddings sur CPU pour préserver la VRAM » et « Ollama avec `keep_alive=-1` ».

Si `nomic-embed-text` est servi **par Ollama**, Ollama le charge sur GPU par
défaut, comme second modèle résident. Sur 10 Go de VRAM déjà occupés à ~5,2 Go par
le modèle principal plus son cache KV, l'ingestion d'un lot de PDF peut provoquer
soit l'éviction du modèle principal — ruinant l'objectif d'ADR-003 — soit un
dépassement de VRAM.

## Décision

**Les embeddings sortent d'Ollama.**

1. Génération dans le processus Python via `fastembed` (ONNX Runtime, CPU) ou
   `sentence-transformers` avec `device="cpu"`.
2. Modèle : `nomic-embed-text-v1.5`, dimension **768**, stockée dans
   `model_config.embedding_dim`.
3. **Préfixes obligatoires** — `nomic` est entraîné avec eux et leur omission
   dégrade nettement le rappel :
   - indexation : `search_document: ` ;
   - requête : `search_query: `.
4. Batching configurable (défaut 32), parallélisme borné au nombre de cœurs
   physiques moins un.
5. Aucun appel réseau après le téléchargement initial du modèle, lui-même soumis
   au consentement `model_download` (ADR-010).

## Options écartées

| Option | Motif |
|---|---|
| `nomic-embed-text` via Ollama | Charge sur GPU par défaut : conflit de VRAM |
| Ollama + `num_gpu: 0` | Dépend d'un comportement d'API non garanti dans le temps |
| Embeddings par le modèle principal | Qualité de rappel très inférieure, VRAM mobilisée |
| Dimension supérieure (1024+) | Coût mémoire et disque sans gain démontré sur ce corpus |

## Conséquences

L'ingestion est plus lente qu'en GPU — cible : 500 chunks de 512 tokens en moins
de 180 s sur un CPU 8 cœurs — mais elle est **prévisible** et n'interfère jamais
avec la rédaction en cours. La dimension 768 est verrouillée : tout changement de
modèle d'embedding impose une réindexation complète, à traiter comme une migration.

## Vérification

`test_embeddings_do_not_use_vram` (variation < 200 Mo) ·
`test_ollama_ps_lists_no_embedding_model` · `test_nomic_prefixes_applied` ·
`test_dimension_locked_against_model_config` · `scripts/check_vram_budget.py`
