# ADR-007 — Compilation dynamique du fichier .bib à l'export

- **Statut :** Accepté
- **Date :** 2026-09-05
- **Lié à :** ADR-006, ADR-010

## Contexte

Un `references.bib` fourni par l'utilisateur diverge inévitablement du contenu
réel du document : entrées orphelines, clés absentes, doublons issus de Zotero.
Dans un mémoire, une entrée bibliographique non citée ou une citation sans entrée
sont des défauts relevés en soutenance.

## Décision

**Le `.bib` est un artefact généré, jamais un fichier d'entrée.**

À chaque export, le service :

1. collecte les entrées `citation` **vérifiées** appartenant aux `draft_section`
   effectivement incluses dans le document exporté ;
2. résout chacune vers son `source_document` ;
3. génère un `.bib` propre, trié, sans doublon, avec des clés normalisées
   (`auteur_année_motclé`) ;
4. échoue l'export si une citation référence une source absente ou non approuvée ;
5. conserve le `.bib` généré comme artefact d'export daté.

Un `.bib` importé par l'utilisateur alimente `source_document` à l'import ; il ne
sert **jamais** directement à la compilation.

## Options écartées

| Option | Motif |
|---|---|
| `.bib` utilisateur direct | Divergence garantie avec le contenu réel |
| Fusion `.bib` utilisateur + généré | Doublons et conflits de clés |
| Bibliographie en base sans `.bib` | Quarto/`citeproc` attend un fichier |

## Conséquences

La bibliographie exportée est, par construction, exactement l'ensemble des
sources citées. Corollaire : une citation invalide **bloque l'export** — comportement
voulu, mais qui exige un message d'erreur pointant la section et le passage fautifs.

## Vérification

`test_bibtex_contains_only_cited` · `test_bibtex_no_duplicate_keys` ·
`test_export_fails_on_unverified_citation` · `test_bib_regenerated_each_export`
