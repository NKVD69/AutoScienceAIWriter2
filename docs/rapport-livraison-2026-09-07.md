# Rapport de livraison — 7 septembre 2026

Traitement du Prompt Pack V0.3.1. Poste : Windows 11 · Python 3.11.15 ·
SQLite 3.53.1 · RTX 3080 10 Go · Ollama présent.

Le format de rapport imposé par `00-source-of-truth.md` §7 exige une section
« Réserves » à chaque story. Elle est consolidée ici, le contenu des fichiers
étant dans le dépôt plutôt que recopié.

---

## 1. Ce qui est livré

| Story | Objet | Vérification |
|---|---|---|
| Phase 1 | Spikes exécutables sur ce poste | spike 01 : 10/10 · spike 02 selftest : 14/14 |
| US-001 | Backend FastAPI, aiosqlite WAL | `check_sqlite_wal.py` 5/5 |
| US-002 | Schéma unique + sqlite-vec | `check_sqlite_vec.py` 9/9 |
| US-003 | LLM Manager, modèle unique persistant | `check_llm_latency.py` — `load_duration` 1,7–13,9 ms |
| US-701 | Journal à détection d'altération | `check_audit_chain.py` 6/6 |

116 tests unitaires passants · 2 tests d'intégration désélectionnés ·
`ruff check` et `ruff format --check` sans avertissement.

Chemin critique : 4 des 11 stories sont livrées. US-005, US-101, US-102,
US-201, US-PLAN-001, US-301, US-501 et US-502 restent à implémenter.

---

## 2. Réserves

### 2.1 Sur le dossier lui-même

**Le prompt de US-001 est absent du pack.** Le `README.md` du pack et le
backlog renvoient tous deux au Prompt Pack V0.2, qui n'est fourni ni dans le
dossier ni dans l'archive. Le périmètre de US-001 a donc été **dérivé** des
spécifications §3 et §4.5 et d'ADR-001. Il est explicite dans le commit
correspondant et reste à valider.

**`check_no_cloud_calls.py` n'appartient au périmètre d'aucune story.** Les
critères d'acceptation de US-002 l'invoquent, la source de vérité §5 l'exige
avant toute livraison, et les spécifications §3 le listent — mais aucune
liste de fichiers ne le crée. Il a été rattaché à US-001, dont le périmètre
était déjà dérivé.

**US-701 exige un index qu'aucun périmètre ne permet d'écrire.** Le point 2
du prompt impose `CREATE UNIQUE INDEX idx_audit_prev`, alors que sa liste de
fichiers exclut `backend/app/db/migrations/`. Modifier `001_initial.sql`
aurait cassé le contrôle SHA-256 sur toute base existante ; une migration
`002_audit_chain.sql` a donc été créée, ce qui a entraîné trois retouches
supplémentaires hors périmètre : l'index ajouté à `schema.sql` pour qu'il
continue de décrire le schéma courant, et deux tests de US-002 qui énumèrent
les versions appliquées.

**La table `schema_migration` décrite par US-002 ne permet pas le contrôle
qu'elle exige.** Le point 5 impose `schema_migration(version, applied_at)`
puis, deux lignes plus bas, de « refuser de démarrer si un fichier de
migration déjà appliqué a changé de somme SHA-256 » — ce qui suppose de
stocker cette somme. Les colonnes `filename` et `sha256` ont été ajoutées.

**`pytest-asyncio` n'est listé nulle part.** §13.1 énumère les dépendances
d'exécution, §13.2 impose `pytest` sur une base de code entièrement
asynchrone. La dépendance est déclarée en extra `dev`, signalée ici.

### 2.2 Défauts trouvés dans mon propre code

**`transaction()` ouvrait un `BEGIN` différé.** Une transaction qui lit avant
d'écrire commence en lecteur ; SQLite refuse alors la montée en écriture par
un `SQLITE_BUSY` **immédiat, sans honorer `busy_timeout`**, pour éviter un
interblocage entre deux lecteurs. C'est exactement le motif du journal
d'audit — lire le dernier hash, puis insérer. La promesse d'ADR-001 selon
laquelle « `busy_timeout` absorbe la contention en écriture » ne tient qu'avec
`BEGIN IMMEDIATE`, désormais utilisé. Le défaut a été révélé par le test de
concurrence de US-701, pas par les tests de US-001.

**`ensure_loaded()` préchauffait sans `num_ctx`.** Ollama recharge
intégralement les poids quand la taille de contexte change : le préchauffage
au démarrage était donc annulé par la première génération réelle, qui payait
le chargement complet. Sur ce poste, cela représente près de huit minutes —
précisément la latence qu'ADR-003 existe pour éliminer.

