# Prompt d'implémentation — US-JUP-001 (support des notebooks Jupyter)

Priorité P2. Conforme au Backlog V0.3 et aux ADR-005, ADR-006.

---

```text
Tu es un agent de codage senior, spécialisé en format .ipynb, exécution de
notebooks et intégration à une chaîne de publication.

SOURCES DE VÉRITÉ : ce prompt > ADR-005 (sandbox à deux niveaux), ADR-006
(Quarto canonique) > Backlog V0.3 US-JUP-001.

DÉPENDANCES : US-004, US-401, US-DATA-001, US-502.

USER STORY

US-JUP-001 — En tant que chercheur, je veux importer et exécuter mes
notebooks existants, afin de réutiliser des analyses déjà écrites sans les
réécrire en scripts.

DÉCISION PRÉALABLE — LE NOTEBOOK N'EST PAS UN FORMAT CANONIQUE

Quarto sait exécuter des blocs de code dans un .qmd (ADR-006). Il serait
tentant de faire du notebook un second format de document : ce serait
rouvrir le débat clos par ADR-006 et introduire un deuxième pivot.

Le notebook est donc traité comme une SOURCE D'ANALYSE, au même titre qu'un
script : on l'importe, on l'exécute, on récupère ses sorties comme
artefacts. On ne rédige jamais le mémoire dedans, et son Markdown n'entre
pas dans le document.

PÉRIMÈTRE

Import .ipynb, exécution cellule par cellule dans la sandbox, capture des
sorties, conversion des sorties en artefacts, export du notebook exécuté.

PAS d'éditeur de notebook interactif : ce serait réimplémenter JupyterLab.
PAS de kernel persistant exposé à l'utilisateur.

FICHIERS

- backend/app/notebooks/parser.py
- backend/app/notebooks/executor.py
- backend/app/notebooks/outputs.py
- backend/app/services/notebook_service.py
- backend/app/api/v1/notebooks.py
- frontend/src/app/features/code/notebook-view.component.ts
- backend/tests/tests_notebooks/
Aucun autre fichier.

EXIGENCES

1. Import : lecture du .ipynb au format nbformat 4. Les cellules Markdown
   sont conservées pour l'affichage et l'export du notebook, mais ne sont
   JAMAIS insérées dans le document du mémoire.

2. EXÉCUTION SANS KERNEL JUPYTER. Le code des cellules est concaténé et
   exécuté dans la sandbox existante (US-004), cellule par cellule, en
   conservant l'état entre cellules au sein d'une même exécution.

   Motif : ajouter ipykernel et un protocole ZeroMQ ferait entrer un
   deuxième mécanisme d'exécution, avec son propre modèle d'isolation,
   à côté de celui d'ADR-005. Une seule sandbox, un seul modèle de
   sécurité.

   Conséquence assumée, à documenter : les magies IPython (%matplotlib,
   !commandes shell, %%time) ne sont PAS supportées. Une magie rencontrée
   produit une erreur explicite nommant la ligne, pas un échec obscur.

3. Origine : un notebook importé par l'utilisateur relève de l'origine
   `user` ; le niveau par défaut reste 1 (Wasm), le niveau 2 est proposé
   sous consentement comme pour un script.

4. SORTIES. Chaque cellule produit : texte, erreurs, images, tableaux
   HTML. Les images sont converties en artefacts (US-401) avec leur code
   d'origine, la graine si déclarée et les SHA-256 des jeux de données.

   Une cellule sans graine déclarée et utilisant l'aléatoire produit un
   AVERTISSEMENT, non une erreur : contrairement au code écrit par l'agent,
   on ne peut pas exiger d'un notebook préexistant qu'il soit
   reproductible. On le signale.

5. Une exécution s'arrête à la première cellule en erreur, comme Jupyter en
   mode « exécuter tout ». Les cellules suivantes sont marquées non
   exécutées, pas en échec.

6. Export : le notebook exécuté peut être joint en annexe, converti en
   Quarto par `quarto convert`. C'est un document annexe, jamais une
   partie du corps.

7. Tests

   test_markdown_cells_never_enter_document
   test_state_preserved_between_cells
   test_no_ipykernel_dependency
   test_magic_command_raises_explicit_error_with_line
   test_shell_escape_rejected
   test_default_level_is_wasm
   test_images_converted_to_artifacts
   test_artifact_records_notebook_origin
   test_missing_seed_warns_not_fails
   test_execution_stops_at_first_error
   test_subsequent_cells_marked_not_executed
   test_export_as_appendix_only

INTERDICTIONS

- Ajouter un kernel Jupyter ou une dépendance ZeroMQ.
- Insérer le Markdown d'un notebook dans le mémoire.
- Supporter silencieusement les magies IPython.
- Faire du notebook un format de document.
- Exécuter un notebook hors de la sandbox d'ADR-005.

ACCEPTATION : pytest -q backend/tests/tests_notebooks ; ruff ;
python scripts/check_sandbox_linux.py

FORMAT : plan 10 lignes, fichiers complets, sorties, "Réserves".
```
