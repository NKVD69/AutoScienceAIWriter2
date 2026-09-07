# ADR-001 — Base SQLite unique par projet, mode WAL, accès aiosqlite

- **Statut :** Accepté
- **Date :** 2026-09-05
- **Remplace :** rien
- **Lié à :** ADR-002, ADR-012

## Contexte

L'outil est local-first : aucun serveur de base de données ne peut être exigé de
l'utilisateur. Un projet de thèse doit se sauvegarder, s'archiver et se transmettre
au directeur de recherche sans procédure. Le backend est FastAPI, donc asynchrone :
plusieurs coroutines (ingestion RAG, journal d'audit, agents) écrivent en parallèle.

La V0.1 prévoyait une file d'écriture applicative (`DbWriteQueue`) pour sérialiser
les accès et éviter `database is locked`. Une file bloquante placée devant SQLite
bloque l'*event loop* de FastAPI et déplace le problème au lieu de le résoudre.

## Décision

1. **Un seul fichier `.sqlite` par projet**, contenant l'intégralité des données.
2. **Mode WAL** activé sur chaque connexion (`PRAGMA journal_mode=WAL`), avec
   `busy_timeout=5000` et `foreign_keys=ON`.
3. **Accès exclusivement via `aiosqlite`.** Aucun usage de `sqlite3` synchrone
   dans le code applicatif.
4. **Pas de `DbWriteQueue`.** WAL autorise des lectures concurrentes illimitées
   pendant une écriture ; `busy_timeout` absorbe la contention en écriture.

## Options écartées

| Option | Motif du rejet |
|---|---|
| PostgreSQL embarqué | Installation lourde, contraire au local-first sans Docker |
| DuckDB | Excellent en analytique, faible en écritures transactionnelles concurrentes |
| `DbWriteQueue` applicative | Bloque l'event loop ; réinvente ce que WAL fait déjà |
| SQLite synchrone + thread pool | Contention et complexité pour un gain nul face à WAL |

## Conséquences

**Positives.** Sauvegarde = copie d'un fichier. Intégrité référentielle native.
Empreinte disque et mémoire minimale. Pas de service à superviser.

**Négatives.** Écriture sérialisée par le moteur : un seul écrivain à la fois.
Acceptable pour un poste mono-utilisateur, à réévaluer si un mode multi-utilisateur
apparaît. Les fichiers `-wal` et `-shm` doivent être *checkpointés*
(`PRAGMA wal_checkpoint(TRUNCATE)`) avant toute copie de sauvegarde.

**Sur le code.** Toute opération multi-tables passe par une transaction explicite.
La sauvegarde de projet est une fonction applicative, pas une copie naïve.

## Vérification

`scripts/check_sqlite_wal.py` · `test_concurrent_writes_no_lock` ·
`test_backup_single_file_contains_everything` · interdiction `import sqlite3`
contrôlée par une règle `ruff` dans le code applicatif.
