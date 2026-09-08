# Spécifications techniques détaillées V0.3 — Science AI Writer IDE

**Statut :** proposé — remplace la V0.2 · révision 0.3.2
**Date :** 2026-09-05, révisé les 2026-09-06 et 2026-09-07
**Autorité :** ce document est subordonné aux ADR. En cas de divergence, l'ADR fait foi.

## Journal des modifications V0.2 → V0.3

| § | Modification | Défaut corrigé | ADR |
|---|---|---|---|
| 6.3 | Schéma vectoriel refondu : `chunk` relationnel + `vec_chunk` virtuel liés par `rowid` | D-01 | ADR-002 |
| 9 | Format canonique : Quarto `.qmd`. MyST retiré du chemin critique | D-02 | ADR-006 |
| 8 | Sandbox à deux niveaux : Wasm (code agent) / natif contraint (code utilisateur) | D-03 | ADR-005 |
| 7.2 | Critère de persistance LLM fondé sur `load_duration` | D-04 | ADR-003 |
| 7.4 | Embeddings sortis d'Ollama, exécutés sur CPU via ONNX | D-05 | ADR-013 |
| 5.2 | Nœuds de plan intégrés au graphe d'états comme préalable à la rédaction | D-06 | ADR-004 |
| 11 | Vocabulaire d'audit : « détection d'altération », jamais « immuable » | D-07 | ADR-009 |
| 7.3 | Arbitrage modèle unique / qualité de code explicité et paramétrable | D-08 | ADR-003 |
| 13.1 | `pyproject.toml` source unique des dépendances | D-09 | — |
| 12.3 | Budget VRAM global mesuré en intégration continue | D-10 | ADR-003 |
| 9.5 | Annexe de déclaration d'usage de l'IA à l'export | D-11 | — |

### Journal des modifications V0.3.1 → V0.3.2

Mise en cohérence avec ADR-014 (moteur LM Studio) et ADR-015 (modèle 31B,
déversement CPU/RAM assumé). Les deux ADR font foi ; ce document les applique.

| § | Modification | Motif | ADR |
|---|---|---|---|
| 6.1 | Moteur LM Studio, modèle `google/gemma-4-31b`, persistance par `ttl` | Le moteur devient un réglage ; le modèle ne tient plus en VRAM, par choix | ADR-014, ADR-015 |
| 6.2 | Critère de persistance dépendant du moteur : état du modèle sous LM Studio, `load_duration` sous Ollama | `load_duration` n'existe pas chez LM Studio ; l'état du modèle est un observable plus direct | ADR-014 |
| 6.3 | Nom du modèle de code spécialisé précisé par moteur | Cohérence | ADR-014 |
| 6.4 | « Hors d'Ollama » devient « hors du serveur d'inférence » | Le modèle d'embedding est installé dans LM Studio ; ADR-013 tient | ADR-013 |
| 12.1 | Budget VRAM remplacé par un budget mémoire ; la RAM devient la ressource contrainte | La saturation de la VRAM est voulue ; 4,2 Go de RAM libres | ADR-015 |
| 12.2 | Budgets de génération levés ; les seuils restants requalifiés en détecteurs de panne | Le temps de génération n'est pas une contrainte du produit | ADR-015 |
| 12.3 | `check_vram_budget.py` respécifié : éviction, OOM et RAM au plus bas | Mesurer une marge de VRAM n'a plus d'objet | ADR-015 |
| 13.1 | Dépendance `ollama` retirée ; dépendances de développement explicitées | Elle n'était jamais importée ; les deux backends sont écrits sur `httpx` | — |

### Journal des modifications V0.3 → V0.3.1

| § | Modification | Motif |
|---|---|---|
| 11.3, 11.4 | `plagiarism_check` : l'étendue du texte transmis est **choisie par l'utilisateur** (passages, section, document). La vérification distante ne dépend plus d'un signalement local préalable. | Décision produit — l'auteur est propriétaire de son texte et seul juge du compromis confidentialité / vérification |

---

## 1. Périmètre et contraintes impératives

Le *Science AI Writer IDE* est un environnement local multi-agents d'assistance à la rédaction de documents académiques auto-portants de 30 à 300 pages (mémoire de master recherche, mémoire d'ingénieur, thèse de doctorat, HDR).

**Contraintes non négociables :**

| Contrainte | Valeur | Origine |
|---|---|---|
| VRAM disponible | 10 Go | Matériel cible |
| Systèmes d'exploitation | Windows 11 et Linux | Public visé |
| Docker | Interdit au MVP | ADR-012 |
| Sortie réseau | Refusée par défaut | ADR-010 |
| Base de données | Un fichier `.sqlite` par projet | ADR-001 |
| Validation humaine | Obligatoire sur plan et sur chaque section | Cahier des charges V3 |

