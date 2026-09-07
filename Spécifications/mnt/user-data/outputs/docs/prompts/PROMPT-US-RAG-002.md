# Prompt d'implémentation — US-RAG-002 (filtres avancés du RAG)

Conforme au Backlog V0.3, aux Spécifications V0.3 §7.2 et à ADR-002.

---

```text
Tu es un agent de codage senior, spécialisé en recherche vectorielle,
optimisation de requêtes SQL et interfaces de filtrage.

SOURCES DE VÉRITÉ : ce prompt > ADR-002 (sqlite-vec, KNN avant jointure) >
Backlog V0.3 US-RAG-002 > Spécifications V0.3 §7.2.

DÉPENDANCES : US-102 (retriever), US-002 (search_similar_chunks).

USER STORY

US-RAG-002 — En tant que chercheur, je veux restreindre la recherche à un
sous-ensemble de mes sources, afin qu'une section méthodologique ne
s'appuie pas sur des travaux périmés ou hors champ.

LA CONTRAINTE TECHNIQUE À RESPECTER

sqlite-vec applique le KNN AVANT toute jointure relationnelle. Un filtre
posé après le KNN ne restreint pas la recherche : il élague un résultat déjà
calculé. Sur k=5 avec un filtre écartant 80 % du corpus, on obtient une ou
deux lignes, pas cinq.

US-002 a posé la parade : élargissement interne de k par un facteur 4,
plafonné à 200, puis troncature après filtrage. Cette story rend le
mécanisme suffisant pour des filtres plus sélectifs, et EXPOSE le fait que
le résultat peut être incomplet plutôt que de le masquer.

PÉRIMÈTRE

Filtres additionnels, stratégie d'élargissement adaptative, signalement
d'incomplétude, filtres enregistrés par nœud de plan, interface de
filtrage.

PAS de reclassement par modèle. PAS de recherche hybride lexicale plus
vectorielle : ce serait un chantier distinct, à ouvrir seulement si le
rappel s'avère insuffisant en usage réel.

FICHIERS

- backend/app/rag/filters.py
- backend/app/rag/retriever.py                  (modification)
- backend/app/db/vector.py                      (modification)
- backend/app/models/plan.py                    (modification : filtres par nœud)
- backend/app/api/v1/sources.py                 (modification)
- frontend/src/app/features/sources/filter-bar.component.ts
- backend/tests/tests_rag/test_filters.py
Aucun autre fichier.

EXIGENCES

1. Filtres disponibles :
   année minimale et maximale ; langue ; type de publication ; exclusion des
   prépublications ; sources explicitement sélectionnées ou exclues par
   identifiant ; distance maximale ; exclusion des chunks de bibliographie,
   déjà par défaut.

   Tous s'expriment en SQL sur `chunk` et `source_document`, jamais en
   filtrage Python après récupération : filtrer en Python multiplierait le
   coût mémoire sans améliorer le rappel.

2. ÉLARGISSEMENT ADAPTATIF. Estimer d'abord la sélectivité du filtre par un
   COUNT sur `chunk` joint à `source_document` : proportion de chunks
   retenus. Le facteur d'élargissement devient max(4, ceil(1 / sélectivité))
   plafonné à 500 au lieu de 200.

   Motif : un facteur fixe de 4 est adapté à un filtre écartant un quart du
   corpus, pas à un filtre n'en retenant que 5 %.

3. SIGNALEMENT D'INCOMPLÉTUDE. Si, après élargissement au plafond, moins de
   k résultats satisfont le filtre, la réponse porte
   `truncated_by_filter: true` et le nombre réellement trouvé.

   L'agent rédacteur DOIT tenir compte de ce drapeau : moins de 3 chunks
   déclenche l'InsufficientContextError de US-301, comme pour une recherche
   non filtrée. Un filtre trop sévère ne doit pas produire une section peu
   sourcée sans avertissement.

4. FILTRES PAR NŒUD DE PLAN. Un nœud peut porter un jeu de filtres
   persistant, appliqué à chaque rédaction de sa section. Cas d'usage réel :
   restreindre l'état de l'art aux cinq dernières années, ou la
   méthodologie aux seules sources primaires.

   Les filtres du nœud sont hérités par ses enfants sauf redéfinition
   explicite. L'héritage est calculé à la lecture, pas dupliqué en base.

5. Performance : sur 50 000 chunks, une recherche filtrée doit rester sous
   400 ms — le double du budget non filtré, l'élargissement ayant un coût.
   Un test le mesure.

6. Tests

   test_filters_applied_in_sql_not_python
   test_selectivity_estimated_before_search
   test_expansion_factor_adapts_to_selectivity
   test_expansion_capped_at_500
   test_truncated_flag_when_fewer_than_k
   test_truncated_flag_reaches_writer_agent
   test_insufficient_context_raised_on_severe_filter
   test_node_filters_persisted
   test_node_filters_inherited_by_children
   test_child_override_replaces_not_merges
   test_inheritance_computed_at_read_time
   test_filtered_search_under_400ms_on_50k_chunks

INTERDICTIONS

- Filtrer en Python après récupération.
- Facteur d'élargissement fixe.
- Masquer un résultat incomplet.
- Dupliquer les filtres hérités en base.
- Implémenter un reclassement ou une recherche hybride.

ACCEPTATION : pytest -q backend/tests/tests_rag ; ruff ;
python scripts/check_sqlite_vec.py

FORMAT : plan 10 lignes, fichiers complets, sorties, "Réserves".
```
