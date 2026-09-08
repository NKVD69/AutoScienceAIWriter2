# Matrice de traçabilité V0.3 — Science AI Writer IDE

Relie exigences du cahier des charges V3 → décisions d'architecture (ADR) → spécifications techniques V0.3 → user stories du Backlog V0.3 → tests automatisés.

**Ordre de priorité documentaire :** ADR > Spécifications V0.3 > Backlog V0.3 > Cahier des charges V3.

---

## 1. Décisions qui remplacent des exigences antérieures

| Exigence V3 / V0.2 | Remplacée par | ADR | Motif |
|---|---|---|---|
| ChromaDB embarqué | `sqlite-vec` dans le fichier projet | ADR-002 | Sauvegarde atomique, filtrage relationnel |
| FK déclarée sur `vec_chunk` | `chunk` relationnel + trigger de cascade | ADR-002 | SQLite ignore les FK sur tables virtuelles |
| MyST canonique compilé via Quarto | Quarto `.qmd` canonique unique | ADR-006 | La chaîne MyST → Quarto n'existe pas |
| `setrlimit` multiplateforme | Wasm (agent) + natif contraint (utilisateur) | ADR-005 | `setrlimit` absent de Windows ; Job Objects n'isolent pas le réseau |
| Un modèle LLM par agent | Modèle généraliste unique persistant | ADR-003 | Latence de swap rédhibitoire |
| `DbWriteQueue` applicative | WAL + `busy_timeout` | ADR-001 | Une file bloquante bloque l'event loop |
| Embeddings via Ollama | ONNX CPU hors Ollama | ADR-013 | Ollama charge les modèles d'embedding sur GPU |
| Journal « immuable » | Journal à détection d'altération | ADR-009 | L'utilisateur possède le fichier |
| Modèles 30B | 7B-9B quantifiés | ADR-003 | OOM certain sur 10 Go |
| Agents en discussion libre | Machine à états LangGraph | ADR-004 | Contexte non borné, audit impossible |

---

## 2. Matrice fonctionnelle

| Exigence V3 | Spéc. V0.3 | User story | Priorité | Statut |
|---|---|---|---|---|
| Saisie d'un sujet et création de projet | §4.2 | US-101 | P0 | **Livrée** |
| Import de PDF et de références | §7.1 | US-102 | P0 | **Livrée** |
| Recherche bibliographique multi-bases | §10 | US-BIBLIO-001 | **P0** | À implémenter |
| Import DOI / BibTeX / RIS | §10 | US-IMPORT-001 | P1 | À implémenter |
| Synchronisation Zotero | §10 | US-ZOTERO-001 | P2 | Non planifié |
| Base de connaissances vectorielle | §4.3, §7 | US-002, US-102 | P0 | **Livrée** |
| Filtres avancés du RAG | §7.2, §7.3 | US-RAG-002 | P1 | À implémenter |
| Génération de problématique et de plan | §5.2 | **US-PLAN-001** | **P0** | À implémenter |
| Édition et validation du plan | §5.2 | US-PLAN-001 | P0 | À implémenter |
| Rédaction de section sourcée | §5.4, §5.5 | US-301 | P1 | À implémenter |
| Relecture et score de qualité | §5.4 | US-302 | P1 | À implémenter |
| Distinction fait sourcé / hypothèse | §5.5 | US-301, US-302 | P1 | À implémenter |
| Exécution de code Python | §8 | US-004, US-401 | P0/P1 | À implémenter |
| Éditeur de code Monaco | §2 | US-UI-003 | P1 | À implémenter |
| Notebooks Jupyter | — | US-JUP-001 | P2 | Non planifié |
| Codes externes OpenFOAM / Serpent | §8.3 | US-CALC-001 | P2 | Non planifié |
| Dépôt de jeux de données | §8.2 | US-DATA-001 | P1 | À implémenter |
| Export PDF / DOCX / HTML | §9.2 | US-502 | P1 | À implémenter |
| Bibliographie dynamique | §9.3 | US-501 | P1 | À implémenter |
| TOC, figures, tableaux, glossaire, index | §9.4 | US-EXPORT-001 | P1 | À implémenter |
| Modèle académique paramétrable | §9.2 | US-EXPORT-002 | P1 | À implémenter |
| Déclaration d'usage de l'IA | §9.5 | **US-EXPORT-003** | P1 | À implémenter |
| Anti-plagiat, étendue choisie par l'auteur | §11.4, §11.5 | US-601 | P1 | À implémenter |
| Journal d'audit | §11.1 | US-701 | P0 | **Livrée** |
| Rôles et authentification locale | — | US-AUTH-001 | P1 | À implémenter |
| Tableau de bord d'avancement | — | US-DASH-001 | P1 | À implémenter |
| Mode pipeline automatique | §5.2 | US-WORKFLOW-001 | P1 | À implémenter |
| Mode pas à pas | §5.2 | US-201 | P0 | **Livrée** |
| Interface IDE à panneaux | §2 | US-801 | P1 | À implémenter |
| Éditeur `.qmd` | §9.1 | US-UI-002 | P1 | À implémenter |
| Versioning et comparaison | — | US-UI-004 | P2 | Non planifié |
| Commentaires et annotations | — | US-UI-005 | P2 | Non planifié |
| Suivi du budget de tokens distant | — | US-LLM-002 | P2 | Non planifié |

