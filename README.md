# Science AI Writer IDE

Environnement local multi-agents d'assistance à la rédaction de documents
académiques auto-portants de 30 à 300 pages — mémoire de master recherche,
mémoire d'ingénieur, thèse de doctorat, HDR.

**Version documentaire :** V0.3.1 · **Matériel cible :** 10 Go de VRAM,
Windows 11 et Linux · **Docker :** interdit au MVP (ADR-012).

L'outil ne produit jamais un document sans intervention humaine. Toute
affirmation scientifique générée est soit rattachée à une source approuvée,
soit marquée comme synthèse, hypothèse ou limite.

---

## Hiérarchie documentaire

En cas de divergence entre deux documents, l'échelon supérieur l'emporte.

| Rang | Document |
|---|---|
| 1 | [`docs/adr/`](docs/adr/) — décisions figées, non rediscutables |
| 2 | [`docs/specs/specifications-techniques-v0.3.md`](docs/specs/specifications-techniques-v0.3.md) |
| 3 | [`docs/backlog/backlog-v0.3.md`](docs/backlog/backlog-v0.3.md) |
| 4 | [`docs/prompts/`](docs/prompts/) — instructions d'exécution par story |

Commencer par [`docs/prompts/00-source-of-truth.md`](docs/prompts/00-source-of-truth.md).
Le contrat d'API [`contracts/openapi.yaml`](contracts/openapi.yaml) est normatif :
une divergence entre le code et lui est un défaut du code.

