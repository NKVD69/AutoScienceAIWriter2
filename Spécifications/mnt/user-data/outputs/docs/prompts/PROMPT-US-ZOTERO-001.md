# Prompt d'implémentation — US-ZOTERO-001 (synchronisation Zotero locale)

Priorité P2. Conforme au Backlog V0.3 et aux ADR-001, ADR-010.

---

```text
Tu es un agent de codage senior, spécialisé en intégration de bases locales
tierces, lecture sûre de SQLite et synchronisation unidirectionnelle.

SOURCES DE VÉRITÉ : ce prompt > ADR-010 (local strict), ADR-001 (SQLite),
ADR-007 (.bib généré) > Backlog V0.3 US-ZOTERO-001.

DÉPENDANCES : US-IMPORT-001 (normalisation, réconciliation), US-102.

USER STORY

US-ZOTERO-001 — En tant que chercheur, je veux retrouver ma bibliothèque
Zotero dans le projet, afin de ne pas maintenir deux bibliographies.

DÉCISION STRUCTURANTE — LECTURE SEULE ET UNIDIRECTIONNELLE

L'outil LIT la bibliothèque Zotero. Il n'y écrit jamais.

Motifs : la base `zotero.sqlite` est un format interne non documenté, sans
garantie de stabilité entre versions ; y écrire risquerait de corrompre des
années de collecte bibliographique. Et Zotero verrouille sa base quand il
est ouvert.

Toute demande future de synchronisation bidirectionnelle passera par
l'API HTTP officielle de Zotero, pas par le fichier. Écris-le en
commentaire de tête du module pour que la question ne se repose pas.

PÉRIMÈTRE

Localisation du profil, lecture hors ligne, correspondance des types,
collections, import sélectif, resynchronisation.

PAS d'écriture dans Zotero. PAS d'API Zotero en ligne. PAS de gestion des
pièces jointes PDF au-delà de leur chemin.

FICHIERS

- backend/app/biblio/zotero/locator.py
- backend/app/biblio/zotero/reader.py
- backend/app/biblio/zotero/mapper.py
- backend/app/services/zotero_service.py
- backend/app/api/v1/zotero.py
- frontend/src/app/features/sources/zotero-dialog.component.ts
- backend/tests/tests_biblio/test_zotero.py
- backend/tests/tests_biblio/fixtures/zotero_sample.sqlite
Aucun autre fichier.

EXIGENCES

1. LOCALISATION. Chemins par défaut selon la plateforme —
   %USERPROFILE%\Zotero\zotero.sqlite sous Windows,
   ~/Zotero/zotero.sqlite sous Linux — surchargables par configuration.
   L'absence de fichier désactive la fonctionnalité, sans erreur.

2. LECTURE SÛRE. Ouverture en mode `file:...?mode=ro&immutable=1`.

   Zotero verrouille sa base quand il est ouvert : si la lecture échoue
   pour verrou, COPIER le fichier dans un répertoire temporaire et lire la
   copie. Ne jamais attendre la libération du verrou, ne jamais demander à
   l'utilisateur de fermer Zotero.

   Aucune écriture, aucun PRAGMA modifiant l'état, aucune migration.

3. MODÈLE ZOTERO. Le schéma est en entité-attribut-valeur : `items`,
   `itemData`, `fields`, `itemDataValues`, `creators`, `collections`,
   `collectionItems`, `deletedItems`. Reconstituer une référence exige
   plusieurs jointures.

   - exclure les éléments de `deletedItems` — corbeille Zotero ;
   - exclure les pièces jointes et notes, types `attachment` et `note` ;
   - récupérer le chemin des pièces jointes PDF pour PROPOSER un
     rattachement, sans copier automatiquement le fichier.

4. CORRESPONDANCE DES TYPES. journalArticle vers article, book vers book,
   thesis vers thesis, report vers report, preprint vers preprint avec
   is_preprint vrai, conferencePaper vers article, autres vers misc.

5. IMPORT SÉLECTIF par collection. L'utilisateur choisit une ou plusieurs
   collections ; l'import de la bibliothèque entière est possible mais
   n'est pas le défaut : une bibliothèque Zotero de dix ans contient
   souvent des milliers d'entrées sans rapport avec la thèse en cours.

6. RÉCONCILIATION par le mécanisme de US-IMPORT-001 : doublon exact ignoré,
   divergence mise en arbitrage, jamais de fusion automatique. Les entrées
   importées ne sont pas approuvées automatiquement.

7. RESYNCHRONISATION. Une nouvelle lecture n'écrase rien : elle ajoute les
   nouveautés et signale les divergences. Une entrée SUPPRIMÉE dans Zotero
   n'est jamais supprimée du projet — elle peut être citée dans une section
   déjà rédigée. Elle est signalée comme absente de la source.

8. Tests

   test_missing_zotero_disables_feature_silently
   test_opened_read_only_immutable
   test_locked_database_falls_back_to_copy
   test_no_write_to_zotero_database
   test_deleted_items_excluded
   test_attachments_and_notes_excluded
   test_pdf_path_proposed_not_copied
   test_eav_join_reconstructs_reference
   test_type_mapping_including_preprint
   test_collection_selective_import
   test_full_library_not_default
   test_duplicate_handled_by_import_reconciliation
   test_not_auto_approved
   test_resync_adds_without_overwriting
   test_deleted_in_zotero_not_deleted_in_project

INTERDICTIONS

- Écrire dans la base Zotero.
- Attendre la libération d'un verrou.
- Demander à l'utilisateur de fermer Zotero.
- Importer la bibliothèque entière par défaut.
- Supprimer une source parce qu'elle a disparu de Zotero.
- Copier automatiquement les PDF joints.

ACCEPTATION : pytest -q backend/tests/tests_biblio ; ruff ;
python scripts/check_no_cloud_calls.py

FORMAT : plan 10 lignes, fichiers complets, sorties, "Réserves".
```