**Couverture P0 : 12 exigences, 12 couvertes.** Le trou fonctionnel du Backlog V0.2 (génération de plan) est refermé.

---

## 3. Matrice technique

| Décision | ADR | Spéc. V0.3 | User story | Scripts et tests |
|---|---|---|---|---|
| SQLite unique, WAL, aiosqlite | ADR-001 | §4.5 | US-001 | `check_sqlite_wal.py`, `test_concurrent_writes_no_lock` |
| Pas de `DbWriteQueue` | ADR-001 | §4.5 | US-001 | `test_no_write_queue_in_codebase` |
| `sqlite-vec`, séparation `chunk`/`vec_chunk` | ADR-002 | §4.3 | US-002 | `check_sqlite_vec.py`, `test_rowid_invariant_chunk_vec` |
| FK réellement appliquées | ADR-002 | §4.3 | US-002 | `test_foreign_keys_enforced_on_chunk` |
| Cascade vectorielle par trigger | ADR-002 | §4.3 | US-002 | `test_cascade_delete_source_removes_vectors` |
| Modèle LLM unique persistant | ADR-003 | §6.1 | US-003 | `check_llm_latency.py`, `test_load_duration_below_threshold_on_second_request` |
| Moteur LM Studio derrière `LLMBackend` | **ADR-014** | §6.1 | US-003 | `test_factory_resolves_both_engines`, `test_ttl_sent_on_every_request` |
| Résidence observée, non inférée | **ADR-014** | §6.2 | US-003 | `test_model_stays_resident_across_requests`, `test_load_duration_is_none_not_zero` |
| Prompts système stables | ADR-003 | §6.2 | US-003 | `test_system_prompt_byte_stable_across_calls`, `test_prompts_are_module_level_constants` |
| Option second modèle code | ADR-003 | §6.3 | US-003 | `test_code_model_refused_below_12gb` |
| Budget VRAM global | ADR-003 | §12.3 | **US-006** | `check_vram_budget.py`, `test_vram_budget` |
| Graphe déterministe | ADR-004 | §5.2 | US-201 | `test_transition_persisted`, `test_resume_after_restart` |
| Portes humaines | ADR-004 | §5.2 | US-201, US-PLAN-001 | `test_human_gate_blocks_without_validation` |
| Guardrails Pydantic | ADR-008 | §5.5 | US-201 | `test_invalid_output_triggers_guardrail` |
| Circuit breaker | ADR-008 | §5.3 | US-202 | `test_circuit_breaker_pauses_review_loop` |
| `ERROR_STATE` terminal | ADR-008 | §5.3 | US-202 | `test_error_state_requires_human` |
| Sandbox Wasm pour code agent | ADR-005 | §8.1, §8.2 | US-004 | `test_sandbox_factory_selects_wasm_for_agent_origin` |
| Isolation réseau niveau 1 | ADR-005 | §8.2 | US-004 | `test_network_disabled_level1` (Windows **et** Linux) |
| Isolation disque niveau 1 | ADR-005 | §8.2 | US-004 | `test_filesystem_isolated_level1` |
| Limites natives niveau 2 | ADR-005 | §8.3 | US-004 | `check_sandbox_windows.py`, `check_sandbox_linux.py` |
| Consentement niveau 2 | ADR-005 | §8.1 | US-004, US-401 | `test_consent_required_level2` |
| Quarto `.qmd` canonique | ADR-006 | §9.1 | US-502 | `test_section_stored_as_qmd` |
| Absence de MyST canonique | ADR-006 | §9.1 | US-502 | `test_no_myst_syntax_in_canonical_content` |
| Renvois croisés résolus | ADR-006 | §9.2 | US-502 | `test_crossrefs_resolved_in_pdf`, `check_quarto_export.py` |
| `.bib` généré | ADR-007 | §9.3 | US-501 | `test_bibtex_contains_only_cited` |
| Export bloqué sur citation invalide | ADR-007 | §9.3 | US-501 | `test_export_fails_on_unverified_citation` |
| Audit à détection d'altération | ADR-009 | §11.1 | US-701 | `test_chain_valid_over_500_entries`, `test_tamper_detection_returns_index_and_id` |
| Vocabulaire non trompeur | ADR-009 | §11.1 | US-701 | `test_wording_no_immutable_claim` |
| Local strict par défaut | ADR-010 | §11.3 | Transverse | `check_no_cloud_calls.py` |
| Consentement par périmètre | ADR-010 | §11.3 | US-BIBLIO-001, US-601 | `test_consent_recorded_and_revocable` |
| Étendue anti-plagiat choisie par l'utilisateur | ADR-010 amendé | §11.4 | US-601 | `test_scope_selected_per_request`, `test_scope_not_remembered_between_requests` |
| Aperçu confirmé avant transmission | ADR-010 amendé | §11.4 | US-601 | `test_no_send_without_confirmation_on_displayed_text` |
| Dégradation propre sans consentement | ADR-010 | §11.3 | US-BIBLIO-001 | `test_graceful_degradation_without_consent` |
| Angular, Dockview, Signals | ADR-011 | §2 | US-801 | `test_layout_persisted_per_project`, `test_sse_stream_to_signal_bridge` |
| Pas de Docker | ADR-012 | §1 | Transverse | Absence de `Dockerfile` contrôlée en CI |
| Embeddings CPU hors Ollama | ADR-013 | §6.4 | **US-005** | `test_embeddings_do_not_use_vram` — VRAM mesurée à +0 Mo |
| Préfixes nomic | ADR-013 | §6.4 | US-005 | `test_nomic_prefixes_applied` |
| Dimension verrouillée | ADR-013 | §4.3, §6.4 | US-002, US-005 | `test_dimension_locked_against_model_config` |
| `pyproject.toml` source unique | — | §13.1 | Transverse | `test_requirements_generated_not_edited` |

