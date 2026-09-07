# Prompt d'implémentation — US-801 (layout IDE Angular, Dockview et Signals)

Section US-801 de `docs/prompts/03-story-prompts.md`.
Dernier bloc structurant du dossier. Conforme au Backlog V0.3, aux Spécifications V0.3 §2 et à ADR-011.

---

```text
Tu es un agent de codage senior, spécialisé en Angular moderne, Signals,
interfaces de type IDE, flux temps réel et tests de composants.

PROJET

Science AI Writer IDE — application locale d'assistance à la rédaction de
mémoires et thèses. Backend FastAPI sur 127.0.0.1, API v1 décrite par
contracts/openapi.yaml.

SOURCES DE VÉRITÉ, PAR ORDRE DÉCROISSANT

1. Ce prompt.
2. ADR-011 (Angular, Dockview, Signals), ADR-010 (consentement),
   ADR-004 (portes humaines), ADR-005 (isolation), ADR-009 (audit).
3. Backlog V0.3, US-801.
4. Spécifications V0.3, §2.
5. contracts/openapi.yaml — le contrat fait foi sur les noms de champs,
   les codes de statut et les formes d'erreur.

DÉPENDANCES REQUISES : l'API v1 telle que décrite par le contrat. Tu ne
modifies aucun fichier du backend.

USER STORY

US-801 — En tant qu'utilisateur, je veux un espace de travail à panneaux
dockables reflétant en temps réel l'état du projet, afin de piloter la
rédaction sans perdre le fil entre le plan, les sources, le texte et le
code.

CE QUE CETTE STORY LIVRE, ET CE QU'ELLE NE LIVRE PAS

Elle livre la COQUILLE et les CONTRATS : disposition, navigation, gestion
d'état, pont temps réel, gating par capacités, portes humaines, dialogues de
consentement, présentation des erreurs.

Elle ne livre PAS le contenu métier des panneaux d'édition :
l'éditeur Quarto relève de US-UI-002, Monaco de US-UI-003, le tableau de
bord de US-DASH-001. Tu poses pour chacun un panneau de remplacement
minimal — affichage brut et point d'extension documenté — que ces stories
viendront remplir.

Les signatures de stores et de services que tu définis ici seront consommées
telles quelles : sois explicite et stable.

LE POINT LE PLUS FRAGILE DU FRONTEND

Le pont entre le flux SSE et les Signals concentre l'essentiel du risque
d'intégration. Une génération de section produit des centaines de tokens par
seconde ; un signal mis à jour à chaque token déclenche une détection de
changement par token et fige l'interface. Traite ce point avec le soin
décrit en section 5 : c'est là que se joue la qualité perçue de
l'application.

PÉRIMÈTRE STRICT

Tu implémentes :

- l'ossature Angular standalone et le routage ;
- la disposition Dockview et sa persistance par projet ;
- les stores par domaine, exposés en signaux en lecture seule ;
- le client HTTP typé, généré ou aligné sur le contrat ;
- le pont SSE vers les signaux, avec regroupement et reconnexion ;
- le gating par /system/capabilities ;
- les portes humaines et les dialogues de consentement ;
- la présentation d'ERROR_STATE ;
- les panneaux de remplacement ;
- les tests.

Tu n'implémentes PAS :

- l'éditeur Quarto (US-UI-002), Monaco (US-UI-003), le tableau de bord
  (US-DASH-001), le versioning (US-UI-004), les annotations (US-UI-005) ;
- l'authentification (US-AUTH-001) ;
- toute logique métier dupliquée depuis le backend.

FICHIERS À CRÉER OU MODIFIER

- frontend/src/app/app.config.ts
- frontend/src/app/app.routes.ts
- frontend/src/app/app.component.ts
- frontend/src/app/core/api/api-client.service.ts
- frontend/src/app/core/api/models.ts
- frontend/src/app/core/sse/task-stream.service.ts
- frontend/src/app/core/sse/event-buffer.ts
- frontend/src/app/core/capabilities/capabilities.store.ts
- frontend/src/app/core/errors/error-presenter.service.ts
- frontend/src/app/state/project.store.ts
- frontend/src/app/state/plan.store.ts
- frontend/src/app/state/sources.store.ts
- frontend/src/app/state/sections.store.ts
- frontend/src/app/state/tasks.store.ts
- frontend/src/app/layout/workspace.component.ts
- frontend/src/app/layout/dockview-host.directive.ts
- frontend/src/app/layout/layout-persistence.service.ts
- frontend/src/app/layout/panel-registry.ts
- frontend/src/app/features/plan/plan-panel.component.ts
- frontend/src/app/features/sources/sources-panel.component.ts
- frontend/src/app/features/editor/editor-panel.placeholder.ts
- frontend/src/app/features/code/code-panel.placeholder.ts
- frontend/src/app/features/console/console-panel.component.ts
- frontend/src/app/features/review/review-panel.component.ts
- frontend/src/app/shared/consent-dialog.component.ts
- frontend/src/app/shared/human-gate-button.component.ts
- frontend/src/app/shared/error-state-panel.component.ts
- frontend/src/app/**/*.spec.ts                (tests correspondants)
- frontend/package.json                        (modification : dépendances)

Aucun fichier backend.

EXIGENCES D'IMPLÉMENTATION

1. Ossature

    - Angular standalone components exclusivement. Aucun NgModule.
    - provideRouter, provideHttpClient(withFetch()).
    - Routes : /projects, /projects/:id/workspace.
    - Zoneless si la version d'Angular du projet le permet ; sinon
      OnPush partout. Aucun composant en détection par défaut.

2. Client API (core/api/)

    - types TypeScript alignés sur contracts/openapi.yaml. Si un
      générateur est disponible, génère-les ; sinon écris-les à la main en
      les dérivant du contrat, sans inventer de champ ;
    - une méthode par opération, typée en entrée comme en sortie ;
    - base URL http://127.0.0.1:8000/api/v1, configurable par
      environment ;
    - un intercepteur unique traduit les erreurs HTTP en un type discriminé
      ApiError : NotFound | Validation | Conflict | ConsentRequired |
      Network | Server. Aucun composant ne manipule un HttpErrorResponse
      brut.

3. Stores (state/)

    Un store par domaine, sous forme de service injectable. Règles :

    - l'état interne est un WritableSignal privé ; le store n'expose que
      des Signal en lecture seule et des méthodes de mutation ;
    - AUCUN composant ne détient d'état métier. Un composant possède au
      plus un état d'affichage local : onglet actif, largeur de panneau,
      champ de recherche ;
    - les dérivations passent par computed(), jamais par un recalcul dans
      le template ;
    - effect() est réservé aux effets de bord réels — persistance,
      abonnement, focus. Jamais pour synchroniser deux signaux entre eux ;
    - chaque store expose loading, error et data distincts. Un seul champ
      « state » agrégé rend les templates illisibles.

    RxJS reste utilisé pour ce qui est réellement un flux : SSE, saisie
    avec debounce. Pas pour l'état synchrone.

4. Disposition Dockview (layout/)

    - panneaux : Plan, Sources, Éditeur, Code, Console, Relecture, Tâches ;
    - disposition par défaut : Plan à gauche, Éditeur au centre, Sources et
      Relecture à droite, Console et Tâches en bas ;
    - panel-registry.ts associe un identifiant de panneau à un composant :
      c'est le point d'extension qu'US-UI-002 et US-UI-003 consommeront ;
    - la disposition sérialisée est persistée PAR PROJET via l'API, non
      dans le navigateur : elle fait partie de l'état du projet et doit
      suivre une sauvegarde ;
    - une disposition enregistrée référençant un panneau inconnu — version
      antérieure — est chargée en ignorant ce panneau, jamais en échouant.
      Prévoir un numéro de version dans la structure sérialisée.

5. Pont SSE vers Signals (core/sse/) — SECTION CRITIQUE

    task-stream.service.ts ouvre une connexion sur /tasks/{id}/stream et
    traduit les événements en mises à jour de signaux.

    Exigences impératives :

    a. REGROUPEMENT DES TOKENS. Les événements `token` ne mettent jamais à
       jour un signal individuellement. Ils sont accumulés dans un tampon
       (event-buffer.ts) vidé au rythme de requestAnimationFrame, ou à
       défaut toutes les 50 ms. Une seule écriture de signal par trame,
       portant la concaténation des tokens reçus.

       Sans ce regroupement, une génération à 40 tokens par seconde
       provoque 40 cycles de détection par seconde sur un texte qui
       s'allonge : l'interface devient inutilisable au bout de quelques
       centaines de mots.

    b. LES ÉVÉNEMENTS D'ÉTAT NE SONT JAMAIS REGROUPÉS. `state`,
       `guardrail`, `error` et `done` sont appliqués immédiatement, en
       vidant d'abord le tampon de tokens pour préserver l'ordre.

    c. RECONNEXION. Perte de connexion : reconnexion avec backoff
       exponentiel plafonné à 30 s, et au retour, RÉCUPÉRATION DE L'ÉTAT
       PAR GET /tasks/{id} — le flux SSE ne rejoue pas l'historique. Sans
       cette resynchronisation, l'interface reste figée sur un état périmé
       alors que la tâche a progressé.

    d. NETTOYAGE. Fermeture de la connexion à la destruction du composant
       hôte et au changement de projet. Une connexion fuitée continue de
       consommer et de réécrire des signaux d'un projet quitté. Utilise
       DestroyRef et takeUntilDestroyed.

    e. ORDRE ET IDEMPOTENCE. Chaque événement porte un numéro de séquence ;
       un événement de numéro inférieur ou égal au dernier traité est
       ignoré. La reconnexion peut produire des doublons.

    f. UNE SEULE CONNEXION PAR TÂCHE, quel que soit le nombre de composants
       intéressés. Le service tient un registre et partage le flux.

6. Gating par capacités (core/capabilities/)

    Au chargement d'un projet, GET /system/capabilities alimente un store.
    L'interface DÉSACTIVE, avec une explication, plutôt que de laisser
    échouer :

    - Quarto absent → bouton d'export désactivé, infobulle indiquant
      l'installation requise ;
    - Ollama injoignable → actions de génération désactivées ;
    - pas de GPU → indicateur VRAM masqué, sans message d'erreur ;
    - sandbox native indisponible → mode natif retiré du sélecteur ;
    - native_network_isolation_guaranteed faux → le sélecteur de mode natif
      affiche un avertissement explicite avant confirmation. Ne jamais
      masquer cette information à l'utilisateur (ADR-005).

7. Portes humaines (shared/human-gate-button.component.ts)

    Un composant dédié, utilisé pour la validation du plan et des sections.

    - il appelle exclusivement l'endpoint de validation correspondant ;
    - il ne se déclenche jamais automatiquement : ni sur un score, ni sur
      une minuterie, ni sur un raccourci sans confirmation ;
    - il est désactivé tant que l'état courant ne permet pas la
      transition, et affiche l'état requis ;
    - le libellé mentionne explicitement l'engagement pris : valider une
      section signifie en assumer le contenu.

    Un test vérifie qu'aucun autre chemin du code frontend n'appelle un
    endpoint de validation.

8. Consentements (shared/consent-dialog.component.ts)

    À l'interception d'un ApiError de type ConsentRequired, le dialogue
    affiche le périmètre, le champ will_transmit du contrat — c'est-à-dire
    CE QUI SORTIRA réellement — et deux actions distinctes : accorder pour
    cette fois, accorder durablement.

    Ne jamais pré-cocher, ne jamais grouper plusieurs périmètres dans un
    consentement unique (ADR-010).

9. ERROR_STATE (shared/error-state-panel.component.ts)

    Présentation actionnable, jamais un simple message rouge :
    - ce qui a échoué : agent, section, essai numéro n sur 3 ;
    - ce qui a été tenté : messages de guardrail successifs, abrégés ;
    - ce que l'utilisateur peut faire : réessayer, ignorer cette section,
      abandonner la tâche — les trois actions de resume_from_error.

10. Panneaux de remplacement

    editor-panel.placeholder.ts et code-panel.placeholder.ts affichent le
    contenu brut et exposent en commentaire l'interface attendue par
    US-UI-002 et US-UI-003 : entrées, sorties, événements. Ils sont
    fonctionnels — lecture et enregistrement — sans être confortables.

11. Accessibilité et clavier

    - navigation entre panneaux au clavier, ordre de tabulation cohérent ;
    - rôles ARIA sur les panneaux et les dialogues, piège de focus dans les
      dialogues ;
    - la progression d'une tâche est annoncée par une région aria-live
      polie, mise à jour au maximum toutes les 2 s — pas à chaque trame,
      sous peine de rendre le lecteur d'écran inutilisable ;
    - aucune information portée par la seule couleur : les niveaux de
      gravité des findings portent aussi un libellé.

12. Tests

    Composants testés avec TestBed et harnais ; SSE testé contre une
    implémentation factice d'EventSource.

    test_layout_default_registers_all_panels
    test_layout_persisted_per_project_via_api
    test_layout_not_persisted_in_browser_storage
    test_unknown_panel_in_saved_layout_is_ignored
    test_layout_version_handled

    test_token_events_batched_per_frame
    test_single_signal_write_per_frame
    test_state_event_flushes_buffer_before_applying
    test_state_event_never_batched
    test_reconnect_uses_exponential_backoff
    test_reconnect_resyncs_via_get_task
    test_duplicate_sequence_ignored
    test_out_of_order_event_ignored
    test_stream_closed_on_destroy
    test_stream_closed_on_project_change
    test_single_connection_shared_across_components

    test_store_exposes_readonly_signals_only
    test_no_business_state_in_components
    test_effect_not_used_to_sync_signals

    test_export_disabled_when_quarto_absent
    test_generation_disabled_when_ollama_unavailable
    test_native_mode_warns_when_isolation_not_guaranteed
    test_missing_gpu_hides_vram_without_error

    test_human_gate_only_caller_of_validate_endpoints
    test_human_gate_disabled_in_wrong_state
    test_no_automatic_validation_path

    test_consent_dialog_shows_will_transmit
    test_consent_not_prechecked
    test_consent_scopes_not_grouped

    test_error_state_offers_retry_skip_abort
    test_aria_live_throttled_to_two_seconds
    test_severity_not_conveyed_by_colour_alone

INTERDICTIONS

- NgModules.
- État métier détenu par un composant.
- effect() employé pour synchroniser deux signaux.
- Mise à jour de signal par token individuel.
- Persistance de la disposition dans le stockage du navigateur.
- Plus d'une connexion SSE par tâche.
- Connexion SSE non fermée à la destruction ou au changement de projet.
- Appel d'un endpoint de validation ailleurs que depuis le composant de
  porte humaine.
- Consentement pré-coché ou groupé.
- Masquer l'absence de garantie d'isolation réseau.
- Duplication de logique métier du backend dans le frontend.
- Modification d'un fichier backend.

CRITÈRES D'ACCEPTATION

    cd frontend && npm ci
    ng build --configuration production
    ng test --watch=false --browsers=ChromeHeadless
    ng lint

Les quatre commandes doivent sortir en code 0.

FORMAT DE RÉPONSE

1. Plan d'implémentation, 10 lignes maximum.
2. Contenu intégral de chaque fichier, un bloc par fichier, chemin exact
   en en-tête.
3. Sortie attendue des quatre commandes.
4. Section "Réserves" : notamment toute divergence constatée entre
   contracts/openapi.yaml et ce que l'interface aurait besoin de
   connaître. Ne comble jamais un manque du contrat par une supposition
   sur le backend : signale-le.
```

## Message de commit attendu

```text
feat(frontend): coquille IDE Angular, Dockview et pont SSE vers Signals

Implémente US-801 (Backlog V0.3).

- Angular standalone, stores par domaine exposant des signaux en lecture
  seule ; aucun état métier dans les composants.
- Disposition Dockview persistée par projet via l'API, versionnée,
  tolérante aux panneaux inconnus.
- Pont SSE : tokens regroupés par trame, une écriture de signal par trame ;
  événements d'état appliqués immédiatement après vidage du tampon ;
  reconnexion avec resynchronisation par GET /tasks/{id} ; connexion unique
  partagée et fermée à la destruction.
- Gating par /system/capabilities : désactivation expliquée plutôt
  qu'échec à l'usage.
- Portes humaines centralisées dans un composant unique, seul appelant des
  endpoints de validation.
- Consentements non pré-cochés et non groupés, affichant ce qui sortira.

Refs: US-801, ADR-004, ADR-005, ADR-010, ADR-011
```
