# Prompt d'implémentation — US-101 (CRUD projets)

Section US-101 de `docs/prompts/03-story-prompts.md`.
Conforme au Backlog V0.3, aux Spécifications V0.3 §4 et à ADR-001.

---

```text
Tu es un agent de codage senior, spécialisé en FastAPI, SQLite asynchrone,
conception d'API REST et tests d'intégration.

PROJET

Science AI Writer IDE — IDE scientifique local multi-agents. Un fichier
.sqlite par projet, aucun service externe, sans Docker.

SOURCES DE VÉRITÉ, PAR ORDRE DÉCROISSANT

1. Ce prompt.
2. ADR-001 (SQLite unique, WAL, aiosqlite), ADR-010 (local strict).
3. Backlog V0.3, US-101.
4. Spécifications V0.3, §4.
5. contracts/openapi.yaml, sections /projects.

DÉPENDANCES REQUISES : US-001 (session aiosqlite), US-002 (schéma).

USER STORY

US-101 — En tant que chercheur, je veux créer, consulter, modifier,
supprimer et sauvegarder mes projets, afin de travailler sur plusieurs
documents indépendants.

POINT D'ARCHITECTURE À COMPRENDRE

Un projet correspond à UN FICHIER .sqlite distinct, pas à une ligne dans une
base commune. Il existe donc deux niveaux :

- un registre global, data/registry.sqlite, qui liste les projets connus,
  leur nom et le chemin de leur fichier ;
- un fichier par projet, data/projects/{slug}.sqlite, contenant le schéma
  complet de US-002 et l'unique ligne de la table project.

Ce découpage est ce qui rend la sauvegarde triviale : copier un fichier
transporte l'intégralité d'un projet, sources, chunks, vecteurs, plan,
sections, audit compris. Ne le contourne pas en plaçant tous les projets
dans une base unique.

PÉRIMÈTRE STRICT

Tu implémentes :

- le registre global et sa migration ;
- la résolution projet -> connexion, avec un cache de connexions ;
- les opérations CRUD ;
- la sauvegarde par copie de fichier avec checkpoint WAL ;
- l'endpoint /system/capabilities ;
- les endpoints correspondants et leurs tests.

Tu n'implémentes PAS :

- les sources et l'ingestion (US-102) ;
- le plan, les sections, l'export ;
- l'authentification et les rôles (US-AUTH-001) ;
- le frontend.

FICHIERS À CRÉER OU MODIFIER

- backend/app/db/registry.py
- backend/app/db/registry_schema.sql
- backend/app/db/pool.py
- backend/app/models/project.py
- backend/app/services/project_service.py
- backend/app/api/v1/projects.py
- backend/app/api/v1/system.py
- backend/app/api/v1/__init__.py            (modification : routeurs)
- backend/app/core/config.py                (modification)
- backend/app/core/errors.py                (modification)
- backend/tests/tests_api/test_projects_api.py
- backend/tests/tests_db/test_registry.py
- backend/tests/tests_db/test_pool.py

Aucun autre fichier.

EXIGENCES D'IMPLÉMENTATION

1. Registre global (registry.py, registry_schema.sql)

    CREATE TABLE project_ref (
      id          INTEGER PRIMARY KEY,
      slug        TEXT NOT NULL UNIQUE,
      name        TEXT NOT NULL,
      db_path     TEXT NOT NULL UNIQUE,
      created_at  TEXT NOT NULL,
      last_opened TEXT
    );

    Le slug est dérivé du nom : minuscules, ASCII, tirets, longueur 60 max,
    suffixe numérique en cas de collision. Il ne change jamais après
    création, même si le nom est modifié — le chemin du fichier en dépend.

2. Cache de connexions (pool.py)

    - une connexion aiosqlite par projet ouvert, réutilisée ;
    - PRAGMA de US-001 et chargement de sqlite-vec de US-002 appliqués à
      l'ouverture ;
    - fermeture explicite au delete et à l'arrêt de l'application ;
    - éviction LRU au-delà de settings.max_open_projects (défaut 5) ;
    - get_project_conn(project_id) lève ProjectNotFoundError si le fichier
      est absent du disque alors que le registre le référence — cas réel
      quand l'utilisateur déplace ou supprime un fichier à la main. Le
      message propose de retirer l'entrée du registre.

3. Création d'un projet

    Séquence, dans cet ordre :
      1. valider le corps de requête ;
      2. calculer le slug et le chemin ;
      3. créer le fichier .sqlite et y appliquer toutes les migrations ;
      4. insérer la ligne project et la ligne model_config
         (llm_model, embedding_model, embedding_dim issus de la config) ;
      5. insérer l'entrée du registre ;
      6. journaliser l'événement PROJECT_CREATED dans l'audit du projet.

    Si une étape échoue après la 3, le fichier partiellement créé est
    supprimé : pas de projet fantôme sur le disque.

4. Suppression

    - fermeture de la connexion, retrait du registre, puis déplacement du
      fichier vers data/trash/{slug}-{horodatage}.sqlite ;
    - JAMAIS de suppression définitive : un mémoire représente des mois de
      travail. La purge de la corbeille est une action utilisateur
      explicite, hors périmètre de cette story.

5. Sauvegarde

    POST /projects/{id}/backup :
      1. PRAGMA wal_checkpoint(TRUNCATE) sur la connexion du projet ;
      2. copie atomique vers data/backups/{slug}-{horodatage}.sqlite
         (écriture dans un fichier temporaire puis renommage) ;
      3. retour du chemin, de la taille et de l'horodatage.

    Un test doit vérifier que la copie contient bien les données
    vectorielles, pas seulement les tables relationnelles.

6. Capacités (api/v1/system.py)

    GET /system/capabilities conforme au schéma Capabilities de
    l'openapi.yaml. Chaque détection est indépendante et ne doit jamais
    lever : une dépendance absente retourne false ou null, jamais une
    erreur 500. C'est ce endpoint qui permet au frontend de désactiver une
    fonctionnalité plutôt que de la laisser échouer à l'usage.

    native_network_isolation_guaranteed est false sous Windows, sans
    condition (ADR-005).

7. API

    Conforme à contracts/openapi.yaml. Les codes de statut, les noms de
    champs et les formes d'erreur du contrat font foi : si ton
    implémentation diverge, c'est ton implémentation qui est fautive.

    Erreurs : Error pour 404 et 422, ConflictDetail pour 409.

8. Tests

    test_create_project_creates_dedicated_sqlite_file
    test_create_project_applies_all_migrations
    test_create_project_inserts_model_config
    test_create_project_rolls_back_file_on_failure
    test_slug_collision_gets_numeric_suffix
    test_slug_stable_after_rename
    test_list_projects_from_registry
    test_get_project_404_when_absent
    test_delete_moves_file_to_trash_not_unlink
    test_delete_closes_connection
    test_backup_checkpoints_wal_before_copy
    test_backup_copy_contains_vectors
    test_pool_reuses_connection
    test_pool_evicts_lru_beyond_limit
    test_missing_file_raises_actionable_error
    test_capabilities_never_500_when_dependency_absent
    test_capabilities_windows_network_isolation_false

INTERDICTIONS

- Base unique regroupant plusieurs projets.
- Suppression définitive d'un fichier de projet.
- Copie de sauvegarde sans checkpoint WAL préalable.
- Erreur 500 dans /system/capabilities.
- Divergence par rapport à contracts/openapi.yaml.
- Modification d'un fichier hors périmètre.

CRITÈRES D'ACCEPTATION

    cd backend && pytest -q -m "not integration"
    ruff check backend/ && ruff format --check backend/
    python scripts/check_sqlite_wal.py
    python scripts/check_no_cloud_calls.py

FORMAT DE RÉPONSE

1. Plan d'implémentation, 10 lignes maximum.
2. Contenu intégral de chaque fichier.
3. Sortie attendue des vérifications.
4. Section "Réserves".
```

## Message de commit attendu

```text
feat(projects): CRUD projets, registre global et sauvegarde par fichier

Implémente US-101 (Backlog V0.3).

- Registre data/registry.sqlite + un fichier .sqlite dédié par projet.
- Cache de connexions LRU avec PRAGMA et sqlite-vec appliqués à l'ouverture.
- Création transactionnelle : aucun projet fantôme en cas d'échec.
- Suppression vers corbeille, jamais unlink.
- Sauvegarde avec wal_checkpoint(TRUNCATE) préalable.
- /system/capabilities tolérant aux dépendances absentes.

Refs: US-101, ADR-001, ADR-005
```
