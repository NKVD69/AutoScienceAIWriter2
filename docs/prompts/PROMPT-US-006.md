# Prompt d'implémentation — US-006 (vérification du budget VRAM)

Section US-006 de `docs/prompts/03-story-prompts.md`.
Story nouvelle, introduite par le Backlog V0.3. Conforme à ADR-003 et aux Spécifications V0.3 §12.

---

```text
Tu es un agent de codage senior, spécialisé en instrumentation, tests
d'intégration de bout en bout et diagnostic de ressources GPU.

PROJET

Science AI Writer IDE — poste cible équipé de 10 Go de VRAM. Cette
contrainte est à l'origine de la moitié des décisions d'architecture du
projet : modèles 7B au lieu de 30B, orchestration déterministe au lieu de
conversationnelle, embeddings sur CPU, modèle unique persistant.

SOURCES DE VÉRITÉ, PAR ORDRE DÉCROISSANT

1. Ce prompt.
2. ADR-003 (modèle unique persistant), ADR-013 (embeddings CPU).
3. Backlog V0.3, US-006.
4. Spécifications V0.3, §12.

DÉPENDANCES REQUISES : US-003, US-005, US-102.

USER STORY

US-006 — En tant que développeur, je veux mesurer l'occupation VRAM sur un
cycle complet, afin que la contrainte fondatrice du projet soit vérifiée et
non seulement supposée.

POURQUOI CETTE STORY EXISTE

Le dossier mesurait la latence LLM mais rien ne vérifiait l'invariant dont
dépendent toutes les décisions d'architecture. Une régression de VRAM ne se
manifeste pas par un test rouge : elle se manifeste par un CUDA out of
memory chez l'utilisateur, au milieu de la rédaction d'un chapitre, avec
perte du travail en cours. Un budget non mesuré est un budget non tenu.

PÉRIMÈTRE STRICT

Tu implémentes :

- l'échantillonneur de VRAM ;
- le scénario de cycle complet ;
- le script de vérification ;
- le test d'intégration correspondant ;
- l'exposition de la mesure dans /system/capabilities.

Tu n'implémentes PAS :

- de correction de consommation : cette story mesure, elle n'optimise pas ;
- de tableau de bord de suivi ;
- de limitation dynamique.

FICHIERS À CRÉER OU MODIFIER

- backend/app/llm/vram.py                      (modification : sampler)
- backend/app/api/v1/system.py                 (modification)
- backend/tests/tests_integration/__init__.py
- backend/tests/tests_integration/test_vram_budget.py
- scripts/check_vram_budget.py

Aucun autre fichier.

EXIGENCES D'IMPLÉMENTATION

1. Échantillonneur

    class VramSampler:
        def __init__(self, interval_ms: int = 500): ...
        def __enter__(self) -> VramSampler
        def __exit__(self, *exc) -> None
        @property
        def peak_mb(self) -> int | None
        @property
        def samples(self) -> list[tuple[float, int]]     # (t_relatif, mo)
        @property
        def available(self) -> bool

    - thread dédié appelant
      nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits ;
    - la mesure porte sur le GPU entier, pas sur le seul processus : c'est
      la bonne granularité, puisque le budget doit tenir compte de ce que
      consomment le pilote, le bureau et Ollama ;
    - available vaut False si nvidia-smi est absent ou échoue ; dans ce cas
      peak_mb vaut None et rien ne lève. Le système doit fonctionner sur un
      poste sans GPU NVIDIA ;
    - le thread s'arrête proprement à la sortie du contexte, même en cas
      d'exception.

2. Scénario de cycle complet

    Sous un unique VramSampler :

      1. démarrage du LLMManager et chargement du modèle ;
      2. ingestion de 50 documents synthétiques, embeddings CPU compris ;
      3. génération d'une section de 1500 mots ;
      4. recherche KNN sur 5000 chunks ;
      5. export du document en PDF.

    Les documents sont GÉNÉRÉS par le test, jamais téléchargés.
    Si Quarto est absent, l'étape 5 est ignorée et le fait est signalé dans
    le rapport, sans faire échouer la mesure.

3. Seuils

    - plafond : settings.vram_budget_mb, défaut 9216 (9,0 Go) ;
    - la marge d'un gigaoctet sous les 10 Go est intentionnelle : elle
      couvre le pilote, le compositeur de bureau et les pics transitoires
      du cache KV. Ne pas la réduire pour faire passer un test ;
    - rapport détaillant le pic par étape, afin qu'une régression désigne
      son origine plutôt qu'un simple dépassement global.

4. Contrôle spécifique aux embeddings

    Mesure de la VRAM avant et après l'étape 2 seule. La variation doit
    rester inférieure à 200 Mo (ADR-013). Un dépassement signifie que les
    embeddings sont repassés par le GPU — la régression la plus probable de
    tout le projet, puisqu'il suffit qu'un développeur route
    nomic-embed-text par Ollama pour la réintroduire.

5. Contrôle de persistance

    Vérifier qu'entre les étapes 2 et 3 la VRAM ne redescend pas de plus de
    3 Go : une chute de cet ordre signalerait que le modèle a été déchargé,
    donc que le keep_alive=-1 d'ADR-003 n'est plus effectif.

6. Script (scripts/check_vram_budget.py)

    Exécute le scénario, affiche un tableau : étape, pic VRAM, delta,
    durée. Conclut par le pic global et le verdict.

    Codes de sortie :
      0 — budget tenu ;
      1 — budget dépassé, ou embeddings sur GPU, ou modèle déchargé ;
      2 — mesure impossible, pas de GPU NVIDIA ou Ollama absent.

    Le code 2 n'est pas un échec de construction : il indique un
    environnement où la mesure n'a pas de sens.

7. Exposition

    /system/capabilities reçoit vram_used_mb et vram_peak_last_run_mb, tous
    deux nullables. Aucune de ces lectures ne doit pouvoir provoquer une
    erreur 500.

8. Tests

    test_sampler_returns_none_without_nvidia_smi
    test_sampler_thread_stops_on_exception
    test_sampler_records_samples_at_interval
    test_peak_is_max_of_samples
    [integration] test_full_cycle_under_budget
    [integration] test_embedding_stage_vram_delta_under_200mb
    [integration] test_model_not_unloaded_between_stages
    test_capabilities_vram_fields_nullable

    Les tests d'intégration portent skipif sur l'absence de GPU ou
    d'Ollama.

INTERDICTIONS

- Réduire le seuil pour faire passer un test.
- Lever une exception en l'absence de nvidia-smi.
- Télécharger des documents de test.
- Optimiser la consommation dans cette story.
- Modification d'un fichier hors périmètre.

CRITÈRES D'ACCEPTATION

    cd backend && pytest -q -m "not integration"
    ruff check backend/ && ruff format --check backend/
    python scripts/check_vram_budget.py

FORMAT DE RÉPONSE

1. Plan d'implémentation, 10 lignes maximum.
2. Contenu intégral de chaque fichier.
3. Sortie attendue des vérifications.
4. Section "Réserves".
```

## Message de commit attendu

```text
test(vram): vérification du budget VRAM sur un cycle complet

Implémente US-006 (Backlog V0.3, story nouvelle).

- VramSampler par thread, tolérant à l'absence de GPU NVIDIA.
- Cycle complet mesuré par étape : chargement, ingestion, rédaction,
  recherche, export. Plafond 9,0 Go, marge d'1 Go assumée.
- Contrôle dédié : delta d'ingestion sous 200 Mo, sinon les embeddings
  sont repassés par le GPU.
- Contrôle de persistance : pas de chute de 3 Go entre deux étapes.

Refs: US-006, ADR-003, ADR-013
Corrige: D-10 (invariant fondateur non mesuré)
```