**Principe directeur.** L'outil ne produit jamais un document sans intervention humaine. Toute affirmation scientifique générée est soit rattachée à une source approuvée, soit marquée comme synthèse, hypothèse ou limite. L'invention de références, de données ou de résultats est traitée comme un défaut bloquant, pas comme une imperfection.

---

## 2. Architecture générale

```
┌─────────────────────────────────────────────────────────────┐
│  Frontend Angular (standalone, Signals, Dockview)           │
│  Plan · Éditeur .qmd · Sources · Monaco · Console · Revue   │
└───────────────────────────┬─────────────────────────────────┘
                            │ HTTP + SSE (localhost)
┌───────────────────────────▼─────────────────────────────────┐
│  Backend FastAPI (monolithe modulaire, asynchrone)          │
│                                                             │
│  api/v1  ──►  services  ──►  agents (LangGraph)             │
│                  │                    │                     │
│                  │                    ├─► llm/manager       │
│                  │                    ├─► rag/              │
│                  │                    ├─► sandbox/          │
│                  │                    ├─► biblio/           │
│                  │                    └─► export/           │
│                  ▼                                          │
│              db/ (aiosqlite + sqlite-vec)                   │
└───────────────────────────┬─────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
   Ollama (LLM)      projet.sqlite        Quarto + TinyTeX
   port local        données + vecteurs   binaire local
```

**Monolithe modulaire, pas microservices.** Le déploiement local ne tolère pas l'orchestration de plusieurs processus. Les frontières de modules sont maintenues au niveau du code (interfaces, absence d'import croisé), afin qu'une extraction ultérieure reste possible sans réécriture.

---

## 3. Arborescence du dépôt

```text
science-ai-writer-ide/
├── pyproject.toml                  # source unique des dépendances backend
├── README.md
├── .env.example
├── docs/
│   ├── adr/                        # ADR-000 à ADR-013
│   ├── specs/
│   │   ├── cahier-des-charges-v3.md
│   │   └── specifications-techniques-v0.3.md
│   ├── backlog/backlog-v0.3.md
│   ├── prompts/
│   │   ├── 00-source-of-truth.md
│   │   ├── 01-master-system-prompt.md
│   │   ├── 02-user-story-prompt-template.md
│   │   ├── 03-story-prompts.md
│   │   ├── 04-verification-commands.md
│   │   └── 05-commit-and-pr-rules.md
│   └── tracabilite/matrice-v0.3.md
├── contracts/openapi.yaml
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── core/            config.py · errors.py · logging.py
│   │   ├── db/              session.py · schema.sql · vector.py · migrations/
│   │   ├── models/          pydantic : project, plan, section, citation, review
│   │   ├── api/v1/          health · projects · sources · biblio · plan
│   │   │                    sections · code · export · audit · consent
│   │   ├── services/        project · ingestion · export · consent · audit
│   │   ├── agents/          graph.py · plan_agent · writer_agent
│   │   │                    reviewer_agent · code_agent · guardrails.py
│   │   ├── llm/             manager.py · backends/ollama.py · prompts/
│   │   ├── rag/             chunker.py · embeddings.py · retriever.py
│   │   ├── biblio/          providers/ · dedupe.py · bibtex.py
│   │   ├── sandbox/         base · wasm · native_windows · native_linux · factory
│   │   └── export/          quarto.py · bibliography.py · declaration.py
│   └── tests/               tests_db · tests_api · tests_agents · tests_rag
│                            tests_sandbox · tests_export · tests_integration
├── frontend/                Angular standalone
└── scripts/
    ├── setup_local.sh / .ps1
    ├── check_all.sh / .ps1
    ├── check_sqlite_wal.py
    ├── check_sqlite_vec.py
    ├── check_llm_latency.py
    ├── check_vram_budget.py          # nouveau — D-10
    ├── check_sandbox_windows.py
    ├── check_sandbox_linux.py
    ├── check_quarto_export.py
    └── check_no_cloud_calls.py
```

**Delta par rapport au squelette V0.2 :** ajout de `rag/embeddings.py` (ADR-013), de `sandbox/wasm.py` et `sandbox/factory.py` (ADR-005), de `export/declaration.py` (D-11), de `scripts/check_vram_budget.py` ; suppression de `requirements.txt` comme source de vérité (D-09) ; suppression de toute référence MyST dans `export/` (ADR-006).

---

## 4. Modèle de données

### 4.1 Principes

