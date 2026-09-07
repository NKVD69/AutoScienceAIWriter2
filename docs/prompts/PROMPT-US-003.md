# Prompt d'implémentation — US-003 (LLM Manager, modèle unique persistant)

Section US-003 de `docs/prompts/03-story-prompts.md`.
Conforme au Backlog V0.3, aux Spécifications V0.3 §6 et à ADR-003.

---

```text
Tu es un agent de codage senior, spécialisé en intégration de LLM locaux,
gestion de mémoire GPU, Python asynchrone et tests déterministes.

PROJET

Science AI Writer IDE — IDE scientifique local multi-agents, poste cible
équipé de 10 Go de VRAM, Windows 11 et Linux, sans Docker.

SOURCES DE VÉRITÉ, PAR ORDRE DÉCROISSANT

1. Ce prompt.
2. ADR-003 (modèle LLM unique persistant), ADR-013 (embeddings hors GPU).
3. Backlog V0.3, US-003.
4. Spécifications techniques V0.3, §6.
5. Cahier des charges V3.

Un ADR au statut Accepté n'est pas rediscutable. Toute objection va en
section "Réserves" de ton rapport, jamais dans le code.

USER STORY

US-003 — Configurer le LLMManager pour maintenir un unique modèle
généraliste résident en VRAM, afin d'éliminer la latence de swap entre
agents.

CONTEXTE DE LA DÉCISION — À COMPRENDRE AVANT DE CODER

La conception initiale prévoyait un modèle spécialisé par agent, chargé et
déchargé à chaque transition. Charger 5 à 6 Go depuis un SSD prend de
plusieurs secondes à plusieurs dizaines de secondes. Une thèse compte des
centaines de transitions d'agents : la latence cumulée rendait l'outil
inutilisable. La spécialisation passe donc désormais par le prompt système,
jamais par les poids.

Deux pièges à éviter, tous deux déjà rencontrés dans ce projet :

1. Ne PAS écrire de test affirmant que "le cache KV est réutilisé entre deux
   requêtes". Deux requêtes indépendantes aux prompts différents ne
   partagent aucun cache KV, et l'API Ollama n'expose pas cette information.
   Le seul critère observable est le champ load_duration de la réponse.

2. Ne PAS router les embeddings par ce manager ni par Ollama. Ollama charge
   les modèles d'embedding sur GPU par défaut : cela évincerait le modèle
   principal pendant l'ingestion. Les embeddings font l'objet de US-005 et
   s'exécutent sur CPU, hors d'Ollama.

PÉRIMÈTRE STRICT

Tu implémentes :

- l'interface abstraite LLMBackend ;
- le backend Ollama ;
- le LLMManager avec chargement unique au démarrage ;
- le registre des prompts système par agent, constants et versionnés ;
- la génération en flux (streaming) et hors flux ;
- la détection de VRAM et la politique du second modèle optionnel ;
- l'instrumentation de latence ;
- les tests et le script de vérification.

Tu n'implémentes PAS :

- les agents eux-mêmes (US-201, US-PLAN-001, US-301) ;
- le graphe LangGraph ;
- les embeddings (US-005) ;
- le RAG, la sandbox, l'export, le frontend.

FICHIERS À CRÉER OU MODIFIER

- backend/app/llm/__init__.py
- backend/app/llm/base.py
- backend/app/llm/manager.py
- backend/app/llm/backends/__init__.py
- backend/app/llm/backends/ollama.py
- backend/app/llm/prompts/__init__.py
- backend/app/llm/prompts/registry.py
- backend/app/llm/vram.py
- backend/app/core/config.py                (modification)
- backend/app/core/errors.py                (modification)
- backend/app/main.py                       (modification : lifespan)
- backend/tests/tests_llm/__init__.py
- backend/tests/tests_llm/test_manager.py
- backend/tests/tests_llm/test_prompts.py
- backend/tests/tests_llm/test_vram.py
- scripts/check_llm_latency.py

Aucun autre fichier.

EXIGENCES D'IMPLÉMENTATION

1. Interface LLMBackend (backend/app/llm/base.py)

    class LLMBackend(Protocol):
        async def ensure_loaded(self) -> None: ...
        async def generate(self, system: str, user: str,
                           *, max_tokens: int, temperature: float,
                           stop: list[str] | None = None) -> LLMResult: ...
        async def stream(self, system: str, user: str,
                         **kwargs) -> AsyncIterator[str]: ...
        async def health(self) -> BackendHealth: ...

    LLMResult est un modèle Pydantic portant :
        text, prompt_tokens, completion_tokens,
        load_duration_ms, prompt_eval_duration_ms, eval_duration_ms,
        total_duration_ms, model, backend

    Ces champs proviennent directement de la réponse Ollama, convertis de
    nanosecondes en millisecondes. Ils sont la base de toute mesure de
    latence : tu ne mesures pas le temps avec time.perf_counter côté client
    pour ces métriques.

2. Backend Ollama (backend/app/llm/backends/ollama.py)

    - client httpx.AsyncClient dédié, base_url configurable
      (défaut http://127.0.0.1:11434), timeout de connexion 5 s,
      timeout de lecture 300 s ;
    - ensure_loaded envoie une requête de génération vide avec
      keep_alive=-1 afin de forcer le chargement et de le maintenir ;
    - toute requête de génération passe keep_alive=-1 ;
    - health interroge /api/tags et /api/ps et retourne :
      modèle attendu présent, modèles actuellement chargés,
      taille approximative en VRAM ;
    - si Ollama est injoignable, lever OllamaUnavailableError avec un
      message actionnable indiquant la commande d'installation et de
      démarrage pour Windows et pour Linux.

    INTERDICTION ABSOLUE : ne jamais envoyer keep_alive=0 ni appeler une
    route de déchargement. Aucun unload par agent.

3. LLMManager (backend/app/llm/manager.py)

    - singleton applicatif construit dans le lifespan FastAPI ;
    - au démarrage : health, puis ensure_loaded du modèle principal ;
    - si le modèle n'est pas présent localement, échouer avec
      ModelNotFoundError et la commande ollama pull correspondante ;
      ne PAS télécharger automatiquement — le téléchargement relève du
      consentement model_download (ADR-010) ;
    - expose generate_for_agent(agent: AgentName, user: str, **kwargs) qui
      récupère le prompt système dans le registre et délègue au backend ;
    - un verrou asyncio.Lock sérialise les générations : le modèle est
      unique, deux générations simultanées se disputeraient le contexte et
      dégraderaient la latence sans gain.

4. Registre de prompts (backend/app/llm/prompts/registry.py)

    - AgentName est une énumération : ORCHESTRATOR, PLAN, WRITER, REVIEWER,
      CODE, BIBLIO, RAG ;
    - à chaque agent correspond une CONSTANTE de chaîne, définie au niveau
      du module, jamais construite dynamiquement, jamais formatée avec des
      données variables ;
    - le registre expose get_system_prompt(agent) -> str et
      prompt_version(agent) -> str, où la version est le SHA-256 tronqué à
      12 caractères du prompt ;
    - motif : le prefix caching n'opère que si le préfixe est strictement
      identique d'une requête à l'autre. Toute variable insérée dans le
      prompt système annulerait ce gain. Les données variables vont dans le
      message utilisateur, jamais dans le système.

    Les contenus de prompts peuvent rester succincts à ce stade : les
    prompts définitifs relèvent des stories d'agents. Tu poses la structure
    et l'invariant de stabilité.

5. Détection de VRAM (backend/app/llm/vram.py)

    - read_vram_used_mb() et read_vram_total_mb() via
      nvidia-smi --query-gpu=memory.used,memory.total
      --format=csv,noheader,nounits ;
    - retourner None si nvidia-smi est absent : le système doit fonctionner
      sur un poste sans GPU NVIDIA, en mode dégradé, sans planter ;
    - policy_allows_code_model(total_mb) -> bool : vrai si
      total_mb >= 12288 et settings.code_model_enabled est vrai.

6. Second modèle code, optionnel (ADR-003 §4)

    - settings.code_model_enabled, défaut False ;
    - si activé et VRAM totale >= 12 Go : charger qwen2.5-coder-7b comme
      second résident, réservé à AgentName.CODE ;
    - si activé et VRAM totale < 12 Go : refuser au démarrage avec un
      message explicite, et poursuivre avec le modèle généraliste seul.
      Ne PAS planter : l'option est un confort, pas une exigence.

7. Configuration ajoutée à core/config.py

        llm_backend: str = "ollama"
        llm_base_url: str = "http://127.0.0.1:11434"
        llm_model: str = "qwen2.5:7b-instruct-q4_K_M"
        llm_context_tokens: int = 8192
        llm_temperature_default: float = 0.2
        code_model_enabled: bool = False
        code_model: str = "qwen2.5-coder:7b"
        llm_max_load_duration_ms: int = 50
        llm_max_ttft_ms: int = 2000

8. Tests (backend/tests/tests_llm/)

    Les tests ne doivent PAS exiger un Ollama réel pour la majorité des
    cas : tu implémentes un FakeBackend conforme au Protocol et tu testes
    la logique du manager contre lui. Les tests nécessitant Ollama sont
    marqués @pytest.mark.integration et ignorés si le service est absent.

    test_manager_loads_model_once_at_startup
    test_manager_never_sends_keep_alive_zero
    test_manager_serializes_concurrent_generations
    test_generate_for_agent_uses_registry_prompt
    test_system_prompt_byte_stable_across_calls
    test_system_prompt_contains_no_interpolation
    test_prompt_version_changes_when_prompt_changes
    test_model_not_found_raises_with_pull_command
    test_ollama_unavailable_raises_actionable_error
    test_vram_none_when_nvidia_smi_absent
    test_code_model_refused_below_12gb
    test_code_model_loaded_above_12gb_when_enabled
    test_code_model_not_loaded_when_disabled
    [integration] test_load_duration_below_threshold_on_second_request
    [integration] test_time_to_first_token_below_threshold

9. Script de vérification (scripts/check_llm_latency.py)

    Envoie trois générations courtes consécutives au modèle réel. Vérifie
    que load_duration de la deuxième et de la troisième est inférieur au
    seuil configuré, et que le temps au premier token en streaming reste
    sous le seuil. Affiche un tableau lisible : requête, load_duration,
    ttft, tokens/s. Sortie 0 si les seuils sont tenus, 1 sinon, 2 si Ollama
    est injoignable.

DÉPENDANCES AUTORISÉES

httpx, pydantic, pydantic-settings, pytest, pytest-asyncio.
Aucune autre. Pas de langchain, pas de litellm, pas de openai.

INTERDICTIONS

- keep_alive=0 ou toute route de déchargement.
- Chargement ou déchargement de modèle par agent.
- Passage d'une donnée variable dans un prompt système.
- Routage d'embeddings par ce module.
- Téléchargement automatique de modèle.
- Mesure des métriques de latence côté client plutôt que par les champs
  Ollama.
- Modification d'un fichier hors périmètre.

CRITÈRES D'ACCEPTATION

    cd backend && pytest -q -m "not integration"
    ruff check backend/ && ruff format --check backend/
    python scripts/check_llm_latency.py      # si Ollama disponible

FORMAT DE RÉPONSE

1. Plan d'implémentation, 10 lignes maximum.
2. Contenu intégral de chaque fichier, un bloc par fichier, chemin exact
   en en-tête.
3. Sortie attendue des commandes de vérification.
4. Section "Réserves".

Tu ne poses pas de question préalable. En cas d'ambiguïté, tu retiens
l'option la plus conservatrice et tu la signales en Réserves.
```

## Message de commit attendu

```text
feat(llm): LLMManager avec modèle unique persistant en VRAM

Implémente US-003 (Backlog V0.3).

- Interface LLMBackend + backend Ollama avec keep_alive=-1 permanent.
- Registre de prompts système constants par agent, versionnés par hash,
  condition du prefix caching.
- Verrou de sérialisation des générations : modèle unique.
- Politique de second modèle code conditionnée à 12 Go de VRAM.
- Métriques de latence issues des champs Ollama, pas du client.

Refs: US-003, ADR-003, ADR-013
```
