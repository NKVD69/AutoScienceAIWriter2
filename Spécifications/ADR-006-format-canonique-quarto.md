# ADR-006 — Quarto (.qmd) comme format canonique unique du document

- **Statut :** Accepté
- **Date :** 2026-09-05
- **Remplace :** « MyST canonique compilé via Quarto » (Spécifications V0.2)
- **Corrige :** défaut D-02

## Contexte

La V3 a écarté le Markdown standard, incapable de gérer les renvois croisés
numérotés d'un document de 300 pages. Les spécifications V0.2 ont retenu
« MyST Markdown comme format canonique, compilé vers Quarto puis vers PDF/DOCX ».

Cette chaîne **n'existe pas**. MyST et Quarto sont deux écosystèmes distincts :

| | MyST | Quarto |
|---|---|---|
| Moteur | Sphinx / Jupyter Book | Pandoc + binaire Quarto |
| Renvois | `{ref}`, `{numref}` | `@fig-`, `@tbl-`, `@eq-`, `@sec-` |
| Bibliographie | `sphinxcontrib-bibtex` | `citeproc` + CSL |
| Encadrés | `:::{note}` | `::: {.callout-note}` |

Écrire du MyST et le compiler avec Quarto produit des renvois non résolus et des
figures non numérotées — exactement ce que la V3 cherchait à éviter.

## Décision

**Le format canonique est Quarto Markdown (`.qmd`).**

1. Toute section rédigée est stockée en `.qmd` dans `draft_section.content_qmd`.
2. Les renvois croisés utilisent la syntaxe Quarto (`@fig-`, `@tbl-`, `@sec-`).
3. La bibliographie est rendue par `citeproc` à partir du `.bib` regénéré (ADR-007).
4. `_quarto.yml` est généré depuis les métadonnées du projet.
5. **MyST reste possible en export secondaire**, jamais en format pivot.
6. Le binaire Quarto et sa version minimale sont vérifiés au démarrage ; TinyTeX
   assure la chaîne LaTeX.

## Options écartées

| Option | Motif |
|---|---|
| MyST canonique | Pas de chaîne vers Quarto ; imposerait Sphinx et sa configuration |
| LaTeX natif | Édition hostile pour un non-spécialiste, aperçu live coûteux |
| Markdown standard + post-traitement | Renvois croisés à réimplémenter : la V3 l'avait déjà rejeté |
| Double format canonique | Deux syntaxes à valider, divergence garantie |

## Conséquences

Dépendance à un binaire externe (Quarto), à détecter et versionner — le seul écart
au principe « tout embarqué », assumé pour la qualité de l'export. L'éditeur du
frontend (US-UI-002) doit connaître la syntaxe Quarto, pas MyST. Les prompts de
l'agent rédacteur doivent produire du `.qmd` et sont validés par guardrail
(ADR-008).

## Vérification

`test_section_stored_as_qmd` · `test_no_myst_syntax_in_canonical_content` ·
`test_crossrefs_resolved_in_pdf` · `scripts/check_quarto_export.py`