---

## 4. Matrice des user stories

| ID | Titre | Priorité | Dépendances | Prompt d'implémentation | Statut |
|---|---|---|---|---|---|
| US-001 | Backend FastAPI + aiosqlite WAL | P0 | — | ✅ Prompt Pack V0.2 | **Livrée** |
| **US-002** | Schéma SQLite + sqlite-vec | P0 | US-001 | ✅ `PROMPT-US-002.md` | **Livrée** |
| US-003 | LLM Manager persistant | P0 | — | ✅ `PROMPT-US-003.md` | **Livrée** |
| US-004 | Sandbox à deux niveaux | P0 | US-001 | ✅ `PROMPT-US-004.md` | Prêt |
| **US-005** | Embeddings CPU hors Ollama | P0 | US-002 | ✅ `PROMPT-US-005.md` | **Livrée** |
| **US-006** | Budget VRAM en CI | P0 | US-003, US-005 | ✅ `PROMPT-US-006.md` | Prêt |
| US-101 | CRUD projets | P0 | US-002 | ✅ `PROMPT-US-101.md` | **Livrée** |
| US-102 | Import sources et ingestion RAG | P0 | US-002, US-005 | ✅ `PROMPT-US-102.md` | **Livrée** |
| US-BIBLIO-001 | Recherche bibliographique | P0 | US-101 | ✅ `PROMPT-US-BIBLIO-001.md` | Prêt |
| US-201 | LangGraph + guardrails | P0 | US-003 | ✅ `PROMPT-US-201-202.md` | **Livrée** |
| US-202 | Circuit breaker | P0 | US-201 | ✅ `PROMPT-US-201-202.md` | **Livrée** |
| **US-PLAN-001** | Plan : génération, édition, validation | P0 | US-003, US-201 | ✅ `PROMPT-US-PLAN-001.md` | Prêt |
| US-301 | Rédaction de section | P1 | US-PLAN-001, US-102 | ✅ `PROMPT-US-301.md` | Prêt |
| US-302 | Relecture et score | P1 | US-301 | ✅ `PROMPT-US-302.md` | Prêt |
| US-401 | Exécution de script | P1 | US-004 | ✅ `PROMPT-US-401.md` | Prêt |
| US-501 | BibTeX dynamique | P1 | US-301 | ✅ `PROMPT-US-501-502.md` | Prêt |
| US-502 | Export Quarto | P1 | US-501 | ✅ `PROMPT-US-501-502.md` | Prêt |
| US-EXPORT-003 | Déclaration d'usage de l'IA | P1 | US-701, US-502 | ✅ `PROMPT-US-EXPORT-003.md` | Prêt |
| US-601 | Anti-plagiat avec consentement | P1 | US-502 | ✅ `PROMPT-US-601.md` | Prêt |
| US-701 | Journal d'audit | P0 | US-001 | ✅ `PROMPT-US-701.md` | **Livrée** |
| US-801 | Layout IDE Angular | P1 | API v1 | ✅ `PROMPT-US-801.md` | Prêt |
| US-AUTH-001 | Rôles locaux | P1 | US-101 | ✅ `PROMPT-US-AUTH-001.md` | Prêt |
| US-DATA-001 | Dépôt de datasets | P1 | US-004 | ✅ `PROMPT-US-DATA-001.md` | Prêt |
| US-IMPORT-001 | Import DOI/BibTeX | P1 | US-BIBLIO-001 | ✅ `PROMPT-US-IMPORT-001.md` | Prêt |
| US-RAG-002 | Filtres RAG avancés | P1 | US-102 | ✅ `PROMPT-US-RAG-002.md` | Prêt |
| US-UI-002 | Éditeur `.qmd` | P1 | US-801 | ✅ `PROMPT-US-UI-002.md` | Prêt |
| US-UI-003 | Monaco | P1 | US-801, US-401 | ✅ `PROMPT-US-UI-003.md` | Prêt |
| US-DASH-001 | Tableau de bord | P1 | US-801 | ✅ `PROMPT-US-DASH-001.md` | Prêt |
| US-EXPORT-001 | TOC, figures, glossaire | P1 | US-502 | ✅ `PROMPT-US-EXPORT-001-002.md` | Prêt |
| US-EXPORT-002 | Modèle académique | P1 | US-502 | ✅ `PROMPT-US-EXPORT-001-002.md` | Prêt |
| US-WORKFLOW-001 | Pipeline automatique | P1 | US-202 | ✅ `PROMPT-US-WORKFLOW-001.md` | Prêt |
| US-UI-004, US-UI-005 | Versions et commentaires | P2 | US-UI-002 | ✅ `PROMPT-US-UI-004-005.md` | Prêt |
| US-JUP-001 | Notebooks Jupyter | P2 | US-401 | ✅ `PROMPT-US-JUP-001.md` | Prêt |
| US-CALC-001 | Codes de calcul externes | P2 | US-004 | ✅ `PROMPT-US-CALC-001.md` | Prêt |
| US-ZOTERO-001 | Zotero local | P2 | US-IMPORT-001 | ✅ `PROMPT-US-ZOTERO-001.md` | Prêt |
| US-LLM-002 | LLM distant et budget | P2 | US-003 | ✅ `PROMPT-US-LLM-002.md` | Prêt |

