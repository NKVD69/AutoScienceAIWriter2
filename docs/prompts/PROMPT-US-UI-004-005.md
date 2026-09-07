# Prompt d'implémentation — US-UI-004 et US-UI-005 (versions, comparaison, commentaires)

Priorité P2. Livrées ensemble : la comparaison et les annotations partagent le même modèle d'ancrage dans le texte.
Conforme au Backlog V0.3 et aux ADR-011, ADR-009.

---

```text
Tu es un agent de codage senior, spécialisé en comparaison de documents,
ancrage d'annotations dans du texte modifiable et interfaces de revue
collaborative.

SOURCES DE VÉRITÉ : ce prompt > ADR-011 (Angular, Signals), ADR-009
(audit), ADR-006 (Quarto) > Backlog V0.3 US-UI-004 et US-UI-005.

DÉPENDANCES : US-801, US-UI-002, US-301, US-AUTH-001 (rôles).

USER STORIES

US-UI-004 — Comparer deux versions d'une section, afin de voir ce qu'une
régénération ou une relecture a changé.

US-UI-005 — Recevoir et traiter les commentaires de mon directeur de
recherche dans le document, afin de ne pas les gérer par courriel.

LE PROBLÈME COMMUN — L'ANCRAGE

Un commentaire pointe un passage. Le passage bouge, se réécrit, disparaît.
Un ancrage par position de caractère est cassé au premier mot ajouté avant
lui ; un ancrage par citation exacte est cassé à la première reformulation.

Solution retenue : ancrage à trois niveaux, éprouvé par les outils
d'annotation web —
  1. position de caractère, rapide ;
  2. contexte, trente caractères avant et après ;
  3. citation exacte du passage.
À la réouverture, on tente 1, on vérifie par 2 et 3, et à défaut on
recherche 3 dans le document. Si rien ne correspond, le commentaire devient
ORPHELIN : conservé, affiché à part, jamais supprimé.

Ne jamais supprimer un commentaire dont l'ancre est perdue. Un directeur de
recherche qui voit ses remarques disparaître cesse d'utiliser l'outil.

PÉRIMÈTRE

Modèle d'ancrage partagé, stockage des versions, comparaison, fil de
commentaires, résolution, orphelins.

PAS d'édition simultanée à plusieurs : l'application est mono-poste ;
prétendre au temps réel collaboratif serait hors sujet.

FICHIERS

- backend/app/models/annotation.py
- backend/app/services/annotation_service.py
- backend/app/services/version_service.py
- backend/app/api/v1/annotations.py
- backend/app/db/schema.sql                     (modification)
- frontend/src/app/features/editor/diff-view.component.ts
- frontend/src/app/features/editor/comment-thread.component.ts
- frontend/src/app/features/editor/anchor.ts
- backend/tests/tests_annotations/
Aucun autre fichier.

EXIGENCES

1. VERSIONS. draft_section conserve déjà un numéro de version (US-301). Cette
   story ajoute la table section_version stockant le content_qmd complet de
   chaque version, avec son origine : génération, correction automatique,
   édition manuelle.

   Stockage intégral et non différentiel : une section pèse quelques
   kilooctets et le stockage différentiel introduirait une complexité et un
   risque de corruption sans bénéfice mesurable à cette échelle.

   Rétention : les vingt dernières versions, plus toutes les versions
   validées, conservées indéfiniment.

2. COMPARAISON. Diff au niveau du MOT, pas de la ligne : le Quarto d'un
   mémoire contient des paragraphes d'une seule ligne très longue, et un
   diff par ligne y afficherait « tout a changé » pour un mot corrigé.

   Trois vues : côte à côte, unifiée, et surlignage sur le texte courant.
   Les blocs de code et les mathématiques sont comparés comme du texte
   brut, sans normalisation.

3. COMMENTAIRES. Table annotation(id, section_id, author_user_id,
   anchor_json, body, created_at, resolved_at, parent_id).

   - fil de discussion par parent_id, deux niveaux au maximum ;
   - résolution par l'AUTEUR de la section uniquement : un SUPERVISOR
     commente, il ne clôt pas ; sinon le fil peut être fermé sans que
     l'auteur l'ait vu ;
   - un commentaire résolu reste consultable, il n'est pas supprimé ;
   - la suppression est réservée à son propre auteur.

4. ORPHELINS. À l'ouverture d'une section, le service tente le
   réancrage des commentaires. Ceux qui échouent sont marqués orphelins,
   regroupés dans un volet dédié avec la citation d'origine, et peuvent être
   réancrés manuellement par sélection d'un passage.

5. AUDIT. Création, résolution et suppression d'un commentaire sont
   journalisées avec leur auteur. La comparaison, opération de lecture, ne
   l'est pas.

6. Tests

   test_versions_stored_in_full_not_diff
   test_retention_keeps_twenty_plus_validated
   test_diff_at_word_level
   test_long_paragraph_diff_not_whole_line
   test_code_block_diff_raw

   test_anchor_resolves_by_position_when_unchanged
   test_anchor_resolves_by_context_after_insertion
   test_anchor_resolves_by_quote_after_reflow
   test_anchor_failure_marks_orphan_not_delete
   test_orphan_manually_reanchorable

   test_thread_max_two_levels
   test_supervisor_cannot_resolve
   test_author_can_resolve
   test_resolved_comment_still_readable
   test_delete_restricted_to_own_author
   test_annotation_events_audited

INTERDICTIONS

- Supprimer un commentaire dont l'ancre est perdue.
- Permettre à un SUPERVISOR de résoudre un fil.
- Diff au niveau de la ligne.
- Stockage différentiel des versions.
- Édition simultanée à plusieurs.

ACCEPTATION : pytest -q ; ruff ; ng test --watch=false ; ng lint

FORMAT : plan 10 lignes, fichiers complets, sorties, "Réserves".
```
