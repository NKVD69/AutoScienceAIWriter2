# Prompt d'implémentation — US-501 et US-502 (bibliographie dynamique et export Quarto)

Sections US-501 et US-502 de `docs/prompts/03-story-prompts.md`.
Livrées ensemble : la compilation BibTeX est une étape du pipeline d'export, elle n'a pas de valeur isolée.

Conforme au Backlog V0.3, aux Spécifications V0.3 §9 et aux ADR-006, ADR-007.

---

```text
Tu es un agent de codage senior, spécialisé en chaînes de publication
scientifique, Pandoc, Quarto, LaTeX et gestion bibliographique.

PROJET

Science AI Writer IDE — production de mémoires et thèses de 30 à 300 pages,
en local, sans Docker.

SOURCES DE VÉRITÉ, PAR ORDRE DÉCROISSANT

1. Ce prompt.
2. ADR-006 (Quarto canonique), ADR-007 (.bib généré), ADR-012 (pas de
   Docker), ADR-010 (local strict).
3. Backlog V0.3, US-501 et US-502.
4. Spécifications V0.3, §9.
5. contracts/openapi.yaml, section /export.

DÉPENDANCES REQUISES : US-301 (sections et citations), US-101 (projets).

USER STORIES

US-501 — Regénérer à chaque export un fichier .bib contenant exactement les
sources citées, afin qu'aucune entrée orpheline ni aucune citation
manquante n'apparaisse dans le document rendu.

US-502 — Compiler le document en PDF, DOCX et HTML avec renvois croisés et
bibliographie, afin de produire un livrable soutenable.

CONTEXTE DE LA DÉCISION

Le format canonique est Quarto (.qmd), PAS MyST. Une spécification
antérieure prévoyait « MyST canonique compilé via Quarto » : cette chaîne
n'existe pas. MyST relève de Sphinx et Jupyter Book, ses renvois s'écrivent
{ref} et {numref} ; Quarto s'appuie sur Pandoc et ses renvois s'écrivent
@fig-, @tbl-, @eq-, @sec-. Mélanger les deux produit des renvois non
résolus et des figures non numérotées sur un document long — précisément ce
que l'abandon du Markdown standard visait à éviter.

Tu n'écris aucun code de conversion MyST, aucun repli MyST, aucune
dépendance Sphinx.

PÉRIMÈTRE STRICT

Tu implémentes :

- la génération du fichier .bib depuis la base ;
- la normalisation et la déduplication des clés ;
- l'assemblage du document et la génération de _quarto.yml ;
- l'appel à Quarto et l'analyse de son journal ;
- la détection des renvois non résolus ;
- l'archivage des artefacts d'export ;
- les tests et le script de vérification.

Tu n'implémentes PAS :

- l'annexe de déclaration d'usage de l'IA (US-EXPORT-003 : tu poses
  seulement le point d'extension et le paramètre d'API) ;
- la table des matières avancée, le glossaire et l'index
  (US-EXPORT-001) ;
- les modèles académiques paramétrables (US-EXPORT-002 : un modèle par
  défaut suffit) ;
- l'anti-plagiat (US-601).

FICHIERS À CRÉER OU MODIFIER

- backend/app/export/bibliography.py
- backend/app/export/assembler.py
- backend/app/export/quarto.py
- backend/app/export/templates/default/_quarto.yml.j2
- backend/app/export/templates/default/references.csl
- backend/app/services/export_service.py
- backend/app/api/v1/export.py
- backend/app/api/v1/__init__.py               (modification)
- backend/app/core/config.py                   (modification)
- backend/tests/tests_export/__init__.py
- backend/tests/tests_export/test_bibliography.py
- backend/tests/tests_export/test_assembler.py
- backend/tests/tests_export/test_quarto.py
- scripts/check_quarto_export.py

Aucun autre fichier.

EXIGENCES D'IMPLÉMENTATION — US-501

1. Sélection des entrées

    Le .bib contient EXACTEMENT les source_document référencés par une
    citation verified = 1 appartenant à une draft_section incluse dans
    l'export. Ni plus, ni moins.

    - une section de statut ORPHANED n'est jamais incluse ;
    - une citation verified = 0 fait ÉCHOUER l'export avec un
      ConflictDetail nommant la section, la clé et le passage. Ne jamais
      l'ignorer silencieusement, ne jamais l'inclure quand même ;
    - une entrée présente dans un .bib importé par l'utilisateur mais non
      citée n'apparaît pas. Le .bib utilisateur alimente source_document à
      l'import ; il n'est jamais utilisé pour compiler (ADR-007).

2. Normalisation des clés

    Forme : premierauteur_annee_motclefdutitre, ASCII, minuscules, sans
    ponctuation. Exemple : dupont_2019_genomique.

    - collision : suffixe alphabétique a, b, c dans l'ordre stable des
      identifiants de source ;
    - auteur inconnu : anon ; année inconnue : nd ;
    - la clé retenue est ÉCRITE dans source_document.bibtex_key et
      réutilisée telle quelle aux exports suivants : une clé qui change
      d'un export à l'autre casse les renvois d'un document déjà relu.

3. Génération du fichier

    - un type BibTeX par kind : article, book, phdthesis, techreport,
      misc ;
    - échappement LaTeX des caractères spéciaux dans les titres et les
      noms ;
    - protection de la casse des acronymes par accolades : {ADN}, {IRM} ;
    - les prépublications reçoivent le champ note = {Prépublication, non
      révisé par les pairs}. Ce marquage traverse tout le pipeline sans
      exception ;
    - tri par clé, sortie déterministe : deux exports du même état
      produisent deux fichiers identiques octet pour octet. Un test le
      vérifie.

EXIGENCES D'IMPLÉMENTATION — US-502

4. Assemblage (export/assembler.py)

    - parcours du plan dans l'ordre, concaténation des content_qmd ;
    - chaque nœud produit un titre de niveau correspondant à sa profondeur,
      avec un identifiant de section stable {#sec-<slug>} permettant les
      renvois ;
    - un nœud sans section rédigée insère un marqueur visible
      « [section non rédigée] » et est SIGNALÉ dans le rapport d'export.
      L'export n'échoue pas pour autant : un aperçu partiel est utile ;
    - les artefacts de code (figures) sont copiés dans le répertoire de
      compilation et référencés en chemin relatif.

5. Génération de _quarto.yml

    Depuis un gabarit Jinja et les métadonnées du projet : titre, auteur,
    langue, date, bibliographie, csl, formats demandés, numérotation des
    sections, profondeur de la table des matières.

    Points impératifs :
    - bibliography pointe vers le .bib généré à cet export, pas vers un
      chemin fixe ;
    - lang est renseigné depuis project.language : sans lui, les
      libellés « Figure », « Tableau », « Références » restent en anglais
      dans un mémoire français ;
    - number-sections vrai, crossref configuré.

6. Appel à Quarto (export/quarto.py)

    - binaire détecté par settings.quarto_path ou dans le PATH ;
    - version minimale vérifiée ; en cas d'absence ou de version
      insuffisante, lever QuartoUnavailableError avec la procédure
      d'installation pour Windows et Linux ;
    - exécution en sous-processus avec timeout
      settings.export_timeout_seconds (défaut 600) ;
    - le journal de Quarto est CAPTURÉ ET ANALYSÉ, pas seulement transmis :

        * toute occurrence de renvoi non résolu produit un rapport
          d'export en échec partiel, avec la liste des identifiants
          concernés ;
        * toute citation non résolue par citeproc est traitée de même ;
        * une erreur LaTeX est remontée avec les vingt lignes de contexte
          autour de l'échec, pas avec le journal entier de 4000 lignes.

    - TinyTeX pour la chaîne PDF ; si un paquet LaTeX manque, le message
      d'erreur nomme le paquet et la commande d'installation.

7. Artefacts et archivage

    Chaque export produit un répertoire daté contenant : le .qmd assemblé,
    le .bib généré, le _quarto.yml, les figures, les sorties demandées et un
    rapport JSON. Le rapport porte : sections incluses, sections manquantes,
    nombre d'entrées bibliographiques, renvois non résolus, durée,
    version de Quarto.

    Ce répertoire est la preuve de ce qui a été produit : il n'est jamais
    écrasé par un export ultérieur.

8. Point d'extension pour la déclaration d'usage de l'IA

    Le paramètre include_ai_declaration de l'API est accepté et stocké dans
    le rapport. L'assembleur expose un point d'insertion en fin de document.
    L'implémentation du contenu relève de US-EXPORT-003 : tu ne la rédiges
    pas.

9. Script de vérification (scripts/check_quarto_export.py)

    Construit un projet de démonstration en mémoire : 3 chapitres, 8
    sections, 12 figures, 5 tableaux, 20 sources dont 3 prépublications, des
    renvois @fig-, @tbl- et @sec-. Compile en PDF. Vérifie qu'aucun renvoi
    n'est non résolu, que la bibliographie contient exactement 20 entrées,
    que les 3 prépublications portent leur note. Sortie 0 si conforme, 1 en
    cas d'échec, 2 si Quarto est absent.

10. Tests

    US-501 :
    test_bib_contains_only_verified_cited_sources
    test_bib_excludes_orphaned_sections
    test_export_fails_on_unverified_citation_with_location
    test_user_bib_never_used_for_compilation
    test_key_normalization_format
    test_key_collision_gets_stable_suffix
    test_key_persisted_and_reused_across_exports
    test_latex_escaping_in_titles
    test_acronym_case_protected
    test_preprint_note_present
    test_bib_output_deterministic

    US-502 :
    test_assembler_respects_plan_order
    test_assembler_emits_stable_section_ids
    test_assembler_marks_missing_sections_without_failing
    test_quarto_yml_sets_lang_from_project
    test_quarto_yml_points_to_generated_bib
    test_quarto_missing_raises_actionable_error
    test_log_parser_detects_unresolved_crossref
    test_log_parser_detects_unresolved_citation
    test_latex_error_reported_with_context_window
    test_export_artifacts_archived_and_never_overwritten
    test_export_report_fields
    [integration] test_export_pdf_docx_html

INTERDICTIONS

- Toute conversion, tout repli ou toute dépendance MyST ou Sphinx.
- Compiler à partir d'un .bib fourni par l'utilisateur.
- Inclure une citation non vérifiée.
- Changer une clé BibTeX déjà attribuée.
- Transmettre le journal Quarto brut comme message d'erreur.
- Écraser un répertoire d'export antérieur.
- Appel réseau pendant l'export.
- Modification d'un fichier hors périmètre.

CRITÈRES D'ACCEPTATION

    cd backend && pytest -q backend/tests/tests_export -m "not integration"
    ruff check backend/ && ruff format --check backend/
    python scripts/check_quarto_export.py
    python scripts/check_no_cloud_calls.py

FORMAT DE RÉPONSE

1. Plan d'implémentation, 10 lignes maximum.
2. Contenu intégral de chaque fichier.
3. Sortie attendue des vérifications.
4. Section "Réserves".
```

## Messages de commit attendus

```text
feat(export): compilation dynamique du fichier BibTeX

Implémente US-501 (Backlog V0.3).

- .bib regénéré à chaque export depuis les seules citations vérifiées des
  sections incluses ; le .bib utilisateur ne sert jamais à compiler.
- Clés normalisées, collisions suffixées de façon stable, clé persistée et
  réutilisée pour ne pas casser un document déjà relu.
- Note de prépublication propagée jusqu'à la bibliographie rendue.
- Sortie déterministe, vérifiée par test.

Refs: US-501, ADR-007
```

```text
feat(export): compilation Quarto multi-format avec analyse du journal

Implémente US-502 (Backlog V0.3).

- Assemblage du plan en .qmd avec identifiants de section stables.
- _quarto.yml généré, lang issu du projet, crossref et numérotation.
- Journal Quarto analysé : renvois et citations non résolus remontés
  explicitement ; erreur LaTeX rapportée avec fenêtre de contexte.
- Répertoire d'export daté, jamais écrasé, avec rapport JSON.

Refs: US-502, ADR-006, ADR-012
```
