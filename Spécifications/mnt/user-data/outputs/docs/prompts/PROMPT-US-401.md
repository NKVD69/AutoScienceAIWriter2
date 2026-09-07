# Prompt d'implémentation — US-401 (exécution de script et production de figures)

Section US-401 de `docs/prompts/03-story-prompts.md`.
Conforme au Backlog V0.3, aux Spécifications V0.3 §8 et aux ADR-005, ADR-006.

---

```text
Tu es un agent de codage senior, spécialisé en exécution de code
scientifique, génération de figures reproductibles et intégration
d'artefacts dans une chaîne de publication.

PROJET

Science AI Writer IDE — production de figures et de tableaux pour mémoires
scientifiques, à partir des jeux de données de l'utilisateur.

SOURCES DE VÉRITÉ, PAR ORDRE DÉCROISSANT

1. Ce prompt.
2. ADR-005 (sandbox à deux niveaux), ADR-006 (Quarto canonique),
   ADR-008 (guardrails), ADR-009 (audit).
3. Backlog V0.3, US-401.
4. Spécifications V0.3, §8.

DÉPENDANCES REQUISES : US-004 (sandbox), US-201, US-003, US-301.

USER STORY

US-401 — En tant que chercheur, je veux qu'un agent produise et exécute des
scripts d'analyse, afin d'obtenir des figures et des tableaux intégrables
au document et reproductibles.

CE QUI DISTINGUE CETTE STORY DE US-004

US-004 fournit le MOTEUR d'exécution isolée. US-401 fournit l'USAGE
scientifique : l'agent code, la production d'artefacts, leur rattachement à
une section et leur reproductibilité. Tu consommes la sandbox, tu ne la
réimplémentes pas et tu ne modifies aucun de ses fichiers.

Rappel de la règle d'isolation : tout code produit par un agent s'exécute au
niveau 1 (WebAssembly), quel que soit le mode demandé. Un agent ne choisit
jamais son niveau d'isolation.

PÉRIMÈTRE STRICT

Tu implémentes :

- le modèle CodeProposal et ses validateurs ;
- l'agent code et son prompt système ;
- l'exécution avec capture d'artefacts ;
- la reproductibilité : graine, versions, jeu de données ;
- le rattachement des figures et tableaux aux sections ;
- la boucle de correction sur erreur d'exécution ;
- l'API ;
- les tests.

Tu n'implémentes PAS :

- la sandbox (US-004) ;
- les notebooks Jupyter (US-JUP-001) ;
- les codes externes OpenFOAM et Serpent (US-CALC-001) ;
- le dépôt de jeux de données (US-DATA-001 : tu consommes ce qui existe) ;
- l'éditeur Monaco (US-UI-003).

FICHIERS À CRÉER OU MODIFIER

- backend/app/models/code.py
- backend/app/agents/code_agent.py
- backend/app/agents/graph.py                  (modification : nœud code)
- backend/app/llm/prompts/registry.py          (modification : prompt CODE)
- backend/app/services/code_service.py
- backend/app/services/artifact_service.py
- backend/app/api/v1/code.py
- backend/app/api/v1/__init__.py               (modification)
- backend/tests/tests_agents/test_code_agent.py
- backend/tests/tests_api/test_code_api.py
- backend/tests/tests_integration/test_figure_pipeline.py

Aucun autre fichier.

EXIGENCES D'IMPLÉMENTATION

1. Modèle (models/code.py)

    class ExpectedArtifact(BaseModel):
        filename: str                    # nom relatif, sans chemin
        kind: Literal["figure", "table", "data"]
        caption: str
        label: str                       # fig-xxx ou tbl-xxx

    class CodeProposal(BaseModel):
        code: str
        intent: str
        datasets: list[str] = []
        expected_artifacts: list[ExpectedArtifact]
        random_seed: int | None = None

    Validateurs :
    - label conforme à la syntaxe Quarto : fig- ou tbl- suivi d'un slug
      (ADR-006). Un label MyST est rejeté ;
    - filename sans séparateur de chemin ni "..": l'agent n'écrit que dans
      le répertoire de sortie ;
    - datasets doit référencer des jeux de données existants du projet ; un
      nom inconnu est rejeté avant exécution, pas découvert dans une
      traceback ;
    - si le code importe numpy ou random, random_seed est OBLIGATOIRE.
      Une figure de thèse doit être reproductible ; un résultat qui change
      à chaque exécution est indéfendable en soutenance.

2. Prompt système CODE (registry.py)

    Constante de module. Il doit poser : rôle d'analyste scientifique
    Python ; bibliothèques disponibles au niveau 1 — numpy, pandas, scipy,
    matplotlib, sympy, scikit-learn ; backend matplotlib Agg imposé ;
    obligation d'écrire les figures dans le répertoire de sortie sous les
    noms déclarés ; interdiction de tout accès réseau ; obligation de fixer
    la graine aléatoire ; format de sortie JSON strict.

3. Exécution (services/code_service.py)

    Séquence :
      1. validation Pydantic de CodeProposal, guardrail de US-201 ;
      2. résolution des datasets en MountSpec en lecture seule ;
      3. création d'un répertoire de sortie dédié à l'exécution ;
      4. SandboxFactory.select(origin=AGENT, ...) — toujours niveau 1 ;
      5. exécution ;
      6. rapprochement entre expected_artifacts et fichiers réellement
         produits.

    Le point 6 est important : un fichier attendu mais absent, ou un fichier
    produit mais non déclaré, sont tous deux des anomalies. Le premier
    relance l'agent avec un message précis ; le second est conservé mais
    signalé, jamais rattaché automatiquement à une section.

4. Boucle de correction sur erreur

    Un exit_code non nul relance l'agent avec :
    - les vingt dernières lignes de stderr, pas la traceback entière ;
    - le code source numéroté ;
    - la nature du dépassement si limit_exceeded est renseigné.

    Compteur de US-202, limite 3, puis ERROR_STATE. Un dépassement mémoire
    ou de temps produit un message distinct d'une erreur de syntaxe :
    l'agent doit savoir s'il a écrit du code faux ou du code trop coûteux.

    Cas particulier : PackageUnavailableInWasmError (US-004) ne relance PAS
    l'agent. Elle remonte à l'utilisateur avec la proposition de passer au
    niveau 2 sous consentement — c'est une décision humaine, pas une erreur
    de code.

5. Reproductibilité (services/artifact_service.py)

    Chaque artefact conservé porte : le code exact l'ayant produit, la
    graine, les versions des bibliothèques relevées dans la sandbox, les
    SHA-256 des jeux de données utilisés, la date et la durée.

    C'est ce qui permet de répondre en soutenance à « comment cette figure
    a-t-elle été obtenue ». Sans le SHA-256 des données, la réponse est
    incomplète : un fichier modifié depuis produirait une autre figure.

6. Rattachement au document

    - un artefact de kind figure ou table est rattaché à une
      draft_section ;
    - le rattachement insère la référence Quarto correspondante dans
      content_qmd si elle n'y figure pas déjà :
      ![caption](chemin){#label} pour une figure ;
    - la suppression d'un artefact référencé est refusée avec 409 : elle
      casserait un renvoi à l'export (US-502) ;
    - les artefacts sont copiés dans le répertoire de compilation au
      moment de l'export, jamais déplacés.

7. API

    POST /projects/{pid}/code/propose          -> 202, l'agent génère
    POST /projects/{pid}/code/execute          -> exécution directe (US-004)
    GET  /projects/{pid}/code/executions
    POST /projects/{pid}/sections/{sid}/artifacts/{aid}/attach
    DELETE /projects/{pid}/artifacts/{aid}

8. Tests

    test_label_must_be_quarto_syntax
    test_myst_label_rejected
    test_filename_rejects_path_separator
    test_unknown_dataset_rejected_before_execution
    test_seed_required_when_numpy_imported
    test_agent_origin_always_level1
    test_agent_cannot_request_native_level

    test_missing_expected_artifact_reruns_agent
    test_undeclared_artifact_kept_but_flagged
    test_undeclared_artifact_not_auto_attached
    test_stderr_truncated_to_last_20_lines
    test_limit_exceeded_message_differs_from_syntax_error
    test_package_unavailable_does_not_rerun_agent
    test_correction_loop_capped_at_three

    test_artifact_records_seed_and_library_versions
    test_artifact_records_dataset_sha256
    test_attach_inserts_quarto_reference
    test_attach_is_idempotent
    test_delete_referenced_artifact_returns_409
    test_execution_audited_with_level

    [integration] test_figure_pipeline_end_to_end :
        dataset CSV généré par le test -> proposition de code ->
        exécution niveau 1 -> figure produite -> rattachement ->
        référence présente dans le .qmd.

INTERDICTIONS

- Modifier un fichier du module sandbox.
- Exécuter du code d'agent au niveau 2.
- Rattacher automatiquement un artefact non déclaré.
- Accepter du code sans graine quand l'aléatoire est importé.
- Relancer l'agent sur une indisponibilité de paquet Wasm.
- Transmettre une traceback entière au modèle.
- Supprimer un artefact référencé.
- Modification d'un fichier hors périmètre.

CRITÈRES D'ACCEPTATION

    cd backend && pytest -q -m "not integration"
    ruff check backend/ && ruff format --check backend/
    python scripts/check_sandbox_linux.py    # ou windows

FORMAT DE RÉPONSE

1. Plan d'implémentation, 10 lignes maximum.
2. Contenu intégral de chaque fichier.
3. Sortie attendue des vérifications.
4. Section "Réserves".
```

## Message de commit attendu

```text
feat(code): agent d'analyse Python et production de figures reproductibles

Implémente US-401 (Backlog V0.3).

- CodeProposal avec labels Quarto, graine obligatoire dès que l'aléatoire
  est importé, datasets validés avant exécution.
- Exécution au niveau Wasm systématique pour tout code d'agent.
- Rapprochement artefacts attendus / produits ; l'inattendu est conservé
  et signalé, jamais rattaché seul.
- Traçabilité de reproduction : code, graine, versions, SHA-256 des
  données.
- Rattachement insérant la référence Quarto ; suppression d'un artefact
  référencé refusée.

Refs: US-401, ADR-005, ADR-006
```
