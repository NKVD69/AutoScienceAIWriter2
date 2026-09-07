# Backlog Agile V0.3 — Science AI Writer IDE

**Statut :** proposé — remplace le Backlog V0.2
**Base :** Spécifications techniques V0.2 + corrections D-01 à D-11 de l'analyse critique
**Public :** IA de codage (Cursor, Qwen Coder, Claude Code, Copilot Workspace)

---

## Conventions

- **Priorité** : `P0` bloquant MVP · `P1` V1 · `P2` V1.5+
- **Format** : Gherkin (Given/When/Then) + Notes techniques impératives + Interdictions
- **Dépendances** : explicites. Une story ne démarre pas si ses dépendances ne sont pas `Done`.
- **Toute story modifie uniquement les fichiers listés.** Aucun fichier hors périmètre.

### Ce qui change par rapport au V0.2

| Changement | Motif |
|---|---|
| US-002 : schéma vectoriel refondu (`chunk` + `vec_chunk` liés par rowid) | D-01 — pas de FK sur table virtuelle |
| US-004 / US-401 : sandbox à deux niveaux (Wasm + natif) | D-03 — Job Objects n'isolent pas le réseau |
| US-003 : critère `load_duration` au lieu de « cache KV réutilisé » | D-04 |
| US-005 (nouvelle) : embeddings hors Ollama, CPU/ONNX | D-05 |
| US-PLAN-001 promue P0, insérée avant US-301 | D-06 — le MVP ne rédigeait rien |
| US-BIBLIO-001 promue P0 | D-06 |
| Format canonique : Quarto `.qmd` (MyST retiré du chemin critique) | D-02 |
| US-701 : vocabulaire « tamper-evident » | D-07 |
| US-006 (nouvelle) : test de budget VRAM global | D-10 |
| US-EXPORT-003 (nouvelle) : annexe de déclaration d'usage IA | D-11 |

---

# Epic 0 — Fondations techniques `[P0]`

## US-001 — Initialisation backend FastAPI + aiosqlite WAL
`P0` · Dépendances : aucune · **Inchangée par rapport au V0.2** (voir Prompt Pack V0.2).

---

## US-002 — Schéma SQLite unique avec sqlite-vec `[RÉVISÉE — D-01]`

**Priorité :** P0 · **Dépendances :** US-001

**Story :** En tant que développeur, je veux un schéma unique où données relationnelles et vecteurs cohabitent dans un seul fichier `.sqlite`, avec une intégrité référentielle réellement appliquée, afin qu'un projet se sauvegarde par une simple copie de fichier.

```gherkin
Scenario: Chargement de l'extension sqlite-vec
  Given une connexion aiosqlite ouverte
  When l'extension vec0 est chargée
  Then SELECT vec_version() retourne une chaîne non vide
  And la table virtuelle vec_chunk est créée sans erreur

Scenario: Intégrité référentielle réellement appliquée
  Given PRAGMA foreign_keys=ON
  When un chunk est inséré avec un source_id inexistant
  Then l'insertion échoue avec FOREIGN KEY constraint failed

Scenario: Correspondance rowid entre chunk et vec_chunk
  Given un chunk inséré avec l'id 42
  When son embedding est inséré dans vec_chunk
  Then vec_chunk.rowid vaut 42
  And une jointure chunk JOIN vec_chunk ON chunk.id = vec_chunk.rowid retourne 1 ligne

Scenario: Suppression en cascade des vecteurs
  Given une source possédant 10 chunks vectorisés
  When la source est supprimée
  Then les 10 lignes de chunk sont supprimées par ON DELETE CASCADE
  And les 10 lignes correspondantes de vec_chunk sont supprimées par trigger
  And SELECT count(*) FROM vec_chunk retourne 0

Scenario: Recherche vectorielle avec provenance
  Given 100 chunks vectorisés issus de 3 sources
  When une recherche KNN k=5 est exécutée
  Then 5 résultats sont retournés
  And chaque résultat porte source_id, titre, page_start et distance

Scenario: Sauvegarde par copie de fichier unique
  Given un projet complet avec sources, chunks et embeddings
  When le fichier .sqlite est copié après un checkpoint WAL
  Then la copie contient l'intégralité des données relationnelles ET vectorielles
```

