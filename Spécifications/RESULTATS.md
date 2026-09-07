# Résultats de spikes — état au 7 septembre 2026

| Spike | Objet | Verdict | Où |
|---|---|---|---|
| 01 | sqlite-vec : intégrité et performance | **Conforme, 10/10** | Linux · Python 3.12 · SQLite 3.45 |
| 02 | Sortie structurée d'un 7B local | **Non mesuré** — instrument validé | À exécuter sur la machine cible |
| 03 | Pyodide : isolation et paquets | **Partiel** — deux constats bloquants | Linux · node 22 · sans accès CDN |
| 04 | Quarto : document long | **Partiel** — DOCX et HTML conformes, PDF non mesurable | Linux · Quarto 1.8.25 · TeX Live incomplet |

---

## Spike 01 — conforme

Voir `README.md`. Dix critères sur dix, à 50 000 chunks. Le point saillant reste
la taille du fichier : **151 Mo pour 50 000 chunks**, soit environ 3 Ko par
chunk. À confirmer sous Windows, où le support des extensions SQLite dépend de
la provenance de l'interpréteur Python.

---

## Spike 02 — non mesuré, mais l'instrument est vérifié

Ce spike exige Ollama, un modèle 7B quantifié et la VRAM de la machine cible.
Exécuté ailleurs, il ne mesure rien d'utile.

**Ce qui a été fait à la place.** `spike_02_selftest.py` soumet l'extracteur JSON
et les validateurs Pydantic à quatorze cas dont la conformité est connue
d'avance : JSON nu, entouré de prose, en bloc Markdown, accolade dans une
chaîne, guillemet échappé, objet non refermé, problématique trop courte, moins
de quatre chapitres, titres frères dupliqués, sous-section unique, `target_words`
hors bornes, plan plat, aucune question de recherche.

**Résultat : 14 cas sur 14 correctement classés.**

Ce n'est pas une formalité. Le spike 02 produit un taux de conformité ; si son
harnais était fautif, il mesurerait la qualité de son propre code et un mauvais
score serait imputé au modèle à tort. L'auto-test établit qu'un échec observé
pendant le spike sera imputable au 7B.

**Exécuter dans cet ordre :**

```bash
python spike_02_selftest.py        # doit sortir 0 avant tout
python spike_02_structured_output.py --n 15
```

---

## Spike 03 — deux constats, conclusion suspendue

1. **Pyodide télécharge ses wheels scientifiques depuis un CDN.** Incompatible
   avec ADR-010. La distribution doit être vendorisée localement et `indexURL`
   pointé dessus. La variable `PYODIDE_INDEX_URL` est prévue pour le vérifier.
2. **L'isolation réseau demande une vérification plus fine que prévu.** `urllib`
   échoue, mais `socket.connect` n'a pas levé d'exception. La sonde exige
   désormais un aller-retour réel avant de conclure : un `connect()` qui réussit
   ne prouve pas qu'une connexion existe.

Le système de fichiers, lui, est bien isolé : `/etc/passwd` et un fichier hôte
arbitraire sont inaccessibles, et un montage explicite fonctionne. Reste à
trancher le point 2 sur une distribution vendorisée complète.

---

## Spike 04 — DOCX et HTML conformes, PDF non mesurable ici

Quarto 1.8.25 installé, projet de 12 chapitres généré : 12 figures exécutables,
12 tableaux, 20 références, renvois `@fig-`, `@tbl-` et `@sec-` croisés entre
chapitres.

| Mesure | Résultat |
|---|---|
| Export DOCX | conforme · 46,9 s |
| Export HTML | conforme · 54,8 s |
| Export PDF | **non mesurable** — `lmodern.sty` absent, CTAN injoignable |
| Renvoi cassé détectable dans le journal | **conforme** |

**H4.7 est le résultat qui compte.** Un renvoi volontairement cassé — `@fig-inexistante`
et `[@refinexistante]` — laisse bien une trace exploitable dans le journal de
Quarto. L'analyse de journal prévue par US-502 est donc réalisable : elle n'a pas
besoin d'un repli par relecture du `.qmd` assemblé.

Les durées de 47 et 55 secondes portent sur 12 chapitres avec exécution de
12 blocs Python. Elles restent sous le budget de 90 s, mais sur une machine sans
GPU et sans cache de figures. Le PDF, plus coûteux, devra être mesuré séparément.

### Un défaut trouvé dans l'instrument lui-même

La première version cherchait `not found` dans le journal pour détecter un renvoi
non résolu. Sur cette distribution TeX Live incomplète, elle a compté
`File 'lmodern.sty' not found` comme un renvoi cassé : **un paquet LaTeX manquant
était rapporté comme un défaut du document.**

Le script distingue maintenant deux familles de motifs :

- `UNRESOLVED` — motifs propres à Pandoc et LaTeX pour les renvois et citations
  non résolus, ancrés sur `Reference \`x' on page N undefined` et équivalents ;
- `LATEX_MISSING` — paquet, police ou moteur absent, qui déclenche un verdict
  **non mesuré** (code 2) et non une non-conformité (code 1).

Un instrument qui confond une panne d'environnement avec une non-conformité est
pire qu'absent : il ferait rouvrir ADR-006 pour un `tlmgr install` manquant.

---

## Ce qui reste à faire, et où

| Mesure | Machine requise |
|---|---|
| Spike 02 en entier | Poste cible, 10 Go de VRAM, Ollama + qwen2.5 7B |
| Spike 01 sous Windows | Poste Windows, interpréteur Python de production |
| Spike 03, conclusion sur le réseau | Distribution Pyodide vendorisée complète |
| Spike 04, compilation PDF | `quarto install tinytex` ou TeX Live complet |

Trois d'entre elles peuvent être menées en une demi-journée sur le poste cible.
Le spike 02 est le seul qui puisse rouvrir un ADR structurant ; c'est celui à
faire en premier.
