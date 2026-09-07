# Prompt d'implémentation — US-LLM-002 (mode LLM distant, budget et suivi des tokens)

Priorité P2. Conforme au Backlog V0.3, aux Spécifications V0.3 §6 et aux ADR-003, ADR-010.

---

```text
Tu es un agent de codage senior, spécialisé en intégration de fournisseurs
LLM, comptabilité de tokens et conception de garde-fous de dépense.

SOURCES DE VÉRITÉ : ce prompt > ADR-010 (local strict, consentement),
ADR-003 (interface LLMBackend), ADR-009 (audit) > Backlog V0.3 US-LLM-002
> Spécifications V0.3 §6.

DÉPENDANCES : US-003 (LLMManager et LLMBackend), US-701 (audit), US-801.

USER STORY

US-LLM-002 — En tant qu'utilisateur, je veux pouvoir recourir à un modèle
distant plus capable pour certaines tâches, avec un budget que je contrôle,
afin d'améliorer la qualité sans dépense imprévue.

DEUX TENSIONS À TRAITER EXPLICITEMENT

1. CONFIDENTIALITÉ. Un LLM distant reçoit les prompts, donc des extraits de
   sources et du texte de la thèse. C'est la sortie de données la plus
   volumineuse de tout le produit — davantage que l'anti-plagiat, qui ne
   transmet que des passages. Le consentement remote_llm doit être au moins
   aussi explicite, et le mode local doit rester le défaut absolu.

2. DÉPENSE. Un pipeline automatique (US-WORKFLOW-001) sur trente sections
   peut consommer des millions de tokens. Un budget qui n'arrête rien n'est
   pas un budget : il doit BLOQUER, pas avertir.

PÉRIMÈTRE

Backend distant derrière l'interface existante, comptabilité, budget
bloquant, sélection par tâche, suivi.

PAS de fournisseur codé en dur. PAS de gestion de facturation. PAS de
bascule automatique du local vers le distant.

FICHIERS

- backend/app/llm/backends/remote.py
- backend/app/llm/budget.py
- backend/app/llm/routing.py
- backend/app/llm/manager.py                    (modification)
- backend/app/api/v1/llm.py
- backend/app/db/schema.sql                     (modification : token_usage)
- frontend/src/app/features/settings/llm-settings.component.ts
- frontend/src/app/features/dashboard/token-budget.component.ts
- backend/tests/tests_llm/test_remote.py
- backend/tests/tests_llm/test_budget.py
Aucun autre fichier.

EXIGENCES

1. BACKEND DISTANT. Implémente le Protocol LLMBackend de US-003, sans le
   modifier. Configuration par l'utilisateur : URL compatible OpenAI,
   identifiant de modèle, clé. Aucun fournisseur nommé dans le code, aucune
   URL par défaut.

   La clé est stockée dans le trousseau du système d'exploitation via
   `keyring` — jamais dans le fichier de projet, qui est destiné à être
   copié et partagé avec un directeur de recherche.

2. ROUTAGE PAR TÂCHE (llm/routing.py). Le mode distant n'est jamais global.
   L'utilisateur choisit, par agent, entre local et distant. Défaut : local
   partout.

   Un routage distant pour l'agent PLAN ou WRITER déclenche un
   avertissement supplémentaire : ce sont les agents qui transmettent le
   plus de contenu de la thèse.

3. COMPTABILITÉ. Table token_usage(project_id, agent, backend, model,
   prompt_tokens, completion_tokens, estimated_cost, created_at).

   Les tokens sont relevés dans la réponse du fournisseur, jamais estimés
   par un compteur local. Le coût est calculé depuis un tarif saisi par
   l'utilisateur ; en l'absence de tarif, le coût reste nul et l'interface
   affiche des tokens, pas une monnaie. Ne jamais afficher un coût inventé.

4. BUDGET BLOQUANT (llm/budget.py). Budget par projet, en tokens et
   optionnellement en monnaie, avec deux seuils :
   - avertissement à 80 %, notifié une seule fois ;
   - blocage à 100 % : tout appel distant est REFUSÉ avec
     BudgetExceededError. Le système ne bascule PAS silencieusement vers le
     modèle local : l'utilisateur décide, soit en relevant le budget, soit
     en repassant l'agent en local.

   Le contrôle précède l'appel, il n'est pas appliqué après coup.

   Dans un pipeline (US-WORKFLOW-001), un dépassement arrête le lot comme
   toute erreur, conformément à l'arrêt sur erreur non configurable.

5. AUDIT. Chaque appel distant journalise agent, modèle, fournisseur,
   tokens, et le fait qu'il s'agissait d'un appel sortant. Jamais le
   contenu du prompt (US-701).

6. INTERFACE. Le tableau de bord affiche la consommation par agent et le
   reste de budget, en tokens et, si un tarif est saisi, en monnaie. Le
   mode actif de chaque agent est visible en permanence dans la barre
   d'état : un utilisateur ne doit jamais ignorer qu'il travaille en
   distant.

7. Tests

   test_remote_backend_implements_protocol_unchanged
   test_no_hardcoded_provider_or_url
   test_api_key_stored_in_keyring_not_project_file
   test_key_never_in_project_backup
   test_routing_defaults_to_local_for_all_agents
   test_remote_routing_requires_consent
   test_writer_remote_triggers_extra_warning
   test_tokens_read_from_response_not_estimated
   test_no_cost_displayed_without_tariff
   test_warning_at_eighty_percent_once
   test_block_at_hundred_percent
   test_no_silent_fallback_to_local
   test_budget_checked_before_call
   test_pipeline_stops_on_budget_exceeded
   test_audit_records_call_without_prompt
   test_active_mode_visible_in_status_bar

INTERDICTIONS

- Coder en dur un fournisseur ou une URL.
- Stocker une clé dans le fichier de projet.
- Basculer automatiquement du distant vers le local.
- Afficher un coût sans tarif saisi.
- Estimer les tokens localement quand la réponse les fournit.
- Activer le mode distant globalement d'un seul geste.
- Modifier le Protocol LLMBackend.

ACCEPTATION : pytest -q backend/tests/tests_llm ; ruff ;
python scripts/check_no_cloud_calls.py

FORMAT : plan 10 lignes, fichiers complets, sorties, "Réserves".
```
