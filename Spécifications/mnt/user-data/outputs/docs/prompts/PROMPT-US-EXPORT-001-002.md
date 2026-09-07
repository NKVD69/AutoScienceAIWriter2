# Prompt d'implémentation — US-EXPORT-001 et US-EXPORT-002 (éléments documentaires et modèle académique)

Livrées ensemble : les éléments documentaires se paramètrent dans le modèle.
Conforme au Backlog V0.3, aux Spécifications V0.3 §9.4 et à ADR-006.

---

```text
Tu es un agent de codage senior, spécialisé en composition de documents
académiques, Quarto, LaTeX et gabarits paramétrables.

SOURCES DE VÉRITÉ : ce prompt > ADR-006 (Quarto canonique), ADR-007
(bibliographie générée) > Backlog V0.3 US-EXPORT-001 et US-EXPORT-002 >
Spécifications V0.3 §9.4.

DÉPENDANCES : US-502 (export), US-501 (bibliographie), US-401 (artefacts).

USER STORIES

US-EXPORT-001 — Produire table des matières, listes de figures et de
tableaux, glossaire, index et annexes, afin que le document réponde aux
attentes formelles d'un jury.

US-EXPORT-002 — Paramétrer le modèle académique, afin de respecter les
exigences de mise en forme de mon établissement.

CE QUI REND CETTE STORY MOINS SIMPLE QU'ELLE N'EN A L'AIR

Les exigences de mise en forme des établissements sont arbitraires et
contradictoires entre eux : interligne, marges, page de garde, position du
résumé, style de citation, numérotation. Il n'existe pas de modèle
universel.

Tu ne tentes donc PAS de couvrir tous les cas par des options. Tu fournis un
modèle par défaut correct et un mécanisme d'ÉCHAPPEMENT : l'utilisateur peut
fournir son propre gabarit LaTeX et son propre CSL. Un système d'options
exhaustif serait ingérable et toujours insuffisant.

PÉRIMÈTRE

Éléments documentaires, glossaire, index, gabarit par défaut paramétrable,
mécanisme de gabarit personnalisé, validation d'un gabarit fourni.

PAS de bibliothèque de modèles par université. PAS d'éditeur visuel de
gabarit.

FICHIERS

- backend/app/export/front_matter.py
- backend/app/export/glossary.py
- backend/app/export/index_terms.py
- backend/app/export/template_manager.py
- backend/app/export/templates/default/           (gabarit complet)
- backend/app/models/glossary.py
- backend/app/api/v1/glossary.py
- backend/app/api/v1/export.py                    (modification)
- frontend/src/app/features/export/template-settings.component.ts
- backend/tests/tests_export/
Aucun autre fichier.

EXIGENCES — US-EXPORT-001

1. Éléments produits par Quarto lui-même quand c'est possible : table des
   matières, listes de figures et de tableaux relèvent de la configuration
   `toc`, `lof`, `lot` de _quarto.yml. Ne les réimplémente pas.

2. GLOSSAIRE. Table glossary_entry(project_id, term, definition, acronym).
   - saisie manuelle, plus détection assistée : un sigle en majuscules
     apparaissant plus de deux fois et absent du glossaire est PROPOSÉ,
     jamais ajouté automatiquement ;
   - à l'export, première occurrence d'un terme du glossaire développée
     en note, occurrences suivantes inchangées ;
   - tri alphabétique, sigles regroupés à part.

3. INDEX. Termes déclarés par l'utilisateur, plus les entrées du glossaire.
   Génération via `makeindex` dans la chaîne LaTeX pour le PDF ; pour DOCX
   et HTML, l'index est OMIS avec une mention explicite dans le rapport
   d'export — Pandoc ne le produit pas, et simuler un index par des ancres
   donnerait un résultat inutilisable. Dire ce qu'on ne fait pas vaut mieux
   que le faire mal.

4. ANNEXES. Numérotation alphabétique séparée, remise à zéro des compteurs
   de figures et tableaux avec préfixe de lettre (Figure A.1). La
   déclaration d'usage de l'IA (US-EXPORT-003) est la dernière annexe.

EXIGENCES — US-EXPORT-002

5. Gabarit par défaut, paramétrable par un jeu FERMÉ d'options :
   police et corps, interligne, marges, recto ou recto-verso, position du
   résumé, langue des libellés, style de citation parmi une liste de CSL
   embarqués (APA, IEEE, Chicago, Vancouver, ISO 690), page de garde
   renseignée par des champs.

6. GABARIT PERSONNALISÉ. L'utilisateur dépose un gabarit LaTeX et,
   optionnellement, un CSL. Le gestionnaire :
   - vérifie la présence des variables Pandoc requises ($body$, $title$,
     $bibliography$…) et échoue en nommant celles qui manquent, AVANT toute
     tentative de compilation ;
   - compile un document de test d'une page pour valider le gabarit, et
     conserve le journal en cas d'échec ;
   - n'exécute JAMAIS le gabarit en dehors de la chaîne LaTeX normale et
     n'accorde aucun privilège supplémentaire.

7. Le choix du modèle est un attribut du PROJET, versionné : changer de
   modèle ne doit pas invalider un export archivé.

8. Tests

   test_toc_lof_lot_delegated_to_quarto
   test_acronym_proposed_not_auto_added
   test_glossary_first_occurrence_expanded
   test_glossary_acronyms_grouped
   test_index_generated_for_pdf
   test_index_omitted_for_docx_with_report_mention
   test_appendix_numbering_resets_with_letter_prefix
   test_ai_declaration_is_last_appendix
   test_default_template_options_closed_set
   test_csl_list_embedded
   test_custom_template_missing_variable_fails_before_compile
   test_custom_template_validated_by_one_page_build
   test_custom_template_failure_keeps_log
   test_template_choice_versioned_per_project
   test_archived_export_unaffected_by_template_change

INTERDICTIONS

- Réimplémenter la table des matières ou les listes de figures.
- Simuler un index en DOCX ou HTML.
- Ajouter automatiquement un sigle au glossaire.
- Accorder un privilège d'exécution à un gabarit fourni.
- Construire une bibliothèque de modèles par établissement.

ACCEPTATION : pytest -q backend/tests/tests_export ; ruff ;
python scripts/check_quarto_export.py

FORMAT : plan 10 lignes, fichiers complets, sorties, "Réserves".
```