**Notes techniques impératives :**

- Charger l'extension via le paquet Python `sqlite-vec` : `sqlite_vec.load(conn)`. Vérifier au démarrage la disponibilité de `enable_load_extension` et **échouer explicitement** avec un message actionnable si le binaire SQLite de l'interpréteur a été compilé sans le support des extensions.
- Schéma vectoriel **obligatoire** :
  ```sql
  CREATE TABLE chunk (
    id         INTEGER PRIMARY KEY,
    source_id  INTEGER NOT NULL REFERENCES source_document(id) ON DELETE CASCADE,
    ordinal    INTEGER NOT NULL,
    text       TEXT NOT NULL,
    page_start INTEGER, page_end INTEGER,
    token_count INTEGER,
    UNIQUE(source_id, ordinal)
  );
  CREATE VIRTUAL TABLE vec_chunk USING vec0(embedding float[768]);
  CREATE TRIGGER chunk_ad AFTER DELETE ON chunk BEGIN
    DELETE FROM vec_chunk WHERE rowid = old.id;
  END;
  ```
- Invariant documenté en tête de `schema.sql` : `vec_chunk.rowid == chunk.id`.
- Tables relationnelles dans le **même fichier** : `project`, `source_document`, `chunk`, `plan`, `plan_node`, `draft_section`, `citation`, `code_execution`, `task`, `audit_log`, `consent`, `model_config`.
- Dimension d'embedding : `768` (nomic-embed-text). Stocker la valeur dans `model_config` pour interdire tout mélange de dimensions.

**Interdictions :** aucune contrainte `REFERENCES` déclarée sur `vec_chunk` (ignorée silencieusement par SQLite) · pas de ChromaDB · pas de second fichier de base.

**Fichiers :** `backend/app/db/schema.sql`, `backend/app/db/vector.py`, `backend/app/db/migrations/`, `backend/tests/tests_db/test_schema.py`, `backend/tests/tests_db/test_vector.py`, `scripts/check_sqlite_vec.py`

---

## US-003 — LLM Manager, modèle unique persistant `[RÉVISÉE — D-04, D-08]`

**Priorité :** P0 · **Dépendances :** aucune

**Story :** En tant que développeur, je veux un modèle généraliste unique résident en VRAM, afin d'éliminer la latence de swap entre agents.

```gherkin
Scenario: Chargement unique au démarrage
  Given le backend démarre
  When le LLMManager s'initialise
  Then qwen2.5-7b-instruct-q4_k_m est chargé avec keep_alive=-1
  And aucun unload par agent n'est configuré

Scenario: Les poids ne sont pas rechargés entre deux requêtes
  Given une première requête a été traitée
  When une seconde requête est envoyée depuis un autre agent
  Then load_duration de la seconde réponse est inférieur à 50 ms
  And le temps au premier token est inférieur à 2 s

Scenario: Préfixe système stable par agent
  Given deux appels consécutifs du même agent
  When les prompts système sont comparés
  Then ils sont strictement identiques octet pour octet

Scenario: Modèle code optionnel
  Given la VRAM disponible est inférieure à 12 Go
  When l'agent code demande une génération
  Then le modèle généraliste est utilisé
  And aucun second modèle n'est chargé
```

**Notes techniques impératives :**

- `keep_alive=-1` sur le modèle principal uniquement.
- Prompts système figés par agent, définis comme constantes — condition du *prefix caching*.
- Option `code_model_enabled` (défaut `false`) : si activée et VRAM ≥ 12 Go, charge `qwen2.5-coder-7b` en second résident pour l'agent code seul. Arbitrage documenté dans ADR-003.
- Backend alternatif `llama-cpp-python` prévu derrière l'interface `LLMBackend`, non implémenté au MVP.

**Interdictions :** pas de load/unload par agent · ne pas affirmer dans les tests que le cache KV est réutilisé (non observable via Ollama).

**Fichiers :** `backend/app/llm/manager.py`, `backend/app/llm/backends/ollama.py`, `backend/app/llm/prompts/`, `scripts/check_llm_latency.py`

---

## US-004 — Sandbox à deux niveaux `[RÉVISÉE — D-03]`

