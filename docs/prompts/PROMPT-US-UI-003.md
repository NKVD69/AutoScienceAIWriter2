# Prompt d'implémentation — US-UI-003 (éditeur de code Monaco)

Section US-UI-003 de `docs/prompts/03-story-prompts.md`.
Conforme au Backlog V0.3, aux Spécifications V0.3 §8 et aux ADR-005, ADR-011.

---

```text
Tu es un agent de codage senior, spécialisé en intégration de Monaco
Editor, Angular et interfaces d'exécution de code.

PROJET

Science AI Writer IDE — exécution de scripts Python d'analyse scientifique
en environnement isolé, production de figures pour le document.

SOURCES DE VÉRITÉ, PAR ORDRE DÉCROISSANT

1. Ce prompt.
2. ADR-005 (sandbox à deux niveaux), ADR-011 (Angular, Signals),
   ADR-010 (consentement).
3. Backlog V0.3, US-UI-003.
4. Spécifications V0.3, §8.
5. contracts/openapi.yaml, sections /code.

DÉPENDANCES REQUISES : US-801 (coquille, stores, capacités), US-401
(agent code et artefacts côté backend).

USER STORY

US-UI-003 — En tant que chercheur, je veux écrire, relire et exécuter des
scripts d'analyse depuis l'IDE, afin de produire mes figures sans quitter
l'environnement de rédaction.

RÈGLE D'ISOLATION À REFLÉTER FIDÈLEMENT

Deux niveaux existent (ADR-005) :
  - niveau 1 WebAssembly : isolation réseau et disque garantie, identique
    sur Windows et Linux ; c'est le niveau de tout code produit par un
    agent, sans exception et sans possibilité de le changer ;
  - niveau 2 natif : capacités plus larges, sous consentement, et — sous
    Windows — SANS garantie d'isolation réseau.

L'interface doit exposer cette différence honnêtement. Ne jamais présenter
le niveau 2 comme « sécurisé », ne jamais masquer l'avertissement Windows,
ne jamais proposer de changer le niveau d'un code d'origine agent.

PÉRIMÈTRE STRICT

Tu implémentes :

- le panneau de code remplaçant le composant de remplacement d'US-801 ;
- l'éditeur Monaco configuré pour Python ;
- le sélecteur de niveau d'exécution et ses garde-fous d'affichage ;
- la console de sortie et la présentation des erreurs ;
- la galerie d'artefacts et leur rattachement à une section ;
- l'affichage de la traçabilité de reproduction ;
- les tests.

Tu n'implémentes PAS :

- l'exécution elle-même (US-004, US-401) ;
- les notebooks (US-JUP-001) ;
- l'éditeur Quarto (US-UI-002) ;
- toute modification du backend.

FICHIERS À CRÉER OU MODIFIER

- frontend/src/app/features/code/code-panel.component.ts
- frontend/src/app/features/code/monaco-setup.ts
- frontend/src/app/features/code/sandbox-level-selector.component.ts
- frontend/src/app/features/code/output-console.component.ts
- frontend/src/app/features/code/artifact-gallery.component.ts
- frontend/src/app/features/code/reproducibility-card.component.ts
- frontend/src/app/state/code.store.ts
- frontend/src/app/layout/panel-registry.ts        (modification)
- frontend/src/app/features/code/code-panel.placeholder.ts  (suppression)
- frontend/src/app/**/*.spec.ts                    (tests correspondants)

Aucun fichier backend.

EXIGENCES D'IMPLÉMENTATION

1. Monaco

    - chargement PARESSEUX : Monaco pèse plusieurs mégaoctets et ne doit
      pas entrer dans le paquet initial. Import dynamique à la première
      ouverture du panneau, avec indicateur de chargement ;
    - langage Python, thème suivant celui de l'application ;
    - workers configurés correctement pour le service de langage ; un
      Monaco sans worker dégrade en éditeur de texte silencieusement, ce
      qui doit être détecté et signalé en développement ;
    - pas de complétion par service de langage distant : aucune analyse
      Python côté serveur n'existe dans ce projet, et en inventer une
      serait hors périmètre.

2. Sélecteur de niveau (sandbox-level-selector.component.ts)

    Trois cas d'affichage :

    a. CODE D'ORIGINE AGENT : le sélecteur est absent, remplacé par une
       mention indiquant que l'exécution se fait en WebAssembly, isolation
       réseau et disque garantie. Aucun contrôle ne permet de changer.

    b. CODE UTILISATEUR, NIVEAU 1 : proposé par défaut. Mention de
       l'isolation garantie et de la liste des bibliothèques disponibles.

    c. CODE UTILISATEUR, NIVEAU 2 : proposé seulement si
       capabilities.native_sandbox_available est vrai. Avant confirmation,
       un avertissement affiche les limites réelles, et sous Windows —
       native_network_isolation_guaranteed faux — indique explicitement que
       l'isolation réseau n'est pas garantie. Le consentement
       native_execution est demandé par le dialogue d'US-801.

    Le libellé du niveau 2 n'emploie jamais le mot « sécurisé ».

3. Console (output-console.component.ts)

    - stdout et stderr distingués visuellement ET par un libellé, pas par
      la seule couleur ;
    - sortie longue virtualisée : une exécution peut produire des dizaines
      de milliers de lignes, et les afficher toutes fige le navigateur.
      Fenêtre glissante avec conservation des 5000 dernières lignes et
      mention explicite de la troncature ;
    - un limit_exceeded est présenté distinctement d'une erreur de code :
      dépassement mémoire, temps CPU ou temps mural, avec la limite
      atteinte. L'utilisateur doit savoir s'il a écrit du code faux ou du
      code trop coûteux ;
    - PackageUnavailableInWasm est présenté comme une PROPOSITION —
      exécuter au niveau 2 — et non comme une erreur. C'est une décision
      humaine (ADR-005).

4. Galerie d'artefacts (artifact-gallery.component.ts)

    - vignettes des figures, aperçu des tableaux, téléchargement des
      données ;
    - chaque artefact indique s'il était DÉCLARÉ ou INATTENDU : un fichier
      produit sans avoir été annoncé par l'agent est conservé mais signalé,
      et ne peut pas être rattaché en un clic sans confirmation (US-401) ;
    - rattachement à une section : sélection de la section cible, aperçu de
      la référence Quarto qui sera insérée, puis appel de l'API ;
    - la suppression d'un artefact référencé est refusée par le backend
      avec 409 ; l'interface l'anticipe en désactivant l'action et en
      nommant la section qui le référence.

5. Traçabilité (reproducibility-card.component.ts)

    Pour chaque artefact : graine aléatoire, versions des bibliothèques,
    SHA-256 abrégés des jeux de données, date, durée, niveau de sandbox.

    Bouton « copier les informations de reproduction » produisant un bloc
    de texte insérable en annexe de thèse. C'est ce qui permet de répondre
    en soutenance à la question « comment cette figure a-t-elle été
    obtenue ».

6. Store (state/code.store.ts)

    Signaux en lecture seule : code courant, exécution en cours, historique
    des exécutions, artefacts, niveau sélectionné, consentement natif
    accordé. Aucun état métier dans les composants (ADR-011).

7. Tests

    test_monaco_loaded_lazily_not_in_initial_bundle
    test_monaco_worker_failure_detected
    test_python_language_configured

    test_agent_origin_hides_level_selector
    test_agent_origin_shows_wasm_notice
    test_level2_hidden_when_unavailable
    test_level2_warns_before_confirmation
    test_windows_warns_network_not_guaranteed
    test_level2_label_never_says_secure

    test_console_distinguishes_streams_beyond_colour
    test_console_virtualizes_long_output
    test_console_states_truncation
    test_limit_exceeded_presented_distinctly
    test_package_unavailable_presented_as_proposal

    test_undeclared_artifact_marked
    test_undeclared_artifact_requires_confirmation_to_attach
    test_attach_previews_quarto_reference
    test_delete_disabled_for_referenced_artifact
    test_delete_names_referencing_section

    test_reproducibility_card_lists_seed_versions_hashes
    test_copy_reproduction_block

    test_store_exposes_readonly_signals_only

INTERDICTIONS

- Charger Monaco dans le paquet initial.
- Permettre de changer le niveau d'un code d'origine agent.
- Employer le mot « sécurisé » pour le niveau natif.
- Masquer l'avertissement d'isolation sous Windows.
- Afficher une sortie longue sans virtualisation.
- Rattacher un artefact inattendu sans confirmation.
- Inventer un service de langage Python côté serveur.
- Modification d'un fichier backend.

CRITÈRES D'ACCEPTATION

    cd frontend && ng build --configuration production
    ng test --watch=false --browsers=ChromeHeadless
    ng lint

La construction de production doit montrer Monaco dans un fragment
paresseux, pas dans le paquet principal.

FORMAT DE RÉPONSE

1. Plan d'implémentation, 10 lignes maximum.
2. Contenu intégral de chaque fichier.
3. Sortie attendue des commandes, fragments de construction inclus.
4. Section "Réserves".
```

## Message de commit attendu

```text
feat(frontend): panneau de code Monaco, console et galerie d'artefacts

Implémente US-UI-003 (Backlog V0.3).

- Monaco chargé paresseusement, hors paquet initial.
- Niveau d'isolation présenté honnêtement : sélecteur absent pour le code
  d'agent, avertissement Windows explicite au niveau natif, jamais le mot
  « sécurisé ».
- Console virtualisée, flux distingués autrement que par la couleur,
  dépassement de limite présenté distinctement d'une erreur de code.
- Artefacts inattendus signalés et non rattachables sans confirmation.
- Carte de reproduction copiable pour annexe de thèse.

Refs: US-UI-003, ADR-005, ADR-011
```