Un fichier `.sqlite` par projet. Toutes les tables relationnelles et la table virtuelle vectorielle y cohabitent. Horodatages en TEXT ISO-8601 UTC. Booléens en INTEGER 0/1. Toute colonne `REFERENCES` porte une clause `ON DELETE` explicite. Colonnes JSON suffixées `_json`.

### 4.2 Tables relationnelles

| Table | Rôle | Clés étrangères |
|---|---|---|
| `project` | Sujet, discipline, langue, niveau académique | — |
| `source_document` | Référence bibliographique et fichier associé | `project_id` CASCADE |
| `chunk` | Fragment de texte indexé, avec pagination | `source_id` CASCADE |
| `plan` | Problématique et version du plan | `project_id` CASCADE |
| `plan_node` | Arbre du plan | `plan_id` CASCADE, `parent_id` CASCADE |
| `draft_section` | Contenu `.qmd`, statut, score qualité | `plan_node_id` CASCADE |
| `citation` | Lien section → source → chunk | `draft_section_id` CASCADE, `source_id` RESTRICT, `chunk_id` SET NULL |
| `code_execution` | Trace d'exécution avec niveau de sandbox | `project_id` CASCADE |
| `task` | État courant du graphe LangGraph | `project_id` CASCADE |
| `audit_log` | Journal chaîné | `project_id` (sans cascade) |
| `consent` | Consentements par périmètre | `project_id` CASCADE |
| `model_config` | Modèles et dimension d'embedding du projet | `project_id` CASCADE |

`citation.source_id` est en `RESTRICT` délibérément : on n'autorise pas la suppression d'une source encore citée dans une section.

### 4.3 Stockage vectoriel `[RÉVISÉ — D-01]`

```sql
-- INVARIANT : vec_chunk.rowid == chunk.id
-- Les contraintes de clé étrangère ne s'appliquent PAS aux tables virtuelles.
-- L'intégrité référentielle vit donc dans `chunk`, jamais dans `vec_chunk`.

CREATE TABLE chunk (
  id          INTEGER PRIMARY KEY,
  source_id   INTEGER NOT NULL REFERENCES source_document(id) ON DELETE CASCADE,
  ordinal     INTEGER NOT NULL,
  text        TEXT    NOT NULL,
  page_start  INTEGER,
  page_end    INTEGER,
  token_count INTEGER,
  UNIQUE(source_id, ordinal)
);

CREATE VIRTUAL TABLE vec_chunk USING vec0(embedding float[768]);

CREATE TRIGGER chunk_after_delete AFTER DELETE ON chunk
BEGIN
  DELETE FROM vec_chunk WHERE rowid = old.id;
END;
```

La dimension provient de `settings.embedding_dim` et est verrouillée par projet dans `model_config.embedding_dim`. Un écart au démarrage lève `DimensionMismatchError` : changer de modèle d'embedding impose une réindexation, traitée comme une migration.

### 4.4 Index

```sql
CREATE INDEX idx_source_project   ON source_document(project_id);
CREATE INDEX idx_source_doi       ON source_document(doi);
CREATE INDEX idx_chunk_source     ON chunk(source_id);
CREATE INDEX idx_plan_node_plan   ON plan_node(plan_id, ordinal);
CREATE INDEX idx_draft_node       ON draft_section(plan_node_id);
CREATE INDEX idx_citation_section ON citation(draft_section_id);
CREATE INDEX idx_task_state       ON task(project_id, state);
CREATE INDEX idx_audit_project    ON audit_log(project_id, id);
```

### 4.5 Accès et concurrence

`aiosqlite` exclusivement. PRAGMA appliqués à chaque connexion : `journal_mode=WAL`, `busy_timeout=5000`, `foreign_keys=ON`. Pas de file d'écriture applicative (ADR-001). Toute opération multi-tables est encapsulée dans une transaction explicite. La sauvegarde de projet est une opération applicative : `wal_checkpoint(TRUNCATE)` puis copie.

---

## 5. Orchestration LangGraph

### 5.1 État partagé

```python
class GraphState(TypedDict):
    project_id: int
    plan_id: int | None
    current_node_id: int | None
    state: WorkflowState
    retry_count: int
    review_loop_count: int
    last_error: str | None
    payload_json: dict
```

L'état est persisté dans `task` à chaque transition. Une reprise après arrêt du backend repart du dernier état enregistré.

### 5.2 Graphe `[RÉVISÉ — D-06]`