## Installation

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"     # Windows
```

`pyproject.toml` est la source unique des dépendances backend.
`requirements.txt` n'est pas édité à la main.

## Moteur d'inférence

Le moteur par défaut est **LM Studio** ([ADR-014](docs/adr/ADR-014-backend-lmstudio.md),
qui amende ADR-003). Le serveur local n'est pas démarré automatiquement :

```bash
lms server start
```

Le modèle par défaut est **`google/gemma-4-31b`** (Q8_0, 33,8 Go). Il ne tient
pas en VRAM : LM Studio en déverse une partie sur le CPU et la RAM. C'est un
mode nominal, décidé par [ADR-015](docs/adr/ADR-015-modele-31b-deversement-cpu.md),
pas une dégradation.

Ce que cela implique concrètement :

| Grandeur | Mesure |
|---|---|
| VRAM occupée | 9 713 / 10 240 Mo |
| RAM occupée | 59,7 / 63,9 Go |
| Débit de génération | ~2,3 tokens/s |
| Section de 1 500 mots | ~15 minutes |
| Plan complet (spike 02) | 24 minutes |

La qualité du modèle prime sur le temps de génération : un mémoire se rédige
sur des semaines. Les budgets de latence de §12.2 sont levés en conséquence.
Le rechargement des poids se détecte par l'**état de résidence** du modèle, non
par une durée : `llm_max_ttft_ms` n'est qu'un plafond de sécurité.

**LM Studio ou Ollama, indifféremment.** Les deux moteurs vivent derrière la
même interface, et basculer ne demande qu'un réglage :

```bash
SAW_LLM_BACKEND=ollama
```

Chaque moteur garde son propre identifiant de modèle — `google/gemma-4-31b`
n'existe pas chez Ollama, qui le connaît sous `gemma4:31b` — dans
`SAW_LMSTUDIO_MODEL` et `SAW_OLLAMA_MODEL`. `SAW_LLM_MODEL` n'existe plus : un
réglage unique obligeait à changer aussi le modèle en changeant de moteur.
Aucun modèle n'est téléchargé d'office (ADR-010) : `ollama pull gemma4:31b` ou
`lms get google/gemma-4-31b` restent un geste de l'utilisateur.

## Garde-fous de véracité

Une citation inventée dans un mémoire de doctorat détruit la crédibilité du
travail et celle de l'outil. Quatre contrôles s'appliquent à chaque section
produite, **avant** toute écriture en base ([§5.5](docs/specs/specifications-techniques-v0.3.md),
[ADR-008](docs/adr/ADR-008-guardrails-circuit-breaker.md)) :

| | Contrôle | Rejet |
|---|---|---|
| **V1** | Toute clé de citation, déclarée ou écrite `@clef` dans le texte, figure dans la liste close dérivée des extraits fournis | clé inconnue |
| **V2** | Toute affirmation présentée comme sourcée et portant un chiffre significatif désigne l'extrait qui l'établit | chiffre non rattaché |
| **V3** | Tout DOI et toute URL du texte existent dans les sources du projet | identifiant hors base |
| **V4** | Toute clé déclarée par une affirmation sourcée figure aussi dans le texte | citation invisible dans le document rendu |

**Ils sont syntaxiques, et c'est ce qui les rend fiables.** Aucun ne demande à
un modèle de juger sa propre production : un modèle qui invente une référence
la trouve tout aussi plausible à la relecture, et un contrôle fondé sur son
jugement échouerait précisément dans le cas qu'il doit attraper.

Un rejet renvoie au rédacteur, jamais au relecteur, et le circuit breaker
arrête la boucle au troisième essai — la section n'est alors pas produite.
Sous trois extraits pertinents, la rédaction est refusée avant même de
commencer : un texte écrit sans matière serait rempli de mémoire par le
modèle, c'est-à-dire d'affirmations sans source présentées comme si elles en
avaient une.

## Exécution de code

Le code écrit par une IA est le moins fiable du système : il reçoit donc
l'isolation la plus forte. Deux niveaux, et le serveur seul décide du niveau
effectif ([ADR-005](docs/adr/ADR-005-sandbox-deux-niveaux.md)).

| Niveau | Moteur | Réseau | Disque | Consentement |
|---|---|---|---|---|
| **1** | WebAssembly (Pyodide) | isolé, sur tout OS | hors montages déclarés : inaccessible | aucun |
| **2** | natif contraint | **non isolé sous Windows** | accessible | `native_execution` |

**Tout code d'origine agent s'exécute au niveau 1**, quel que soit le niveau
demandé. L'abaissement n'est pas une erreur : il est silencieux, journalisé, et
rapporté dans `downgraded_from_requested_mode`. Un agent ne choisit jamais sa
propre isolation.

Le champ `network_isolation_guaranteed` est **honnête par construction** et
persisté avec chaque exécution. Faux au niveau 2 sous Windows : les Job Objects
bornent la mémoire, le CPU et le nombre de processus, mais n'ont aucune prise
sur les sockets. Sous Linux, il n'est vrai que si `unshare -n` a réellement
abouti — il n'est donc jamais déduit du niveau, toujours relu de ce qui a été
observé.

### Prérequis

Le niveau 1 demande **Node.js** et une distribution **Pyodide vendorisée
hors-ligne** : rien n'est téléchargé au lancement (ADR-010).

```bash
cd backend/app/sandbox/runtime && npm install pyodide
```

Ce répertoire n'est pas versionné. Sans lui, l'exécution de niveau 1 est
refusée avec la marche à suivre, et les tests d'isolation Wasm sautent — ils ne
sont jamais conditionnés à la plateforme, seulement à la présence du runtime,
puisque la garantie est la même partout.

Le niveau 2 sous Windows repose sur **pywin32**, posé par
`pip install -e ".[dev]"` (dépendance déclarée `sys_platform == 'win32'`).

**Seul Pyodide « core » est vendorisé.** Les roues scientifiques — numpy,
pandas, scipy, matplotlib, sympy, scikit-learn — ne le sont pas encore : une
figure demandée à un agent n'est donc pas encore rendue au niveau 1, et le test
de bout en bout correspondant saute. Les garde-fous d'isolation, eux, sont
éprouvés sans elles.

## Export

Le format canonique est **Quarto** (`.qmd`), et lui seul —
[ADR-006](docs/adr/ADR-006-format-canonique-quarto.md). Ni MyST, ni Sphinx,
ni repli : leurs renvois s'écrivent `{ref}` et `{numref}` là où Quarto écrit
`@fig-`, `@tbl-`, `@sec-`, et les mélanger produit des renvois non résolus
sur un document long.

Quarto n'est pas embarqué (ADR-012, aucun conteneur) :

```bash
winget install --id Posit.Quarto
```

```bash
quarto install tinytex
```

La seconde commande n'est pas facultative : l'installeur ne pose pas la
chaîne LaTeX, et sans elle le PDF échoue sur un paquet manquant. Vérifié sur
le poste cible avec **Quarto 1.10.18** et TinyTeX (TeX Live 2026) : mémoire de
démonstration de 3 chapitres, 8 sections, 12 figures, 5 tableaux et 20 sources
compilé en PDF, DOCX et HTML, sans un seul renvoi ni une seule citation non
résolus.

Si le binaire n'est pas dans le `PATH`, le désigner par `SAW_QUARTO_PATH`.

**Sous Windows, gardez `SAW_DATA_DIR` court.** Quarto ouvre des fichiers de
session sous le répertoire d'export ; au-delà de 260 caractères ils ne s'ouvrent
pas, et activer les chemins longs de Windows n'y change rien (mesuré). L'export
est donc refusé avant compilation, avec la longueur atteinte, dès que le
répertoire de données dépasse environ 150 caractères — ce qui arrive vite avec
un profil redirigé vers OneDrive.

À chaque export, le `.bib` est **regénéré depuis la base**, à partir des
seules citations vérifiées des sections incluses
([ADR-007](docs/adr/ADR-007-bibtex-dynamique.md)). Le `.bib` que vous importez
alimente vos sources ; il ne compile jamais. Une citation non vérifiée bloque
l'export en nommant la section, la clé et le passage — elle n'est ni ignorée,
ni incluse quand même.

Chaque export écrit un répertoire daté, jamais écrasé, contenant le `.qmd`
assemblé, la bibliographie, `_quarto.yml`, les figures, les sorties demandées
et un rapport JSON. Le journal de Quarto est **analysé, pas seulement
relayé** : Quarto sort en code 0 sur un document dont les renvois ne se
résolvent pas, et le rapport les liste explicitement.

## Lancement

```bash
.venv/Scripts/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

