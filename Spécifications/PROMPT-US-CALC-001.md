# Prompt d'implémentation — US-CALC-001 (codes de calcul externes via WSL2 ou Linux natif)

Priorité P2. Conforme au Backlog V0.3, aux Spécifications V0.3 §8.3 et aux ADR-005, ADR-010, ADR-012.

---

```text
Tu es un agent de codage senior, spécialisé en intégration de codes de
calcul scientifique, exécution longue et gestion de dépendances système.

SOURCES DE VÉRITÉ : ce prompt > ADR-005 (sandbox), ADR-010 (consentement),
ADR-012 (pas de Docker) > Backlog V0.3 US-CALC-001 > Spécifications V0.3
§8.3.

DÉPENDANCES : US-004, US-401, US-DATA-001.

USER STORY

US-CALC-001 — En tant qu'ingénieur, je veux lancer mes calculs OpenFOAM ou
Serpent depuis l'IDE et récupérer leurs résultats, afin d'intégrer mes
simulations au mémoire sans changer d'outil.

AVERTISSEMENT DE CONCEPTION — À LIRE INTÉGRALEMENT

Cette story est la plus éloignée du modèle de sécurité du produit. Elle
exécute des binaires système arbitraires, potentiellement pendant des
heures, avec accès complet au disque et au réseau. Aucune des garanties
d'ADR-005 ne s'y applique.

Elle est donc conçue comme un LANCEUR EXPLICITE, pas comme une extension de
la sandbox :

  - jamais accessible à un agent, en aucune circonstance ;
  - jamais activée par défaut ;
  - consentement dédié, distinct de native_execution, affichant sans
    euphémisme que l'exécution n'est pas isolée ;
  - la commande est composée par l'UTILISATEUR, pas générée par un LLM.

Si tu te surprends à concevoir une interface où un agent propose une
commande de calcul, arrête-toi : c'est hors périmètre et contraire à
l'intention.

PÉRIMÈTRE

Détection de l'environnement, définition d'un cas de calcul, lancement,
suivi de journal, récupération des résultats, traçabilité.

PAS de génération de cas par un agent. PAS d'installation des codes. PAS de
soumission à un ordonnanceur de cluster.

FICHIERS

- backend/app/compute/environment.py
- backend/app/compute/runner.py
- backend/app/compute/case.py
- backend/app/services/compute_service.py
- backend/app/api/v1/compute.py
- frontend/src/app/features/compute/compute-panel.component.ts
- backend/tests/tests_compute/
Aucun autre fichier.

EXIGENCES

1. DÉTECTION D'ENVIRONNEMENT. Sous Windows, présence de WSL2 et
   distributions disponibles via `wsl.exe -l -v`. Sous Linux, exécution
   native. Les binaires attendus sont déclarés par l'utilisateur ; l'outil
   vérifie seulement qu'ils répondent, il n'installe rien.

   Aucun code de calcul n'est présumé présent. L'absence désactive la
   fonctionnalité dans l'interface, avec explication (US-801).

2. CAS DE CALCUL. Un cas est un RÉPERTOIRE du projet, plus une commande et
   des variables d'environnement, tous saisis par l'utilisateur. Table
   compute_case(project_id, name, working_dir, command, env_json,
   created_at).

   La commande n'est jamais construite par concaténation depuis un
   formulaire à champs multiples : l'utilisateur saisit la ligne complète,
   qui est exécutée telle quelle. Un assemblage automatique donnerait
   l'illusion d'un contrôle qui n'existe pas.

3. EXÉCUTION LONGUE. Détachée, avec identifiant de processus persisté :
   un calcul doit survivre au redémarrage du backend. À la reprise, l'état
   est retrouvé par le fichier de verrou et le journal, pas par une
   référence en mémoire.

   Limites : temps mural maximal configurable, défaut 24 h ; arrêt
   utilisateur possible à tout moment, propagé au groupe de processus.

4. JOURNAL. Suivi par lecture incrémentale du fichier de sortie, diffusé
   par le flux SSE existant. Rotation à 100 Mo ; au-delà, seules les
   200 dernières lignes sont diffusées, le fichier complet restant sur le
   disque. Un solveur bavard produit des gigaoctets de journal.

5. RÉSULTATS. À la fin, les fichiers désignés par des motifs déclarés dans
   le cas sont enregistrés comme artefacts (US-401), avec la commande, la
   version du code relevée si elle est interrogeable, la durée, la machine
   et les SHA-256 des entrées.

   Le niveau de sandbox enregistré vaut `external`, distinct des niveaux 1
   et 2 : la traçabilité ne doit jamais laisser croire qu'un résultat
   OpenFOAM a été produit sous isolation.

6. CONSENTEMENT dédié `external_compute`. Le dialogue énonce : exécution
   non isolée, accès complet au disque et au réseau, commande fournie par
   l'utilisateur, durée potentiellement longue. Consentement par cas, non
   global.

7. Tests

   test_agent_cannot_create_or_launch_case
   test_disabled_when_no_environment_detected
   test_command_stored_verbatim_not_assembled
   test_requires_external_compute_consent
   test_consent_is_per_case
   test_consent_text_states_no_isolation
   test_run_survives_backend_restart
   test_state_recovered_from_lockfile
   test_wall_timeout_enforced
   test_stop_propagates_to_process_group
   test_log_rotation_at_100mb
   test_only_tail_streamed_beyond_threshold
   test_results_recorded_with_level_external
   test_artifact_never_labeled_isolated

INTERDICTIONS

- Rendre cette fonctionnalité accessible à un agent.
- Générer ou assembler une commande automatiquement.
- Enregistrer un résultat sous un niveau de sandbox 1 ou 2.
- Réutiliser le consentement native_execution.
- Installer un code de calcul.
- Diffuser un journal complet sans limite.

ACCEPTATION : pytest -q backend/tests/tests_compute ; ruff

FORMAT : plan 10 lignes, fichiers complets, sorties, "Réserves".
```
