# Prompt d'implémentation — US-102 (import de sources et ingestion RAG)

Section US-102 de `docs/prompts/03-story-prompts.md`.
Conforme au Backlog V0.3, aux Spécifications V0.3 §7 et aux ADR-002, ADR-013.

---

```text
Tu es un agent de codage senior, spécialisé en extraction documentaire,
pipelines d'ingestion, recherche vectorielle et traitement asynchrone.

PROJET

Science AI Writer IDE — IDE scientifique local multi-agents. Corpus type :
30 à 300 articles scientifiques en PDF par projet.

SOURCES DE VÉRITÉ, PAR ORDRE DÉCROISSANT

1. Ce prompt.
2. ADR-002 (sqlite-vec, chunk/vec_chunk), ADR-013 (embeddings CPU),
   ADR-010 (local strict).
3. Backlog V0.3, US-102.
4. Spécifications V0.3, §7.
5. contracts/openapi.yaml, sections /sources, /ingest, /search.

DÉPENDANCES REQUISES : US-002 (schéma et vector.py), US-005
(EmbeddingService), US-101 (projets et pool de connexions).

USER STORY

US-102 — En tant que chercheur, je veux importer des documents et les
transformer en base de connaissances interrogeable, afin que la rédaction
s'appuie sur mes sources et non sur la mémoire du modèle.

EXIGENCE STRUCTURANTE — LA PAGINATION

Chaque chunk conserve page_start et page_end. Sans pagination, une citation
n'est pas vérifiable dans le PDF d'origine, et une citation non vérifiable
est sans valeur dans un mémoire. Un chunk sans pagination issu d'un PDF est
un défaut bloquant, pas une imperfection tolérable.

PÉRIMÈTRE STRICT

Tu implémentes :

- l'import de fichiers et la détection de doublons ;
- l'extraction de texte paginé ;
- le découpage en chunks ;
- l'orchestration de l'ingestion comme tâche suivie ;
- l'approbation humaine des sources ;
- la recherche sémantique exposée en API ;
- les tests.

Tu n'implémentes PAS :

- la recherche bibliographique en ligne (US-BIBLIO-001) ;
- le calcul des embeddings lui-même (US-005 : tu consommes son service) ;
- les filtres avancés du RAG (US-RAG-002 : tu implémentes seulement
  year_min et exclude_preprints) ;
- le reclassement par modèle (hors périmètre) ;
- les agents et la rédaction.

FICHIERS À CRÉER OU MODIFIER

- backend/app/rag/extractor.py
- backend/app/rag/chunker.py
- backend/app/rag/retriever.py
- backend/app/services/ingestion_service.py
- backend/app/services/task_service.py
- backend/app/models/source.py
- backend/app/api/v1/sources.py
- backend/app/api/v1/__init__.py            (modification)
- backend/app/core/config.py                (modification)
- backend/tests/tests_rag/test_extractor.py
- backend/tests/tests_rag/test_chunker.py
- backend/tests/tests_rag/test_retriever.py
- backend/tests/tests_api/test_sources_api.py
- backend/tests/fixtures/                    (PDF de test générés, pas téléchargés)

Aucun autre fichier.

EXIGENCES D'IMPLÉMENTATION

1. Import (api/v1/sources.py)

    - multipart, extensions acceptées : .pdf, .txt, .md ;
    - calcul du SHA-256 à la réception ; si une source du projet porte déjà
      ce hash, retourner 200 avec la source existante et un champ
      duplicate: true, plutôt que de créer un doublon ;
    - le fichier est stocké dans data/projects/{slug}/sources/{sha256}.pdf ;
    - métadonnées extraites du PDF si présentes (titre, auteurs, DOI dans
      les XMP ou la première page), proposées à l'utilisateur mais JAMAIS
      considérées comme vérifiées : le champ approved_at reste nul.

2. Extraction (extractor.py)

    - PyMuPDF, texte par page, en conservant le numéro de page ;
    - suppression des en-têtes et pieds de page récurrents : une ligne
      identique sur plus de 60 % des pages est retirée ;
    - détection des PDF sans couche texte (image seule) : lever
      ScannedPdfError avec un message indiquant qu'une OCR est nécessaire.
      Ne PAS tenter d'OCR : hors périmètre, et le silence produirait des
      chunks vides ;
    - .txt et .md sont traités comme une page unique, page_start = 1.

3. Découpage (chunker.py)

    - stratégie primaire : découpage aux frontières de sections détectées
      par les titres numérotés et les intitulés canoniques d'article
      (Abstract, Introduction, Methods, Results, Discussion, References,
      et leurs équivalents français) ;
    - repli : fenêtre glissante de settings.chunk_tokens (défaut 512) avec
      settings.chunk_overlap (défaut 64) ;
    - un chunk ne franchit jamais une frontière de document ;
    - la section References est indexée séparément et marquée : elle sert à
      l'extraction bibliographique, pas à la rédaction. Un chunk de
      références ne doit jamais être retourné à l'agent rédacteur ;
    - page_start et page_end sont calculés à partir de l'offset des
      caractères dans le texte paginé, jamais estimés.

4. Ingestion (ingestion_service.py)

    Pipeline, par source approuvée non encore ingérée :
      extraction -> découpage -> embeddings par lots (US-005) ->
      insert_chunk_with_embedding (US-002) -> mise à jour du compteur.

    - transaction par source, pas par chunk : une source échouée ne laisse
      aucun chunk partiel ;
    - deux essais par source (circuit breaker de l'ingestion, ADR-008), puis
      marquage en échec et poursuite du pipeline. Une source illisible ne
      doit jamais bloquer l'ingestion des 200 autres ;
    - progression émise à chaque lot via le service de tâches ;
    - annulation prise en compte entre deux sources et entre deux lots.

5. Service de tâches (task_service.py)

    - création d'une ligne task, mise à jour de l'état et de la
      progression ;
    - file d'événements par tâche, consommée par le flux SSE
      /tasks/{id}/stream ;
    - types d'événements conformes à l'openapi : state, progress, error,
      done ;
    - une tâche interrompue par un arrêt du backend est reprise à l'état
      persisté, jamais relancée depuis le début.

6. Recherche (retriever.py)

    async def retrieve(conn, query: str, k: int = 5,
                       year_min: int | None = None,
                       exclude_preprints: bool = False,
                       include_references: bool = False) -> list[ChunkHit]

    - embedding de la requête via embed_query, donc préfixe search_query
      appliqué par le service (US-005) ;
    - délégation à search_similar_chunks de US-002, y compris
      l'élargissement de k en présence de filtres ;
    - include_references est faux par défaut : les chunks de bibliographie
      sont exclus des résultats destinés à la rédaction.

7. Approbation

    POST /sources/{id}/approve : porte de validation humaine. Seules les
    sources approuvées sont ingérées et utilisables par le rédacteur.
    L'approbation est journalisée.

8. Jeux de test

    Les PDF de test sont GÉNÉRÉS par le code de test (reportlab ou PyMuPDF)
    dans backend/tests/fixtures/, jamais téléchargés : la suite doit passer
    hors ligne. Prévoir au minimum : un article multi-pages avec sections,
    un PDF sans couche texte, un PDF avec en-têtes récurrents.

9. Tests

    test_import_computes_sha256
    test_import_duplicate_returns_existing_source
    test_import_rejects_unsupported_extension
    test_extract_preserves_page_numbers
    test_extract_removes_recurring_headers
    test_extract_scanned_pdf_raises_actionable_error
    test_chunker_splits_on_section_boundaries
    test_chunker_falls_back_to_sliding_window
    test_chunker_never_crosses_document_boundary
    test_chunk_has_page_start_and_page_end       # bloquant
    test_references_section_marked_and_excluded
    test_ingest_transaction_per_source
    test_ingest_failed_source_does_not_block_pipeline
    test_ingest_retries_twice_then_marks_failed
    test_ingest_emits_progress_events
    test_ingest_cancellation_between_batches
    test_task_resumes_from_persisted_state
    test_retrieve_applies_query_prefix
    test_retrieve_excludes_references_by_default
    test_retrieve_year_filter
    test_retrieve_exclude_preprints
    test_unapproved_source_not_ingested

INTERDICTIONS

- Chunk sans pagination issu d'un PDF.
- Tentative d'OCR.
- Téléchargement d'un fichier de test.
- Retour de chunks de bibliographie à la rédaction.
- Ingestion d'une source non approuvée.
- Recalcul des embeddings hors du service de US-005.
- Modification d'un fichier hors périmètre.

CRITÈRES D'ACCEPTATION

    cd backend && pytest -q backend/tests/tests_rag backend/tests/tests_api -m "not integration"
    ruff check backend/ && ruff format --check backend/
    python scripts/check_sqlite_vec.py
    python scripts/check_no_cloud_calls.py

FORMAT DE RÉPONSE

1. Plan d'implémentation, 10 lignes maximum.
2. Contenu intégral de chaque fichier.
3. Sortie attendue des vérifications.
4. Section "Réserves".
```

## Message de commit attendu

```text
feat(rag): import de sources et pipeline d'ingestion vectorielle

Implémente US-102 (Backlog V0.3).

- Extraction PyMuPDF paginée, suppression des en-têtes récurrents,
  détection des PDF sans couche texte.
- Découpage aux frontières de sections, repli fenêtre glissante 512/64.
- Pagination obligatoire sur chaque chunk : condition de vérifiabilité
  des citations.
- Section References indexée à part, exclue de la rédaction.
- Transaction par source, deux essais, poursuite du pipeline en cas d'échec.
- Déduplication par SHA-256, approbation humaine avant ingestion.

Refs: US-102, ADR-002, ADR-013
```