L'API est liée à la boucle locale et n'est jamais exposée sur le réseau.

## Vérifications

```bash
.venv/Scripts/python -m pytest backend/tests -q -m "not integration"
.venv/Scripts/python -m ruff check backend/ scripts/
.venv/Scripts/python -m ruff format --check backend/ scripts/
.venv/Scripts/python scripts/check_sqlite_wal.py
.venv/Scripts/python scripts/check_sqlite_vec.py
.venv/Scripts/python scripts/check_audit_chain.py
.venv/Scripts/python scripts/check_embeddings_cpu.py
.venv/Scripts/python scripts/check_no_cloud_calls.py
.venv/Scripts/python scripts/check_quarto_export.py # code 2 si Quarto est absent
.venv/Scripts/python scripts/check_llm_latency.py   # si le moteur est joignable
.venv/Scripts/python scripts/check_sandbox_windows.py # ou check_sandbox_linux.py
```

`check_sandbox_*` exerce les cinq sondes aux deux niveaux — inoffensive, réseau,
disque hors montage, dépassement mémoire, boucle infinie — et compare le résultat
aux garanties **déclarées** pour la plateforme. Que le niveau 2 sous Windows
joigne le réseau est donc un résultat ATTENDU, pas un échec. La sonde réseau vise
un écho TCP local : rien ne quitte la machine.

Un script indisponible faute d'environnement — pas de GPU, pas d'Ollama, pas de
Quarto — sort en code 2 : ce n'est pas un échec. Un script qui sort en 1 en est un.

## État d'avancement

| Story | Objet | État |
|---|---|---|
| US-001 | Backend FastAPI + aiosqlite WAL | livrée |
| US-002 | Schéma SQLite unique + sqlite-vec | livrée |
| US-003 | LLM Manager, modèle unique persistant | livrée (LM Studio + Ollama) |
| US-701 | Journal d'audit à détection d'altération | livrée |
| US-005 | Embeddings CPU hors Ollama | livrée |
| US-101 | CRUD projets, registre, sauvegarde | livrée |
| US-102 | Import de sources et ingestion RAG | livrée |
| US-201/202 | Machine à états, guardrails, circuit breaker | livrée |
| US-PLAN-001 | Plan : génération, édition, validation | livrée |
| US-301 | Rédaction de section sourcée, garde-fous de véracité | livrée |
| US-302 | Relecture hiérarchisée et score de qualité | livrée |
| US-501 | Bibliographie compilée depuis les citations vérifiées | livrée |
| US-502 | Export Quarto multi-format, journal analysé | livrée |
| US-004 | Bac à sable à deux niveaux, Wasm et natif contraint | livrée |
| US-401 | Agent d'analyse, figures reproductibles, artefacts | livrée (roues scientifiques à vendoriser) |

Chemin critique complet et ordre de traitement : [`docs/plan-execution.md`](docs/plan-execution.md).

## Spikes de dérisquage

Résultats et méthode dans [`spikes/RESULTATS.md`](spikes/RESULTATS.md).
Le spike 01 (sqlite-vec) est conforme sous Linux **et** sous Windows. Le
spike 02 — le décisif — est exécuté : 4/4 hypothèses conformes, sept
générations de plan sur sept valides au premier essai. Son seuil de 95 %
reste non certifié, sept essais ne garantissant que 65 %.
L'addendum du 7 septembre 2026 consigne trois constats d'environnement
mesurés sur le poste cible, dont un chargement de modèle à froid de
7 min 44 s qui rend la persistance d'ADR-003 indispensable et non
optionnelle.
