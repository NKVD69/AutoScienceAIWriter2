# Prompt d'implémentation — US-WORKFLOW-001 (mode pipeline automatique contrôlé)

Conforme au Backlog V0.3, aux Spécifications V0.3 §5.2 et aux ADR-004, ADR-008.

---

```text
Tu es un agent de codage senior, spécialisé en orchestration de tâches
longues, points d'arrêt et conception d'automatismes sûrs.

SOURCES DE VÉRITÉ : ce prompt > ADR-004 (portes humaines), ADR-008
(circuit breaker), ADR-009 (audit) > Backlog V0.3 US-WORKFLOW-001 >
Spécifications V0.3 §5.2.

DÉPENDANCES : US-201, US-202, US-PLAN-001, US-301, US-302.

USER STORY

US-WORKFLOW-001 — En tant que doctorant, je veux lancer la rédaction de
plusieurs sections d'affilée, afin de récupérer un premier jet de chapitre
sans relancer chaque section à la main.

LA LIGNE À NE PAS FRANCHIR

L'automatisation porte sur l'ENCHAÎNEMENT, jamais sur la VALIDATION.

Un pipeline qui validerait automatiquement les sections détruirait la
propriété centrale du produit : chaque affirmation d'un mémoire est assumée
par son auteur. Les portes humaines d'ADR-004 restent infranchissables, et
un test énumère les chemins pour le prouver.

Ce que le pipeline fait : enchaîner rédaction et relecture sur plusieurs
nœuds. Ce qu'il produit : des sections à l'état SECTION_REVIEWING, en
attente de l'auteur. Rien de plus.

PÉRIMÈTRE

Définition d'un lot, exécution séquentielle, points d'arrêt configurables,
arrêt sur erreur, reprise, suivi et annulation, estimation de durée.

PAS de parallélisme : le modèle est unique et les générations sont
sérialisées (ADR-003). Lancer deux sections en parallèle ne les produirait
pas plus vite et saturerait le contexte.

FICHIERS

- backend/app/services/pipeline_service.py
- backend/app/models/pipeline.py
- backend/app/agents/graph.py                   (modification : nœud de lot)
- backend/app/api/v1/pipeline.py
- backend/app/api/v1/__init__.py                (modification)
- frontend/src/app/features/pipeline/pipeline-panel.component.ts
- backend/tests/tests_agents/test_pipeline.py
Aucun autre fichier.

EXIGENCES

1. Définition d'un lot : liste ordonnée de nœuds de plan, options
   `run_review` (défaut vrai) et `auto_correct` (défaut FAUX, cohérent avec
   US-302). Le lot est persisté : il survit à un redémarrage.

2. Exécution strictement SÉQUENTIELLE, dans l'ordre du plan. Chaque nœud
   parcourt SECTION_DRAFTING, SECTION_GUARDRAIL, puis SECTION_REVIEWING si
   `run_review`. Le lot s'arrête là pour ce nœud et passe au suivant.

3. POINTS D'ARRÊT configurables :
   - après chaque section (mode pas à pas assisté) ;
   - après N sections ;
   - à la fin du lot uniquement.
   Un point d'arrêt met le lot en PAUSED, il ne l'annule pas.

4. ARRÊT SUR ERREUR — comportement par défaut, non modifiable :
   un ERROR_STATE sur un nœud ARRÊTE le lot. Ne jamais poursuivre en
   ignorant l'échec : les nœuds suivants s'appuient souvent sur le contenu
   des précédents, et enchaîner sur une base fautive produit un chapitre
   entier à jeter.

   Exception unique et explicite : InsufficientContextError (US-301) marque
   le nœud comme non rédigeable, faute de sources, et le lot CONTINUE. Ce
   n'est pas une erreur du système mais un manque de matière, propre à ce
   nœud.

5. ANNULATION prise en compte entre deux nœuds et entre deux étapes d'un
   nœud, jamais au milieu d'un appel LLM. Une génération entamée va à son
   terme : l'interrompre gaspillerait le calcul sans rien libérer.

6. ESTIMATION DE DURÉE fondée sur les durées observées des générations
   précédentes du même projet, exprimée en FOURCHETTE et jamais en date
   d'achèvement. En l'absence d'historique, aucune estimation n'est
   affichée — mieux vaut rien qu'un chiffre inventé.

7. Audit : début et fin de lot, et chaque nœud traité avec son issue. Le
   journal doit permettre de reconstituer quelles sections proviennent d'un
   enchaînement automatique — information reprise par la déclaration
   d'usage de l'IA (US-EXPORT-003).

8. Tests

   test_pipeline_never_validates_a_section
   test_no_path_from_pipeline_to_validated_state
   test_execution_is_sequential_not_parallel
   test_batch_persisted_across_restart
   test_breakpoint_after_each_section
   test_breakpoint_after_n_sections
   test_paused_is_resumable
   test_error_stops_batch
   test_error_stop_not_configurable
   test_insufficient_context_marks_node_and_continues
   test_cancel_between_nodes
   test_cancel_not_mid_llm_call
   test_duration_estimate_is_range
   test_no_estimate_without_history
   test_no_completion_date
   test_audit_records_pipeline_origin_per_section

INTERDICTIONS

- Valider une section automatiquement.
- Rendre configurable l'arrêt sur erreur.
- Exécuter plusieurs nœuds en parallèle.
- Interrompre un appel LLM en cours.
- Afficher une date d'achèvement.
- Activer auto_correct par défaut.

ACCEPTATION : pytest -q backend/tests/tests_agents ; ruff

FORMAT : plan 10 lignes, fichiers complets, sorties, "Réserves".
```
