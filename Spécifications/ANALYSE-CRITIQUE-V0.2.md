# Analyse critique de la chaîne documentaire — Science AI Writer IDE

*Revue indépendante des documents produits au fil de la discussion Qwen (CDC V1 → V2 consolidé → V3 relu par Gemini → Spécifications techniques V0.1 → V0.2 → Backlog V0.2 → Prompt Pack V0.2 → Repository initial + traçabilité).*

---

## 1. Ce que vaut le dossier aujourd'hui

Le dossier est **au-dessus de la moyenne de ce qu'on voit en démarrage de projet**. Trois choses sont particulièrement bien faites :

- **La contrainte matérielle est traitée comme une contrainte de conception, pas comme un détail d'exploitation.** Le passage de modèles 30B à 7B-9B, le déchargement des embeddings sur CPU, le refus de l'orchestration conversationnelle libre au profit de LangGraph : ces trois décisions découlent toutes du même budget de 10 Go de VRAM. C'est cohérent.
- **La séparation « fait sourcé / synthèse / hypothèse » est posée dès le CDC**, pas ajoutée après coup. Pour un outil de rédaction doctorale, c'est le point qui décide de la crédibilité du produit.
- **Le dossier est réellement orienté « IA de codage »** : Gherkin, notes techniques impératives, interdictions explicites, périmètre strict par user story, commandes de vérification. C'est le bon format pour ce mode de développement.

La progression V1 → V0.2 est saine : chaque itération a retiré de l'ambition irréaliste et ajouté de la contrainte vérifiable.

---

## 2. Défauts bloquants (à corriger avant d'écrire du code)

### D-01 — Une clé étrangère sur une table virtuelle `vec0` : impossible

**Gravité : bloquante.** US-002 spécifie :

```
vec_chunks(embedding float[768], chunk_id INTEGER PRIMARY KEY,
           source_id INTEGER REFERENCES SourceDocument(id))
```

et le scénario Gherkin `Intégrité référentielle chunks-sources` attend un échec `FK violated`.

SQLite **n'applique pas les contraintes de clé étrangère sur les tables virtuelles**, et `vec0` est une table virtuelle. La contrainte sera silencieusement ignorée : le test ne passera jamais, et l'IA de codage passera un temps considérable à tenter de le faire passer.

**Correction.** Séparer stockage relationnel et stockage vectoriel, reliés par le `rowid` :

```sql
CREATE TABLE chunk (
  id         INTEGER PRIMARY KEY,
  source_id  INTEGER NOT NULL REFERENCES source_document(id) ON DELETE CASCADE,
  ordinal    INTEGER NOT NULL,
  text       TEXT    NOT NULL,
  page_start INTEGER,
  page_end   INTEGER
);

CREATE VIRTUAL TABLE vec_chunk USING vec0(
  embedding float[768]
);
-- invariant : vec_chunk.rowid == chunk.id
```

L'intégrité référentielle vit dans `chunk`, la recherche vectorielle dans `vec_chunk`, et la jointure se fait sur `rowid`. La suppression en cascade des vecteurs doit alors être assurée par un **trigger** `AFTER DELETE ON chunk`, puisque `ON DELETE CASCADE` ne traverse pas la table virtuelle.

### D-02 — Le format canonique du document est incohérent

**Gravité : bloquante pour l'export.** Le dossier dit à plusieurs endroits : *« le format canonique est MyST Markdown, qui permet une compilation robuste vers Quarto puis vers PDF/DOCX/LaTeX »*.

MyST et Quarto sont **deux écosystèmes distincts et non interopérables** :

| | MyST | Quarto |
|---|---|---|
| Moteur | Sphinx / Jupyter Book (Python) | Pandoc + binaire Quarto |
| Renvois croisés | rôles `{ref}`, `{numref}` | `@fig-x`, `@tbl-x`, `@eq-x` |
| Bibliographie | `sphinxcontrib-bibtex` | `citeproc` + CSL |
| Directives | `:::{note}` MyST | `::: {.callout-note}` |

Il n'existe pas de chaîne « MyST → Quarto ». Écrire du MyST canonique et espérer que Quarto le compile produira des renvois cassés et des figures non numérotées — exactement ce que la V3 cherchait à éviter en abandonnant le Markdown standard.