```
PROJECT_CREATED
   └─► SOURCES_INGESTING ──► SOURCES_READY
          │
          └─► PLAN_DRAFTING ──► PLAN_GUARDRAIL ──► PLAN_REVIEW
                                     │  (échec)         │ (validation humaine)
                                     └──► PLAN_DRAFTING ▼
                                                   PLAN_VALIDATED
                                                        │
     ┌──────────────────────────────────────────────────┘
     ▼
  SECTION_DRAFTING ──► SECTION_GUARDRAIL ──► SECTION_REVIEWING
        ▲                     │ (échec)             │
        └─────────────────────┘                     ▼
        ▲                                  SECTION_CORRECTING
        └──────────────────────────────────────────┘  (max 3 boucles)
                                                    │ (validation humaine)
                                                    ▼
                                           SECTION_VALIDATED
                                                    │ (toutes sections)
                                                    ▼
                                            DOCUMENT_READY ─► EXPORTING ─► EXPORTED

  Tout nœud ──(dépassement de compteur, erreur non récupérable)──► ERROR_STATE
```

**Portes humaines obligatoires :** `PLAN_REVIEW → PLAN_VALIDATED` et `SECTION_REVIEWING → SECTION_VALIDATED`. Aucune transition automatique ne les franchit, quel que soit le score de qualité.

**`PLAN_VALIDATED` est un préalable dur.** Une demande de rédaction de section dans un état antérieur est refusée avec `409 Conflict` et l'état requis.

### 5.3 Compteurs et circuit breaker

| Boucle | Limite | Dépassement |
|---|---|---|
| Guardrail → agent | 3 | `ERROR_STATE`, intervention humaine |
| `SECTION_REVIEWING` ⇄ `SECTION_CORRECTING` | 3 | Pause, meilleure version proposée |
| Ingestion d'une source | 2 | Source en échec, pipeline poursuivi |

`ERROR_STATE` est terminal sans action humaine. Aucune reprise automatique.

### 5.4 Agents

| Agent | Entrée | Sortie validée | Outils |
|---|---|---|---|
| Orchestrateur | événement | transition | graphe, audit |
| Plan | sujet, sources approuvées | `PlanTree` | LLM |
| Bibliographique | requête | `SourceCandidate[]` | APIs (consentement) |
| RAG | requête, filtres | `ChunkHit[]` | `vec_chunk`, embeddings CPU |
| Rédacteur | nœud de plan, `ChunkHit[]` | `SectionDraft` (`.qmd`) | LLM, RAG |
| Code | intention, jeux de données | `CodeProposal` | sandbox |
| Relecteur | `SectionDraft` | `ReviewReport` | LLM, RAG |

Chaque agent possède un **prompt système constant** (ADR-003, condition du *prefix caching*) et ne reçoit que le strict nécessaire — jamais l'historique complet.

### 5.5 Garde-fous de véracité

L'agent rédacteur reçoit uniquement les `ChunkHit` retournés par le RAG et la liste des clés BibTeX autorisées. Le guardrail rejette toute sortie contenant :

- une clé de citation absente de la liste fournie ;
- un chiffre, un pourcentage ou une statistique dans un passage marqué « sourcé » sans identifiant de chunk associé ;
- un DOI ou une URL non présent dans `source_document`.

Ces trois contrôles sont **syntaxiques**, donc fiables — ils ne dépendent pas du jugement d'un LLM.

---

## 6. Couche LLM

### 6.1 Modèle et persistance `[RÉVISÉ — ADR-014, ADR-015]`

**Moteur : LM Studio**, via son API native `/api/v0` — seule à publier les statistiques de génération et l'état de chargement des modèles. L'API OpenAI `/v1` du même serveur ne les expose pas. Ollama reste implémenté derrière l'interface `LLMBackend` et sélectionnable par `llm_backend`.

**Modèle unique `google/gemma-4-31b` (Q8_0, 33,8 Go).** Il ne tient pas en VRAM : `llama.cpp` répartit les couches entre GPU et CPU, et le reste réside en RAM. Ce déversement est un **mode nominal**, décidé par ADR-015, non une dégradation. L'hypothèse d'ADR-003 selon laquelle un modèle de 30B provoquerait un OOM sur 10 Go supposait qu'il devait tenir entièrement en VRAM ; cette hypothèse est fausse.

**Persistance : `ttl` long, transmis à chaque requête.** LM Studio n'accepte pas de durée infinie ; on demande une durée que le service ne dépassera pas en usage (24 h par défaut). Le paramètre se réarme à chaque appel : une seule requête sans `ttl` rendrait le modèle éligible au déchargement, exactement comme un `keep_alive=0` sous Ollama.

Aucun `load`/`unload` par agent. La spécialisation passe par le prompt système.

### 6.2 Critère de persistance `[RÉVISÉ — D-04, puis ADR-014]`

