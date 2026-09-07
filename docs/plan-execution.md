# Plan d'exécution — du dossier au premier PDF

Trois phases, dans cet ordre. La phase 3 démarre en parallèle de la phase 2,
après US-001 et avant US-801.

---

## Phase 1 — Spikes de dérisquage · avant toute story

Quatre hypothèses portent le dossier sans avoir jamais été vérifiées sur une
machine réelle. Voir `spikes/README.md`.

| Spike | Objet | Décision en jeu | Durée |
|---|---|---|---|
| 02 | **Sortie structurée d'un 7B local** — le décisif | ADR-003, ADR-008 | 1 j |
| 01 | sqlite-vec sur Windows | ADR-002 | 0,5 j |
| 03 | Pyodide : isolation réelle et paquets hors ligne | ADR-005 | 1 j |
| 04 | Quarto sur 150 pages | ADR-006 | 0,5 j |

**Porte de sortie.** `rapport-spikes.md` produit, et pour chaque hypothèse non
tenue, une décision écrite : ADR amendé ou ADR rouvert. Aucune story n'est
engagée avant.

Le spike 01 a déjà été exécuté sous Linux : conforme sur dix critères, D-01
confirmé empiriquement. Reste Windows, où le support des extensions SQLite
dépend de la provenance de l'interpréteur Python.

---

## Phase 2 — Fondation backend · 9 stories

Aucune de ces stories ne dépend de l'interface. Les geler en attendant un
travail d'IHM serait du gaspillage.

### Lot A — socle, séquentiel

```
US-001  backend FastAPI + aiosqlite WAL
  └─ US-002  schéma SQLite + sqlite-vec          [dépend du spike 01]
       └─ US-101  CRUD projets, registre, sauvegarde
```

### Lot B — moteurs, parallélisables dès US-001

```
US-003  LLM Manager, modèle unique persistant     [dépend du spike 02]
US-004  sandbox à deux niveaux                    [dépend du spike 03]
US-701  journal d'audit à chaîne de hachage
```

### Lot C — après A et B

```
US-005  embeddings CPU hors Ollama    → US-102  ingestion RAG
US-201 + US-202  machine à états, guardrails, circuit breaker
US-006  budget VRAM sur cycle complet
```

**Porte de sortie de la phase 2.** Un projet se crée, ingère 50 PDF, expose une
recherche KNN avec provenance, journalise ses opérations en chaîne vérifiable,
et le budget VRAM est mesuré sous 9 Go sur un cycle complet.

Trois stories sont indépendantes et peuvent être confiées à trois agents de
codage en parallèle : US-003, US-004 et US-701 ne partagent aucun fichier.
US-002 et US-101 doivent rester séquentielles, elles touchent le même schéma.

### Ordre de traitement recommandé

| Rang | Stories | Parallélisme |
|---|---|---|
| 1 | US-001 | — |
| 2 | US-002 · US-003 · US-701 | 3 agents |
| 3 | US-101 · US-004 | 2 agents |
| 4 | US-005 | — |
| 5 | US-102 · US-201+202 | 2 agents |
| 6 | US-006 | — |

---

## Phase 3 — IHM ciblée · en parallèle, après US-001, avant US-801

Trois maquettes, pas un système de design. Elles répondent aux seules
questions d'interface que le dossier laisse ouvertes.

| Maquette | Question à laquelle elle répond |
|---|---|
| **Boucle de traçabilité** | Comment un doctorant distingue une affirmation sourcée d'une hypothèse, puis remonte d'une phrase au chunk, puis à la page du PDF ? C'est la promesse qui justifie l'outil, et elle n'a aucune conception d'interface. |
| **Relecture d'une section** | Comment se présentent les findings hiérarchisés, le score par catégorie, et la porte de validation humaine — sans que le score ressemble à un feu vert ? |
| **Premier lancement** | Que voit quelqu'un qui ouvre l'application sur un projet vide ? Le chemin sujet → sources → plan validé n'existe nulle part. |

Une quatrième, optionnelle : la lisibilité en rédaction longue — un outil de
thèse s'utilise six heures d'affilée.

**Porte de sortie.** Les maquettes fixent les contrats d'interface consommés
par US-801, US-UI-002 et US-302. Elles ne produisent pas de code de production.

---

## Ce qui reste hors de ces trois phases

- Jeu de PDF libres de droits pour les tests d'ingestion, à constituer au
  moment d'implémenter US-102.
- Prompts système définitifs des sept agents : leur structure est posée par
  US-003, leur contenu métier se stabilisera à l'usage.
- Stories P1 et P2 de complément : toutes pourvues en prompts, aucune sur le
  chemin critique.