**Correction.** Choisir **Quarto (`.qmd`) comme format canonique unique** : binaire autonome, pas de dépendance Sphinx, export PDF (via LaTeX), DOCX et HTML natif, `citeproc` intégré, renvois croisés `@fig-`/`@tbl-`/`@sec-` robustes sur 300 pages, blocs de code exécutables. MyST reste possible en *export secondaire*, jamais en format pivot. Voir ADR-006.

### D-03 — Sur Windows, les Job Objects n'isolent ni le réseau ni le disque

**Gravité : haute.** La V0.2 corrige à juste titre l'usage de `setrlimit` (POSIX-only) par les Job Objects Win32. Mais les Job Objects ne couvrent que **mémoire, temps CPU, nombre de processus**. Ils ne bloquent **ni les sockets, ni l'accès au système de fichiers**.

Le scénario `test_network_disabled` (US-004, US-401) est donc **infaisable en natif sous Windows** avec l'architecture retenue. Bloquer le réseau d'un processus fils sous Windows sans Docker demande soit le Windows Filtering Platform, soit un token restreint + AppContainer — deux chantiers hors de portée d'un MVP.

**Correction : sandbox à deux niveaux** (ADR-005).

- **Niveau 1 — code généré par le LLM** : exécution dans **Pyodide/WebAssembly**. Isolation réseau et disque parfaite et *identique sur les deux OS*, car garantie par le runtime Wasm et non par l'OS. Couvre numpy, pandas, scipy, matplotlib, sympy, scikit-learn — soit l'écrasante majorité des figures et statistiques d'une thèse.
- **Niveau 2 — code de l'utilisateur, calcul lourd** : sous-processus natif, Job Objects (Windows) / `setrlimit` + `unshare` (Linux), **avec un avertissement explicite que l'isolation réseau n'est pas garantie sous Windows**, et consentement au lancement.

Ce découpage a un avantage supplémentaire : le code que l'IA écrit toute seule est justement celui qui doit être le plus contraint.

### D-04 — Le critère « le cache KV est réutilisé » n'est pas testable via Ollama