La vérification ne porte pas sur le cache KV, non observable par aucune des deux API. **Elle dépend du moteur, parce que les moteurs ne publient pas la même chose.**

| Moteur | Observable | Seuil |
|---|---|---|
| LM Studio | `state: loaded` sur `/api/v0/models`, relevé avant et après une série de requêtes | le modèle est résident, ou il ne l'est pas |
| Ollama | `load_duration` de la réponse | `< 50 ms` sur toute requête postérieure à la première |

Sous LM Studio, la résidence est donc **observée directement** au lieu d'être inférée d'une durée — un critère plus fort, pas un repli.

**Une métrique qu'un moteur ne publie pas vaut `None`, jamais `0`.** Un `load_duration` à zéro se lirait comme « poids restés résidents », c'est-à-dire comme la preuve même de ce que la vérification cherche à établir. Et une métrique *remplacée* par un autre observable n'est pas une métrique *manquante* : `check_llm_latency.py` la signale comme information, sans dégrader son verdict.

Le gain réel de cache passe par la **stabilité octet pour octet du prompt système** de chaque agent, contrôlée par un test dédié. Sous Ollama s'y ajoute la stabilité de `num_ctx` : en changer force un rechargement complet des poids.

### 6.3 Arbitrage code `[EXPLICITÉ — D-08]`

Le modèle généraliste est moins performant en génération de code qu'un modèle spécialisé (`qwen/qwen3-coder-next` sous LM Studio, `qwen2.5-coder-7b` sous Ollama). Arbitrage assumé : latence contre qualité de code. Option `code_model_enabled` (défaut `false`) : si activée **et** VRAM ≥ 12 Go, charge un second résident réservé à l'agent code. Sur 10 Go, l'option est refusée au démarrage avec un message explicite.

### 6.4 Embeddings `[RÉVISÉ — D-05]`

Hors du serveur d'inférence, quel qu'il soit. `text-embedding-nomic-embed-text-v1.5` est installé dans LM Studio ; l'y router le chargerait sur GPU et évincerait le modèle principal pendant l'ingestion. ADR-013 tient. `fastembed` (ONNX Runtime, CPU) ou `sentence-transformers` avec `device="cpu"`. Modèle `nomic-embed-text-v1.5`, dimension 768.

**Préfixes obligatoires :** `search_document: ` à l'indexation, `search_query: ` à la requête. Leur omission dégrade nettement le rappel et constitue un défaut bloquant.

Batch par défaut 32. Parallélisme borné aux cœurs physiques moins un. Cible de débit : 500 chunks de 512 tokens en moins de 180 s sur un CPU 8 cœurs.

---

## 7. RAG

### 7.1 Découpage

Découpage sémantique par section de document, repli sur fenêtre glissante de 512 tokens avec 64 tokens de recouvrement. Conservation systématique de `page_start` et `page_end` : sans pagination, une citation n'est pas vérifiable dans le PDF d'origine.

### 7.2 Recherche

```python
async def search_similar_chunks(
    conn, query_embedding, k=5, project_id=None,
    year_min=None, exclude_preprints=False,
) -> list[ChunkHit]
```

`sqlite-vec` applique le KNN **avant** toute jointure. Lorsque des filtres relationnels sont fournis, `k` est élargi en interne (facteur 4, plafond 200) puis le résultat est tronqué à `k` après filtrage. Ce comportement est documenté dans le code.

Chaque `ChunkHit` porte : `chunk_id`, `source_id`, `text`, `page_start`, `page_end`, `distance`, `source_title`, `source_year`, `source_doi`, `is_preprint`.

### 7.3 Politique de sources

Sources acceptées : articles à comité de lecture, ouvrages, thèses, rapports institutionnels, normes, prépublications **signalées comme telles**. Le marqueur `is_preprint` est propagé du résultat de recherche jusqu'à la bibliographie exportée, sans exception.

---

## 8. Exécution de code `[RÉVISÉ — D-03]`

### 8.1 Sélection du niveau

```python
SandboxFactory(origin, mode, platform) -> SandboxExecutor
```

| `origin` | `mode` | Exécuteur | Isolation réseau | Isolation disque |
|---|---|---|---|---|
| `agent` | — | **WasmSandbox** | Garantie, Windows et Linux | Garantie (FS virtuel) |
| `user` | `wasm` | **WasmSandbox** | Garantie | Garantie |
| `user` | `native` | **NativeSandbox** | **Non garantie sous Windows** | Restreinte au répertoire projet |

Le niveau natif exige un consentement explicite journalisé à chaque lancement, avec avertissement affiché.

