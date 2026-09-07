# ADR-005 — Sandbox à deux niveaux : WebAssembly puis natif contraint

- **Statut :** Accepté
- **Date :** 2026-09-05
- **Remplace :** l'approche « Job Objects seuls » du Backlog V0.2
- **Corrige :** défaut D-03

## Contexte

L'outil exécute du code Python de deux provenances très différentes : du code
**généré par le LLM** (non revu, potentiellement fautif ou dangereux) et du code
**écrit par l'utilisateur** (calcul scientifique lourd, parfois via WSL).

La V0.1 proposait `setrlimit` — API POSIX inexistante sous Windows. La V0.2 a
corrigé par les **Job Objects Win32**, ce qui est juste pour la mémoire, le temps
CPU et le nombre de processus. Mais les Job Objects **n'isolent ni le réseau ni le
système de fichiers**. Le critère d'acceptation « le réseau est désactivé » est
donc infaisable en natif sous Windows sans WFP ni AppContainer — hors de portée
d'un MVP. Docker est exclu (ADR-012).

## Décision

Deux niveaux, sélectionnés par `SandboxFactory(origin, mode, platform)`.

**Niveau 1 — WebAssembly (Pyodide). Défaut pour tout code généré par un agent.**
- Isolation réseau et disque garantie par le runtime, donc **identique sur Windows
  et Linux**.
- Système de fichiers virtuel ; seuls les jeux de données du projet sont montés,
  en lecture seule, sauf un répertoire de sortie.
- Couvre `numpy`, `pandas`, `scipy`, `matplotlib`, `sympy`, `scikit-learn` — soit
  l'essentiel des figures et statistiques d'un mémoire.

**Niveau 2 — Sous-processus natif contraint. Opt-in, code utilisateur, calcul lourd.**
- Windows : Job Objects (`ProcessMemoryLimit`, `PerProcessUserTimeLimit`,
  `ActiveProcessLimit`, `KILL_ON_JOB_CLOSE`).
- Linux : `setrlimit` + `unshare -n` si disponible.
- **Avertissement explicite en interface** : sous Windows, l'isolation réseau
  n'est pas garantie. Consentement requis et journalisé avant chaque lancement.

## Justification du découpage

Le code le moins fiable — celui que l'IA écrit seule — reçoit l'isolation la plus
forte. Le code le plus fiable — celui que l'utilisateur assume — reçoit les
capacités les plus larges, sous consentement éclairé.

## Options écartées

| Option | Motif |
|---|---|
| Job Objects seuls | N'isolent ni réseau ni disque ; critère de test infaisable |
| Docker | Exclu au MVP (ADR-012) |
| WFP / AppContainer | Complexité disproportionnée pour un MVP |
| Wasm exclusivement | Interdirait OpenFOAM, Serpent, les stacks natives |

## Conséquences

Deux implémentations à maintenir derrière une interface commune. Certaines
bibliothèques ne sont pas disponibles en Wasm : le message d'erreur doit proposer
explicitement le passage au niveau 2 plutôt qu'échouer sèchement.

## Vérification

`test_sandbox_factory_selects_wasm_for_agent_origin` ·
`test_network_disabled_level1` (Windows **et** Linux) ·
`test_filesystem_isolated_level1` · `test_memory_limit_level2` ·
`test_consent_required_level2` · `scripts/check_sandbox_windows.py` ·
`scripts/check_sandbox_linux.py`
