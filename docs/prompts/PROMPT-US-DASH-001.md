# Prompt d'implémentation — US-DASH-001 (tableau de bord d'avancement)

Section US-DASH-001 de `docs/prompts/03-story-prompts.md`.
Conforme au Backlog V0.3 et aux ADR-011, ADR-009, ADR-003.

---

```text
Tu es un agent de codage senior, spécialisé en visualisation de données
applicatives, Angular, Signals et conception d'indicateurs honnêtes.

PROJET

Science AI Writer IDE — rédaction de mémoires de 30 à 300 pages, sur des
projets qui s'étendent souvent sur plusieurs mois.

SOURCES DE VÉRITÉ, PAR ORDRE DÉCROISSANT

1. Ce prompt.
2. ADR-011 (Angular, Signals), ADR-009 (audit à détection d'altération),
   ADR-003 (budget VRAM).
3. Backlog V0.3, US-DASH-001.
4. contracts/openapi.yaml.

DÉPENDANCES REQUISES : US-801 (coquille, stores, capacités), US-302
(scores de relecture), US-701 (audit), US-006 (mesure VRAM).

USER STORY

US-DASH-001 — En tant que doctorant, je veux voir où j'en suis, afin de
savoir quoi faire ensuite plutôt que de me perdre dans un document de trois
cents pages.

PRINCIPE DIRECTEUR — DES INDICATEURS HONNÊTES

Un tableau de bord de rédaction est facile à rendre flatteur et donc
inutile. Trois règles :

  1. Ne jamais présenter un pourcentage d'avancement global unique. « 68 %
     de la thèse » n'a aucun sens : une section validée de 500 mots ne vaut
     pas une section validée de 5000, et le nombre de mots ne mesure pas
     l'avancement intellectuel. Décomposer par dimension.
  2. Ne jamais afficher une estimation de date d'achèvement. L'outil n'a
     aucune base pour la produire, et une échéance fausse est nuisible.
  3. Le score de relecture est un CONSEIL, pas une note. Il s'affiche avec
     le rappel que seule la validation humaine fait foi (US-302).

PÉRIMÈTRE STRICT

Tu implémentes :

- le panneau tableau de bord et son enregistrement au panel-registry ;
- les indicateurs d'avancement par dimension ;
- la carte des sections avec leur état ;
- les indicateurs de sourçage ;
- le suivi de la tâche en cours et de l'historique récent ;
- l'état d'intégrité et l'état des ressources ;
- la liste des actions suivantes proposées ;
- les tests.

Tu n'implémentes PAS :

- de nouveaux endpoints : tu consommes ceux du contrat ;
- de calcul métier dupliqué depuis le backend ;
- le suivi du budget de tokens distant (US-LLM-002) ;
- l'export de rapports.

FICHIERS À CRÉER OU MODIFIER

- frontend/src/app/features/dashboard/dashboard-panel.component.ts
- frontend/src/app/features/dashboard/progress-by-dimension.component.ts
- frontend/src/app/features/dashboard/section-map.component.ts
- frontend/src/app/features/dashboard/sourcing-indicators.component.ts
- frontend/src/app/features/dashboard/activity-feed.component.ts
- frontend/src/app/features/dashboard/integrity-badge.component.ts
- frontend/src/app/features/dashboard/next-actions.component.ts
- frontend/src/app/state/dashboard.store.ts
- frontend/src/app/layout/panel-registry.ts        (modification)
- frontend/src/app/**/*.spec.ts                    (tests correspondants)

Aucun fichier backend.

EXIGENCES D'IMPLÉMENTATION

1. Avancement par dimension (progress-by-dimension.component.ts)

    Quatre barres distinctes, jamais agrégées en une seule :

      STRUCTURE   — nœuds du plan validés sur nœuds totaux
      RÉDACTION   — sections ayant un contenu sur sections planifiées
      RELECTURE   — sections relues sur sections rédigées
      VALIDATION  — sections validées par l'auteur sur sections rédigées

    Chaque barre affiche le rapport brut — « 14 / 38 sections » — et non
    seulement un pourcentage. Le nombre absolu est plus informatif sur un
    document long.

    Le compte de mots est affiché à part, comparé à la cible du projet,
    avec la mention qu'il s'agit d'un volume et non d'un avancement.

2. Carte des sections (section-map.component.ts)

    Représentation compacte de l'arbre du plan, une cellule par section,
    état indiqué par un libellé ET une forme, jamais par la seule couleur :
    non rédigée, en rédaction, relue, validée, orpheline.

    - une cellule est cliquable et ouvre la section dans l'éditeur ;
    - les sections ORPHANED sont regroupées à part avec un avertissement :
      elles subsistent après suppression d'un nœud de plan (US-PLAN-001) et
      ne seront PAS exportées. L'utilisateur doit pouvoir les repérer, sans
      quoi il perdra du travail sans le savoir ;
    - le survol indique le nombre de mots, le score de relecture s'il
      existe, et la date de validation.

3. Sourçage (sourcing-indicators.component.ts)

    - nombre de sources approuvées, part ingérée ;
    - part de prépublications parmi les sources citées — un ratio élevé
      est un signal à porter à la connaissance du doctorant, sans jugement
      automatique ;
    - sections comportant moins de N sources distinctes, N configurable,
      défaut 3 : liste cliquable ;
    - citations non vérifiées : ce sont elles qui BLOQUERONT l'export
      (US-501). Elles s'affichent en tête, avec la section et le passage,
      parce que c'est l'information la plus actionnable du tableau de bord.

4. Activité (activity-feed.component.ts)

    - tâche en cours : état du graphe, progression, agent actif, temps
      écoulé ; alimentée par le pont SSE d'US-801, jamais par une
      interrogation périodique ;
    - vingt derniers événements d'audit lisibles, traduits en langage
      naturel — « Section 3.2 validée le 4 mars à 14 h 12 » — et non sous
      forme de types techniques ;
    - un ERROR_STATE en cours s'affiche en tête avec le lien vers le
      panneau d'erreur d'US-801.

5. Intégrité et ressources (integrity-badge.component.ts)

    - résultat de la dernière vérification de chaîne, avec un bouton pour
      relancer POST /audit/verify. Le libellé emploie « détection
      d'altération », jamais « immuable » (ADR-009). Le test lexical de
      US-701 couvre aussi ce composant ;
    - VRAM courante et pic du dernier cycle, issus de
      /system/capabilities. Champs nullables : en l'absence de GPU, le bloc
      est MASQUÉ, sans message d'erreur ni valeur factice.

6. Actions suivantes (next-actions.component.ts)

    Trois à cinq propositions, calculées à partir de l'état, ordonnées par
    ce qui débloque le plus :

      - plan non validé → valider le plan ;
      - citations non vérifiées → les traiter avant export ;
      - sections rédigées non relues → lancer la relecture ;
      - sections relues non validées → valider ;
      - sections sous le seuil de sources → enrichir la bibliographie ;
      - sections orphelines → les rattacher ou les archiver.

    Chaque proposition est un lien vers l'endroit exact où agir.
    AUCUNE proposition ne déclenche d'action automatique : ce sont des
    liens, pas des boutons d'exécution.

7. Store (state/dashboard.store.ts)

    Toutes les valeurs sont des computed() dérivés des stores existants —
    plan, sections, sources, tâches, capacités. Le tableau de bord ne
    possède aucun état propre et n'émet aucune requête que les autres
    stores n'émettent déjà, hormis la vérification d'intégrité à la
    demande.

8. Performance

    Sur un projet de 300 sections et 300 sources, le panneau doit se rendre
    en moins de 300 ms. La carte des sections est virtualisée au-delà de
    100 cellules. Un test de performance mesure ce seuil sur un jeu de
    données synthétique.

9. Tests

    test_no_single_global_progress_percentage
    test_four_dimensions_displayed_separately
    test_absolute_counts_shown_with_ratios
    test_word_count_labelled_as_volume_not_progress
    test_no_completion_date_estimate

    test_section_state_conveyed_beyond_colour
    test_orphaned_sections_grouped_and_warned
    test_section_cell_opens_editor

    test_unverified_citations_listed_first
    test_preprint_share_displayed_without_judgement
    test_sections_below_source_threshold_listed

    test_activity_fed_by_sse_not_polling
    test_audit_events_rendered_in_natural_language
    test_error_state_surfaced_at_top

    test_integrity_wording_tamper_evident
    test_vram_block_hidden_without_gpu
    test_vram_never_shows_placeholder_value

    test_next_actions_are_links_not_executors
    test_next_actions_ordered_by_unblocking_value

    test_store_uses_computed_only
    test_dashboard_emits_no_extra_requests
    test_render_under_300ms_with_300_sections

INTERDICTIONS

- Pourcentage d'avancement global unique.
- Estimation de date d'achèvement.
- Présenter le score de relecture comme une validation.
- État transmis par la seule couleur.
- Interrogation périodique du backend.
- Valeur factice quand une mesure est indisponible.
- Action automatique déclenchée depuis le tableau de bord.
- Duplication d'un calcul métier du backend.
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
feat(frontend): tableau de bord d'avancement

Implémente US-DASH-001 (Backlog V0.3).

- Avancement décomposé en quatre dimensions, avec comptes absolus ; aucun
  pourcentage global, aucune estimation de date.
- Carte des sections virtualisée ; sections orphelines regroupées et
  signalées comme non exportables.
- Citations non vérifiées en tête : ce sont elles qui bloqueront l'export.
- Activité alimentée par SSE, audit traduit en langage naturel.
- Bloc VRAM masqué sans GPU, jamais de valeur factice.
- Actions suivantes sous forme de liens, jamais d'exécuteurs.

Refs: US-DASH-001, ADR-003, ADR-009, ADR-011
```