### 8.2 Niveau 1 — WebAssembly

Runtime Pyodide. Paquets autorisés : `numpy`, `pandas`, `scipy`, `matplotlib`, `sympy`, `scikit-learn`, `biopython` si disponible. Système de fichiers virtuel ; seuls les jeux de données du projet sont montés, en lecture seule, hors répertoire de sortie.

Un paquet indisponible produit un message qui **propose explicitement le passage au niveau 2**, jamais un échec sec.

### 8.3 Niveau 2 — Natif contraint

**Windows :** Job Objects (`ProcessMemoryLimit`, `PerProcessUserTimeLimit`, `ActiveProcessLimit`, `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`).
**Linux :** `setrlimit(RLIMIT_AS, RLIMIT_CPU, RLIMIT_NPROC, RLIMIT_FSIZE)` + `unshare -n` si disponible.

Limites par défaut : mémoire 2 Go, CPU 60 s, temps mural 120 s, taille de fichier 512 Mo, 8 processus.

### 8.4 Trace

Toute exécution écrit dans `code_execution` : origine, niveau de sandbox, code, `stdout`, `stderr`, code de sortie, durée. Les figures produites sont rattachées à la section qui les référence.

---

## 9. Rédaction et export `[RÉVISÉ — D-02]`

### 9.1 Format canonique

**Quarto Markdown (`.qmd`)**, stocké dans `draft_section.content_qmd`. Renvois croisés en syntaxe Quarto : `@fig-`, `@tbl-`, `@eq-`, `@sec-`. Aucune syntaxe MyST dans le contenu canonique, contrôlée par test.

### 9.2 Chaîne de compilation

`_quarto.yml` généré depuis les métadonnées du projet. Binaire Quarto détecté au démarrage, version minimale vérifiée. TinyTeX pour la chaîne LaTeX. Sorties : PDF, DOCX, HTML. Bibliographie rendue par `citeproc` dans les trois formats.

### 9.3 Bibliographie

Le `.bib` est un **artefact généré à chaque export** (ADR-007) : seules les citations vérifiées des sections effectivement incluses y figurent, clés normalisées `auteur_année_motclé`, sans doublon. Une citation référençant une source absente ou non approuvée **bloque l'export**, avec un message pointant la section et le passage fautifs.

### 9.4 Éléments documentaires

Table des matières, liste des figures, liste des tableaux, glossaire, index, annexes. Numérotation par chapitre.

### 9.5 Annexe de déclaration d'usage de l'IA `[NOUVEAU — D-11]`

Générée depuis `audit_log` : modèles utilisés et versions ; pour chaque section, part générée, part réécrite par l'auteur, date de validation humaine ; scripts exécutés et figures produites automatiquement.

Désactivable par l'utilisateur ; la désactivation est journalisée.

---

## 10. Bibliographie et réseau

Fournisseurs : OpenAlex, Crossref, PubMed/E-utilities, Semantic Scholar, arXiv. `httpx.AsyncClient` avec limite de débit et *backoff* exponentiel. En-tête `mailto` pour les pools polis. Aucune clé d'API obligatoire au MVP. Cache local par requête normalisée.

Déduplication : DOI normalisé, puis titre + année après normalisation typographique.

**Aucun texte du mémoire ne transite jamais par ces APIs** — seules les requêtes de recherche sortent, sous consentement `biblio_search`.

---

## 11. Audit et sécurité `[RÉVISÉ — D-07]`

### 11.1 Journal

Table `audit_log`, append-only par convention applicative : une seule fonction d'insertion, aucun `UPDATE` ni `DELETE` dans le code, contrôlé par analyse statique. Chaînage `hash = sha256(canonical(entry) || prev_hash)`.

**Vocabulaire imposé** dans le code, l'interface et la documentation : « journal à détection d'altération », « tamper-evident ». Les termes « immuable » et « infalsifiable » sont proscrits et contrôlés par un test lexical. La chaîne détecte une altération ; elle ne l'empêche pas, l'utilisateur étant propriétaire du fichier.

### 11.2 Événements obligatoires

Transitions du graphe · appels LLM (modèle, agent, section, tokens) · validations humaines · exécutions de code avec niveau de sandbox · consentements accordés ou refusés · exports · déclenchements de guardrail ou de circuit breaker.

### 11.3 Consentements

| Périmètre | Ce qui sort | Défaut |
|---|---|---|
| `biblio_search` | Requêtes de recherche | Refusé |
| `model_download` | Rien du projet | Refusé |
| `plagiarism_check` | Texte du mémoire, à l'étendue choisie par l'utilisateur | Refusé |
| `remote_llm` | Prompts et extraits de sources | Refusé |
| `native_execution` | Rien ; exécution non isolée locale | Refusé |
| `external_compute` | Rien ; exécution système non isolée | Refusé |