**Gravité : moyenne.** US-003 pose : *« deux requêtes successives → le cache KV est réutilisé (pas de rechargement weights) »*. Deux requêtes indépendantes avec des prompts différents ne partagent pas de cache KV ; seul un **préfixe commun** peut être mis en cache. Le critère mélange deux choses distinctes : la persistance des *poids* (réelle, mesurable) et la réutilisation du *cache KV* (conditionnelle, non observable depuis l'API Ollama).

**Correction.** Reformuler en un critère mesurable : la réponse Ollama expose `load_duration`. Le test devient :

```gherkin
Scenario: Les poids ne sont pas rechargés entre deux requêtes
  Given une première requête a été traitée
  When une seconde requête est envoyée
  Then load_duration de la seconde réponse est < 50 ms
  And le temps au premier token est < 2 s
```

Et déplacer le vrai gain de cache dans une exigence séparée : **préfixe système stable** (le prompt système d'un agent ne doit pas varier d'une requête à l'autre), qui rend le *prefix caching* effectif.

### D-05 — Les embeddings passeront sur GPU malgré la spécification

**Gravité : moyenne.** La spec dit « embeddings sur CPU pour préserver la VRAM » et, en parallèle, « Ollama avec `keep_alive=-1` ». Si `nomic-embed-text` est servi *par Ollama*, Ollama le chargera **sur GPU par défaut**, en second modèle résident, et le `keep_alive=-1` du modèle principal peut alors provoquer une éviction ou un dépassement de VRAM au pire moment — pendant l'ingestion RAG.

**Correction.** Sortir les embeddings d'Ollama : `fastembed` (ONNX Runtime, CPU) ou `sentence-transformers` avec `device="cpu"`, en processus Python. On y gagne le contrôle total du batch, la reproductibilité, et — point non négligeable pour `nomic-embed-text` — la maîtrise des préfixes obligatoires `search_document:` / `search_query:`, dont l'oubli dégrade nettement la qualité du rappel.

### D-06 — Le MVP ne peut pas produire un document

**Gravité : bloquante fonctionnellement.** Le backlog V0.2 contient US-001 à US-801, mais **aucune user story de génération et de validation du plan**. Le gap est identifié (GAP-02, marqué P0) sans être intégré. Or le pipeline complet est : sujet → **plan validé** → rédaction section par section. Sans plan, US-301 (génération de section) n'a pas d'entrée. Le backlog V0.2, exécuté tel quel, produit une infrastructure qui ne rédige rien.

**Correction.** US-PLAN-001 remonte en P0, avant US-301, dans le backlog V0.3. De même US-BIBLIO-001 : sans recherche bibliographique, la base de connaissances ne peut être alimentée que par des PDF déposés à la main.

---

## 3. Défauts non bloquants mais à arbitrer

### D-07 — « Journal d'audit immuable » est une promesse trop forte

Une chaîne de hachage dans SQLite fournit une **détection d'altération**, pas une **immuabilité**. L'utilisateur est propriétaire du fichier : il peut réécrire la table et recalculer toute la chaîne. Pour un outil dont un argument de vente est l'intégrité académique, l'écart entre les deux notions est significatif.

Reformuler partout en **« journal d'audit infalsifiable sans détection » / « tamper-evident »**. Si une garantie plus forte est un jour requise (dépôt de thèse), la voie est l'ancrage périodique du dernier hash chez un tiers (horodatage RFC 3161), à documenter comme évolution — pas comme fonction MVP.

### D-08 — Le modèle unique dégrade l'agent code

La V0.2 supprime les modèles spécialisés par agent (bonne décision de latence) et retient `qwen2.5-7b-instruct`. Conséquence non énoncée : l'agent d'exécution Python perd `qwen2.5-coder-7b`, nettement meilleur en génération de code. Ce n'est pas un défaut — c'est un arbitrage **latence contre qualité de code** qui doit être écrit noir sur blanc, avec sa porte de sortie : un second modèle chargé *à la demande, uniquement pour l'agent code*, activable en option pour les postes disposant de plus de 12 Go de VRAM.

### D-09 — Duplication `requirements.txt` / `pyproject.toml`

Le repository initial fournit les deux, sans indiquer lequel fait foi. Sur un projet piloté par IA de codage, deux sources de vérité pour les dépendances garantissent une divergence à la troisième story. Retenir `pyproject.toml` comme source unique, et générer `requirements.txt` verrouillé via `uv pip compile` dans le script de vérification.

### D-10 — Aucun test ne mesure le budget VRAM global

La matrice de traçabilité couvre la latence LLM (`check_llm_latency.py`) mais aucun test ne vérifie l'invariant central du projet : *l'occupation VRAM totale reste sous 10 Go pendant un cycle complet* (modèle chargé + ingestion RAG + export). C'est pourtant l'exigence dont dépendent toutes les autres décisions d'architecture. Ajouter `scripts/check_vram_budget.py` (via `nvidia-smi --query-gpu=memory.used`) et un test d'intégration de bout en bout.

### D-11 — Déclaration d'usage de l'IA

Absent du dossier. La plupart des établissements imposent désormais une déclaration d'usage des outils d'IA générative en annexe des mémoires et thèses. L'outil dispose déjà de tout le nécessaire (journal d'audit, traçabilité par section, modèles utilisés) : générer automatiquement cette annexe à l'export est un différenciateur à faible coût et à forte valeur institutionnelle. Proposé en US-EXPORT-003 (P1).

---

## 4. Recommandation de séquence

1. Appliquer D-01, D-02, D-03 aux **spécifications techniques** → V0.3.
2. Publier les **ADR individuels** (ils figent les arbitrages et empêchent l'IA de codage de les rejouer à chaque story).
3. Passer au **Backlog V0.3** : réintégration des gaps, US-PLAN-001 en P0, critères Gherkin corrigés (D-01, D-03, D-04).
4. Reprendre l'implémentation à **US-002** avec le schéma corrigé.

Ne pas lancer US-002 sur le schéma actuel : le corriger après coup coûtera une migration de base et une réindexation complète du RAG.
