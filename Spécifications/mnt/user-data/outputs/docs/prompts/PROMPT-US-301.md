# Prompt d'implémentation — US-301 (rédaction de section avec RAG ciblé)

Section US-301 de `docs/prompts/03-story-prompts.md`.
Conforme au Backlog V0.3, aux Spécifications V0.3 §5.4 et §5.5, et aux ADR-003, ADR-006, ADR-008.

---

```text
Tu es un agent de codage senior, spécialisé en RAG, sorties structurées,
rédaction assistée par LLM et garde-fous de véracité.

PROJET

Science AI Writer IDE — IDE scientifique local multi-agents. Modèle 7B
quantifié, contexte 8192 tokens, 10 Go de VRAM.

SOURCES DE VÉRITÉ, PAR ORDRE DÉCROISSANT

1. Ce prompt.
2. ADR-006 (Quarto canonique), ADR-008 (guardrails), ADR-003 (modèle
   unique, prompts système constants), ADR-002 (RAG).
3. Backlog V0.3, US-301.
4. Spécifications V0.3, §5.4 et §5.5.
5. contracts/openapi.yaml, schémas DraftSection et Citation.

DÉPENDANCES REQUISES : US-PLAN-001, US-102, US-201, US-003.

USER STORY

US-301 — En tant que doctorant, je veux qu'une section soit rédigée à
partir de mon plan validé et de mes sources approuvées, afin d'obtenir un
texte de niveau académique dont chaque affirmation est traçable.

L'EXIGENCE CENTRALE DU PRODUIT

C'est la story la plus sensible du projet. Une citation inventée dans un
mémoire de doctorat détruit la crédibilité du travail et celle de l'outil.
Les garde-fous décrits en section 5 ne sont pas des validations de confort :
ils sont la raison d'être du produit et ils sont SYNTAXIQUES, donc fiables,
parce qu'ils ne demandent jamais à un LLM de juger sa propre production.

PÉRIMÈTRE STRICT

Tu implémentes :

- la construction du contexte RAG ciblé par nœud de plan ;
- le modèle SectionDraft et ses validateurs ;
- l'agent rédacteur et son prompt système ;
- les trois garde-fous de véracité ;
- l'extraction et la persistance des citations ;
- le budget de tokens et le contrôle de longueur ;
- la génération en flux vers le SSE ;
- les tests.

Tu n'implémentes PAS :

- la relecture et le score de qualité (US-302) ;
- l'export et la compilation BibTeX (US-501, US-502) ;
- l'exécution de code et les figures (US-401) ;
- l'éditeur frontend (US-UI-002).

FICHIERS À CRÉER OU MODIFIER

- backend/app/models/section.py
- backend/app/agents/writer_agent.py
- backend/app/agents/veracity.py
- backend/app/agents/graph.py                  (modification : nœuds section)
- backend/app/llm/prompts/registry.py          (modification : prompt WRITER)
- backend/app/rag/context_builder.py
- backend/app/services/section_service.py
- backend/app/api/v1/sections.py
- backend/app/api/v1/__init__.py               (modification)
- backend/tests/tests_agents/test_writer_agent.py
- backend/tests/tests_agents/test_veracity.py
- backend/tests/tests_rag/test_context_builder.py
- backend/tests/tests_api/test_sections_api.py

Aucun autre fichier.

EXIGENCES D'IMPLÉMENTATION

1. Construction du contexte (rag/context_builder.py)

    async def build_section_context(
        conn, plan_node: PlanNode, plan: Plan, budget_tokens: int
    ) -> SectionContext

    - la requête de recherche est construite à partir du TITRE et de
      l'OBJECTIF du nœud, plus la problématique du projet — pas du titre
      seul, trop pauvre pour un KNN utile ;
    - récupération de k=12 chunks, puis sélection sous budget :
      settings.writer_context_tokens (défaut 4000), afin de laisser de la
      place à la génération dans une fenêtre de 8192 ;
    - déduplication par source : au plus 3 chunks d'une même source, pour
      éviter qu'un article dominant écrase les autres ;
    - chunks de bibliographie exclus (US-102) ;
    - SectionContext porte la liste des chunks retenus ET la liste close
      des clés BibTeX autorisées, dérivée de ces chunks uniquement.

    Si moins de 3 chunks sont retenus, lever InsufficientContextError :
    mieux vaut refuser de rédiger que produire une section non sourcée. Le
    message propose d'importer davantage de sources ou d'élargir le nœud.

2. Modèle de sortie (models/section.py)

    class Claim(BaseModel):
        text: str
        kind: Literal["sourced", "synthesis", "hypothesis", "limitation"]
        citation_keys: list[str] = []
        chunk_ids: list[int] = []

    class SectionDraft(BaseModel):
        content_qmd: str
        claims: list[Claim]
        word_count: int

    Validateurs :
    - un Claim de kind "sourced" DOIT porter au moins une citation_key et
      au moins un chunk_id ;
    - un Claim de kind "hypothesis" ou "synthesis" ne doit porter AUCUNE
      citation_key : une hypothèse sourcée est une contradiction ;
    - content_qmd ne doit contenir aucune syntaxe MyST : rejeter la
      présence de ":::{" suivi d'un mot sans point, de "{ref}" et de
      "{numref}" (ADR-006) ;
    - word_count doit être compris entre 0,6 et 1,4 fois
      plan_node.target_words.

3. Prompt système WRITER (registry.py)

    Constante de module, sans interpolation. Il doit poser : rôle de
    rédacteur scientifique de niveau doctoral ; obligation de n'employer
    que les sources fournies dans le message utilisateur ; interdiction
    absolue d'inventer une référence, un chiffre, un résultat ou un DOI ;
    obligation de qualifier chaque affirmation par son kind ; format de
    sortie JSON strict ; syntaxe Quarto pour le contenu.

    Les chunks, les clés autorisées, le titre et l'objectif du nœud vont
    dans le message utilisateur. Jamais dans le système (ADR-003).

4. Agent rédacteur (agents/writer_agent.py)

    - conforme au Protocol Agent de US-201 : il retourne le texte brut, il
      ne valide pas et ne décide pas de la suite ;
    - température settings.writer_temperature, défaut 0.3 ;
    - génération en flux : les tokens sont émis en événements `token` vers
      le SSE au fil de l'eau, la validation n'intervient qu'à la fin ;
    - le message utilisateur présente chaque chunk avec son identifiant,
      sa clé BibTeX, sa source et ses pages, dans un format stable.

5. Garde-fous de véracité (agents/veracity.py) — LE CŒUR DE LA STORY

    Trois contrôles purement syntaxiques, exécutés dans le nœud
    SECTION_GUARDRAIL après la validation Pydantic :

    V1 — Clé de citation hors liste blanche.
         Toute citation_key et toute clé apparaissant dans content_qmd sous
         la forme @clef ou [@clef] doit figurer dans la liste close du
         SectionContext. Une clé inconnue est un rejet.

    V2 — Affirmation chiffrée non rattachée.
         Tout Claim de kind "sourced" contenant un motif numérique
         significatif — nombre décimal, pourcentage, intervalle, valeur p,
         effectif — doit porter au moins un chunk_id. Un chiffre présenté
         comme sourcé sans chunk d'origine est un rejet.
         Les nombres non significatifs sont exclus du contrôle : numéros de
         section, années isolées, numéros de figure.

    V3 — DOI ou URL hors base.
         Tout DOI ou URL apparaissant dans content_qmd doit exister dans
         source_document pour ce projet. Un DOI inconnu est un rejet.

    Chaque rejet produit un message actionnable pour le modèle : la clé, le
    chiffre ou le DOI fautif, et la consigne de le retirer ou de le
    rattacher. Le nœud relance le même agent, compteur de US-202 appliqué.

    Ces contrôles ne demandent JAMAIS au LLM de vérifier son propre
    travail : c'est ce qui les rend fiables.

6. Persistance (services/section_service.py)

    - la section est écrite dans draft_section avec content_qmd et un
      numéro de version incrémenté à chaque génération ;
    - les Claim de kind "sourced" produisent des lignes citation avec
      verified = 1 : elles ont franchi V1, V2 et V3 ;
    - une modification manuelle du contenu par l'utilisateur (PUT) remet
      verified = 0 sur les citations dont la clé n'est plus présente dans
      le texte, et ne supprime aucune ligne ;
    - les versions antérieures restent lisibles.

7. API

    POST /projects/{pid}/sections/{nodeId}/draft  -> 202 + tâche
    GET  /projects/{pid}/sections/{sectionId}
    PUT  /projects/{pid}/sections/{sectionId}

    La demande de rédaction alors que plan.status != VALIDATED retourne 409
    avec current_state et required_state (contrôle déjà posé par
    US-PLAN-001 : tu le consommes, tu ne le réimplémentes pas).

8. Tests

    test_context_query_uses_title_objective_and_problematique
    test_context_respects_token_budget
    test_context_caps_chunks_per_source
    test_context_excludes_reference_chunks
    test_context_insufficient_raises_rather_than_writing
    test_allowed_keys_derived_only_from_selected_chunks

    test_sourced_claim_requires_key_and_chunk
    test_hypothesis_claim_rejects_citation_key
    test_myst_syntax_rejected_in_content
    test_word_count_out_of_range_rejected

    test_v1_unknown_citation_key_rejected
    test_v1_inline_at_key_also_checked
    test_v2_numeric_claim_without_chunk_rejected
    test_v2_section_number_not_flagged_as_numeric_claim
    test_v2_isolated_year_not_flagged
    test_v3_unknown_doi_rejected
    test_v3_known_doi_accepted
    test_veracity_message_names_offending_token
    test_veracity_failure_reruns_writer_not_reviewer

    test_citations_persisted_as_verified
    test_manual_edit_unverifies_removed_citations
    test_manual_edit_never_deletes_citation_rows
    test_version_incremented_per_generation
    test_draft_blocked_when_plan_not_validated
    test_tokens_streamed_before_validation

    L'agent est testé contre un FakeLLMBackend alimenté de réponses
    préenregistrées : sortie valide, clé inconnue, chiffre non rattaché,
    DOI inventé, syntaxe MyST, hypothèse avec citation. Aucun Ollama réel.

INTERDICTIONS

- Rédiger une section sans contexte RAG suffisant.
- Faire vérifier la véracité par un LLM.
- Accepter une clé, un DOI ou une URL hors base.
- Insérer des données variables dans le prompt système.
- Produire de la syntaxe MyST.
- Supprimer une ligne de citation lors d'une édition manuelle.
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
feat(agents): rédaction de section sourcée avec garde-fous de véracité

Implémente US-301 (Backlog V0.3).

- Contexte RAG ciblé par nœud, sous budget de 4000 tokens, plafonné à
  3 chunks par source ; refus de rédiger sous 3 chunks.
- SectionDraft qualifiant chaque affirmation : sourced, synthesis,
  hypothesis, limitation.
- Trois garde-fous syntaxiques : clé hors liste blanche, chiffre sourcé
  non rattaché à un chunk, DOI hors base. Aucun jugement délégué au LLM.
- Citations persistées vérifiées ; édition manuelle dévérifie sans
  supprimer.

Refs: US-301, ADR-002, ADR-003, ADR-006, ADR-008
```
