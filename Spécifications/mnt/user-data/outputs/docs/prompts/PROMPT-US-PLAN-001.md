# Prompt d'implémentation — US-PLAN-001 (plan : génération, édition, validation)

Section US-PLAN-001 de `docs/prompts/03-story-prompts.md`.
Story absente du Backlog V0.2, promue P0 par le Backlog V0.3 : sans elle, le MVP ne rédige rien.

---

```text
Tu es un agent de codage senior, spécialisé en orchestration d'agents LLM,
sorties structurées, machines à états et modélisation de données
arborescentes.

PROJET

Science AI Writer IDE — IDE scientifique local multi-agents pour la
rédaction de mémoires et thèses de 30 à 300 pages.

SOURCES DE VÉRITÉ, PAR ORDRE DÉCROISSANT

1. Ce prompt.
2. ADR-004 (LangGraph), ADR-008 (guardrails et circuit breaker),
   ADR-003 (modèle unique persistant).
3. Backlog V0.3, US-PLAN-001.
4. Spécifications techniques V0.3, §5.2, §5.4, §5.5.

USER STORY

US-PLAN-001 — En tant que doctorant, je veux obtenir un plan structuré à
partir de mon sujet, puis le corriger et le valider, afin que la rédaction
s'appuie sur une structure que j'ai approuvée.

POURQUOI CETTE STORY EST P0

Le pipeline est : sujet -> plan validé -> rédaction section par section.
Sans plan, l'agent rédacteur n'a pas d'entrée. Le backlog précédent
contenait toute l'infrastructure et aucune génération de plan : exécuté tel
quel, il produisait un système incapable de rédiger. Cette story ferme ce
trou. Elle précède obligatoirement US-301.

PÉRIMÈTRE STRICT

Tu implémentes :

- les modèles Pydantic PlanTree et PlanNode ;
- l'agent de plan et son prompt système ;
- le guardrail de validation structurelle et sa boucle de correction ;
- les nœuds LangGraph PLAN_DRAFTING, PLAN_GUARDRAIL, PLAN_REVIEW,
  PLAN_VALIDATED ;
- la persistance de l'arbre et son versionnement ;
- les endpoints d'édition et de validation ;
- le verrou empêchant la rédaction avant validation ;
- les tests.

Tu n'implémentes PAS :

- l'agent rédacteur (US-301) ;
- la relecture (US-302) ;
- la recherche bibliographique (US-BIBLIO-001) ;
- l'interface d'édition par glisser-déposer (US-UI-002) — tu exposes
  seulement l'API qu'elle consommera ;
- l'export.

DÉPENDANCES REQUISES : US-002 (schéma), US-003 (LLMManager),
US-201 (graphe et guardrails). Tu supposes leurs interfaces disponibles.

FICHIERS À CRÉER OU MODIFIER

- backend/app/models/plan.py
- backend/app/agents/plan_agent.py
- backend/app/agents/graph.py                  (modification : nœuds plan)
- backend/app/llm/prompts/registry.py          (modification : prompt PLAN)
- backend/app/services/plan_service.py
- backend/app/api/v1/plan.py
- backend/app/api/v1/__init__.py               (modification : routeur)
- backend/tests/tests_agents/test_plan_agent.py
- backend/tests/tests_api/test_plan_api.py
- backend/tests/tests_db/test_plan_persistence.py

Aucun autre fichier.

EXIGENCES D'IMPLÉMENTATION

1. Modèles (backend/app/models/plan.py)

    class PlanNode(BaseModel):
        title: str                      # 3 à 200 caractères
        objective: str                  # ce que la section doit établir
        target_words: int               # 150 à 20000
        children: list[PlanNode] = []

    class PlanTree(BaseModel):
        problematique: str              # 40 à 1500 caractères
        research_questions: list[str]   # 1 à 6 entrées
        methodology_note: str
        nodes: list[PlanNode]           # racine = chapitres

    Validateurs obligatoires :
    - profondeur comprise entre 3 et 5 niveaux ;
    - entre 4 et 12 chapitres racine ;
    - au moins 2 enfants par nœud non terminal, sinon rejet : un plan avec
      une sous-section unique est un défaut de structuration ;
    - titres uniques entre frères ;
    - somme des target_words des feuilles comprise entre 0,7 et 1,3 fois la
      longueur cible du projet.

    PlanNode est récursif : appelle model_rebuild().

2. Agent de plan (plan_agent.py)

    async def generate_plan(
        project: Project,
        approved_sources: list[SourceDocument],
        user_problematique: str | None,
        target_words: int,
    ) -> PlanTree

    Règles :
    - si user_problematique est fournie, elle est REPRISE TELLE QUELLE dans
      PlanTree.problematique, sans reformulation. Le plan est construit
      pour y répondre. C'est une exigence explicite du cahier des charges ;
    - l'agent reçoit les titres, années et résumés des sources approuvées,
      jamais leur texte intégral : le budget de contexte est de 8192 tokens ;
    - l'agent NE PRODUIT AUCUNE CITATION à ce stade. Le plan est une
      structure, pas un texte sourcé. Toute clé de citation dans la sortie
      est rejetée par le guardrail ;
    - température basse, 0.2 par défaut ;
    - le prompt demande une sortie JSON stricte, sans préambule ni
      délimiteurs Markdown.

3. Prompt système PLAN (registry.py)

    Constante de module, sans interpolation (ADR-003). Il doit poser :
    rôle d'architecte de plan de recherche de niveau doctoral ; interdiction
    d'inventer des références ; obligation de formuler des objectifs
    vérifiables par section ; obligation de signaler dans methodology_note
    les zones exigeant un arbitrage du directeur de recherche ; format JSON
    strict conforme au schéma fourni dans le message utilisateur.

    Les données variables — sujet, discipline, langue, sources, longueur —
    vont dans le message utilisateur, jamais dans le prompt système.

4. Guardrail (ADR-008)

    Nœud PLAN_GUARDRAIL, exécuté avant PLAN_REVIEW :
    - désérialisation en PlanTree ; en cas d'échec, construction d'un
      message d'erreur TECHNIQUE ET PRÉCIS — chemin du champ fautif,
      contrainte violée, valeur reçue tronquée ;
    - relance du MÊME agent avec ce message ajouté, jamais de l'agent de
      relecture ;
    - compteur retry_count ; au-delà de 3, transition vers ERROR_STATE
      avec le dernier message d'erreur exposé à l'utilisateur ;
    - chaque déclenchement produit une entrée d'audit avec la sortie
      fautive tronquée à 2 Ko.

5. Graphe (graph.py)

    PLAN_DRAFTING -> PLAN_GUARDRAIL -> PLAN_REVIEW -> PLAN_VALIDATED
    PLAN_GUARDRAIL -> PLAN_DRAFTING            (échec de validation)
    PLAN_GUARDRAIL -> ERROR_STATE              (retry_count > 3)

    PLAN_REVIEW -> PLAN_VALIDATED est une PORTE HUMAINE : aucune transition
    automatique ne la franchit, quel que soit le contenu du plan. L'état
    est persisté dans task à chaque transition.

6. Persistance et versionnement (plan_service.py)

    - l'arbre est aplati dans plan_node : parent_id, ordinal, level ;
    - chaque génération ou modification structurelle incrémente
      plan.version ; les versions antérieures restent lisibles ;
    - la validation renseigne plan.status = VALIDATED et un horodatage ;
    - un plan validé qui est modifié repasse en DRAFT, et les sections déjà
      rédigées rattachées à un nœud supprimé sont marquées ORPHANED — elles
      ne sont jamais supprimées silencieusement.

7. API (backend/app/api/v1/plan.py)

    POST   /api/v1/projects/{pid}/plan/generate     -> 202, tâche
    GET    /api/v1/projects/{pid}/plan              -> PlanTree + version + statut
    PUT    /api/v1/projects/{pid}/plan/nodes/{nid}  -> renommer, objectif, cible
    POST   /api/v1/projects/{pid}/plan/nodes        -> ajouter un nœud
    DELETE /api/v1/projects/{pid}/plan/nodes/{nid}  -> supprimer un sous-arbre
    POST   /api/v1/projects/{pid}/plan/reorder      -> réordonner par lot
    POST   /api/v1/projects/{pid}/plan/validate     -> porte humaine
    GET    /api/v1/projects/{pid}/plan/versions     -> historique

    La suppression d'un nœud portant des sections rédigées retourne 409
    avec la liste des sections concernées, sauf si le paramètre
    force=true est fourni ; dans ce cas les sections passent en ORPHANED.

8. Verrou de rédaction

    Toute demande de rédaction alors que plan.status != VALIDATED retourne
    409 Conflict avec un corps indiquant l'état courant et l'état requis.
    Ce contrôle est implémenté dans le service, pas seulement dans l'API :
    un appel interne au graphe doit être bloqué de la même manière.

9. Tests

    test_plan_tree_rejects_depth_below_3
    test_plan_tree_rejects_single_child_node
    test_plan_tree_rejects_duplicate_sibling_titles
    test_plan_tree_rejects_word_budget_out_of_range
    test_user_problematique_preserved_verbatim
    test_agent_receives_abstracts_not_full_text
    test_agent_output_with_citation_key_rejected
    test_guardrail_retries_same_agent_with_field_path
    test_guardrail_error_state_after_three_retries
    test_guardrail_logs_truncated_faulty_output
    test_plan_persisted_as_flat_tree
    test_plan_version_incremented_on_edit
    test_validated_plan_returns_to_draft_on_edit
    test_delete_node_with_sections_returns_409
    test_delete_node_force_marks_sections_orphaned
    test_reorder_preserves_children
    test_writing_blocked_before_validation      # via API ET via service
    test_human_gate_cannot_be_crossed_automatically

    L'agent est testé contre un FakeLLMBackend retournant des réponses
    préenregistrées : valide, JSON malformé, contrainte violée, citation
    interdite. Aucun test unitaire n'exige un Ollama réel.

INTERDICTIONS

- Reformuler une problématique fournie par l'utilisateur.
- Envoyer le texte intégral des sources à l'agent de plan.
- Produire ou accepter une citation dans la sortie du plan.
- Franchir PLAN_REVIEW -> PLAN_VALIDATED sans action humaine.
- Supprimer silencieusement une section rédigée.
- Interpoler une variable dans le prompt système.
- Modifier un fichier hors périmètre.

CRITÈRES D'ACCEPTATION

    cd backend && pytest -q -m "not integration"
    ruff check backend/ && ruff format --check backend/

FORMAT DE RÉPONSE

1. Plan d'implémentation, 10 lignes maximum.
2. Contenu intégral de chaque fichier.
3. Sortie attendue des vérifications.
4. Section "Réserves".
```

## Message de commit attendu

```text
feat(plan): génération, édition et validation du plan de recherche

Implémente US-PLAN-001 (Backlog V0.3, story nouvelle P0).

- PlanTree/PlanNode Pydantic avec validateurs de structure doctorale.
- Agent de plan : problématique utilisateur préservée telle quelle,
  résumés de sources uniquement, aucune citation produite.
- Guardrail avec chemin de champ fautif et circuit breaker à 3 essais.
- Porte humaine PLAN_REVIEW -> PLAN_VALIDATED infranchissable
  automatiquement ; rédaction verrouillée avant validation.
- Versionnement de l'arbre, sections orphelines jamais supprimées
  silencieusement.

Refs: US-PLAN-001, ADR-004, ADR-008
Corrige: D-06 (aucune génération de plan dans le backlog V0.2)
```
