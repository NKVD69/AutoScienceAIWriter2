# Prompt d'implémentation — US-005 (embeddings CPU hors Ollama)

Section US-005 de `docs/prompts/03-story-prompts.md`.
Story nouvelle, introduite par le Backlog V0.3. Conforme à ADR-013 et aux Spécifications V0.3 §6.4.

---

```text
Tu es un agent de codage senior, spécialisé en NLP appliqué, ONNX Runtime,
recherche vectorielle et optimisation CPU.

PROJET

Science AI Writer IDE — IDE scientifique local multi-agents, 10 Go de VRAM,
Windows 11 et Linux, sans Docker.

SOURCES DE VÉRITÉ, PAR ORDRE DÉCROISSANT

1. Ce prompt.
2. ADR-013 (embeddings CPU hors Ollama), ADR-003 (modèle LLM persistant),
   ADR-002 (sqlite-vec).
3. Backlog V0.3, US-005.
4. Spécifications techniques V0.3, §6.4 et §7.

USER STORY

US-005 — Générer les embeddings hors d'Ollama, sur CPU, afin que
l'ingestion RAG ne dispute jamais la VRAM au modèle de rédaction.

CONTEXTE DE LA DÉCISION

Deux exigences des spécifications antérieures se contredisaient :
"embeddings sur CPU pour préserver la VRAM" et "Ollama avec keep_alive=-1".
Si nomic-embed-text est servi par Ollama, Ollama le charge sur GPU par
défaut, comme second modèle résident. Sur 10 Go déjà occupés à environ
5,2 Go par le modèle principal plus son cache KV, l'ingestion d'un lot de
PDF provoque soit l'éviction du modèle de rédaction — ruinant l'objectif
d'ADR-003 — soit un dépassement de VRAM en plein travail de l'utilisateur.

Les embeddings sortent donc entièrement d'Ollama.

PÉRIMÈTRE STRICT

Tu implémentes :

- le service d'embeddings CPU ;
- l'application des préfixes nomic ;
- le batching et la limitation du parallélisme ;
- le verrouillage de la dimension contre model_config ;
- l'intégration avec insert_chunk_with_embedding de US-002 ;
- les tests, dont la mesure de non-consommation de VRAM.

Tu n'implémentes PAS :

- l'extraction de texte des PDF (US-102) ;
- le découpage en chunks (US-102) ;
- la récupération et le reclassement (US-102, US-RAG-002) ;
- le LLM de génération (US-003).

FICHIERS À CRÉER OU MODIFIER

- backend/app/rag/__init__.py
- backend/app/rag/embeddings.py
- backend/app/core/config.py                (modification)
- backend/app/core/errors.py                (modification)
- backend/tests/tests_rag/__init__.py
- backend/tests/tests_rag/test_embeddings.py
- backend/tests/tests_rag/test_embeddings_vram.py
- scripts/check_embeddings_cpu.py

Aucun autre fichier.

EXIGENCES D'IMPLÉMENTATION

1. Service (backend/app/rag/embeddings.py)

    class EmbeddingService:
        def __init__(self, model_name: str, dim: int,
                     batch_size: int = 32,
                     max_workers: int | None = None): ...

        async def embed_documents(self, texts: list[str]) -> list[list[float]]
        async def embed_query(self, text: str) -> list[float]
        def dimension(self) -> int
        def model_id(self) -> str          # nom + révision, pour model_config

    Implémentation par fastembed (ONNX Runtime, fournisseur CPU) ; repli
    sur sentence-transformers avec device="cpu" si fastembed est
    indisponible. Le choix est encapsulé derrière une fonction
    _load_backend() afin de rester substituable.

    Le calcul est bloquant : il s'exécute dans un ThreadPoolExecutor dédié
    via asyncio.to_thread ou loop.run_in_executor. Il ne bloque JAMAIS
    l'event loop de FastAPI.

2. Préfixes nomic — EXIGENCE BLOQUANTE

    nomic-embed-text est entraîné avec des préfixes de tâche. Leur
    omission dégrade nettement le rappel et constitue un défaut bloquant,
    pas une optimisation.

        embed_documents  ->  "search_document: " + texte
        embed_query      ->  "search_query: " + texte

    Les préfixes sont appliqués DANS le service, jamais par l'appelant :
    un appelant qui oublierait le préfixe produirait des vecteurs
    silencieusement incompatibles avec l'index.

    Le service expose une méthode interne _apply_prefix(text, task)
    testable directement.

3. Verrouillage de la dimension

    - settings.embedding_dim, défaut 768 ;
    - au démarrage d'un projet, comparer avec model_config.embedding_dim ;
      en cas d'écart, lever DimensionMismatchError avec un message
      indiquant que le changement de modèle d'embedding impose une
      réindexation complète ;
    - embed_documents lève ValueError si le backend retourne une dimension
      différente de celle attendue. Le contrôle porte sur la sortie réelle,
      pas sur la configuration déclarée.

4. Batching et parallélisme

    - taille de lot configurable, défaut 32 ;
    - nombre de threads borné à max(1, cœurs_physiques - 1), afin de
      laisser une marge à l'interface et au serveur ;
    - progression exposée par un callback optionnel
      on_progress(done: int, total: int), destiné à l'ingestion ;
    - l'annulation d'une tâche d'ingestion doit interrompre le traitement
      entre deux lots, pas seulement à la fin.

5. Absence d'appel réseau

    - le modèle est téléchargé au premier usage uniquement, et ce
      téléchargement est soumis au consentement model_download (ADR-010) ;
    - une fois le modèle en cache local, aucun appel réseau n'est émis ;
    - le chemin du cache est configurable
      (settings.embedding_cache_dir, défaut ./data/models) ;
    - si le modèle est absent et le consentement non accordé, lever
      ModelDownloadConsentRequiredError avec un message actionnable.

6. Configuration ajoutée

        embedding_model: str = "nomic-ai/nomic-embed-text-v1.5"
        embedding_dim: int = 768
        embedding_batch_size: int = 32
        embedding_cache_dir: Path = Path("./data/models")
        embedding_max_workers: int | None = None

7. Tests

    test_embed_documents_returns_expected_dimension
    test_embed_query_returns_expected_dimension
    test_document_prefix_applied
    test_query_prefix_applied
    test_prefixes_differ_between_document_and_query
    test_wrong_dimension_from_backend_raises
    test_dimension_mismatch_with_model_config_raises
    test_batching_respects_batch_size
    test_worker_count_bounded_by_cores
    test_progress_callback_invoked
    test_cancellation_between_batches
    test_no_network_call_when_model_cached      # httpx patché, assert non appelé
    test_missing_model_without_consent_raises
    test_event_loop_not_blocked                 # une tâche concurrente progresse
                                                # pendant l'embedding

    test_embeddings_do_not_use_vram :
        mesure la VRAM avant et après la vectorisation de 500 textes
        courts ; la variation doit rester inférieure à 200 Mo ;
        le test s'ignore proprement en l'absence de nvidia-smi.

    test_ollama_ps_lists_no_embedding_model :
        marqué @pytest.mark.integration ; interroge /api/ps et vérifie
        qu'aucun modèle dont le nom contient "embed" n'est chargé.

8. Script de vérification (scripts/check_embeddings_cpu.py)

    Vectorise 500 textes synthétiques de 512 tokens. Affiche : durée
    totale, débit en chunks/s, VRAM avant et après, modèles listés par
    ollama ps. Sortie 0 si la durée est inférieure à 180 s sur 8 cœurs et
    la variation de VRAM inférieure à 200 Mo, 1 sinon.

DÉPENDANCES AUTORISÉES

fastembed, onnxruntime, pydantic, pydantic-settings, pytest,
pytest-asyncio. Repli sentence-transformers toléré.
Pas de torch avec support CUDA. Pas d'appel à l'API Ollama depuis ce
module.

INTERDICTIONS

- Router les embeddings par Ollama ou par le LLMManager de US-003.
- Laisser l'appelant appliquer les préfixes.
- Exécuter le calcul sur l'event loop.
- Utiliser un fournisseur ONNX GPU.
- Téléchargement automatique sans consentement.
- Modification d'un fichier hors périmètre.

CRITÈRES D'ACCEPTATION

    cd backend && pytest -q backend/tests/tests_rag -m "not integration"
    ruff check backend/ && ruff format --check backend/
    python scripts/check_embeddings_cpu.py
    python scripts/check_no_cloud_calls.py

FORMAT DE RÉPONSE

1. Plan d'implémentation, 10 lignes maximum.
2. Contenu intégral de chaque fichier.
3. Sortie attendue des vérifications.
4. Section "Réserves".
```

## Message de commit attendu

```text
feat(rag): service d'embeddings CPU hors Ollama

Implémente US-005 (Backlog V0.3, story nouvelle).

- fastembed / ONNX Runtime CPU, hors de l'event loop.
- Préfixes nomic search_document / search_query appliqués dans le service.
- Dimension verrouillée contre model_config, contrôle sur la sortie réelle.
- Aucun appel réseau après mise en cache du modèle.

Refs: US-005, ADR-013
Corrige: D-05 (embeddings chargés sur GPU par Ollama malgré la spécification)
```