**Priorité :** P0 · **Dépendances :** US-001

**Story :** En tant que développeur, je veux deux niveaux d'exécution isolée afin que le code produit par le LLM soit contraint par le runtime et non par l'OS, tout en conservant la possibilité d'exécuter du calcul lourd de l'utilisateur.

```gherkin
Scenario: Code généré par le LLM en niveau 1
  Given une exécution dont l'origine est "agent"
  When le SandboxFactory sélectionne un exécuteur
  Then l'exécuteur WasmSandbox est retourné sur Windows comme sur Linux

Scenario: Isolation réseau garantie en niveau 1
  Given un script niveau 1 tentant urllib.request.urlopen("http://example.com")
  When il est exécuté
  Then l'exécution échoue
  And le résultat est identique sur Windows et sur Linux

Scenario: Isolation disque garantie en niveau 1
  Given un script niveau 1 tentant open("C:/Windows/System32/drivers/etc/hosts")
  When il est exécuté
  Then l'accès échoue
  And seuls les fichiers montés explicitement dans le FS virtuel sont lisibles

Scenario: Calcul lourd en niveau 2 avec avertissement
  Given une exécution dont l'origine est "utilisateur" et le mode est "natif"
  When l'exécution est demandée sur Windows
  Then un avertissement explicite indique que l'isolation réseau n'est pas garantie
  And un consentement est requis et journalisé avant lancement

Scenario: Limites de ressources en niveau 2
  Given un script niveau 2 allouant 8 Go de mémoire
  When il est exécuté avec une limite de 2 Go
  Then le processus est terminé par le Job Object sur Windows
  And par setrlimit sur Linux
  And le code de sortie et la cause sont journalisés

Scenario: Timeout dans les deux niveaux
  Given un script contenant une boucle infinie
  When il est exécuté avec un timeout de 5 s
  Then il est interrompu entre 5 et 6 s
```

**Notes techniques impératives :**

- **Niveau 1 (défaut, code agent)** : Pyodide/WebAssembly. Paquets autorisés : `numpy`, `pandas`, `scipy`, `matplotlib`, `sympy`, `scikit-learn`, `biopython` si disponible. Système de fichiers virtuel, montage explicite des seuls datasets du projet, en lecture seule sauf répertoire de sortie.
- **Niveau 2 (opt-in, code utilisateur)** : sous-processus natif. Windows → `win32job` (`JOBOBJECT_EXTENDED_LIMIT_INFORMATION` : `ProcessMemoryLimit`, `PerProcessUserTimeLimit`, `ActiveProcessLimit`, `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`). Linux → `setrlimit(RLIMIT_AS, RLIMIT_CPU, RLIMIT_NPROC, RLIMIT_FSIZE)` + `unshare -n` si disponible.
- Interface commune `SandboxExecutor.run(code, files, limits) -> ExecutionResult` ; sélection par `SandboxFactory(origin, mode, platform)`.
- **Documenter explicitement** dans l'UI et dans le rapport d'audit que le niveau 2 sous Windows n'isole pas le réseau.

**Interdictions :** ne pas utiliser `setrlimit` sur Windows · ne pas prétendre bloquer le réseau en niveau 2 sous Windows · pas de Docker au MVP.

**Fichiers :** `backend/app/sandbox/base.py`, `sandbox/wasm.py`, `sandbox/native_windows.py`, `sandbox/native_linux.py`, `sandbox/factory.py`, `scripts/check_sandbox_windows.py`, `scripts/check_sandbox_linux.py`

---

## US-005 — Service d'embeddings CPU hors Ollama `[NOUVELLE — D-05]`

**Priorité :** P0 · **Dépendances :** US-002

**Story :** En tant que développeur, je veux générer les embeddings hors d'Ollama, sur CPU, afin que l'ingestion RAG ne dispute jamais la VRAM au modèle de rédaction.