**Chemin critique du MVP :** US-001 → US-002 → US-003 → US-005 → US-101 → US-102 → US-201 → **US-PLAN-001** → US-301 → US-501 → US-502.

---

## 5. Couverture des risques critiques

| Risque | Test de couverture | User story |
|---|---|---|
| Citation inventée | `test_no_invented_citation`, `test_export_fails_on_unverified_citation` | US-301, US-501 |
| Statistique non sourcée | `test_numeric_claim_requires_chunk_id` | US-301 |
| Fuite de données du projet | `check_no_cloud_calls.py`, `test_no_consent_blocks_biblio_search` | Transverse |
| Exécution de code hostile | `test_network_disabled_level1`, `test_filesystem_isolated_level1` | US-004 |
| Dépassement de VRAM | `check_vram_budget.py` | US-006 |
| Blocage de la base | `test_concurrent_writes_no_lock` | US-001 |
| Perte de vecteurs orphelins | `test_cascade_delete_source_removes_vectors` | US-002 |
| Boucle infinie d'agents | `test_circuit_breaker_pauses_review_loop` | US-202 |
| Altération du journal | `test_tamper_detection_returns_index` | US-701 |
| Export cassé sur 300 pages | `test_crossrefs_resolved_in_pdf` | US-502 |

---

