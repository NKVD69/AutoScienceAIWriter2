# Prompt d'implémentation — US-UI-002 (éditeur Quarto)

Section US-UI-002 de `docs/prompts/03-story-prompts.md`.
Conforme au Backlog V0.3, aux Spécifications V0.3 §9.1 et aux ADR-006, ADR-011.

---

```text
Tu es un agent de codage senior, spécialisé en éditeurs de texte enrichi,
Angular, coloration syntaxique et interfaces de rédaction longue.

PROJET

Science AI Writer IDE — rédaction de mémoires de 30 à 300 pages. Format
canonique Quarto Markdown (.qmd).

SOURCES DE VÉRITÉ, PAR ORDRE DÉCROISSANT

1. Ce prompt.
2. ADR-006 (Quarto canonique), ADR-011 (Angular, Signals), ADR-004
   (portes humaines).
3. Backlog V0.3, US-UI-002.
4. Spécifications V0.3, §9.1.
5. contracts/openapi.yaml, sections /sections.

DÉPENDANCES REQUISES : US-801 (coquille, stores, panel-registry), US-301
(sections et citations côté backend).

USER STORY

US-UI-002 — En tant que doctorant, je veux rédiger et corriger mes sections
dans un éditeur qui connaît la syntaxe Quarto, afin de ne jamais produire de
renvoi cassé ni de citation inexistante.

CONTEXTE DE LA DÉCISION

Le format canonique est Quarto, PAS MyST. Ces deux écosystèmes ont des
syntaxes de renvoi incompatibles : @fig- et @tbl- pour Quarto, {ref} et
{numref} pour MyST. Une spécification antérieure les confondait ; la
correction figure en ADR-006.

L'éditeur doit donc connaître la syntaxe Quarto et REFUSER la syntaxe MyST,
au lieu de la laisser passer pour qu'elle échoue silencieusement à la
compilation, trois cents pages plus tard.

PÉRIMÈTRE STRICT

Tu implémentes :

- le panneau éditeur remplaçant le composant de remplacement d'US-801 ;
- la coloration syntaxique Quarto ;
- l'autocomplétion des citations et des renvois ;
- la validation locale en temps réel ;
- l'insertion assistée d'éléments Quarto ;
- l'enregistrement, l'état de modification et la reprise ;
- l'affichage du flux de génération ;
- les tests.

Tu n'implémentes PAS :

- l'aperçu compilé en PDF : la compilation relève de US-502 et prend
  plusieurs dizaines de secondes. Un aperçu HTML léger côté client suffit ;
- l'éditeur de code Python (US-UI-003) ;
- le versioning et la comparaison (US-UI-004) ;
- les commentaires et annotations (US-UI-005) ;
- toute modification du backend.

FICHIERS À CRÉER OU MODIFIER

- frontend/src/app/features/editor/editor-panel.component.ts
- frontend/src/app/features/editor/qmd-language.ts
- frontend/src/app/features/editor/qmd-linter.ts
- frontend/src/app/features/editor/completion/citation-completion.ts
- frontend/src/app/features/editor/completion/crossref-completion.ts
- frontend/src/app/features/editor/toolbar/insert-actions.ts
- frontend/src/app/features/editor/preview/light-preview.component.ts
- frontend/src/app/layout/panel-registry.ts        (modification)
- frontend/src/app/features/editor/editor-panel.placeholder.ts  (suppression)
- frontend/src/app/**/*.spec.ts                    (tests correspondants)

Aucun fichier backend.

EXIGENCES D'IMPLÉMENTATION

1. Socle technique

    CodeMirror 6 plutôt que Monaco. Motif : Monaco est conçu pour le code et
    pèse lourd ; CodeMirror 6 gère mieux le texte long avec retour à la
    ligne, le rendu de paragraphes et les décorations en ligne, et son
    système d'extensions convient à une syntaxe composite.

    Monaco reste réservé au panneau de code Python (US-UI-003) : les deux
    éditeurs coexistent, chacun pour son usage.

    Intégration en composant standalone, état exposé par signaux, aucune
    manipulation directe du DOM hors de l'API de l'éditeur.

2. Coloration Quarto (qmd-language.ts)

    Markdown de base, plus :
    - en-tête YAML délimité par --- ;
    - blocs de code ```{python} et ```{r} avec leurs options #| ;
    - citations [@clef] et @clef ;
    - renvois @fig-, @tbl-, @eq-, @sec- ;
    - identifiants {#fig-xxx}, {#tbl-xxx}, {#sec-xxx} ;
    - encadrés ::: {.callout-note} ;
    - mathématiques $ et $$.

3. Validation locale (qmd-linter.ts) — LE CŒUR DE LA STORY

    Diagnostics en temps réel, sans appel réseau :

    L1 — SYNTAXE MYST. Toute occurrence de {ref}, {numref}, ou d'un
         encadré ::: {mot} sans point initial est signalée en ERREUR, avec
         la correction Quarto proposée en action rapide. C'est le contrôle
         qui empêche la confusion des deux écosystèmes de se propager
         jusqu'à l'export.

    L2 — CITATION INCONNUE. Toute clé @clef absente de la liste des
         citations connues du projet est signalée en ERREUR. La liste
         provient du store des sources, alimentée par l'API : l'éditeur ne
         l'invente pas.

    L3 — RENVOI NON RÉSOLU. Tout @fig-x, @tbl-x, @eq-x, @sec-x sans
         identifiant correspondant dans le document assemblé est signalé en
         AVERTISSEMENT — et non en erreur, car la cible peut se trouver
         dans une autre section pas encore rédigée.

    L4 — IDENTIFIANT DUPLIQUÉ. Deux {#fig-x} identiques dans le projet :
         ERREUR. La compilation Quarto échouerait ou numéroterait mal.

    L5 — PRÉFIXE INCORRECT. {#fig-x} apposé à un tableau, ou {#tbl-x} à une
         figure : AVERTISSEMENT.

    Chaque diagnostic porte une position exacte, un message en français et,
    quand c'est possible, une action rapide. Un diagnostic sans action est
    acceptable ; un diagnostic sans position ne l'est pas.

4. Autocomplétion

    - CITATIONS : @ déclenche la liste des sources du projet, filtrée à la
      frappe sur auteur, année et titre. L'entrée affiche auteur, année,
      revue, et un marqueur PRÉPUBLICATION quand is_preprint est vrai —
      l'utilisateur doit savoir au moment d'insérer, pas à la relecture ;
    - RENVOIS : @fig- déclenche la liste des identifiants de figures
      existants avec leur légende ; idem pour @tbl-, @eq-, @sec- ;
    - les listes proviennent des stores, jamais d'une requête par frappe.

5. Insertion assistée (toolbar/insert-actions.ts)

    Actions produisant du Quarto correct :
    figure avec légende et identifiant, tableau, équation numérotée,
    encadré callout, bloc de code, citation, renvoi.

    L'identifiant est proposé sous forme de slug dérivé de la légende, et
    l'unicité est vérifiée avant insertion. L'utilisateur peut le modifier ;
    il ne peut pas insérer un doublon sans avertissement.

6. Enregistrement et état

    - enregistrement automatique après 2 s d'inactivité, et à la perte du
      focus du panneau ;
    - indicateur d'état à trois valeurs : modifié, enregistrement en cours,
      enregistré à telle heure ;
    - conflit de version — la section a changé côté serveur pendant
      l'édition : NE PAS écraser. Afficher les deux versions et laisser
      l'utilisateur choisir. Une thèse ne tolère pas la perte silencieuse
      d'un paragraphe ;
    - un brouillon local non enregistré survit à un rechargement de la
      page, conservé en mémoire du store le temps de la session.

7. Affichage de la génération

    Pendant une génération de section, l'éditeur passe en lecture seule et
    affiche le flux de tokens fourni par le pont SSE d'US-801. Il consomme
    le signal déjà regroupé par trame : il ne s'abonne jamais directement à
    l'EventSource et ne met jamais à jour le document token par token.

    À la fin, le contenu validé remplace le flux et l'édition redevient
    possible.

8. Aperçu léger (preview/light-preview.component.ts)

    Rendu HTML côté client, sans Quarto : titres, emphases, listes,
    tableaux, blocs de code, mathématiques via KaTeX. Les renvois et
    citations sont affichés sous forme de jetons, PAS résolus — les
    résoudre demanderait citeproc et donnerait une fausse impression de
    fidélité.

    Un libellé indique explicitement qu'il s'agit d'un aperçu approximatif
    et que le rendu final provient de l'export.

9. Tests

    test_qmd_highlighting_covers_quarto_constructs
    test_myst_ref_flagged_as_error
    test_myst_directive_flagged_as_error
    test_myst_quick_fix_produces_quarto_syntax
    test_unknown_citation_key_flagged_as_error
    test_known_citation_key_not_flagged
    test_unresolved_crossref_flagged_as_warning_not_error
    test_duplicate_identifier_flagged_as_error
    test_wrong_prefix_flagged_as_warning
    test_every_diagnostic_has_position

    test_citation_completion_lists_project_sources
    test_citation_completion_marks_preprints
    test_crossref_completion_lists_existing_labels
    test_completion_reads_store_not_network

    test_insert_figure_generates_valid_quarto
    test_insert_checks_identifier_uniqueness

    test_autosave_after_two_seconds_idle
    test_autosave_on_panel_blur
    test_version_conflict_shows_both_versions
    test_version_conflict_never_overwrites
    test_unsaved_draft_survives_reload

    test_editor_readonly_during_generation
    test_editor_consumes_batched_signal_not_eventsource
    test_preview_does_not_resolve_citations
    test_preview_labeled_as_approximate

INTERDICTIONS

- Accepter la syntaxe MyST sans diagnostic.
- Résoudre les citations ou renvois dans l'aperçu.
- S'abonner directement à l'EventSource depuis l'éditeur.
- Mettre à jour le document token par token.
- Écraser une version serveur divergente.
- Requête réseau par frappe pour l'autocomplétion.
- Employer Monaco pour ce panneau.
- Modification d'un fichier backend.

CRITÈRES D'ACCEPTATION

    cd frontend && ng build --configuration production
    ng test --watch=false --browsers=ChromeHeadless
    ng lint

FORMAT DE RÉPONSE

1. Plan d'implémentation, 10 lignes maximum.
2. Contenu intégral de chaque fichier.
3. Sortie attendue des commandes.
4. Section "Réserves".
```

## Message de commit attendu

```text
feat(frontend): éditeur Quarto avec validation locale et autocomplétion

Implémente US-UI-002 (Backlog V0.3).

- CodeMirror 6, coloration Quarto complète : YAML, blocs exécutables,
  citations, renvois, identifiants, callouts, mathématiques.
- Cinq diagnostics locaux, dont le rejet de la syntaxe MyST avec action
  rapide de conversion.
- Autocomplétion des citations depuis les stores, prépublications marquées
  au moment de l'insertion.
- Conflit de version affiché, jamais écrasé.
- Flux de génération consommé depuis le signal regroupé d'US-801.

Refs: US-UI-002, ADR-006, ADR-011
```
