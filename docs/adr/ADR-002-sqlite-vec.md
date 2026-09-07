# ADR-002 — sqlite-vec plutôt que ChromaDB, avec séparation chunk / vec_chunk

- **Statut :** Accepté
- **Date :** 2026-09-05
- **Lié à :** ADR-001
- **Corrige :** défaut D-01 du Backlog V0.2

## Contexte

La V0.1 associait SQLite (relationnel) et ChromaDB embarqué (vectoriel). Deux
moteurs signifient deux formats de persistance, deux procédures de sauvegarde,
deux sources d'incohérence possible entre un document et ses vecteurs, et une
empreinte mémoire accrue sur une machine déjà contrainte à 10 Go de VRAM.

Le Backlog V0.2 a retenu `sqlite-vec` mais a spécifié une clé étrangère sur la
table virtuelle `vec0` — construction que **SQLite accepte syntaxiquement puis
ignore à l'exécution**, les contraintes de clé étrangère ne s'appliquant pas aux
tables virtuelles.

## Décision

1. **`sqlite-vec` remplace ChromaDB.** Vecteurs et relations dans le même fichier.
2. **Séparation stricte** entre intégrité et stockage vectoriel :
   - `chunk` : table relationnelle ordinaire, porteuse des clés étrangères ;
   - `vec_chunk` : table virtuelle `vec0` ne contenant que `embedding float[768]` ;
   - **invariant `vec_chunk.rowid == chunk.id`**, documenté et testé.
3. **Cascade par trigger.** `ON DELETE CASCADE` ne traverse pas une table
   virtuelle : un `AFTER DELETE ON chunk` supprime la ligne vectorielle.
4. **Aucune clause `REFERENCES` dans un `CREATE VIRTUAL TABLE`.**

## Options écartées

| Option | Motif du rejet |
|---|---|
| ChromaDB embarqué | Second moteur, sauvegarde en deux temps, désynchronisation possible |
| FAISS + index sur disque | Pas de persistance transactionnelle, pas de filtrage relationnel |
| LanceDB | Prometteur mais second format de fichier, même objection que Chroma |
| FK déclarée sur `vec0` | Ignorée silencieusement par SQLite — fausse sécurité |

## Conséquences

**Positives.** Sauvegarde atomique. Filtrage relationnel (année, langue, statut
prépublication) exprimable en SQL. Une seule dépendance vectorielle.

**Négatives.** `sqlite-vec` applique le KNN **avant** toute jointure : les filtres
relationnels s'appliquent en aval. Les requêtes filtrées doivent donc élargir `k`
en interne (facteur 4, plafond 200) puis tronquer. À très grand volume
(> 500 000 chunks) les performances devront être réévaluées ; hors périmètre pour
un corpus de thèse.

**Risque à couvrir.** Certains binaires SQLite livrés avec Python sont compilés
sans support des extensions. Le démarrage doit détecter ce cas et échouer avec un
message actionnable, jamais basculer silencieusement sur un repli.

## Vérification

`scripts/check_sqlite_vec.py` · `test_foreign_keys_enforced_on_chunk` ·
`test_rowid_invariant_chunk_vec` · `test_cascade_delete_source_removes_vectors` ·
`test_knn_with_year_filter`