```gherkin
Scenario: Les embeddings n'occupent pas la VRAM
  Given le modèle principal est résident en VRAM
  When 500 chunks sont vectorisés
  Then la VRAM occupée ne varie pas de plus de 200 Mo
  And ollama ps ne liste aucun modèle d'embedding

Scenario: Préfixes nomic obligatoires
  Given un chunk de document à indexer
  When son embedding est calculé
  Then le texte est préfixé par "search_document: "
  And une requête utilisateur est préfixée par "search_query: "

Scenario: Dimension verrouillée
  Given model_config déclare dim=768
  When un embedding de dimension différente est proposé
  Then l'insertion est rejetée avant écriture

Scenario: Débit d'ingestion
  Given 500 chunks de 512 tokens
  When ils sont vectorisés par lots de 32 sur CPU
  Then l'opération se termine en moins de 180 s sur un CPU 8 cœurs
```

**Notes techniques impératives :** `fastembed` (ONNX Runtime CPU) ou `sentence-transformers` avec `device="cpu"` · modèle `nomic-embed-text-v1.5` · batching configurable · pas d'appel réseau après le téléchargement initial du modèle, qui doit être explicitement consenti et journalisé.

**Interdictions :** ne pas router les embeddings via l'API Ollama · ne pas omettre les préfixes.

**Fichiers :** `backend/app/rag/embeddings.py`, `backend/tests/tests_rag/test_embeddings.py`

---

## US-006 — Vérification du budget VRAM global `[NOUVELLE — D-10]`

**Priorité :** P0 · **Dépendances :** US-003, US-005

```gherkin
Scenario: Budget respecté sur un cycle complet
  Given un poste équipé de 10 Go de VRAM
  When un cycle complet est exécuté : chargement modèle, ingestion 50 PDF, rédaction d'une section, export
  Then la VRAM maximale observée reste inférieure à 9,0 Go
  And aucun événement CUDA out of memory n'est journalisé
```

**Notes techniques :** échantillonnage `nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits` toutes les 500 ms dans un thread dédié · marge de sécurité de 1 Go sous la limite matérielle · le test s'ignore proprement (`skip`) en l'absence de GPU NVIDIA.

**Fichiers :** `scripts/check_vram_budget.py`, `backend/tests/tests_integration/test_vram_budget.py`

---

# Epic 1 — Projet, sources et bibliographie `[P0/P1]`

## US-101 — CRUD projets avec aiosqlite
`P0` · Dépendances : US-002 · **Inchangée.**

## US-102 — Import de sources et ingestion RAG
`P0` · Dépendances : US-002, US-005 · **Révisée** : consomme désormais le service d'embeddings de US-005 et le schéma `chunk`/`vec_chunk` de US-002.

## US-BIBLIO-001 — Recherche bibliographique multi-API `[GAP-01, promue P0]`

**Priorité :** P0 · **Dépendances :** US-101

**Story :** En tant que chercheur, je veux interroger les bases académiques depuis l'outil, afin de constituer ma bibliographie sans quitter l'IDE.

```gherkin
Scenario: Recherche multi-sources avec consentement
  Given le mode local strict est actif
  When une recherche bibliographique est lancée
  Then un consentement de sortie réseau est demandé et journalisé
  And seules les APIs de la liste blanche sont contactées

Scenario: Agrégation et déduplication
  Given une requête retournant 40 résultats sur OpenAlex, PubMed et Crossref
  When les résultats sont agrégés
  Then les doublons sont fusionnés sur DOI normalisé puis sur titre + année
  And chaque résultat porte titre, auteurs, année, DOI, revue, type, langue

Scenario: Signalement des prépublications
  Given un résultat provenant d'arXiv, bioRxiv ou medRxiv
  When il est affiché
  Then il porte le marqueur PREPRINT non révisé par les pairs
  And ce marqueur est conservé jusque dans la bibliographie exportée

Scenario: Validation humaine obligatoire
  Given une liste de résultats
  When aucune source n'a été approuvée par l'utilisateur
  Then l'ingestion RAG est refusée
```

**Notes techniques :** APIs autorisées — OpenAlex, Crossref, PubMed/E-utilities, Semantic Scholar, arXiv · `httpx.AsyncClient` avec limite de débit et *backoff* exponentiel · en-tête `mailto` pour le pool poli Crossref/OpenAlex · aucune clé d'API obligatoire au MVP · cache local des réponses par requête normalisée.

**Fichiers :** `backend/app/biblio/providers/`, `backend/app/biblio/dedupe.py`, `backend/app/api/v1/biblio.py`

