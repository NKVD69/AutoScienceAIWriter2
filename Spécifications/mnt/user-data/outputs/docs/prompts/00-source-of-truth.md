# Source de vérité — Science AI Writer IDE

**Version :** V0.3.1 · **Date :** 2026-09-06
À placer dans `docs/prompts/00-source-of-truth.md`. Ce fichier est le premier
que toute IA de codage doit lire avant d'intervenir sur le dépôt.

---

## 1. Hiérarchie documentaire

En cas de divergence entre deux documents, **l'échelon supérieur l'emporte**,
sans exception et sans arbitrage local.

| Rang | Document | Emplacement | Autorité |
|---|---|---|---|
| 1 | **Architecture Decision Records** | `docs/adr/` | Décisions figées. Non rediscutables en cours d'implémentation. |
| 2 | **Spécifications techniques V0.3.1** | `docs/specs/specifications-techniques-v0.3.md` | Contrats, modèle de données, budgets. |
| 3 | **Backlog V0.3** | `docs/backlog/backlog-v0.3.md` | Périmètre et critères d'acceptation par story. |
| 4 | **Prompt d'implémentation de la story** | `docs/prompts/03-story-prompts.md` | Instructions d'exécution. |
| 5 | **Cahier des charges V3** | `docs/specs/cahier-des-charges-v3.md` | Intention produit. Contexte, pas contrat. |

> Le prompt d'une story peut préciser un ADR, jamais le contredire. S'il le
> contredit, c'est le prompt qui est fautif : signale-le, applique l'ADR.

## 2. Documents périmés — ne pas consulter

| Document | Motif |
|---|---|
| Spécifications techniques V0.1 et V0.2 | Remplacées ; contiennent trois défauts bloquants corrigés depuis |
| Backlog V0.2 | Remplacé ; US-002 et US-004 y sont fautives, US-PLAN-001 absente |
| Prompt Pack V0.2, hors US-001 | Remplacé par la présente V0.3 |
| Toute version du cahier des charges antérieure à la V3 | Historique |

## 3. Les treize décisions figées

Ces décisions ne se rejouent pas. Elles sont détaillées dans `docs/adr/`.

| ADR | Décision | Ce qui est interdit en conséquence |
|---|---|---|
| 001 | Un `.sqlite` par projet, WAL, `aiosqlite` | `sqlite3` synchrone dans le code applicatif ; file d'écriture bloquante ; second fichier de base |
| 002 | `sqlite-vec`, `chunk` relationnel + `vec_chunk` virtuel liés par `rowid` | Clause `REFERENCES` dans un `CREATE VIRTUAL TABLE` ; ChromaDB |
| 003 | Modèle LLM unique persistant, `keep_alive=-1` | `keep_alive=0` ; load/unload par agent ; variable dans un prompt système |
| 004 | Orchestration LangGraph déterministe | Discussion libre entre agents ; transition automatique d'une porte humaine |
| 005 | Sandbox à deux niveaux, Wasm puis natif contraint | `setrlimit` sous Windows ; prétendre isoler le réseau en natif sous Windows ; niveau choisi par un agent |
| 006 | Quarto `.qmd` canonique | Syntaxe MyST dans le contenu canonique ; double format pivot |
| 007 | `.bib` généré à chaque export | Compiler à partir d'un `.bib` fourni par l'utilisateur |
| 008 | Guardrails Pydantic et circuit breaker | Réessais illimités ; réparation de JSON par expression régulière |
| 009 | Audit à détection d'altération | Employer « immuable » ou « infalsifiable » ; `UPDATE` ou `DELETE` sur `audit_log` |
| 010 | Local strict, consentement par périmètre ; étendue anti-plagiat choisie par l'utilisateur | Appel réseau hors liste blanche ; consentement global unique ; restreindre l'étendue anti-plagiat au-delà du choix de l'auteur |
| 011 | Angular standalone, Signals, Dockview | NgModules ; état métier dans un composant |
| 012 | Pas de Docker au MVP | `Dockerfile`, `docker-compose.yml`, instruction supposant Docker |
| 013 | Embeddings CPU hors Ollama | Router les embeddings par Ollama ; omettre les préfixes nomic |

## 4. Règles de conduite

**Périmètre.** Une story ne modifie que les fichiers listés dans son prompt.
Un fichier hors liste, même trivial, fait échouer la revue.

**Pas de dépendance non listée.** Chaque prompt donne une liste close. Une
dépendance jugée nécessaire mais absente se signale en Réserves ; elle ne
s'ajoute pas.

**Pas d'invention de service.** Aucun appel à une API, un binaire ou un
service qui ne figure pas dans les spécifications.

**Réserves plutôt que déviation.** Une objection à une décision figée se
consigne en fin de rapport, en section « Réserves ». Le code applique la
décision. Une déviation silencieuse est le seul défaut qui invalide
automatiquement une livraison.

**Option conservatrice en cas d'ambiguïté.** Ne pas poser de question
préalable : retenir l'option la plus restrictive et la signaler.

**Tests d'abord vérifiables.** Un critère d'acceptation qui ne peut pas être
observé n'est pas un critère. Si un scénario Gherkin te paraît infaisable,
signale-le en Réserves plutôt que d'écrire un test qui simule le succès.
Ce projet a déjà produit deux critères de ce type — une clé étrangère sur
table virtuelle et un blocage réseau en natif sous Windows — tous deux
corrigés en V0.3.

## 5. Vérifications obligatoires avant livraison

```
cd backend && pytest -q -m "not integration"
ruff check backend/ && ruff format --check backend/
python scripts/check_sqlite_wal.py
python scripts/check_sqlite_vec.py
python scripts/check_llm_latency.py          # si Ollama disponible
python scripts/check_embeddings_cpu.py
python scripts/check_vram_budget.py          # si GPU NVIDIA disponible
python scripts/check_sandbox_windows.py      # ou check_sandbox_linux.py
python scripts/check_quarto_export.py        # si Quarto disponible
python scripts/check_no_cloud_calls.py
cd frontend && ng test --watch=false && ng lint
```

Un script indisponible faute d'environnement (pas de GPU, pas d'Ollama)
sort en code 2 et n'est pas un échec. Un script qui sort en 1 est un échec.

## 6. Chemin critique du MVP

```
US-001 → US-002 → US-003 → US-005 → US-101 → US-102
       → US-201 → US-PLAN-001 → US-301 → US-501 → US-502
```

Onze stories entre le dépôt vide et un PDF compilé. Toute story hors de ce
chemin attend, quelle que soit sa facilité apparente.

## 7. Format de rapport attendu à chaque story

1. Plan d'implémentation, 10 lignes maximum.
2. Contenu intégral de chaque fichier créé ou modifié, un bloc par fichier,
   chemin exact en en-tête.
3. Sortie attendue des commandes de vérification.
4. Section « Réserves » : tout point de la spécification jugé discutable,
   toute ambiguïté arbitrée, toute dépendance souhaitée mais non ajoutée.
   Cette section peut être vide ; elle ne peut pas être omise.