## 6. État du dossier

**Le backlog V0.3 est intégralement pourvu.** Les 31 user stories disposent
d'un prompt d'implémentation aligné sur les ADR, les spécifications V0.3 et
le contrat OpenAPI.

Aucune story n'attend de spécification. Le dossier passe de la conception à
l'implémentation.

| Livrable | État |
|---|---|
| Analyse critique de la chaîne V0.2 | ✅ |
| Spécifications techniques V0.3 | ✅ |
| 14 ADR + index | ✅ |
| Backlog V0.3 | ✅ |
| Contrat OpenAPI, 30 chemins, 25 schémas | ✅ |
| Prompt Pack V0.3, 21 fichiers couvrant 31 stories | ✅ |
| Matrice de traçabilité | ✅ |

## Ce qui reste hors périmètre documentaire

- Jeu de PDF libres de droits pour les tests d'ingestion — à constituer au
  moment d'implémenter US-102.
- Prompts système définitifs des sept agents — leur contenu métier se
  stabilisera à l'usage, la structure est posée par US-003.
- Gabarits LaTeX d'établissements — hors périmètre par décision
  (US-EXPORT-002).

---

## 6. État d'implémentation — mise à jour du 7 septembre 2026

Quatre stories livrées, vérifiées sur poste Windows 11 / RTX 3080 10 Go.

| Story | Vérifications passantes |
|---|---|
| US-001 | `pytest` · `ruff` · `check_sqlite_wal.py` 5/5 · `check_no_cloud_calls.py` |
| US-002 | `check_sqlite_vec.py` 9/9 |
| US-003 | `check_llm_latency.py` — `load_duration` mesuré entre 1,7 et 13,9 ms |
| US-701 | `check_audit_chain.py` 6/6 — altération détectée à l'index 2500 sur 5000 |

115 tests unitaires passants, 2 tests d'intégration désélectionnés.

Toutes les autres stories restent **Prêt** : leur prompt existe, aucun code
ne les implémente.

### Écarts consignés

- **`test_no_write_queue_in_codebase`** et **`test_application_code_never_imports_sqlite3`**
  ajoutés à US-001 : la matrice les exigeait, aucun prompt ne les portait.
- **`transaction()` ouvre `BEGIN IMMEDIATE`**, non `BEGIN`. Une transaction
  différée qui lit avant d'écrire reçoit un `SQLITE_BUSY` immédiat sans
  honorer `busy_timeout` : la promesse d'ADR-001 ne tient pas autrement.
  Défaut trouvé par le test de concurrence de US-701.
- **Migration `002_audit_chain.sql`** créée hors des listes de fichiers de
  US-002 et US-701, l'index unique de chaînage n'étant rattaché à aucun
  périmètre alors que US-701 l'exige.

---

## 7. Amendement du 7 septembre 2026 — ADR-014

Le moteur d'inférence passe d'Ollama à **LM Studio**, décision consignée dans
[ADR-014](../adr/ADR-014-backend-lmstudio.md). Les principes d'ADR-003 sont
inchangés ; seul le moteur qui les applique devient un réglage.