## US-IMPORT-001 — Import DOI / BibTeX / RIS `[GAP-03]`
`P1` · Dépendances : US-BIBLIO-001

## US-ZOTERO-001 — Synchronisation Zotero locale `[GAP-04]`
`P2` · Dépendances : US-IMPORT-001 · lecture de la base `zotero.sqlite` locale en lecture seule, ou API locale du connecteur.

## US-DATA-001 — Dépôt de jeux de données `[GAP-06]`
`P1` · CSV, Excel, FASTA, VCF · montage en lecture seule dans le FS virtuel Wasm (US-004).

---

# Epic 2 — Plan et workflow LangGraph `[P0]`

## US-PLAN-001 — Génération, édition et validation du plan `[GAP-02, promue P0]`

**Priorité :** P0 · **Dépendances :** US-003, US-201 · **Précède obligatoirement US-301**

**Story :** En tant que doctorant, je veux obtenir un plan structuré à partir de mon sujet, puis le corriger et le valider, afin que la rédaction s'appuie sur une structure que j'ai approuvée.

```gherkin
Scenario: Génération d'un plan depuis un sujet
  Given un projet dont le sujet et la discipline sont renseignés
  When la génération de plan est lancée
  Then une problématique est proposée
  And un arbre de plan sur 3 niveaux au minimum est produit
  And chaque nœud porte un titre, un objectif et une longueur cible en mots

Scenario: Problématique fournie par l'utilisateur
  Given l'utilisateur a saisi sa propre problématique
  When le plan est généré
  Then la problématique de l'utilisateur est conservée telle quelle
  And le plan est construit pour y répondre

Scenario: Sortie structurée validée
  Given la réponse du LLM
  When elle est désérialisée
  Then elle est conforme au modèle Pydantic PlanTree
  And en cas d'échec, le guardrail relance l'agent avec l'erreur de validation

Scenario: Édition et réordonnancement
  Given un plan généré
  When l'utilisateur déplace, renomme, ajoute ou supprime un nœud
  Then l'arbre est persisté dans plan_node avec son ordre
  And la version précédente reste consultable

Scenario: La validation est un verrou
  Given un plan à l'état DRAFT
  When la rédaction d'une section est demandée
  Then l'opération est refusée avec l'état requis PLAN_VALIDATED
```

**Notes techniques :** modèle `PlanTree` / `PlanNode` Pydantic · état LangGraph `PLAN_DRAFTING → PLAN_REVIEW → PLAN_VALIDATED` · l'agent plan n'invente aucune référence à ce stade · la longueur cible par nœud alimente le budget de rédaction.

**Fichiers :** `backend/app/agents/plan_agent.py`, `backend/app/models/plan.py`, `backend/app/api/v1/plan.py`, `frontend/src/app/features/plan/`

## US-201 — Machine à états LangGraph + guardrails Pydantic
`P0` · **Inchangée.**

## US-202 — Circuit breaker dans les boucles de correction
`P0` · **Inchangée.**

## US-WORKFLOW-001 — Mode pipeline automatique contrôlé `[GAP-15]`
`P1` · Dépendances : US-201, US-202, US-PLAN-001 · exécution enchaînée avec points d'arrêt configurables et arrêt inconditionnel sur `ERROR_STATE`.

---

# Epic 3 — Rédaction, relecture, citations `[P1]`

## US-301 — Génération de section avec RAG ciblé
`P1` · **Dépendances révisées :** US-PLAN-001, US-102, US-201

## US-302 — Relecture avec score de qualité
`P1` · **Inchangée.**

## US-RAG-002 — Filtres avancés du RAG `[GAP-16]`
`P1` · filtrage par année, langue, type de publication, statut prépublication, score de pertinence minimal · les filtres s'appliquent en SQL sur `chunk`/`source_document` **avant** le KNN, pas après.

---

# Epic 4 — Exécution scientifique `[P1]`

## US-401 — Exécution de script dans la sandbox
`P1` · **Dépendances :** US-004 · critères réseau/disque repris de US-004, différenciés par niveau.

