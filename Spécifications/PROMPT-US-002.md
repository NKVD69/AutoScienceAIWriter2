# Prompt d'implémentation — US-002 (schéma SQLite unique + sqlite-vec)

À placer dans `docs/prompts/03-story-prompts.md`, section US-002.
Prêt à copier-coller dans Qwen 3.8 Max, Cursor, Claude Code ou Copilot Workspace.

> **Attention :** ce prompt implémente la **version révisée** de US-002 (Backlog V0.3). Il corrige le défaut D-01 du backlog V0.2, qui plaçait une clé étrangère sur une table virtuelle `vec0` — contrainte que SQLite ignore silencieusement. Ne pas utiliser l'ancienne version.

---

```text
Tu es un agent de codage senior, spécialisé en SQLite, Python asynchrone, recherche vectorielle et architectures local-first.

Tu interviens sur le projet :

Science AI Writer IDE — IDE scientifique local multi-agents pour la rédaction de documents académiques de 30 à 300 pages.

SOURCES DE VÉRITÉ, PAR ORDRE DE PRIORITÉ DÉCROISSANTE

1. Ce prompt.
2. ADR-002 (sqlite-vec) et ADR-001 (SQLite unique, WAL, aiosqlite).
3. Backlog V0.3, user story US-002 révisée.
4. Spécifications techniques V0.2, section 6 (modèle de données).
5. Cahier des charges V3.

En cas de conflit, l'échelon supérieur l'emporte. Tu ne réinterprètes pas une décision figée dans un ADR : si tu penses qu'elle est mauvaise, tu l'appliques et tu ouvres une section "Réserves" en fin de rapport.

USER STORY À IMPLÉMENTER

US-002 — Schéma SQLite unique avec extension sqlite-vec.

Objectif : un seul fichier .sqlite par projet contenant à la fois les données
relationnelles et les vecteurs, avec une intégrité référentielle réellement
appliquée par le moteur.

PÉRIMÈTRE STRICT

Tu implémentes uniquement :

- le chargement de l'extension sqlite-vec sur une connexion aiosqlite ;
- le schéma relationnel complet du projet ;
- la table virtuelle vectorielle et son trigger de cascade ;
- les index ;
- le mécanisme de migration initial ;
- une couche d'accès minimale pour insérer un chunk avec son embedding et
  exécuter une recherche KNN avec provenance ;
- les tests correspondants ;
- le script de vérification scripts/check_sqlite_vec.py.

Tu n'implémentes PAS :

- le calcul des embeddings (US-005) ;
- l'extraction de texte des PDF (US-102) ;
- les endpoints métier (US-101) ;
- le LLM, les agents, LangGraph ;
- la sandbox ;
- le frontend ;
- l'export.

Si une de ces briques te manque pour tester, tu utilises des vecteurs
générés aléatoirement et des textes factices. Tu n'ajoutes aucune dépendance
hors de la liste ci-dessous.

CONTRAINTE ARCHITECTURALE CENTRALE — À LIRE AVANT D'ÉCRIRE UNE LIGNE

SQLite n'applique PAS les contraintes de clé étrangère déclarées sur une
table virtuelle. vec0 est une table virtuelle. Toute clause REFERENCES
écrite dans un CREATE VIRTUAL TABLE ... USING vec0 sera acceptée
syntaxiquement puis ignorée à l'exécution.

Le schéma impose donc une séparation stricte :

- l'intégrité référentielle vit dans une table relationnelle ordinaire
  nommée chunk ;
- les vecteurs vivent dans une table virtuelle nommée vec_chunk ne
  contenant QUE la colonne embedding ;
- le lien entre les deux est l'égalité des rowid :
      vec_chunk.rowid == chunk.id
  Cet invariant doit être écrit en commentaire en tête de schema.sql et
  vérifié par un test.
- la suppression en cascade des vecteurs est assurée par un TRIGGER, car
  ON DELETE CASCADE ne traverse pas la table virtuelle.

Tu ne dévies pas de ce modèle.

FICHIERS À CRÉER OU MODIFIER

- backend/app/db/schema.sql
- backend/app/db/migrations/__init__.py
- backend/app/db/migrations/001_initial.sql
- backend/app/db/migrations/runner.py
- backend/app/db/vector.py
- backend/app/db/session.py            (modification : chargement extension)
- backend/app/core/config.py           (modification : embedding_dim)
- backend/tests/tests_db/test_schema.py
- backend/tests/tests_db/test_vector.py
- backend/tests/tests_db/test_migrations.py
- scripts/check_sqlite_vec.py

Aucun autre fichier.

EXIGENCES D'IMPLÉMENTATION

1. Chargement de l'extension

Dans backend/app/db/session.py, après ouverture de chaque connexion et
après les PRAGMA de US-001 :

    import sqlite_vec
    await conn.enable_load_extension(True)
    await conn.load_extension(sqlite_vec.loadable_path())
    await conn.enable_load_extension(False)

Certains interpréteurs Python sont livrés avec un binaire SQLite compilé
sans support des extensions. Tu dois détecter ce cas au démarrage et lever
une exception applicative dédiée, ExtensionLoadError, portant un message
actionnable qui indique :

- que le module sqlite3 de l'interpréteur courant ne supporte pas les
  extensions ;
- la remédiation : installer python.org officiel, ou pysqlite3-binary.

Tu ne contournes pas silencieusement ce cas et tu ne bascules pas sur une
recherche vectorielle en Python pur.

Tu exposes une fonction async vec_version(conn) -> str retournant le
résultat de SELECT vec_version().

2. Schéma relationnel

Tables à créer dans backend/app/db/schema.sql, toutes dans le même fichier
de base de données :

    project(id, name, subject, discipline, language, academic_level,
            created_at, updated_at)

    source_document(id, project_id -> project.id ON DELETE CASCADE,
            kind, title, authors, year, doi, url, venue, is_preprint,
            file_path, sha256, imported_at, approved_at)

    chunk(id, source_id -> source_document.id ON DELETE CASCADE,
            ordinal, text, page_start, page_end, token_count,
            UNIQUE(source_id, ordinal))

    plan(id, project_id -> project.id ON DELETE CASCADE,
            problematique, status, version, created_at)

    plan_node(id, plan_id -> plan.id ON DELETE CASCADE,
            parent_id -> plan_node.id ON DELETE CASCADE,
            ordinal, level, title, objective, target_words)

    draft_section(id, plan_node_id -> plan_node.id ON DELETE CASCADE,
            content_qmd, status, quality_score, version,
            generated_at, validated_at)

    citation(id, draft_section_id -> draft_section.id ON DELETE CASCADE,
            source_id -> source_document.id ON DELETE RESTRICT,
            chunk_id -> chunk.id ON DELETE SET NULL,
            bibtex_key, locator, verified)

    code_execution(id, project_id -> project.id ON DELETE CASCADE,
            origin, sandbox_level, code, stdout, stderr, exit_code,
            duration_ms, started_at)

    task(id, project_id -> project.id ON DELETE CASCADE,
            state, agent, payload_json, retry_count, created_at, updated_at)

    audit_log(id, project_id, event_type, payload_json, prev_hash, hash,
            created_at)

    consent(id, project_id -> project.id ON DELETE CASCADE,
            scope, granted, granted_at, details)

    model_config(id, project_id -> project.id ON DELETE CASCADE,
            llm_model, llm_quant, embedding_model, embedding_dim,
            created_at)

Règles :

- clés primaires INTEGER PRIMARY KEY ;
- horodatages en TEXT ISO-8601 UTC ;
- booléens en INTEGER 0/1 ;
- toutes les colonnes REFERENCES portent une clause ON DELETE explicite ;
- pas de colonne JSON sans suffixe _json.

3. Table vectorielle et cascade

    CREATE VIRTUAL TABLE vec_chunk USING vec0(
      embedding float[768]
    );

    CREATE TRIGGER chunk_after_delete AFTER DELETE ON chunk
    BEGIN
      DELETE FROM vec_chunk WHERE rowid = old.id;
    END;

La dimension 768 doit provenir de la configuration (settings.embedding_dim,
défaut 768) et non être codée en dur ailleurs que dans le SQL généré. Au
démarrage, si model_config.embedding_dim d'un projet existant diffère de
settings.embedding_dim, lever DimensionMismatchError.

4. Index

    CREATE INDEX idx_source_project      ON source_document(project_id);
    CREATE INDEX idx_source_doi          ON source_document(doi);
    CREATE INDEX idx_chunk_source        ON chunk(source_id);
    CREATE INDEX idx_plan_node_plan      ON plan_node(plan_id, ordinal);
    CREATE INDEX idx_draft_node          ON draft_section(plan_node_id);
    CREATE INDEX idx_citation_section    ON citation(draft_section_id);
    CREATE INDEX idx_task_state          ON task(project_id, state);
    CREATE INDEX idx_audit_project       ON audit_log(project_id, id);

5. Migrations

backend/app/db/migrations/runner.py doit :

- créer si absente la table schema_migration(version INTEGER PRIMARY KEY,
  applied_at TEXT) ;
- appliquer dans l'ordre les fichiers NNN_*.sql non encore enregistrés ;
- exécuter chaque migration dans une transaction ;
- être idempotent : un second appel n'applique rien ;
- refuser de démarrer si un fichier de migration déjà appliqué a changé de
  somme SHA-256.

6. Couche d'accès vectorielle

backend/app/db/vector.py expose :

    async def insert_chunk_with_embedding(
        conn, source_id: int, ordinal: int, text: str,
        embedding: list[float], page_start: int | None = None,
        page_end: int | None = None,
    ) -> int

        Insère dans chunk, récupère l'id, insère dans vec_chunk avec
        rowid = cet id, le tout dans UNE transaction. Retourne l'id.
        Lève ValueError si len(embedding) != settings.embedding_dim.

    async def search_similar_chunks(
        conn, query_embedding: list[float], k: int = 5,
        project_id: int | None = None,
        year_min: int | None = None,
        exclude_preprints: bool = False,
    ) -> list[ChunkHit]

        Recherche KNN retournant, pour chaque résultat :
        chunk_id, source_id, text, page_start, page_end, distance,
        source_title, source_year, source_doi, is_preprint.

        Les filtres relationnels s'appliquent par jointure sur chunk et
        source_document. Tu documentes en commentaire que sqlite-vec
        applique le KNN avant la jointure : si des filtres sont fournis,
        tu élargis k en interne (facteur 4, plafonné à 200) puis tu
        tronques à k après filtrage.

    ChunkHit est un modèle Pydantic.

Sérialisation des vecteurs : utiliser sqlite_vec.serialize_float32().
Ne pas passer une liste Python brute.

7. Tests

Tu écris au minimum les tests suivants, tous en pytest-asyncio, sur une base
temporaire par test (tmp_path) :

    test_load_sqlite_vec_extension
    test_vec_version_not_empty
    test_foreign_keys_enforced_on_chunk        (source_id invalide -> IntegrityError)
    test_rowid_invariant_chunk_vec             (chunk.id == vec_chunk.rowid)
    test_join_chunk_vec_returns_row
    test_cascade_delete_source_removes_chunks
    test_cascade_delete_source_removes_vectors (via trigger)
    test_insert_rejects_wrong_dimension
    test_knn_returns_k_results_with_provenance
    test_knn_with_year_filter
    test_knn_excludes_preprints
    test_backup_single_file_contains_everything (checkpoint WAL puis copie)
    test_migrations_idempotent
    test_migrations_detect_modified_file
    test_unique_source_ordinal

8. Script de vérification

scripts/check_sqlite_vec.py : ouvre une base temporaire, charge l'extension,
crée le schéma, insère 100 vecteurs aléatoires de dimension 768, exécute une
recherche KNN, vérifie l'invariant rowid, supprime une source et vérifie que
les vecteurs correspondants ont disparu. Sortie 0 si tout passe, 1 sinon,
avec un rapport lisible sur stdout.

DÉPENDANCES AUTORISÉES

aiosqlite, sqlite-vec, pydantic, pydantic-settings, pytest, pytest-asyncio.
Aucune autre. Pas de SQLAlchemy, pas d'Alembic, pas de ChromaDB, pas de
numpy dans le code applicatif.

INTERDICTIONS EXPLICITES

- Aucune clause REFERENCES dans le CREATE VIRTUAL TABLE.
- Aucun second fichier de base de données.
- Aucun accès sqlite3 synchrone dans le code applicatif.
- Aucune écriture dans vec_chunk sans écriture correspondante dans chunk.
- Aucun appel réseau.
- Aucune modification de fichier hors de la liste du périmètre.

CRITÈRES D'ACCEPTATION AUTOMATISÉS

    cd backend && pytest -q
    ruff check backend/ && ruff format --check backend/
    python scripts/check_sqlite_vec.py
    python scripts/check_sqlite_wal.py
    python scripts/check_no_cloud_calls.py

Les cinq commandes doivent sortir en code 0.

FORMAT DE TA RÉPONSE

1. Un plan d'implémentation en 10 lignes maximum.
2. Le contenu intégral de chaque fichier, un bloc de code par fichier,
   précédé de son chemin exact.
3. La sortie attendue des cinq commandes de vérification.
4. Une section "Réserves" listant tout point où tu estimes la spécification
   discutable — sans l'avoir modifiée unilatéralement.

Tu ne poses pas de question préalable : la spécification est complète.
Si une ambiguïté subsiste, tu retiens l'option la plus conservatrice et tu la
signales en Réserves.
```

---

## Message de commit attendu

```text
feat(db): schéma SQLite unique avec sqlite-vec et intégrité référentielle

Implémente US-002 (Backlog V0.3).

- Table relationnelle `chunk` porteuse des FK, table virtuelle `vec_chunk`
  limitée à l'embedding, liées par l'invariant rowid.
- Cascade des vecteurs par trigger AFTER DELETE (ON DELETE CASCADE ne
  traverse pas les tables virtuelles).
- Runner de migrations idempotent avec contrôle SHA-256.
- Recherche KNN avec provenance et filtres relationnels.

Refs: US-002, ADR-001, ADR-002
Corrige: D-01 (FK déclarée sur table virtuelle dans le backlog V0.2)
```
