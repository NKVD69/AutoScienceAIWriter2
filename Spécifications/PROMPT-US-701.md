# Prompt d'implémentation — US-701 (journal d'audit à détection d'altération)

Section US-701 de `docs/prompts/03-story-prompts.md`.
Conforme au Backlog V0.3, aux Spécifications V0.3 §11 et à ADR-009.

---

```text
Tu es un agent de codage senior, spécialisé en journalisation vérifiable,
chaînes de hachage, sérialisation canonique et transactions SQLite.

PROJET

Science AI Writer IDE — outil dont un argument central est l'intégrité
académique : pouvoir établir quelles parties d'un mémoire ont été générées,
à partir de quelles sources, validées par qui et quand.

SOURCES DE VÉRITÉ, PAR ORDRE DÉCROISSANT

1. Ce prompt.
2. ADR-009 (audit à détection d'altération), ADR-001 (SQLite, WAL).
3. Backlog V0.3, US-701.
4. Spécifications V0.3, §11.
5. contracts/openapi.yaml, sections /audit.

DÉPENDANCES REQUISES : US-001, US-002.

USER STORY

US-701 — En tant que superviseur, je veux un journal dont toute altération
est détectable, afin de pouvoir vérifier la traçabilité d'un mémoire.

PRÉCISION DE VOCABULAIRE — EXIGENCE CONTRACTUELLE

Les documents antérieurs qualifiaient ce journal d'« immuable ». C'est faux.
L'utilisateur possède le fichier .sqlite : il peut réécrire la table et
recalculer l'intégralité de la chaîne. Une chaîne de hachage locale apporte
une DÉTECTION D'ALTÉRATION, pas une IMMUABILITÉ.

Pour un outil vendu sur l'intégrité, l'écart entre les deux notions est
décisif. Tu emploies exclusivement « détection d'altération » ou
« tamper-evident » dans le code, les messages, les docstrings et les
libellés d'API. Les mots « immuable » et « infalsifiable » sont proscrits,
et un test lexical le vérifie.

PÉRIMÈTRE STRICT

Tu implémentes :

- la sérialisation canonique d'une entrée ;
- le chaînage SHA-256 et son unique chemin d'insertion ;
- la vérification de chaîne ;
- l'intégration transactionnelle avec les opérations métier ;
- les endpoints de consultation et de vérification ;
- l'analyse statique interdisant UPDATE et DELETE ;
- les tests.

Tu n'implémentes PAS :

- l'ancrage RFC 3161 chez un tiers (hors MVP, documenté en évolution) ;
- l'annexe de déclaration d'usage de l'IA (US-EXPORT-003, qui consommera
  ce journal) ;
- les rôles et l'authentification (US-AUTH-001).

FICHIERS À CRÉER OU MODIFIER

- backend/app/services/audit_service.py
- backend/app/models/audit.py
- backend/app/api/v1/audit.py
- backend/app/api/v1/__init__.py               (modification)
- backend/app/core/errors.py                   (modification)
- backend/tests/tests_audit/__init__.py
- backend/tests/tests_audit/test_chain.py
- backend/tests/tests_audit/test_integration.py
- backend/tests/tests_audit/test_wording.py
- scripts/check_audit_chain.py

Aucun autre fichier.

EXIGENCES D'IMPLÉMENTATION

1. Sérialisation canonique

    hash = sha256( canonical(entry) || prev_hash ).encode()

    canonical(entry) est un JSON produit avec :
    - clés triées ;
    - séparateurs sans espace : (",", ":") ;
    - ensure_ascii false, encodage UTF-8 ;
    - NaN et Infinity interdits, rejetés à l'écriture ;
    - flottants sérialisés par repr Python.

    La fonction est isolée et testée directement : c'est elle qui décide si
    une vérification faite dans six mois donnera le même résultat
    qu'aujourd'hui. Toute évolution du format canonique casserait toutes
    les chaînes existantes ; un commentaire l'indique explicitement.

    prev_hash de la première entrée d'un projet vaut la chaîne vide.

2. Chemin d'insertion unique

    async def append(conn, project_id, event_type, payload, *, tx) -> AuditEntry

    - c'est LA SEULE fonction du code base autorisée à écrire dans
      audit_log ;
    - elle exige une transaction déjà ouverte, passée en paramètre : elle
      n'en ouvre pas et n'en valide pas. Motif au point 3 ;
    - elle lit le dernier hash du projet, calcule le nouveau, insère.

    CONCURRENCE — POINT DÉLICAT

    Lire le dernier hash puis insérer n'est atomique que si les deux
    opérations sont sérialisées. Deux coroutines concurrentes liraient le
    même prev_hash et produiraient une fourche silencieuse.

    Tu protèges la séquence par un asyncio.Lock par projet, ET tu poses une
    contrainte de base qui rend la fourche impossible à passer inaperçue :

        CREATE UNIQUE INDEX idx_audit_prev ON audit_log(project_id, prev_hash);

    Deux entrées partageant le même prev_hash dans un projet deviennent
    ainsi une violation de contrainte, pas une corruption discrète. Un test
    provoque le cas et vérifie l'échec.

3. Intégration transactionnelle

    L'écriture d'audit se fait DANS LA MÊME TRANSACTION que l'opération
    métier qu'elle décrit.

    Justification : si l'audit était écrit dans une transaction séparée,
    une opération pourrait réussir sans être journalisée, ou être
    journalisée sans avoir réussi. Les deux cas ruinent la valeur probante
    du journal. En les liant, une transition annulée n'est pas journalisée,
    et une transition journalisée a nécessairement eu lieu — ce qui est
    exactement la propriété recherchée.

    Conséquence assumée : un échec d'écriture d'audit fait échouer
    l'opération métier. C'est voulu.

4. Événements obligatoires

    Types au minimum : PROJECT_CREATED, SOURCE_IMPORTED, SOURCE_APPROVED,
    INGESTION_STARTED, INGESTION_COMPLETED, INGESTION_FAILED,
    STATE_TRANSITION, LLM_CALL, GUARDRAIL_TRIGGERED, BREAKER_TRIGGERED,
    HUMAN_VALIDATION, CODE_EXECUTED, CONSENT_GRANTED, CONSENT_REVOKED,
    EXPORT_STARTED, EXPORT_COMPLETED, AI_DECLARATION_DISABLED.

    LLM_CALL porte : agent, modèle, version du prompt système, tokens
    d'entrée et de sortie, section concernée. Il ne porte JAMAIS le contenu
    intégral du prompt ni de la réponse : le journal doit rester consultable
    et de taille raisonnable sur une thèse. GUARDRAIL_TRIGGERED porte la
    sortie fautive tronquée à 2 Ko.

5. Vérification

    async def verify_chain(conn, project_id) -> ChainVerification

    ChainVerification : status VALIDE | ALTERE, entries_checked,
    first_invalid_index, first_invalid_id.

    - parcours par lots de 1000 entrées, sans charger le journal entier en
      mémoire : une thèse peut en produire des dizaines de milliers ;
    - la vérification est en lecture seule et ne modifie jamais le
      journal ;
    - un journal vide est VALIDE.

6. Analyse statique

    test_no_update_or_delete_on_audit_log parcourt l'arbre syntaxique
    (module ast) de backend/app/ et échoue si une chaîne SQL contenant
    UPDATE audit_log ou DELETE FROM audit_log apparaît ailleurs que dans
    les tests. Une convention non vérifiée n'est pas une convention.

7. Contrôle lexical

    test_wording_no_immutable_claim parcourt backend/app/, docs/ et
    frontend/src/ et échoue si les mots « immuable », « immutable » ou
    « infalsifiable » apparaissent en dehors d'une citation explicite des
    ADR expliquant pourquoi ils sont proscrits.

8. API

    GET  /projects/{id}/audit           filtres event_type et limit
    POST /projects/{id}/audit/verify

    Conformes à contracts/openapi.yaml.

9. Script (scripts/check_audit_chain.py)

    Crée un projet temporaire, insère 5000 entrées, vérifie la chaîne,
    altère la 2500e directement en SQL, vérifie de nouveau et confirme que
    le statut est ALTERE et que first_invalid_index vaut 2500. Mesure la
    durée de vérification. Sortie 0 si conforme.

10. Tests

    test_canonical_serialization_stable_across_key_order
    test_canonical_rejects_nan_and_infinity
    test_first_entry_prev_hash_is_empty
    test_chain_valid_over_500_entries
    test_tamper_detection_returns_index_and_id
    test_verify_is_read_only
    test_empty_journal_is_valid
    test_verify_batches_without_loading_all
    test_append_requires_open_transaction
    test_append_is_only_write_path
    test_concurrent_append_serialized_by_lock
    test_duplicate_prev_hash_violates_unique_index
    test_audit_written_in_same_transaction_as_business_op
    test_rolled_back_operation_leaves_no_audit_entry
    test_audit_failure_fails_business_operation
    test_llm_call_does_not_store_full_prompt
    test_guardrail_payload_truncated_to_2kb
    test_no_update_or_delete_on_audit_log
    test_wording_no_immutable_claim

INTERDICTIONS

- Employer « immuable » ou « infalsifiable ».
- UPDATE ou DELETE sur audit_log dans le code applicatif.
- Écrire l'audit dans une transaction distincte de l'opération métier.
- Ouvrir une transaction depuis append.
- Stocker le contenu intégral des prompts et des réponses.
- Charger le journal entier en mémoire pour vérifier.
- Modification d'un fichier hors périmètre.

CRITÈRES D'ACCEPTATION

    cd backend && pytest -q backend/tests/tests_audit
    ruff check backend/ && ruff format --check backend/
    python scripts/check_audit_chain.py

FORMAT DE RÉPONSE

1. Plan d'implémentation, 10 lignes maximum.
2. Contenu intégral de chaque fichier.
3. Sortie attendue des vérifications.
4. Section "Réserves".
```

## Message de commit attendu

```text
feat(audit): journal chaîné à détection d'altération

Implémente US-701 (Backlog V0.3).

- Sérialisation canonique isolée et testée : condition de reproductibilité
  d'une vérification dans le temps.
- Chemin d'insertion unique, transaction partagée avec l'opération métier :
  une transition journalisée a nécessairement eu lieu.
- Index unique (project_id, prev_hash) : une fourche concurrente devient
  une violation de contrainte, pas une corruption discrète.
- Vérification par lots, en lecture seule, avec index de première
  incohérence.
- Vocabulaire « détection d'altération » imposé et contrôlé par test.

Refs: US-701, ADR-009
Corrige: D-07 (promesse d'immuabilité inexacte)
```
