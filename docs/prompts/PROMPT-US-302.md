# Prompt d'implémentation — US-302 (relecture et score de qualité)

Section US-302 de `docs/prompts/03-story-prompts.md`.
Conforme au Backlog V0.3, aux Spécifications V0.3 §5.4 et aux ADR-008, ADR-004.

---

```text
Tu es un agent de codage senior, spécialisé en évaluation automatisée de
texte, sorties structurées et boucles de correction contrôlées.

PROJET

Science AI Writer IDE — rédaction de mémoires de niveau doctoral, modèle 7B
local, orchestration déterministe.

SOURCES DE VÉRITÉ, PAR ORDRE DÉCROISSANT

1. Ce prompt.
2. ADR-008 (guardrails et circuit breaker), ADR-004 (portes humaines),
   ADR-003 (prompts système constants).
3. Backlog V0.3, US-302.
4. Spécifications V0.3, §5.4.

DÉPENDANCES REQUISES : US-301, US-201, US-202.

USER STORY

US-302 — En tant que doctorant, je veux un rapport de relecture hiérarchisé
et un score de qualité, afin de savoir où porter mon attention avant de
valider une section.

CE QUE LA RELECTURE EST — ET N'EST PAS

La relecture est un CONSEIL, pas une autorisation. Elle ne valide jamais
une section : seule la porte humaine le fait (ADR-004). Un score de 95 ne
franchit rien.

La distinction avec le guardrail de US-301 est nette et doit être respectée
dans le code :

  - le GUARDRAIL contrôle la FORME et la VÉRACITÉ VÉRIFIABLE — clé de
    citation inconnue, chiffre non rattaché, DOI hors base. Contrôles
    syntaxiques, fiables, bloquants ;
  - la RELECTURE apprécie le FOND — cohérence, argumentation, style,
    complétude. Appréciation d'un LLM, faillible, jamais bloquante.

Ne fais jamais dépendre un contrôle syntaxique du jugement du relecteur, ni
l'inverse.

PÉRIMÈTRE STRICT

Tu implémentes :

- le modèle ReviewReport et ses validateurs ;
- l'agent relecteur et son prompt système ;
- le calcul du score et sa décomposition ;
- l'application des corrections en boucle contrôlée ;
- la conservation de la meilleure version ;
- l'API de relecture et de consultation du rapport ;
- les tests.

Tu n'implémentes PAS :

- l'anti-plagiat externe (US-601) ;
- la validation humaine elle-même (US-201, déjà faite) ;
- l'interface d'affichage du rapport (US-801).

FICHIERS À CRÉER OU MODIFIER

- backend/app/models/review.py
- backend/app/agents/reviewer_agent.py
- backend/app/agents/graph.py                  (modification : nœuds revue)
- backend/app/llm/prompts/registry.py          (modification : prompt REVIEWER)
- backend/app/services/review_service.py
- backend/app/api/v1/sections.py               (modification)
- backend/tests/tests_agents/test_reviewer_agent.py
- backend/tests/tests_agents/test_review_loop.py
- backend/tests/tests_api/test_review_api.py

Aucun autre fichier.

EXIGENCES D'IMPLÉMENTATION

1. Modèle (models/review.py)

    class Finding(BaseModel):
        severity: Literal["blocking", "major", "minor", "suggestion"]
        category: Literal["coherence", "argumentation", "sourcing",
                          "style", "structure", "completeness"]
        excerpt: str            # extrait du texte concerné, 20 à 300 car.
        message: str
        suggestion: str | None = None

    class ReviewReport(BaseModel):
        findings: list[Finding]
        scores: dict[str, float]        # une entrée par category, 0 à 100
        overall_score: float            # 0 à 100
        verdict: Literal["ready", "needs_work", "insufficient"]

    Validateurs :
    - excerpt doit être une sous-chaîne EXACTE de content_qmd. Un relecteur
      qui cite un passage inexistant a halluciné : la sortie est rejetée par
      le guardrail. C'est le seul contrôle de véracité applicable à la
      relecture, et il est syntaxique ;
    - scores contient exactement les six catégories ;
    - overall_score est recalculé côté serveur à partir de scores et des
      poids de configuration, jamais repris de la sortie du modèle. Le
      modèle propose des scores par catégorie, il ne décide pas de la
      note globale ;
    - un verdict "ready" avec au moins un finding de severity "blocking"
      est incohérent : rejet.

2. Pondération du score

    settings.review_weights, défaut :
      sourcing 0.30, coherence 0.25, argumentation 0.20,
      completeness 0.15, structure 0.05, style 0.05.

    Le sourçage pèse le plus : c'est ce qui distingue un mémoire d'un
    devoir. Les poids sont configurables mais leur somme est vérifiée à
    1.0 au démarrage.

3. Agent relecteur (agents/reviewer_agent.py)

    - conforme au Protocol Agent de US-201 : retourne le texte brut ;
    - reçoit : le content_qmd de la section, l'objectif du nœud de plan, la
      liste des chunks utilisés à la rédaction, la longueur cible ;
    - ne reçoit PAS l'historique de génération ni les versions
      antérieures : il évalue le texte, pas le processus ;
    - température settings.reviewer_temperature, défaut 0.1 — l'évaluation
      doit être aussi reproductible que possible ;
    - prompt système constant, sans interpolation.

4. Contrôle de complétude par comparaison

    En complément du jugement du modèle, un contrôle déterministe :
    - la longueur réelle par rapport à la cible du nœud ;
    - la proportion d'affirmations de kind "sourced" parmi les Claim
      persistés de la section ;
    - le nombre de sources distinctes citées.

    Ces trois mesures sont calculées EN PYTHON et injectées dans le score
    de completeness et de sourcing, à hauteur de la moitié de chaque. Un
    LLM estime mal les proportions ; une requête SQL ne se trompe pas.

5. Boucle de correction

    SECTION_REVIEWING -> SECTION_CORRECTING lorsque verdict vaut
    "needs_work" ou "insufficient" ET que la correction automatique est
    demandée. Le rédacteur est relancé avec les findings de severity
    blocking et major uniquement : les mineurs et les suggestions relèvent
    de l'utilisateur.

    Compteur review_loop_count de US-202, limite 3. Au dépassement :
    PAUSE et proposition de la MEILLEURE VERSION, c'est-à-dire celle au
    plus haut overall_score rencontré, pas la dernière produite. Les
    modèles 7B dégradent fréquemment leur sortie en corrigeant.

    Chaque version et son rapport sont conservés : l'utilisateur doit
    pouvoir comparer.

6. API

    POST /projects/{pid}/sections/{sid}/review   -> 202 + tâche
    GET  /projects/{pid}/sections/{sid}/review   -> dernier ReviewReport
    GET  /projects/{pid}/sections/{sid}/reviews  -> historique des rapports

    Paramètre auto_correct booléen, défaut false : la correction
    automatique n'est jamais implicite.

7. Tests

    test_excerpt_must_be_exact_substring
    test_hallucinated_excerpt_rejected_by_guardrail
    test_scores_require_all_six_categories
    test_overall_score_recomputed_server_side
    test_model_supplied_overall_score_ignored
    test_ready_verdict_with_blocking_finding_rejected
    test_weights_sum_validated_at_startup

    test_completeness_uses_deterministic_length_ratio
    test_sourcing_uses_deterministic_claim_ratio
    test_deterministic_measures_weigh_half

    test_reviewer_receives_no_generation_history
    test_correction_passes_only_blocking_and_major
    test_review_loop_capped_at_three
    test_pause_returns_best_scored_version_not_last
    test_all_versions_and_reports_retained
    test_auto_correct_defaults_to_false

    test_review_never_validates_section
    test_high_score_does_not_cross_human_gate

    L'agent est testé contre un FakeLLMBackend : rapport valide, extrait
    halluciné, catégorie manquante, verdict incohérent, dégradation
    progressive sur trois itérations.

INTERDICTIONS

- Faire valider une section par la relecture.
- Reprendre le score global fourni par le modèle.
- Accepter un extrait absent du texte.
- Transmettre l'historique de génération au relecteur.
- Corriger automatiquement sans demande explicite.
- Retourner la dernière version plutôt que la meilleure à la pause.
- Modification d'un fichier hors périmètre.

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
feat(agents): relecture hiérarchisée et score de qualité

Implémente US-302 (Backlog V0.3).

- ReviewReport avec findings hiérarchisés et scores par catégorie ;
  extrait obligatoirement sous-chaîne exacte du texte relu.
- Score global recalculé côté serveur, sourçage pondéré à 0,30.
- Complétude et sourçage mesurés pour moitié en Python, pas estimés par
  le modèle.
- Boucle de correction plafonnée à 3 ; pause proposant la meilleure
  version, pas la dernière.
- La relecture conseille, elle ne valide jamais.

Refs: US-302, ADR-004, ADR-008
```