## US-JUP-001 — Support des notebooks Jupyter `[GAP-07]` — `P2`
## US-CALC-001 — Codes externes OpenFOAM / Serpent via WSL2 `[GAP-08]` — `P2` · niveau 2 exclusivement, consentement explicite.

---

# Epic 5 — Export Quarto et bibliographie `[P1]`

## US-501 — Compilation BibTeX dynamique
`P1` · **Inchangée** — le `.bib` est regénéré à partir des seules citations validées présentes dans les sections incluses.

## US-502 — Export multi-format via Quarto `[RÉVISÉE — D-02]`

**Priorité :** P1 · **Dépendances :** US-501

```gherkin
Scenario: Format canonique unique
  Given une section rédigée
  When elle est persistée
  Then elle est stockée en Quarto Markdown (.qmd)
  And aucune syntaxe MyST n'est présente dans le contenu canonique

Scenario: Renvois croisés numérotés
  Given un document contenant 12 figures et 5 tableaux répartis sur 8 chapitres
  When il est compilé en PDF
  Then chaque renvoi @fig- et @tbl- est résolu
  And aucune référence non résolue n'apparaît dans le journal de compilation

Scenario: Export triple
  Given un document validé
  When l'export est lancé
  Then PDF, DOCX et HTML sont produits
  And la bibliographie est rendue par citeproc dans les trois formats
```

**Notes techniques :** binaire Quarto détecté au démarrage, version minimale vérifiée · TinyTeX pour la chaîne LaTeX · `_quarto.yml` généré depuis le projet · MyST possible en export secondaire, jamais en pivot.

## US-EXPORT-001 — Table des matières, listes de figures et tableaux, glossaire, index `[GAP-13]` — `P1`
## US-EXPORT-002 — Modèle académique paramétrable `[GAP-14]` — `P1`

## US-EXPORT-003 — Annexe de déclaration d'usage de l'IA `[NOUVELLE — D-11]`

**Priorité :** P1 · **Dépendances :** US-701, US-502

```gherkin
Scenario: Génération automatique de la déclaration
  Given un document exporté
  When l'annexe de déclaration est générée
  Then elle liste les modèles utilisés avec leur version
  And pour chaque section : part générée, part réécrite par l'auteur, date de validation humaine
  And elle liste les scripts exécutés et les figures produites automatiquement

Scenario: Déclaration désactivable mais tracée
  Given l'utilisateur désactive l'annexe
  When l'export est lancé
  Then l'annexe est absente du document
  And la désactivation est journalisée dans audit_log
```

---

# Epic 6 — Intégrité `[P1]`

## US-601 — Anti-plagiat, local puis distant à l'étendue choisie `[RÉVISÉE — 0.3.1]`

**Priorité :** P1 · **Dépendances :** US-301, US-701, US-801

**Story :** En tant que doctorant, je veux vérifier la similarité de mon texte avec la littérature, localement d'abord, puis auprès d'un service que je configure et à l'étendue que je décide.

```gherkin
Scenario: Vérification locale sans réseau
  Given un projet avec des sources ingérées
  When une vérification locale est lancée sur une section
  Then aucune requête réseau n'est émise
  And les passages proches d'un chunk ingéré ou d'une autre section sont signalés

Scenario: L'étendue est choisie par l'utilisateur
  Given le consentement plagiarism_check est accordé
  When une vérification distante est demandée
  Then l'utilisateur choisit entre passages, section et document
  And le texte exact qui sera transmis lui est affiché avec son volume en mots
  And l'envoi n'a lieu qu'après confirmation sur cette base

Scenario: La vérification distante ne dépend pas du résultat local
  Given une vérification locale n'a signalé aucun passage
  When une vérification distante à l'étendue section est demandée
  Then elle est exécutée
  And aucun message ne la présente comme superflue

Scenario: Consentement requis
  Given le consentement plagiarism_check est refusé
  When une vérification distante est demandée
  Then l'opération est refusée avec le périmètre et ce qui serait transmis
  And la vérification locale reste disponible

Scenario: Journalisation sans contenu
  Given une vérification distante exécutée
  When le journal d'audit est consulté
  Then l'entrée porte l'étendue, le volume en mots, le fournisseur et la date
  And elle ne contient aucun extrait du texte transmis
```

