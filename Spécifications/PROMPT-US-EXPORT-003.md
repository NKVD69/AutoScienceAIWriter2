# Prompt d'implémentation — US-EXPORT-003 (annexe de déclaration d'usage de l'IA)

Section US-EXPORT-003 de `docs/prompts/03-story-prompts.md`.
Story nouvelle, introduite par le Backlog V0.3. Conforme aux Spécifications V0.3 §9.5 et à ADR-009.

---

```text
Tu es un agent de codage senior, spécialisé en génération de documents,
agrégation de journaux et restitution de traçabilité.

PROJET

Science AI Writer IDE — assistance à la rédaction de mémoires et thèses.

SOURCES DE VÉRITÉ, PAR ORDRE DÉCROISSANT

1. Ce prompt.
2. ADR-009 (journal à détection d'altération), ADR-006 (Quarto),
   ADR-007 (bibliographie générée).
3. Backlog V0.3, US-EXPORT-003.
4. Spécifications V0.3, §9.5.

DÉPENDANCES REQUISES : US-701 (audit), US-502 (export).

USER STORY

US-EXPORT-003 — En tant que doctorant, je veux que l'outil produise
automatiquement l'annexe de déclaration d'usage de l'IA, afin de satisfaire
les exigences de mon établissement sans reconstituer cet historique à la
main.

POURQUOI CETTE STORY EXISTE

La plupart des établissements imposent désormais une déclaration d'usage des
outils d'IA générative en annexe des mémoires et thèses. Reconstituer cette
information a posteriori est fastidieux et peu fiable.

L'outil dispose déjà de tout le nécessaire dans le journal d'audit :
modèles employés, sections générées, dates de validation humaine, scripts
exécutés. Produire l'annexe est un agrégat, pas une nouvelle collecte. À
faible coût, c'est ce qui rend l'outil recevable institutionnellement plutôt
que suspect.

EXIGENCE D'HONNÊTETÉ

L'annexe rapporte ce que le journal contient, sans arrondir en faveur de
l'utilisateur. Une section entièrement générée et validée sans modification
est déclarée comme telle. Le rôle de ce document est d'être exact, pas
avantageux.

PÉRIMÈTRE STRICT

Tu implémentes :

- l'agrégation du journal en statistiques par section ;
- le calcul de la part réécrite par l'auteur ;
- la génération de l'annexe en Quarto ;
- son insertion au point d'extension posé par US-502 ;
- la journalisation de sa désactivation ;
- les tests.

Tu n'implémentes PAS :

- de modification du journal d'audit ;
- de nouveaux événements : tu consommes ceux de US-701 ;
- l'anti-plagiat (US-601) ;
- l'affichage dans l'interface (US-801).

FICHIERS À CRÉER OU MODIFIER

- backend/app/export/declaration.py
- backend/app/export/templates/default/ai_declaration.qmd.j2
- backend/app/export/assembler.py              (modification : insertion)
- backend/app/services/export_service.py       (modification)
- backend/tests/tests_export/test_declaration.py

Aucun autre fichier.

EXIGENCES D'IMPLÉMENTATION

1. Agrégation

    Depuis audit_log, pour l'ensemble du projet :

    - modèles employés, versions et quantifications, avec la période
      d'utilisation de chacun — un projet mené sur deux ans peut avoir
      changé de modèle ;
    - versions des prompts système par agent (champ déjà journalisé par
      LLM_CALL en US-701) ;
    - par section : date de première génération, nombre de régénérations,
      nombre de cycles de relecture, date de validation humaine,
      identité du validateur si les rôles sont actifs ;
    - scripts exécutés : nombre, niveau de sandbox, artefacts produits ;
    - recherches bibliographiques : nombre, fournisseurs interrogés ;
    - consentements accordés et révoqués, avec dates.

2. Part réécrite par l'auteur

    Pour chaque section, comparaison entre la dernière version générée par
    l'agent et la version finale validée, par distance de Levenshtein
    normalisée au niveau des tokens.

    Restitution en tranches, jamais en pourcentage au dixième :

      « non modifiée », « légèrement révisée » (moins de 10 %),
      « substantiellement révisée » (10 à 40 %),
      « largement réécrite » (40 à 80 %),
      « rédigée par l'auteur » (plus de 80 %).

    Un pourcentage précis donnerait une fausse impression d'exactitude sur
    une mesure qui n'en a pas : la distance d'édition ne mesure pas
    l'apport intellectuel. Les tranches sont honnêtes sur leur propre
    imprécision.

3. Contenu de l'annexe

    Structure imposée :

      1. Préambule : nature de l'outil, principe de validation humaine
         systématique, mention que chaque affirmation sourcée est rattachée
         à une source approuvée par l'auteur.
      2. Modèles employés : tableau modèle, version, période, usage.
      3. Traitement par section : tableau chapitre, section, date de
         validation, niveau de révision par l'auteur, nombre de sources
         citées.
      4. Analyses exécutées : tableau des scripts et de leurs artefacts.
      5. Recherche documentaire : fournisseurs interrogés, nombre de
         sources retenues, part de prépublications.
      6. Vérification d'intégrité : résultat de verify_chain à la date de
         l'export, et mention explicite que ce mécanisme détecte une
         altération sans l'empêcher (ADR-009).

    Le point 6 emploie le vocabulaire imposé par ADR-009. Le test lexical
    de US-701 couvre aussi ce gabarit.

4. Génération

    - gabarit Jinja produisant du Quarto, avec un identifiant de section
      stable {#sec-declaration-ia} ;
    - langue du projet : les libellés proviennent du gabarit, pas de
      chaînes codées en dur en français ;
    - insertion au point d'extension exposé par l'assembleur de US-502,
      en dernière annexe, après la bibliographie ;
    - l'annexe n'est jamais numérotée comme un chapitre de contenu.

5. Désactivation

    include_ai_declaration à faux : l'annexe est absente et un événement
    AI_DECLARATION_DISABLED est journalisé, avec la date et l'identifiant
    de l'export. La désactivation est un droit de l'utilisateur ; sa trace
    en est un autre.

6. Cas limites

    - projet sans aucun appel LLM — sections entièrement écrites à la
      main : l'annexe est générée et déclare l'absence de génération
      automatique. Ne pas l'omettre : une déclaration disant « aucun
      contenu généré » a de la valeur ;
    - chaîne d'audit ALTERE : l'annexe est générée mais porte un
      avertissement visible en tête, et l'export est signalé dans son
      rapport. Ne pas bloquer l'export : l'utilisateur doit pouvoir
      constater le problème sur le document rendu ;
    - section ORPHANED : exclue, comme partout ailleurs.

7. Tests

    test_declaration_lists_models_with_periods
    test_declaration_lists_prompt_versions
    test_revision_level_bands_not_percentages
    test_unmodified_section_declared_as_such
    test_author_written_section_declared_above_80
    test_declaration_includes_script_executions
    test_declaration_includes_biblio_providers
    test_declaration_reports_preprint_share
    test_integrity_section_uses_tamper_evident_wording
    test_declaration_language_follows_project
    test_declaration_inserted_after_bibliography
    test_declaration_section_id_stable
    test_disabled_declaration_logs_audit_event
    test_project_without_llm_calls_still_generates_declaration
    test_altered_chain_adds_warning_without_blocking_export
    test_orphaned_sections_excluded

INTERDICTIONS

- Arrondir ou minorer la part générée automatiquement.
- Exprimer le niveau de révision en pourcentage précis.
- Employer « immuable » ou « infalsifiable » dans le gabarit.
- Bloquer un export parce que la chaîne d'audit est altérée.
- Omettre l'annexe faute d'appels LLM.
- Écrire de nouveaux événements dans le journal d'audit.
- Modification d'un fichier hors périmètre.

CRITÈRES D'ACCEPTATION

    cd backend && pytest -q backend/tests/tests_export
    ruff check backend/ && ruff format --check backend/
    python scripts/check_audit_chain.py
    python scripts/check_quarto_export.py

FORMAT DE RÉPONSE

1. Plan d'implémentation, 10 lignes maximum.
2. Contenu intégral de chaque fichier.
3. Sortie attendue des vérifications.
4. Section "Réserves".
```

## Message de commit attendu

```text
feat(export): annexe de déclaration d'usage de l'IA

Implémente US-EXPORT-003 (Backlog V0.3, story nouvelle).

- Agrégation du journal d'audit : modèles et périodes, versions de prompts,
  traitement par section, scripts, recherches documentaires, consentements.
- Niveau de révision par l'auteur restitué en tranches, jamais en
  pourcentage : la distance d'édition ne mesure pas l'apport intellectuel.
- Section d'intégrité employant le vocabulaire de détection d'altération.
- Générée même sans appel LLM ; avertissement sans blocage si la chaîne
  est altérée ; désactivation journalisée.

Refs: US-EXPORT-003, ADR-009
Corrige: D-11 (déclaration d'usage absente du dossier)
```
