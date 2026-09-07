# Prompt d'implémentation — US-201 et US-202 (machine à états, guardrails, circuit breaker)

Sections US-201 et US-202 de `docs/prompts/03-story-prompts.md`.
Les deux stories sont livrées ensemble : le circuit breaker est un compteur porté par l'état du graphe, il ne s'implémente pas séparément sans code jetable.

Conforme au Backlog V0.3, aux Spécifications V0.3 §5 et aux ADR-004, ADR-008.

---

```text
Tu es un agent de codage senior, spécialisé en machines à états,
orchestration LangGraph, sorties structurées et résilience applicative.

PROJET

Science AI Writer IDE — IDE scientifique local multi-agents, modèle 7B
quantifié en local, 10 Go de VRAM.

SOURCES DE VÉRITÉ, PAR ORDRE DÉCROISSANT

1. Ce prompt.
2. ADR-004 (LangGraph déterministe), ADR-008 (guardrails et circuit
   breaker), ADR-003 (modèle unique persistant), ADR-009 (audit).
3. Backlog V0.3, US-201 et US-202.
4. Spécifications V0.3, §5.
5. contracts/openapi.yaml, schémas TaskState et Task.

DÉPENDANCES REQUISES : US-001, US-002, US-003, US-101.

USER STORIES

US-201 — En tant que développeur, je veux une machine à états déterministe
dont chaque transition est validée structurellement, afin que le système
soit auditable et ne dépende jamais du bon vouloir d'un LLM pour progresser.

US-202 — En tant qu'utilisateur, je veux que le système s'arrête et me
sollicite plutôt que de boucler indéfiniment, afin qu'une machine locale ne
soit jamais mobilisée des heures sans résultat.

CONTEXTE DE LA DÉCISION

Deux approches existaient : agents en discussion libre, ou machine à états
explicite. La discussion libre accumule l'historique dans le contexte,
consomme des tokens sans borne, sature le cache KV sur 10 Go de VRAM, et
rend l'audit impossible : on ne peut pas établir pourquoi une section a été
écrite ainsi. Sur un modèle 7B, elle échoue de surcroît fréquemment à
converger.

Le graphe est donc déterministe : aucun agent ne décide du prochain agent.
Chaque nœud reçoit un état typé et retourne un état typé.

PÉRIMÈTRE STRICT

Tu implémentes :

- l'énumération des états et le modèle GraphState ;
- la construction du graphe et ses transitions ;
- la persistance et la reprise de l'état ;
- le mécanisme de guardrail générique, réutilisable par tout agent ;
- les compteurs et le circuit breaker ;
- l'émission des événements et l'audit des transitions ;
- un agent factice de démonstration, uniquement pour les tests ;
- les tests.

Tu n'implémentes PAS :

- les agents réels : plan (US-PLAN-001), rédacteur (US-301), relecteur
  (US-302), code (US-401) ;
- les prompts métier ;
- les endpoints des stories ultérieures ;
- le frontend.

Tu poses l'ossature que les stories d'agents viendront brancher. Toute
signature que tu définis ici sera consommée telle quelle par US-PLAN-001 :
sois explicite et stable.

FICHIERS À CRÉER OU MODIFIER

- backend/app/agents/__init__.py
- backend/app/agents/state.py
- backend/app/agents/graph.py
- backend/app/agents/guardrails.py
- backend/app/agents/breaker.py
- backend/app/agents/base_agent.py
- backend/app/services/workflow_service.py
- backend/app/services/task_service.py         (modification : événements)
- backend/app/core/errors.py                   (modification)
- backend/tests/tests_agents/__init__.py
- backend/tests/tests_agents/conftest.py
- backend/tests/tests_agents/test_graph.py
- backend/tests/tests_agents/test_guardrails.py
- backend/tests/tests_agents/test_breaker.py
- backend/tests/tests_agents/test_resume.py

Aucun autre fichier.

EXIGENCES D'IMPLÉMENTATION

1. États (state.py)

    WorkflowState reprend exactement l'énumération TaskState de
    contracts/openapi.yaml. Aucun état supplémentaire, aucun renommage.

    class GraphState(TypedDict):
        project_id: int
        plan_id: int | None
        current_node_id: int | None
        state: WorkflowState
        retry_count: int
        review_loop_count: int
        ingest_retry_count: int
        last_error: str | None
        payload_json: dict

    L'état est SÉRIALISABLE en JSON dans son intégralité : aucun objet
    Python vivant, aucune connexion, aucun callable. C'est la condition de
    la reprise après arrêt.

2. Graphe (graph.py)

    Transitions déclarées, conformes aux Spécifications V0.3 §5.2 :

      PROJECT_CREATED    -> SOURCES_INGESTING
      SOURCES_INGESTING  -> SOURCES_READY | ERROR_STATE
      SOURCES_READY      -> PLAN_DRAFTING
      PLAN_DRAFTING      -> PLAN_GUARDRAIL
      PLAN_GUARDRAIL     -> PLAN_REVIEW | PLAN_DRAFTING | ERROR_STATE
      PLAN_REVIEW        -> PLAN_VALIDATED            [PORTE HUMAINE]
      PLAN_VALIDATED     -> SECTION_DRAFTING
      SECTION_DRAFTING   -> SECTION_GUARDRAIL
      SECTION_GUARDRAIL  -> SECTION_REVIEWING | SECTION_DRAFTING | ERROR_STATE
      SECTION_REVIEWING  -> SECTION_CORRECTING | SECTION_VALIDATED [PORTE HUMAINE]
      SECTION_CORRECTING -> SECTION_REVIEWING | ERROR_STATE
      SECTION_VALIDATED  -> SECTION_DRAFTING | DOCUMENT_READY
      DOCUMENT_READY     -> EXPORTING
      EXPORTING          -> EXPORTED | ERROR_STATE

    Règles impératives :

    - une transition non déclarée lève InvalidTransitionError. Le graphe
      refuse, il ne s'adapte pas ;
    - les PORTES HUMAINES ne sont franchissables que par un appel explicite
      à workflow_service.human_validate(...). Aucun nœud, aucun score de
      qualité, aucune configuration ne les franchit. Un test doit prouver
      qu'aucun chemin automatique n'y mène ;
    - ERROR_STATE est TERMINAL sans action humaine : aucune transition
      sortante automatique. La seule sortie est un appel explicite de
      reprise par l'utilisateur ;
    - chaque transition écrit dans task ET produit une entrée d'audit avant
      d'être considérée comme effectuée. Une transition non journalisée est
      une transition qui n'a pas eu lieu.

3. Agent de base (base_agent.py)

    class Agent(Protocol):
        name: AgentName
        output_model: type[BaseModel]
        def build_user_message(self, state: GraphState, ctx: dict) -> str
        async def run(self, state: GraphState, ctx: dict) -> str   # texte brut du LLM

    L'agent retourne le TEXTE BRUT. Il ne désérialise pas, ne valide pas,
    ne décide pas de la suite. La validation appartient au guardrail, la
    transition au graphe. Cette séparation est ce qui rend les deux
    testables indépendamment.

4. Guardrail générique (guardrails.py)

    async def validate_output(
        raw: str, model: type[BaseModel]
    ) -> GuardrailResult

    GuardrailResult : ok, parsed | None, error_message | None,
    error_path | None.

    Comportement :
    - extraction du JSON même si le modèle l'a entouré de texte ou de
      délimiteurs Markdown : recherche du premier objet équilibré. C'est
      une tolérance de FORMAT, pas de CONTENU ;
    - désérialisation Pydantic ; en cas d'échec, construction d'un message
      TECHNIQUE ET PRÉCIS : chemin du champ (loc), contrainte violée (msg),
      valeur reçue tronquée à 200 caractères ;
    - le message d'erreur est destiné au modèle, pas à l'utilisateur : il
      doit être directement actionnable pour une correction.

    INTERDICTION FORMELLE : ne jamais réparer un JSON par expression
    régulière, ne jamais compléter un champ manquant par une valeur par
    défaut inventée, ne jamais tronquer une liste trop longue pour la faire
    passer. Un guardrail qui répare masque le défaut et produit des données
    silencieusement fausses. Il valide ou il rejette.

    Le nœud de guardrail relance TOUJOURS LE MÊME AGENT, jamais l'agent de
    relecture : une erreur de format n'est pas un problème de fond.

5. Circuit breaker (breaker.py)

    Compteurs portés par GraphState, jamais par une variable de module :
    l'état doit survivre à un redémarrage.

      guardrail -> agent                     : 3 essais
      SECTION_REVIEWING <-> SECTION_CORRECTING : 3 boucles
      ingestion d'une source                 : 2 essais

    Au dépassement :
    - guardrail : ERROR_STATE, dernier message d'erreur exposé ;
    - boucle de relecture : PAUSE et proposition de la MEILLEURE version
      rencontrée, pas de la dernière. Conserver la version au meilleur
      score dans payload_json au fil des itérations ;
    - ingestion : source marquée en échec, pipeline poursuivi.

    Les compteurs sont remis à zéro à l'entrée dans un nouveau nœud de
    plan ou de section, jamais globalement.

6. Reprise (workflow_service.py)

    - au démarrage, toute tâche dans un état non terminal est rechargée
      depuis task et reprise à cet état ;
    - une tâche interrompue pendant un appel LLM reprend au nœud, pas au
      milieu de l'appel : les nœuds sont idempotents par conception ;
    - resume_from_error(task_id, action) où action vaut retry | skip |
      abort, seul chemin de sortie d'ERROR_STATE.

7. Événements

    Émission vers le flux SSE, types conformes à l'openapi : state, token,
    progress, guardrail, error, done. L'événement guardrail porte le nom de
    l'agent, le numéro d'essai et le chemin du champ fautif — c'est ce qui
    permettra d'améliorer les prompts sur la base de faits.

8. Agent factice pour les tests (tests_agents/conftest.py)

    EchoAgent avec un output_model simple, alimenté par une file de
    réponses préenregistrées : sortie valide, JSON malformé, contrainte
    violée, JSON entouré de texte, boucle infinie de sorties invalides.
    Aucun test de cette story n'exige un Ollama réel.

9. Tests

    US-201 :
    test_declared_transition_succeeds
    test_undeclared_transition_raises
    test_transition_persisted_before_returning
    test_transition_writes_audit_entry
    test_no_automatic_path_reaches_plan_validated
    test_no_automatic_path_reaches_section_validated
    test_human_validate_crosses_gate
    test_error_state_has_no_automatic_outgoing_transition
    test_graph_state_is_json_serializable
    test_guardrail_extracts_json_wrapped_in_text
    test_guardrail_reports_field_path_and_constraint
    test_guardrail_never_fills_missing_field
    test_guardrail_never_truncates_oversized_list
    test_guardrail_reruns_same_agent_not_reviewer

    US-202 :
    test_breaker_counters_live_in_state_not_module
    test_guardrail_error_state_after_three_retries
    test_review_loop_pauses_after_three_iterations
    test_review_pause_returns_best_version_not_last
    test_ingest_retries_twice_then_marks_failed
    test_counters_reset_on_new_node
    test_counters_survive_restart
    test_resume_from_error_retry_skip_abort

    test_resume_after_restart_continues_from_persisted_state

INTERDICTIONS

- Transition non déclarée.
- Franchissement automatique d'une porte humaine.
- Sortie automatique d'ERROR_STATE.
- Réparation d'une sortie invalide par expression régulière ou valeur par
  défaut.
- Compteur en variable de module.
- Objet non sérialisable dans GraphState.
- Historique complet de conversation transmis à un agent.
- Modification d'un fichier hors périmètre.

CRITÈRES D'ACCEPTATION

    cd backend && pytest -q backend/tests/tests_agents
    ruff check backend/ && ruff format --check backend/

FORMAT DE RÉPONSE

1. Plan d'implémentation, 10 lignes maximum.
2. Contenu intégral de chaque fichier.
3. Sortie attendue des vérifications.
4. Section "Réserves".
```

## Messages de commit attendus

```text
feat(agents): machine à états LangGraph avec guardrails structurels

Implémente US-201 (Backlog V0.3).

- Transitions déclarées ; toute transition non déclarée est refusée.
- Portes humaines infranchissables automatiquement, prouvé par test.
- ERROR_STATE terminal sans action utilisateur.
- Guardrail générique : valide ou rejette, ne répare jamais.
- Persistance de l'état avant retour, audit de chaque transition.

Refs: US-201, ADR-004, ADR-008, ADR-009
```

```text
feat(agents): circuit breaker et reprise après erreur

Implémente US-202 (Backlog V0.3).

- Compteurs portés par GraphState, survivant à un redémarrage.
- Guardrail 3 essais, boucle de relecture 3 itérations, ingestion 2 essais.
- Pause de relecture proposant la meilleure version, pas la dernière.
- resume_from_error : retry | skip | abort, seule sortie d'ERROR_STATE.

Refs: US-202, ADR-008
```