**Notes techniques impératives :** étendues `passages`, `section`, `document`, choisies à chaque requête et jamais mémorisées implicitement · aucun fournisseur codé en dur, URL et clé saisies par l'utilisateur · pas de verdict global ni de pourcentage de plagiat pour le document · vérification locale par empreintes winnowing sur n-grammes de 5 mots.

**Interdictions :** coder en dur un fournisseur · afficher un pourcentage global de plagiat · journaliser le texte transmis · envoyer sans confirmation sur le texte affiché.

---

# Epic 7 — Audit et sécurité `[P0]`

## US-701 — Journal d'audit append-only à chaîne de hachage `[RÉVISÉE — D-07]`

**Priorité :** P0 · **Dépendances :** US-001

```gherkin
Scenario: Chaînage valide
  Given 500 entrées journalisées
  When la chaîne est vérifiée
  Then chaque entrée contient le hash de la précédente
  And la vérification retourne VALIDE

Scenario: Détection d'altération
  Given une entrée modifiée directement en base
  When la chaîne est vérifiée
  Then la vérification retourne ALTÉRÉ
  And l'index de la première entrée incohérente est retourné
```

**Notes techniques :** SHA-256 sur la représentation canonique de l'entrée + hash précédent · insertion via un seul chemin de code · **vocabulaire imposé** : « tamper-evident » / « détection d'altération ». Ne jamais employer « immuable » ou « infalsifiable » dans le code, l'UI ou la documentation : le fichier appartient à l'utilisateur, la chaîne détecte mais n'empêche pas.

## US-AUTH-001 — Rôles et authentification locale `[GAP-05]` — `P1` · rôles Auteur / Superviseur / Lecture seule, comptes locaux, pas d'IdP externe.

---

# Epic 8 — Frontend Angular `[P1]`

## US-801 — Layout IDE avec Dockview et Signals — `P1` · **Inchangée.**
## US-UI-002 — Éditeur Quarto/MyST `[GAP-09]` — `P1` · **révisée** : éditeur `.qmd`, aperçu live, insertion de citations depuis le panneau bibliographique.
## US-UI-003 — Éditeur de code Monaco `[GAP-10]` — `P1`
## US-UI-004 — Versioning et comparaison de versions `[GAP-11]` — `P2`
## US-UI-005 — Commentaires et annotations `[GAP-12]` — `P2`
## US-DASH-001 — Tableau de bord d'avancement `[GAP-17]` — `P1`
## US-LLM-002 — Budget et suivi des tokens en mode distant `[GAP-18]` — `P2`

---

# Ordre d'exécution recommandé pour l'IA de codage

| Rang | Story | Justification |
|---|---|---|
| 1 | US-001 | Fondation, déjà spécifiée |
| 2 | **US-002 révisée** | Schéma corrigé — à faire avant toute écriture de données |
| 3 | US-003 | Indépendante, parallélisable |
| 4 | **US-005** | Débloque toute l'ingestion |
| 5 | US-004 | Sandbox, indépendante |
| 6 | US-101 | CRUD projets |
| 7 | US-102 | Ingestion RAG |
| 8 | US-201, US-202 | Machine à états et garde-fous |
| 9 | **US-PLAN-001** | Sans elle, rien n'est rédigeable |
| 10 | US-BIBLIO-001 | Alimente le RAG autrement qu'à la main |
| 11 | US-301, US-302 | Rédaction et relecture |
| 12 | US-501, US-502 | Export |
| 13 | US-701, US-006 | Audit et vérification du budget VRAM |
| 14 | US-801 | Frontend |

---

# Definition of Done V0.3

Une story est `Done` lorsque :

1. tous les scénarios Gherkin passent en test automatisé ;
2. `ruff check` et `ruff format --check` passent sans avertissement ;
3. la couverture des modules touchés est ≥ 80 % ;
4. `scripts/check_no_cloud_calls.py` passe (hors stories bibliographiques, où seule la liste blanche est autorisée) ;
5. aucun fichier hors du périmètre déclaré n'a été modifié ;
6. l'ADR concerné est référencé dans le message de commit lorsque la story applique une décision d'architecture ;
7. la matrice de traçabilité est mise à jour.