Par périmètre et par projet, révocables. Liste blanche de domaines codée en dur. Refuser un consentement dégrade proprement : recherche bibliographique refusée → import manuel de PDF proposé.

### 11.4 Vérification anti-plagiat — étendue choisie par l'utilisateur

Le périmètre `plagiarism_check` est le seul par lequel du texte non publié du mémoire quitte le poste. L'outil ne restreint pas cette sortie au-delà du consentement : **l'auteur choisit l'étendue transmise**, parmi trois valeurs.

| Étendue | Ce qui sort | Usage |
|---|---|---|
| `passages` | Uniquement les passages signalés par la vérification locale, avec 500 mots de contexte au plus | Défaut proposé ; minimise l'exposition |
| `section` | Le contenu complet d'une section | Vérification avant validation d'un chapitre |
| `document` | L'ensemble des sections validées | Contrôle final avant dépôt |

Règles :

- l'étendue est choisie **à chaque requête**, jamais mémorisée comme préférence implicite ;
- avant l'envoi, l'interface affiche **le texte exact** qui sera transmis et son volume en mots ; l'utilisateur confirme sur cette base ;
- la vérification locale (§11.5) s'exécute d'abord et informe le choix, mais **ne le conditionne pas** : une vérification distante peut être demandée même sans signalement local ;
- l'étendue retenue et le volume transmis sont journalisés ; le texte ne l'est jamais.

### 11.5 Vérification anti-plagiat locale

Comparaison hors ligne, sans aucune sortie réseau, contre les chunks ingérés du projet et les autres sections du même document. Activée par défaut, elle couvre le risque le plus fréquent — la paraphrase trop proche d'une source importée par l'auteur — et reste disponible quand le consentement distant est refusé.

---

## 12. Performance et budgets

### 12.1 Budget mémoire `[RÉVISÉ — ADR-015]`

**Le plafond de VRAM ne gouverne plus.** Le modèle retenu sature délibérément la carte et déverse le reste en RAM. Un budget de marge n'a plus d'objet ; le critère devient l'absence d'éviction et d'OOM sur un cycle complet.

Relevé du 7 septembre 2026, `google/gemma-4-31b` Q8_0 résident :

| Poste | Mesure | Statut |
|---|---|---|
| VRAM occupée | 9 713 / 10 240 Mo | saturation voulue |
| RAM occupée | 59,7 / 63,9 Go | **ressource critique** |
| RAM disponible | 4,2 Go | à surveiller |
| Embeddings | **0 Go de VRAM** (CPU, ADR-013) | inchangé |

**La RAM est désormais la ressource contrainte, pas la VRAM.** ADR-013 place les embeddings sur CPU, donc en RAM, et §7.1 prévoit l'ingestion de 50 PDF. Les deux charges se disputent les mêmes 4,2 Go restants. Le budget d'ingestion de §12.2 a été établi sans modèle de 34 Go en mémoire : il doit être revérifié avant l'implémentation de l'ingestion. C'est, à la date de cette révision, le risque le plus concret du dossier.

Porte de sortie si la RAM devient bloquante : `google/gemma-4-31b-qat` (Q4_0), installé, environ deux fois plus compact.

### 12.2 Latences cibles `[RÉVISÉ — ADR-015]`

**Les budgets de génération sont levés.** Le temps de production d'un document n'est pas une contrainte du produit : un mémoire se rédige sur des semaines, et la qualité du modèle prime. Ce qui subsiste n'est plus un budget de confort mais un **détecteur de panne**.

| Opération | Cible | Nature |
|---|---|---|
| Modèle résident avant et après une série | vrai | critère de persistance (§6.2) |
| `load_duration` après première requête | < 50 ms *si le moteur le publie* | détecteur de rechargement |
| Temps au premier token | < 300 s | plafond contre un état pathologique, **ni confort ni détecteur** |
| Recherche KNN, 50 000 chunks | < 200 ms | budget réel |
| Ingestion, 500 chunks de 512 tokens | < 180 s (CPU 8 cœurs) | budget réel, **à revérifier** (§12.1) |
| Export PDF, 150 pages | < 90 s | budget réel |

Le temps au premier token **ne détecte pas un rechargement de poids** : il est dominé par le traitement du prompt, qui croît avec le contexte — 3,7 s sur une invite courte, 13,5 s sur 700 tokens, de l'ordre de 160 s attendues à 8 192 tokens. Un seuil serré se déclencherait en régime sain, un seuil large recouvrirait le coût d'un rechargement : les deux grandeurs ne se séparent pas. Le détecteur de rechargement est la **résidence du modèle** (§6.2) ; ce seuil n'est qu'un plafond.

