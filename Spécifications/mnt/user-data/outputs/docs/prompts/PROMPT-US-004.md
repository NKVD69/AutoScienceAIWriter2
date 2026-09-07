# Prompt d'implémentation — US-004 (sandbox à deux niveaux)

Section US-004 de `docs/prompts/03-story-prompts.md`.
Conforme au Backlog V0.3, aux Spécifications V0.3 §8 et à ADR-005.

> **Attention :** ce prompt remplace la version V0.2, fondée sur les seuls Job Objects Win32. Celle-ci posait un critère d'acceptation infaisable — voir la section « Contexte de la décision » ci-dessous.

---

```text
Tu es un agent de codage senior, spécialisé en isolation de processus,
API Win32, primitives POSIX, WebAssembly et sécurité applicative.

PROJET

Science AI Writer IDE — IDE scientifique local multi-agents. Windows 11 et
Linux, sans Docker, 10 Go de VRAM.

SOURCES DE VÉRITÉ, PAR ORDRE DÉCROISSANT

1. Ce prompt.
2. ADR-005 (sandbox à deux niveaux), ADR-012 (pas de Docker).
3. Backlog V0.3, US-004.
4. Spécifications techniques V0.3, §8.

USER STORY

US-004 — Fournir deux niveaux d'exécution isolée, afin que le code produit
par un agent soit contraint par le runtime et non par l'OS, tout en
préservant la possibilité d'exécuter du calcul scientifique lourd.

CONTEXTE DE LA DÉCISION — À LIRE AVANT DE CODER

La première conception utilisait setrlimit, fonction POSIX inexistante sous
Windows. La correction a introduit les Job Objects Win32, ce qui est juste
pour la mémoire, le temps CPU et le nombre de processus.

Mais les Job Objects N'ISOLENT NI LE RÉSEAU NI LE SYSTÈME DE FICHIERS. Le
critère "le réseau est désactivé" est donc infaisable en natif sous Windows
sans Windows Filtering Platform ni AppContainer — hors de portée. Docker est
exclu.

D'où la conception retenue : le code le moins fiable, celui que l'IA écrit
seule, reçoit l'isolation la plus forte (WebAssembly, garantie par le
runtime donc identique sur les deux OS) ; le code que l'utilisateur assume
reçoit les capacités les plus larges, sous consentement éclairé et avec un
avertissement explicite sur les limites de l'isolation.

Tu n'écris aucun test ni aucun message affirmant que le niveau natif isole
le réseau sous Windows.

PÉRIMÈTRE STRICT

Tu implémentes :

- l'interface SandboxExecutor et les modèles de données associés ;
- WasmSandbox (Pyodide) ;
- NativeSandbox pour Windows (Job Objects) ;
- NativeSandbox pour Linux (setrlimit, unshare si disponible) ;
- SandboxFactory ;
- la persistance des exécutions dans code_execution ;
- les tests et les deux scripts de vérification.

Tu n'implémentes PAS :

- l'agent code (US-401) ;
- les endpoints HTTP ;
- la gestion des jeux de données (US-DATA-001) ;
- le frontend, le RAG, l'export.

FICHIERS À CRÉER OU MODIFIER

- backend/app/sandbox/__init__.py
- backend/app/sandbox/base.py
- backend/app/sandbox/wasm.py
- backend/app/sandbox/native_windows.py
- backend/app/sandbox/native_linux.py
- backend/app/sandbox/factory.py
- backend/app/sandbox/limits.py
- backend/app/core/config.py                (modification)
- backend/app/core/errors.py                (modification)
- backend/tests/tests_sandbox/__init__.py
- backend/tests/tests_sandbox/test_factory.py
- backend/tests/tests_sandbox/test_wasm.py
- backend/tests/tests_sandbox/test_native.py
- scripts/check_sandbox_windows.py
- scripts/check_sandbox_linux.py

Aucun autre fichier.

EXIGENCES D'IMPLÉMENTATION

1. Modèles et interface (base.py)

    class SandboxOrigin(StrEnum):   AGENT | USER
    class SandboxMode(StrEnum):     WASM | NATIVE
    class SandboxLevel(IntEnum):    WASM = 1 | NATIVE = 2

    class ResourceLimits(BaseModel):
        memory_mb: int = 2048
        cpu_seconds: int = 60
        wall_seconds: int = 120
        max_file_mb: int = 512
        max_processes: int = 8

    class MountSpec(BaseModel):
        host_path: Path
        guest_path: PurePosixPath
        writable: bool = False

    class ExecutionResult(BaseModel):
        exit_code: int
        stdout: str
        stderr: str
        duration_ms: int
        level: SandboxLevel
        timed_out: bool
        limit_exceeded: str | None      # "memory" | "cpu" | "wall" | None
        artifacts: list[Path]           # fichiers produits dans le répertoire de sortie
        network_isolation_guaranteed: bool

    class SandboxExecutor(Protocol):
        level: SandboxLevel
        async def run(self, code: str, mounts: list[MountSpec],
                      limits: ResourceLimits,
                      output_dir: Path) -> ExecutionResult: ...

    Le champ network_isolation_guaranteed est OBLIGATOIRE et honnête :
    True pour le niveau 1 sur tout OS, True pour le niveau 2 sous Linux si
    unshare -n a été appliqué avec succès, False pour le niveau 2 sous
    Windows. Ce champ est persisté et affiché.

2. SandboxFactory (factory.py)

    select(origin, mode, platform) -> SandboxExecutor

    Règles, dans cet ordre :
    - origin == AGENT  -> toujours WasmSandbox, quel que soit mode ;
    - origin == USER et mode == WASM   -> WasmSandbox ;
    - origin == USER et mode == NATIVE -> NativeSandbox de la plateforme ;
    - plateforme non reconnue -> UnsupportedPlatformError.

    Le mode NATIVE demandé pour une origine AGENT n'est pas une erreur :
    il est silencieusement ramené au niveau 1, et l'abaissement est
    journalisé. Un agent ne choisit jamais son propre niveau d'isolation.

3. WasmSandbox (wasm.py)

    - runtime Pyodide, exécuté dans un sous-processus Node dédié ou via un
      binding Python selon ce qui est disponible dans l'environnement ;
      tu encapsules ce choix derrière une fonction _spawn_runtime() afin
      qu'il reste substituable ;
    - système de fichiers virtuel : seuls les MountSpec fournis sont
      montés, en lecture seule sauf mention contraire ; output_dir est le
      seul chemin inscriptible ;
    - paquets autorisés, liste blanche explicite : numpy, pandas, scipy,
      matplotlib, sympy, scikit-learn ;
    - un import hors liste blanche lève PackageUnavailableInWasmError dont
      le message PROPOSE EXPLICITEMENT le passage au niveau 2 et nomme le
      paquet manquant. Tu n'échoues jamais sèchement sur ce cas ;
    - matplotlib doit être forcé en backend Agg ;
    - timeout mural appliqué par le superviseur, pas par le code invité ;
    - les fichiers écrits dans output_dir sont collectés dans artifacts.

4. NativeSandbox Windows (native_windows.py)

    - création d'un Job Object via pywin32 :
      JOBOBJECT_EXTENDED_LIMIT_INFORMATION avec ProcessMemoryLimit,
      JobMemoryLimit, PerProcessUserTimeLimit, ActiveProcessLimit,
      et le flag JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE ;
    - le processus enfant est créé SUSPENDU, assigné au Job, puis repris :
      l'assignation doit précéder toute exécution de code invité ;
    - répertoire de travail restreint au répertoire projet ;
    - network_isolation_guaranteed = False, sans exception ;
    - à la fermeture du Job, tous les descendants sont tués.

5. NativeSandbox Linux (native_linux.py)

    - preexec_fn appliquant setrlimit : RLIMIT_AS, RLIMIT_CPU,
      RLIMIT_NPROC, RLIMIT_FSIZE ;
    - tentative d'isolation réseau par unshare -n ; en cas d'échec
      (privilèges insuffisants), poursuivre avec
      network_isolation_guaranteed = False et journaliser ;
    - nouveau groupe de processus, kill du groupe complet au timeout.

6. Persistance

    Chaque exécution écrit une ligne dans code_execution : origin,
    sandbox_level, code, stdout tronqué à 64 Ko, stderr tronqué à 64 Ko,
    exit_code, duration_ms, started_at. Les artefacts sont référencés par
    chemin relatif au projet.

7. Consentement

    Le niveau 2 exige un consentement de périmètre native_execution,
    vérifié AVANT le lancement et journalisé à chaque exécution. En son
    absence, lever ConsentRequiredError. Le niveau 1 n'exige aucun
    consentement.

8. Tests

    test_factory_agent_origin_always_wasm
    test_factory_agent_native_mode_downgraded_and_logged
    test_factory_user_native_selects_platform_executor
    test_factory_unsupported_platform_raises

    test_wasm_network_blocked                  # Windows ET Linux
    test_wasm_filesystem_isolated_outside_mounts
    test_wasm_readonly_mount_rejects_write
    test_wasm_output_dir_writable
    test_wasm_package_not_whitelisted_suggests_level2
    test_wasm_timeout_enforced
    test_wasm_artifacts_collected

    test_native_memory_limit_kills_process     # marqué par plateforme
    test_native_cpu_limit_enforced
    test_native_wall_timeout_kills_process_group
    test_native_windows_reports_network_not_guaranteed
    test_native_requires_consent
    test_execution_persisted_with_level

    Les tests spécifiques à une plateforme utilisent
    @pytest.mark.skipif(sys.platform != ...).
    Les tests d'isolation Wasm ne sont JAMAIS marqués skipif par
    plateforme : leur intérêt est précisément d'être identiques partout.

9. Scripts de vérification

    scripts/check_sandbox_windows.py et check_sandbox_linux.py : exécutent
    en séquence un script inoffensif, un script tentant un accès réseau, un
    script tentant un accès disque hors montage, un script dépassant la
    limite mémoire, un script en boucle infinie. Affichent un tableau
    niveau / test / résultat attendu / résultat obtenu. Sortie 0 si tout
    est conforme aux garanties DÉCLARÉES pour la plateforme — c'est-à-dire
    que l'échec du blocage réseau au niveau 2 sous Windows est un résultat
    ATTENDU, pas un échec du script.

DÉPENDANCES AUTORISÉES

pydantic, pytest, pytest-asyncio, pywin32 (Windows uniquement).
Runtime Pyodide via le mécanisme disponible dans l'environnement.
Pas de docker, pas de firejail, pas de nsjail.

INTERDICTIONS

- setrlimit sous Windows.
- Toute affirmation, en code, en test ou en message, que le niveau 2 isole
  le réseau sous Windows.
- Exécution de code invité avant l'assignation au Job Object.
- Niveau 2 sans consentement vérifié.
- Choix du niveau d'isolation par un agent.
- Modification d'un fichier hors périmètre.

CRITÈRES D'ACCEPTATION

    cd backend && pytest -q backend/tests/tests_sandbox
    ruff check backend/ && ruff format --check backend/
    python scripts/check_sandbox_windows.py   # sur Windows
    python scripts/check_sandbox_linux.py     # sur Linux

FORMAT DE RÉPONSE

1. Plan d'implémentation, 10 lignes maximum.
2. Contenu intégral de chaque fichier.
3. Sortie attendue des vérifications, par plateforme.
4. Section "Réserves".
```

## Message de commit attendu

```text
feat(sandbox): exécution isolée à deux niveaux, Wasm et natif contraint

Implémente US-004 (Backlog V0.3).

- Niveau 1 Pyodide/Wasm pour tout code d'origine agent : isolation réseau
  et disque garantie par le runtime, identique sur Windows et Linux.
- Niveau 2 natif sous consentement : Job Objects (Windows), setrlimit +
  unshare (Linux).
- Champ network_isolation_guaranteed honnête et persisté : False au
  niveau 2 sous Windows.
- Abaissement silencieux et journalisé du niveau demandé par un agent.

Refs: US-004, ADR-005, ADR-012
Corrige: D-03 (critère d'isolation réseau infaisable en natif Windows)
```
