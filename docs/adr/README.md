# Architecture Decision Records — Science AI Writer IDE

Décisions d'architecture figées. Un ADR au statut **Accepté** n'est pas
rediscutable pendant l'implémentation : une objection se consigne en section
« Réserves » du rapport de story, jamais par une déviation silencieuse du code.

| ADR | Décision | Statut | Corrige |
|---|---|---|---|
| [000](ADR-000-template.md) | Modèle d'ADR | Modèle | — |
| [001](ADR-001-sqlite-unique-wal-aiosqlite.md) | Base SQLite unique, WAL, aiosqlite | Accepté | — |
| [002](ADR-002-sqlite-vec.md) | sqlite-vec, séparation `chunk`/`vec_chunk` | Accepté | D-01 |
| [003](ADR-003-modele-llm-unique-persistant.md) | Modèle LLM unique persistant | Accepté | D-08 |
| [004](ADR-004-langgraph.md) | Orchestration déterministe LangGraph | Accepté | — |
| [005](ADR-005-sandbox-deux-niveaux.md) | Sandbox Wasm + natif contraint | Accepté | D-03 |
| [006](ADR-006-format-canonique-quarto.md) | Quarto `.qmd` canonique | Accepté | D-02 |
| [007](ADR-007-bibtex-dynamique.md) | `.bib` généré à l'export | Accepté | — |
| [008](ADR-008-guardrails-circuit-breaker.md) | Guardrails Pydantic + circuit breaker | Accepté | — |
| [009](ADR-009-audit-tamper-evident.md) | Audit à détection d'altération | Accepté | D-07 |
| [010](ADR-010-local-strict-consentement.md) | Local strict, consentement par périmètre | Accepté · amendé 2026-09-06 | — |
| [011](ADR-011-frontend-angular-dockview-signals.md) | Angular, Dockview, Signals | Accepté | — |
| [012](ADR-012-pas-de-docker-au-mvp.md) | Pas de Docker au MVP | Accepté | — |
| [013](ADR-013-embeddings-cpu-hors-ollama.md) | Embeddings CPU hors Ollama | Accepté | D-05 |
| [014](ADR-014-backend-lmstudio.md) | LM Studio comme moteur d'inférence local | Accepté | amende ADR-003 |
| [015](ADR-015-modele-31b-deversement-cpu.md) | Modèle 31B, déversement CPU/RAM assumé | Accepté | amende ADR-003 |

Les identifiants `D-xx` renvoient aux défauts relevés dans
`ANALYSE-CRITIQUE-V0.2.md`.