**Ordre de grandeur à connaître.** À 2,3 tokens/s — débit mesuré sur une génération complète — une section de 1 500 mots demande environ 15 minutes, un plan complet environ 25 minutes, et un mémoire de quarante sections de l'ordre de 10 heures de génération cumulée. Conséquence d'interface, non de performance : l'attente synchrone est exclue, le suivi de progression et la reprise après arrêt deviennent structurants (§2, pont SSE).

> Le débit apparent de 0,6 token/s que rapporte `check_llm_latency.py` n'est **pas extrapolable** : sur un budget de 24 tokens, le temps d'amorce du prompt écrase le débit. Les deux mesures sont exactes ; seule leur confusion est fautive.

### 12.3 Vérification continue `[NOUVEAU — D-10]`

`scripts/check_vram_budget.py` échantillonne la mémoire pendant un cycle complet — chargement du modèle, ingestion de 50 PDF, rédaction d'une section, export.

**Le critère a changé avec ADR-015.** Mesurer une marge de VRAM n'a plus de sens quand la saturation est voulue. Le script doit constater :

- que le modèle reste **résident** du début à la fin du cycle, sans éviction ;
- qu'aucun OOM ne survient, côté GPU comme côté RAM ;
- la **RAM disponible au plus bas** du cycle, qui est la grandeur réellement contrainte.

`vram_offload_expected` signale que le déversement est un choix : le script ne doit pas le rapporter comme un dépassement. Le test s'ignore proprement en l'absence de GPU NVIDIA.

---

## 13. Développement

### 13.1 Dépendances `[RÉVISÉ — D-09]`

`pyproject.toml` est la **source unique**. `requirements.txt` est un artefact verrouillé, généré par `uv pip compile` dans `check_all`, jamais édité à la main.

Backend : `fastapi`, `uvicorn`, `pydantic`, `pydantic-settings`, `aiosqlite`, `sqlite-vec`, `httpx`, `pymupdf`, `fastembed`, `langgraph`, `pywin32` (Windows uniquement).

Développement : `pytest`, `pytest-asyncio`, `pytest-cov`, `ruff`. `pytest-asyncio` n'est pas facultatif : la base de code est intégralement asynchrone.

**Aucun paquet client de moteur d'inférence.** Ni `ollama`, ni `lmstudio`. Les deux backends sont écrits directement sur `httpx` : leurs API sont de simples requêtes HTTP locales, et une dépendance de plus n'apporterait qu'un couplage à une bibliothèque tierce sur le chemin le plus critique du produit. `ollama` figurait dans la V0.3 sans jamais être importé.

### 13.2 Commandes de vérification

```
pytest -q
ruff check backend/ && ruff format --check backend/
python scripts/check_sqlite_wal.py
python scripts/check_sqlite_vec.py
python scripts/check_llm_latency.py
python scripts/check_vram_budget.py
python scripts/check_sandbox_windows.py | check_sandbox_linux.py
python scripts/check_quarto_export.py
python scripts/check_no_cloud_calls.py
cd frontend && ng test --watch=false && ng lint
```

### 13.3 Definition of Done

Scénarios Gherkin automatisés et passants · `ruff` sans avertissement · couverture ≥ 80 % sur les modules touchés · `check_no_cloud_calls` passant · aucun fichier hors périmètre modifié · ADR référencé au commit lorsque la story applique une décision d'architecture · matrice de traçabilité mise à jour.

---

## 14. Risques résiduels

| Risque | Probabilité | Impact | Traitement |
|---|---|---|---|
| Binaire SQLite sans support d'extension | Moyenne | Bloquant | Détection au démarrage, message actionnable, `pysqlite3-binary` en remédiation |
| Paquet scientifique indisponible en Wasm | Élevée | Modéré | Bascule proposée vers le niveau 2 |
| Qualité de génération de code du modèle généraliste | Élevée | Modéré | Option second modèle si VRAM ≥ 12 Go |
| Quarto absent ou version incompatible | Moyenne | Bloquant à l'export | Vérification au démarrage, TinyTeX embarqué |
| Boucle de correction non convergente | Élevée | Faible | Circuit breaker, pause, meilleure version proposée |
| Hallucination de citation | Moyenne | **Critique** | Guardrails syntaxiques §5.5, export bloqué |
| Volumétrie > 500 000 chunks | Faible | Modéré | Hors périmètre thèse ; réévaluer `sqlite-vec` si atteint |
