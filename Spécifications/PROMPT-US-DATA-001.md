# Prompt d'implémentation — US-DATA-001 (dépôt et gestion des jeux de données)

Conforme au Backlog V0.3, aux Spécifications V0.3 §8.2 et à ADR-005.

---

```text
Tu es un agent de codage senior, spécialisé en manipulation de données
scientifiques, formats tabulaires et bioinformatiques, et intégration à un
environnement d'exécution isolé.

SOURCES DE VÉRITÉ : ce prompt > ADR-005 (sandbox), ADR-001 (SQLite unique)
> Backlog V0.3 US-DATA-001 > Spécifications V0.3 §8.2.

DÉPENDANCES : US-101, US-004, US-401.

USER STORY

US-DATA-001 — En tant que chercheur, je veux déposer mes jeux de données
dans le projet, afin que les scripts d'analyse y accèdent de façon
reproductible.

DÉCISION STRUCTURANTE — LES DONNÉES NE VONT PAS DANS LA BASE

Les jeux de données restent des FICHIERS sur le disque, dans le répertoire
du projet. La base ne stocke que leurs métadonnées et leur SHA-256.

Motif : un jeu de données de séquençage pèse couramment plusieurs
gigaoctets. Le mettre dans le .sqlite ruinerait la propriété qui justifie
tout le modèle — une sauvegarde par copie de fichier rapide — et rendrait
le WAL ingérable. La sauvegarde de projet copie la base ; elle propose de
copier les données séparément, et le dit.

PÉRIMÈTRE

Dépôt, inventaire, profilage léger, SHA-256, montage en sandbox,
suppression protégée.

PAS de transformation de données, PAS de visualisation — cela relève des
scripts de US-401.

FICHIERS

- backend/app/data/registry.py
- backend/app/data/profiler.py
- backend/app/models/dataset.py
- backend/app/services/dataset_service.py
- backend/app/api/v1/datasets.py
- backend/app/db/schema.sql                     (modification : table dataset)
- frontend/src/app/features/data/datasets-panel.component.ts
- backend/tests/tests_data/
Aucun autre fichier.

EXIGENCES

1. Formats acceptés : .csv, .tsv, .xlsx, .json, .parquet, .fasta, .fastq,
   .vcf, .nc. Un format inconnu est accepté en « opaque » : déposé, monté,
   mais non profilé. Refuser un format inconnu serait arbitraire ; le
   profiler à tort serait faux.

2. Stockage : data/projects/{slug}/datasets/{sha256}/{nom_original}.
   Le SHA-256 dans le chemin déduplique naturellement et garantit qu'un
   même fichier redéposé n'est pas dupliqué.

   Table dataset(id, project_id, name, original_filename, sha256,
   size_bytes, format, rows, columns, profile_json, imported_at).

3. Profilage LÉGER, sans charger le fichier entier :
   - tabulaires : lecture des 10 000 premières lignes pour déduire les noms
     et types de colonnes, le nombre de colonnes, un échantillon de
     valeurs ; le nombre de lignes total est compté par lecture en flux,
     jamais par chargement en mémoire ;
   - FASTA/FASTQ : nombre de séquences, longueur moyenne ;
   - VCF : nombre de variants, échantillons déclarés dans l'en-tête ;
   - opaque : taille et type MIME seulement.

   Un fichier de 5 Go doit être profilé sans dépasser 200 Mo de mémoire.
   Un test le vérifie sur un fichier synthétique.

4. Montage en sandbox : le service produit un MountSpec en LECTURE SEULE
   vers le répertoire du dataset, chemin invité /data/{name}. Un script ne
   peut jamais écrire dans un jeu de données : les sorties vont dans le
   répertoire d'artefacts.

   Au niveau 1 (Wasm), le fichier est copié dans le système de fichiers
   virtuel ; au-delà de settings.wasm_max_dataset_mb (défaut 200), le
   montage est refusé avec un message proposant le niveau 2. Copier 3 Go
   dans un FS Wasm épuiserait la mémoire du navigateur.

5. Suppression : refusée avec 409 si le SHA-256 apparaît dans la
   traçabilité d'un artefact conservé (US-401). Supprimer les données
   d'origine d'une figure de thèse rendrait celle-ci irreproductible.

6. Sauvegarde : POST /projects/{id}/backup indique dans sa réponse la
   taille totale des jeux de données NON inclus dans la copie, et le chemin
   de leur répertoire. L'utilisateur doit savoir ce qu'il n'a pas
   sauvegardé.

7. Tests

   test_unknown_format_accepted_as_opaque
   test_sha256_deduplicates_redeposit
   test_profile_large_file_under_memory_cap
   test_row_count_streamed_not_loaded
   test_fasta_profile_sequence_count
   test_vcf_profile_samples_from_header
   test_mount_is_read_only
   test_wasm_mount_refused_above_size_cap
   test_wasm_refusal_suggests_level2
   test_delete_refused_when_referenced_by_artifact
   test_backup_reports_excluded_dataset_size

INTERDICTIONS

- Stocker le contenu d'un jeu de données dans la base.
- Charger un fichier entier pour le profiler.
- Monter un jeu de données en écriture.
- Supprimer un jeu de données référencé par un artefact.
- Laisser croire qu'une sauvegarde de projet inclut les données.

ACCEPTATION : pytest -q backend/tests/tests_data ; ruff ;
python scripts/check_sqlite_wal.py

FORMAT : plan 10 lignes, fichiers complets, sorties, "Réserves".
```
