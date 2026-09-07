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
.venv/Scripts/python scripts/check_no_cloud_calls.py
.venv/Scripts/python scripts/check_llm_latency.py   # si Ollama disponible
```

Un script indisponible faute d'environnement — pas de GPU, pas d'Ollama, pas de
Quarto — sort en code 2 : ce n'est pas un échec. Un script qui sort en 1 en est un.

## État d'avancement

| Story | Objet | État |
|---|---|---|
| US-001 | Backend FastAPI + aiosqlite WAL | livrée |
| US-002 | Schéma SQLite unique + sqlite-vec | livrée |
| US-003 | LLM Manager, modèle unique persistant | livrée |
| US-701 | Journal d'audit à détection d'altération | livrée |
| US-005 | Embeddings CPU hors Ollama | à faire |
| US-101 | CRUD projets, registre, sauvegarde | à faire |
| US-102 | Import de sources et ingestion RAG | à faire |

Chemin critique complet et ordre de traitement : [`docs/plan-execution.md`](docs/plan-execution.md).

## Spikes de dérisquage

Résultats et méthode dans [`spikes/RESULTATS.md`](spikes/RESULTATS.md).
Le spike 01 (sqlite-vec) est conforme sous Linux **et** sous Windows.
L'addendum du 7 septembre 2026 consigne trois constats d'environnement
mesurés sur le poste cible, dont un chargement de modèle à froid de
7 min 44 s qui rend la persistance d'ADR-003 indispensable et non
optionnelle.