**`check_llm_latency.py` lisait une absence de token comme une latence
nulle.** Un modèle à raisonnement peut dépenser tout son budget en réflexion
sans rien émettre ; le script affichait alors `ttft = 0,0 ms` et concluait au
succès. C'est le type de critère qui simule la réussite que §4 de la source
de vérité proscrit. Le script distingue maintenant « aucun token » (code 2,
mesure impossible) de « seuil dépassé » (code 1).

### 2.3 Sur les décisions figées

**ADR-002 — le budget KNN est atteint à la coïncidence près.** Sous Windows,
le KNN simple sur 50 000 chunks mesure `p95 = 199,8 ms` pour un plafond de
200 ms fixé en §12.2. Ce n'est pas une marge. Toute évolution — chunks plus
longs, dimension supérieure, disque plus lent — fera basculer la mesure. La
décision n'est pas remise en cause ; le budget mérite d'être réexaminé lors
de US-102, sur volumétrie réelle.

**ADR-003 — l'ordre de grandeur du coût de swap est sous-évalué.** L'ADR
justifie le modèle unique persistant par une latence de chargement « de
plusieurs secondes à plusieurs dizaines de secondes ». Le chargement à froid
mesuré ici est de **7 min 44 s**. La décision est donc plus justifiée encore
qu'énoncé, mais deux conséquences n'en sont pas tirées dans le dossier :
`num_ctx` doit être aussi stable que le prompt système, et un rechargement
accidentel n'est pas une dégradation de latence, c'est une interruption de
service.

**§12.1 — le budget VRAM suppose une carte libre.** Sur ce poste, 8,7 Go des
10 Go sont occupés par d'autres processus **avant** tout chargement de
modèle, et une tentative de chargement dans ces conditions renvoie un 500
d'Ollama. Le plafond « observé < 9,0 Go » n'est pas atteignable sur un poste
de travail en usage normal. À traiter dans US-006, qui devrait mesurer la
VRAM *disponible* et non la seule consommation du processus.

**ADR-009 — le contrôle lexical exige une distinction usage / mention.** Une
recherche de sous-chaîne signale les documents qui *énoncent* l'interdit :
ADR-009 lui-même, la source de vérité, plusieurs prompts. Elle signale aussi
`immutable=1`, paramètre d'URI SQLite employé par US-ZOTERO-001, sans
rapport. Le test retenu ignore les occurrences citées entre guillemets et les
identifiants, et conserve un cas de contrôle vérifiant qu'il détecte encore
une promesse réelle.

### 2.4 Environnement

**La racine du dépôt git était `C:/`.** Le disque système entier était suivi
par git. Aucun commit n'y a été fait ; un dépôt propre a été initialisé dans
le répertoire du projet. `C:\.git` n'a pas été touché et devrait être
supprimé après vérification qu'il n'a pas d'usage.

**Le modèle d'ADR-003 n'est pas installé.** `qwen2.5:7b-instruct-q4_K_M` est
absent du poste. Il n'a pas été téléchargé : `model_download` est refusé par
défaut (ADR-010) et un téléchargement de 5 Go relève d'une décision de
l'utilisateur. Les mesures de latence ont été faites contre
`qwen3.5:latest`, à titre indicatif. Le spike 02 — le seul capable de
rouvrir un ADR structurant — reste **non mesuré**.

**Le spike 02 abandonne à 300 s.** Ce délai est inférieur au temps de
chargement d'un modèle sur ce poste : le spike échoue sur son propre
préchauffage avant d'avoir mesuré quoi que ce soit. Son `timeout` doit être
porté au-delà de 600 s pour être exécutable ici.

---

## 3. Suite recommandée

Dans l'ordre du plan d'exécution, en tenant compte de ce qui est mesurable
sur ce poste :

1. `ollama pull qwen2.5:7b-instruct-q4_K_M`, puis spike 02 en entier — c'est
   le seul point ouvert qui puisse rouvrir ADR-003 ou ADR-008.
2. **US-101** (CRUD projets), qui ferme le registre de projets dont
   `api/v1/audit.py` simule aujourd'hui la résolution par convention de
   nommage.
3. **US-005** (embeddings CPU) — attention : le modèle `nomic-embed-text-v1.5`
   se télécharge, ce qui relève de `model_download`.
4. **US-004** (sandbox), indépendante, et **US-102** (ingestion) après US-005.
