# Prompt Pack V0.3 — Science AI Writer IDE

Documents destinés à une IA de codage. À lire dans l'ordre.

| Fichier | Story | Rôle |
|---|---|---|
| [`00-source-of-truth.md`](00-source-of-truth.md) | — | **À lire en premier.** Hiérarchie documentaire, treize décisions figées, règles de conduite, chemin critique. |
| [`PROMPT-US-002.md`](PROMPT-US-002.md) | US-002 | Schéma SQLite unique + sqlite-vec |
| [`PROMPT-US-003.md`](PROMPT-US-003.md) | US-003 | LLM Manager, modèle unique persistant |
| [`PROMPT-US-004.md`](PROMPT-US-004.md) | US-004 | Sandbox à deux niveaux, Wasm et natif |
| [`PROMPT-US-005.md`](PROMPT-US-005.md) | US-005 | Embeddings CPU hors Ollama |
| [`PROMPT-US-006.md`](PROMPT-US-006.md) | US-006 | Vérification du budget VRAM |
| [`PROMPT-US-101.md`](PROMPT-US-101.md) | US-101 | CRUD projets, registre, sauvegarde |
| [`PROMPT-US-102.md`](PROMPT-US-102.md) | US-102 | Import de sources et ingestion RAG |
| [`PROMPT-US-201-202.md`](PROMPT-US-201-202.md) | US-201, US-202 | Machine à états, guardrails, circuit breaker |
| [`PROMPT-US-PLAN-001.md`](PROMPT-US-PLAN-001.md) | US-PLAN-001 | Plan : génération, édition, validation |
| [`PROMPT-US-BIBLIO-001.md`](PROMPT-US-BIBLIO-001.md) | US-BIBLIO-001 | Recherche bibliographique multi-API |
| [`PROMPT-US-301.md`](PROMPT-US-301.md) | US-301 | Rédaction de section avec garde-fous de véracité |
| [`PROMPT-US-302.md`](PROMPT-US-302.md) | US-302 | Relecture et score de qualité |
| [`PROMPT-US-401.md`](PROMPT-US-401.md) | US-401 | Agent code et figures reproductibles |
| [`PROMPT-US-501-502.md`](PROMPT-US-501-502.md) | US-501, US-502 | Bibliographie dynamique et export Quarto |
| [`PROMPT-US-701.md`](PROMPT-US-701.md) | US-701 | Journal d'audit à détection d'altération |
| [`PROMPT-US-EXPORT-003.md`](PROMPT-US-EXPORT-003.md) | US-EXPORT-003 | Annexe de déclaration d'usage de l'IA |
| [`PROMPT-US-801.md`](PROMPT-US-801.md) | US-801 | Coquille IDE Angular, Dockview, pont SSE |
| [`PROMPT-US-UI-002.md`](PROMPT-US-UI-002.md) | US-UI-002 | Éditeur Quarto, validation locale |
| [`PROMPT-US-UI-003.md`](PROMPT-US-UI-003.md) | US-UI-003 | Panneau de code Monaco et artefacts |
| [`PROMPT-US-DASH-001.md`](PROMPT-US-DASH-001.md) | US-DASH-001 | Tableau de bord d'avancement |

### Compléments P1

| Fichier | Story | Rôle |
|---|---|---|
| [`PROMPT-US-601.md`](PROMPT-US-601.md) | US-601 | Anti-plagiat local puis distant sous consentement |
| [`PROMPT-US-AUTH-001.md`](PROMPT-US-AUTH-001.md) | US-AUTH-001 | Rôles et comptes locaux |
| [`PROMPT-US-EXPORT-001-002.md`](PROMPT-US-EXPORT-001-002.md) | US-EXPORT-001, US-EXPORT-002 | Éléments documentaires et modèle académique |
| [`PROMPT-US-DATA-001.md`](PROMPT-US-DATA-001.md) | US-DATA-001 | Jeux de données et montage en sandbox |
| [`PROMPT-US-IMPORT-001.md`](PROMPT-US-IMPORT-001.md) | US-IMPORT-001 | Import DOI, BibTeX, RIS |
| [`PROMPT-US-RAG-002.md`](PROMPT-US-RAG-002.md) | US-RAG-002 | Filtres avancés du RAG |
| [`PROMPT-US-WORKFLOW-001.md`](PROMPT-US-WORKFLOW-001.md) | US-WORKFLOW-001 | Pipeline automatique contrôlé |

### Extensions P2

| Fichier | Story | Rôle |
|---|---|---|
| [`PROMPT-US-UI-004-005.md`](PROMPT-US-UI-004-005.md) | US-UI-004, US-UI-005 | Versions, comparaison, commentaires ancrés |
| [`PROMPT-US-JUP-001.md`](PROMPT-US-JUP-001.md) | US-JUP-001 | Notebooks Jupyter comme source d'analyse |
| [`PROMPT-US-CALC-001.md`](PROMPT-US-CALC-001.md) | US-CALC-001 | Codes de calcul externes via WSL2 |
| [`PROMPT-US-ZOTERO-001.md`](PROMPT-US-ZOTERO-001.md) | US-ZOTERO-001 | Lecture de la bibliothèque Zotero locale |
| [`PROMPT-US-LLM-002.md`](PROMPT-US-LLM-002.md) | US-LLM-002 | LLM distant, routage par agent, budget bloquant |

Le prompt US-001 (backend FastAPI + aiosqlite WAL) reste valable dans sa
version d'origine : aucune correction V0.3 ne le touche.

## Chemin critique du MVP

```
US-001 ✅ → US-002 ✅ → US-003 ✅ → US-005 ✅ → US-101 ✅ → US-102 ✅
       → US-201 ✅ → US-PLAN-001 ✅ → US-301 ✅ → US-501 ✅ → US-502 ✅
```

**Le chemin critique est entièrement couvert.** Les onze stories menant du
dépôt vide au premier PDF compilé disposent chacune d'un prompt.

**Toutes les stories P0 sont pourvues.** Hors chemin critique : US-004
(sandbox), US-006 (budget VRAM), US-701 (audit), US-BIBLIO-001
(bibliographie) — menables en parallèle dès US-001.

Backend P1 également prêtes : US-302 (relecture), US-401 (agent code),
US-EXPORT-003 (déclaration d'usage de l'IA).

Frontend : US-801 fournit la coquille, les stores et le pont temps réel ;
US-UI-002, US-UI-003 et US-DASH-001 s'y branchent par `panel-registry.ts`.
Les trois panneaux d'édition et de pilotage sont pourvus.

## Couverture

**Le backlog V0.3 est intégralement pourvu en prompts** : 31 user stories,
de US-001 au dépôt vide jusqu'aux extensions P2.

Aucune story n'attend de spécification. Ce qui reste relève de
l'implémentation, pas de la conception.


## Conventions communes

Chaque prompt suit la même structure : sources de vérité hiérarchisées,
contexte de la décision, périmètre strict avec ce qui est **exclu**,
exigences numérotées, liste close de dépendances, interdictions explicites,
critères d'acceptation exécutables, format de réponse imposé avec section
« Réserves » obligatoire.

La section « contexte de la décision » n'est pas décorative : elle explique
*pourquoi* une contrainte existe, ce qui évite qu'un agent de codage la
contourne en croyant bien faire.