| Élément du dossier | État |
|---|---|
| ADR-003 points 2 et 3 (pas de swap par agent, prompts figés) | inchangés, mêmes tests |
| ADR-003 point 1 (`keep_alive=-1`, `qwen2.5-7b`) | amendé par ADR-014 |
| ADR-003 point 5 (backend alternatif) | réalisé |
| Spéc. §6.2 (`load_duration < 50 ms`) | **inapplicable sous LM Studio** — remplacé par l'état du modèle |
| Spéc. §6.1 (modèle `qwen2.5-7b-instruct-q4_k_m`) | à réécrire : aucun modèle de ce profil n'est installé |
| Spéc. §13.1 (dépendance `ollama`) | à retirer : la dépendance est déclarée mais inutilisée, les deux backends étant écrits sur `httpx` |
| ADR-013 (embeddings hors GPU) | **inchangé**, bien que `text-embedding-nomic-embed-text-v1.5` soit installé dans LM Studio |

---

## 8. Amendement du 7 septembre 2026 — ADR-015

Le modèle par défaut devient `google/gemma-4-31b`, avec déversement CPU/RAM
assumé ([ADR-015](../adr/ADR-015-modele-31b-deversement-cpu.md)).

| Élément du dossier | État |
|---|---|
| ADR-003, « Options écartées » : modèle 30B rejeté pour OOM | **renversé** — `llama.cpp` répartit les couches, il n'y a pas d'OOM |
| Spéc. §12.1 (VRAM < 9,0 Go) | ne gouverne plus ; critère remplacé par « aucune éviction, aucun OOM » |
| Spéc. §12.2 (TTFT < 2 s, `load_duration` < 50 ms) | levé pour la génération ; le seuil devient un détecteur de rechargement |
| US-006 (`check_vram_budget.py`) | **à respécifier** : mesurer l'éviction, non une marge de VRAM |
| ADR-013 (embeddings CPU) | **tension nouvelle** : 4,2 Go de RAM libres seulement, les embeddings CPU et le modèle se disputent la même ressource |
| US-102 (ingestion, 500 chunks < 180 s) | budget établi sans modèle de 34 Go en mémoire — à revérifier |
| US-801, US-DASH-001 | une section demande ~15 min et un plan 24 min (mesurés) : le suivi de progression et la reprise deviennent structurants, non décoratifs |

Points d'ADR-003 **inchangés** : modèle unique résident, aucun swap par agent,
prompts système figés, second modèle de code refusé sous 12 Go de VRAM.

---

## 9. Spike 02 — mesuré le 8 septembre 2026

| Décision | Effet du spike |
|---|---|
| ADR-008 (guardrails, circuit breaker) | **Non rouvert** — 7 générations sur 7 conformes au premier essai, validateurs Pydantic personnalisés compris. Son seuil de 95 % reste **non certifié** : 7 essais sans échec ne garantissent que 65 % de succès à 95 % de confiance, et la certification demande 59 générations (~20 h) |
| ADR-004 (LangGraph déterministe) | Confortée — les sorties d'agent sont exploitables sans repli |
| ADR-003, ADR-015 (modèle persistant) | H2.2 conforme — le modèle est resté résident sur toute la série |
| H2.5 (le mode contraint améliore le taux) | **Non discriminée** — validée par une égalité 100 % / 100 %, non par une amélioration. Non testable tant que le mode libre ne produit pas d'échec |

La contrainte par schéma JSON est disponible sous LM Studio, schéma récursif
compris : la porte de sortie qu'ADR-008 envisageait est utilisable sans
changer de moteur.

---

## 10. Sérialisation des charges — ADR-016

| Décision | ADR | Spéc. | User story | Scripts et tests |
|---|---|---|---|---|
| Rédaction et ingestion jamais simultanées | **ADR-016** | §12.1 | US-005, US-102 | `test_generation_and_ingestion_never_overlap` |
| Réservation par lot, non par ingestion | ADR-016 | — | US-005 | `test_ingestion_reserves_per_batch_not_per_run` |
| Réentrance : le rédacteur interroge le RAG | ADR-016 | §5.4 | US-102 | `test_reservation_is_reentrant_within_a_task` |
| Pagination obligatoire sur chaque chunk | — | §7.1 | US-102 | `test_chunk_has_page_start_and_page_end` |
| Bibliographie écartée de la rédaction | — | §5.5 | US-102 | `test_references_section_marked_and_excluded` |
| Aucune tentative d'OCR | — | §7.1 | US-102 | `test_extract_scanned_pdf_raises_actionable_error` |
