# Prompt d'implémentation — US-IMPORT-001 (import DOI, BibTeX, RIS)

Conforme au Backlog V0.3, aux Spécifications V0.3 §10 et aux ADR-007, ADR-010.

---

```text
Tu es un agent de codage senior, spécialisé en formats bibliographiques,
analyse syntaxique tolérante et normalisation de métadonnées.

SOURCES DE VÉRITÉ : ce prompt > ADR-007 (.bib généré, jamais consommé pour
compiler), ADR-010 (consentement) > Backlog V0.3 US-IMPORT-001 >
Spécifications V0.3 §10.

DÉPENDANCES : US-BIBLIO-001 (normalisation, déduplication, providers),
US-102 (sources).

USER STORY

US-IMPORT-001 — En tant que chercheur, je veux importer mes références
existantes par DOI, fichier BibTeX ou RIS, afin de reprendre une
bibliographie déjà constituée sans la ressaisir.

RAPPEL D'ARCHITECTURE — À NE PAS CONFONDRE

Un fichier .bib importé alimente `source_document`. Il n'est JAMAIS utilisé
pour compiler le document : le .bib de compilation est regénéré à chaque
export à partir des seules citations vérifiées (ADR-007). Cette distinction
est la raison d'être du mécanisme ; ne l'affaiblis pas en conservant le
fichier importé comme source de compilation.

PÉRIMÈTRE

Import par DOI unique ou par lot, analyse BibTeX, analyse RIS,
réconciliation avec l'existant, rapport d'import.

PAS de synchronisation continue — c'est US-ZOTERO-001. PAS de
téléchargement de PDF.

FICHIERS

- backend/app/biblio/parsers/bibtex_parser.py
- backend/app/biblio/parsers/ris_parser.py
- backend/app/biblio/importer.py
- backend/app/api/v1/biblio.py                  (modification)
- frontend/src/app/features/sources/import-dialog.component.ts
- backend/tests/tests_biblio/test_import.py
- backend/tests/tests_biblio/fixtures/           (fichiers d'exemple)
Aucun autre fichier.

EXIGENCES

1. IMPORT PAR DOI. Un DOI ou une liste : résolution via Crossref puis
   OpenAlex en repli, à travers le client unique de US-BIBLIO-001 —
   consentement biblio_search requis. Un DOI irrésolvable est signalé, il
   n'interrompt pas le lot.

2. BIBTEX — ANALYSE TOLÉRANTE. Les fichiers .bib réels sont irréguliers :
   commandes LaTeX dans les titres, accolades déséquilibrées, champs
   `month = jan` sans guillemets, entrées `@string`, commentaires,
   encodages mêlés.

   L'analyseur doit :
   - accepter ces irrégularités et produire un résultat partiel plutôt
     qu'échouer sur le fichier entier ;
   - convertir les commandes LaTeX d'accentuation en Unicode
     (\'e vers é, \"o vers ö…) ;
   - préserver la protection de casse par accolades comme information,
     puisque le .bib généré la reconstruira (US-501) ;
   - résoudre les macros @string ;
   - rapporter chaque entrée non analysable avec son numéro de ligne.

   Ne pas écrire un analyseur maison si une bibliothèque éprouvée est
   disponible dans la liste de dépendances autorisée ; sinon, écrire un
   analyseur tolérant plutôt qu'une expression régulière.

3. RIS. Étiquettes à deux lettres, entrées multi-lignes, TY obligatoire.
   Correspondance des types RIS vers les types internes, JOUR vers article,
   BOOK vers book, THES vers thesis, RPRT vers report, autres vers misc.

4. RÉCONCILIATION. Chaque entrée importée est confrontée à l'existant par
   la déduplication de US-BIBLIO-001 — DOI, puis titre plus année à un an
   près. Trois issues :
   - nouvelle : créée avec approved_at nul ;
   - doublon exact : ignorée, comptée ;
   - doublon avec métadonnées divergentes : NI fusion automatique, NI
     rejet. L'entrée est mise en attente d'arbitrage, avec les deux
     versions champ par champ. L'utilisateur choisit. Fusionner
     automatiquement écraserait des corrections manuelles.

5. RAPPORT D'IMPORT, retourné et affiché : total lu, créées, doublons
   ignorés, en attente d'arbitrage, non analysables avec numéro de ligne et
   motif. Un import silencieux de 300 entrées dont 40 ont échoué est pire
   qu'un échec.

6. Les entrées importées ne sont jamais approuvées automatiquement : la
   validation humaine de US-102 reste requise avant ingestion.

7. Tests

   test_doi_batch_partial_failure_does_not_abort
   test_bibtex_latex_accents_converted
   test_bibtex_unbalanced_braces_partial_result
   test_bibtex_string_macros_resolved
   test_bibtex_unparseable_entry_reported_with_line
   test_bibtex_case_protection_preserved
   test_ris_multiline_fields
   test_ris_type_mapping
   test_exact_duplicate_ignored_and_counted
   test_divergent_duplicate_awaits_arbitration
   test_no_automatic_merge
   test_imported_entries_not_auto_approved
   test_import_report_counts_all_categories
   test_imported_bib_not_used_for_compilation

INTERDICTIONS

- Échouer sur un fichier entier à cause d'une entrée fautive.
- Fusionner automatiquement des métadonnées divergentes.
- Approuver automatiquement une source importée.
- Conserver le .bib importé comme source de compilation.
- Analyser le BibTeX par expression régulière.

ACCEPTATION : pytest -q backend/tests/tests_biblio ; ruff ;
python scripts/check_biblio_offline.py

FORMAT : plan 10 lignes, fichiers complets, sorties, "Réserves".
```
