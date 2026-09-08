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

Ollama reste disponible derrière la même interface : `SAW_LLM_BACKEND=ollama`
avec un `SAW_LLM_MODEL` correspondant.

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
.venv/Scripts/python scripts/check_llm_latency.py   # si le moteur est joignable
```

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
