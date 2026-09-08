# Résultats de spikes — état au 7 septembre 2026

| Spike | Objet | Verdict | Où |
|---|---|---|---|
| 01 | sqlite-vec : intégrité et performance | **Conforme, 10/10** | Linux · Python 3.12 · SQLite 3.45 |
| 02 | Sortie structurée d'un 7B local | **Mesuré le 8 sept. 2026** — 4/4, voir plus bas | LM Studio · gemma-4-31b |
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

---

# Addendum — exécution sur poste Windows, 7 septembre 2026

Poste : Windows 11 · Python 3.11.15 (uv) · SQLite 3.53.1 · RTX 3080, 10 Go ·
Ollama présent, `qwen2.5:7b-instruct-q4_K_M` **absent**.

| Spike | Verdict Windows | Écart avec la mesure Linux |
|---|---|---|
| 01 | **Conforme, 10/10** | aucun |
| 02 | **Non mesuré** — modèle d'ADR-003 absent, substitut non concluant | — |
| 03 | non exécuté | — |
| 04 | non exécutable — Quarto absent du poste | — |

## Spike 01 — conforme sous Windows

Le point ouvert depuis la mesure Linux est levé : l'interpréteur Python de ce
poste supporte les extensions SQLite chargeables, et `sqlite-vec` v0.1.9 se
charge sans remédiation.

```
extension chargée · WAL actif · foreign_keys actif
FK sur vec0 ignorée (attendu) · FK appliquée sur chunk · invariant rowid
cascade relationnelle · cascade vectorielle par trigger
indexation 19,9 s · KNN simple p50 193,1 ms, p95 199,8 ms
                    KNN filtré p50 169,7 ms, p95 209,7 ms
fichier : 151 Mo pour 50 000 chunks
```

**Réserve : le KNN simple passe à 0,2 ms du budget.** `p95 = 199,8 ms` pour un
plafond de 200 ms n'est pas une marge, c'est une coïncidence. La cible de
§12.2 doit être considérée comme atteinte *à la limite* sur ce matériel, et
toute régression — chunk plus long, dimension supérieure, disque plus lent —
la fera basculer. À surveiller lors de US-102.

## Spike 02 — toujours non mesuré, et deux constats d'environnement

Le modèle exact d'ADR-003 n'est pas installé. Il n'a pas été téléchargé :
`model_download` est refusé par défaut (ADR-010), et une décision de
télécharger 5 Go revient à l'utilisateur. Le spike a été tenté contre
`qwen3.5:latest` (9,7 B, Q4_K_M) à titre indicatif ; il a expiré deux fois.

**Constat 1 — le chargement à froid d'un modèle prend 7 min 44 s sur ce
poste.** Mesuré au chronomètre sur une requête de préchauffage. Le spike
abandonne à 300 s : son délai n'est pas dimensionné pour cette machine.

Ce chiffre change la lecture d'ADR-003. La décision de modèle unique
persistant y était justifiée par une latence de swap « de plusieurs secondes
à plusieurs dizaines de secondes ». Ici, un déchargement coûte **près de huit
minutes** de réchauffage. La persistance n'est pas une optimisation de
confort : sans elle, l'outil est inutilisable sur ce poste.

**Constat 2 — changer `num_ctx` force un rechargement complet.** Le
préchauffage sans `num_ctx` puis une requête avec `num_ctx: 8192` provoque un
second chargement de sept minutes. Conséquence directe pour US-003 : la
valeur de `num_ctx` doit être **identique sur toutes les requêtes de tous les
agents**, au même titre que le prompt système. Une variation par agent
annulerait la persistance aussi sûrement qu'un `keep_alive=0`.

**Constat 3 — la VRAM est disputée.** Hors modèle chargé, `nvidia-smi`
rapporte déjà 8,7 Go occupés sur 10 Go par d'autres processus du poste. Le
budget de §12.1 — plafond observé < 9,0 Go — suppose une carte
essentiellement libre. Sur un poste de travail réel, l'hypothèse ne tient
pas, et une tentative de chargement dans ces conditions renvoie un 500
d'Ollama. À prendre en compte dans US-006.

## Ce qui reste à mesurer

| Mesure | Condition |
|---|---|
| Spike 02 en entier | `ollama pull qwen2.5:7b-instruct-q4_K_M`, délai du script porté au-delà de 600 s, GPU dégagé |
| Spike 03 | distribution Pyodide vendorisée complète |
| Spike 04 | Quarto + TinyTeX installés sur le poste |

---

# Spike 02 — exécuté le 8 septembre 2026

**Verdict : 4/4 hypothèses conformes.** Mais le nombre d'essais ne permet pas
de certifier le seuil d'ADR-008, et l'une des hypothèses n'est pas discriminée.
Les deux points sont détaillés plus bas ; les lire avant de conclure.

Poste : Windows 11 · LM Studio · `google/gemma-4-31b` Q8_0 résident ·
déversement CPU/RAM assumé (ADR-015).

## Résultats bruts

| Mode | n | 1er essai | 3 essais | Essais moyens | Durée par génération |
|---|---|---|---|---|---|
| prompt seul | 3 | **100 %** | 100 % | 1,00 | 1 150 à 1 381 s |
| schéma JSON contraint | 3 | **100 %** | 100 % | 1,00 | 1 217 à 1 351 s |
| *(essai isolé préalable, mode contraint)* | 1 | 100 % | 100 % | 1,00 | 1 453 s |

Débit stable à **2,3 tokens/s** dans les deux modes. Temps au premier token
p95 : 9,3 s en mode contraint, 9,7 s sans contrainte.

Sept générations, sept `PlanTree` conformes au premier essai — validateurs
Pydantic personnalisés compris : unicité des titres frères, profondeur
minimale, bornes de `target_words`.

## Ce qui est établi

**Le harnais fonctionne et son instrument est vérifié.** `spike_02_selftest.py`
classe 14 cas sur 14. Un échec observé serait imputable au modèle.

**Le modèle produit du JSON conforme au schéma, sans contrainte de décodage.**
C'est le résultat qui compte pour ADR-004 et ADR-008 : la machine à états n'a
pas besoin d'une grammaire contrainte pour fonctionner, et le circuit breaker à
trois essais n'a jamais eu à s'armer.

**La grammaire contrainte est disponible en secours.** LM Studio impose
`response_format: {"type": "json_schema"}` — il refuse le `json_object`
d'OpenAI — et accepte le schéma **récursif** de `PlanNode`. La porte de sortie
qu'envisageait ADR-008 (« grammaire contrainte plutôt qu'ajuster les prompts »)
est donc utilisable immédiatement, sans changer de moteur.

## Ce qui n'est PAS établi — à lire avant de citer ce spike

**Le seuil de 95 % d'ADR-008 n'est pas certifié.** Sept essais sans échec
placent le taux de succès à **≥ 65 % avec 95 % de confiance**, pas à 95 %.
L'écart n'est pas une nuance : c'est la différence entre « le circuit breaker
ne s'arme jamais » et « il s'arme une fois sur trois ».

| Essais sans échec | Taux de succès garanti (confiance 95 %) |
|---|---|
| 3 | ≥ 37 % |
| 7 | ≥ 65 % |
| **59** | **≥ 95 %** |

Certifier le seuil demande **59 générations sans échec**, soit environ **20 h**
sur un mode, 39 h sur les deux, à 20 minutes par génération. C'est un coût
d'exécution, pas un obstacle technique : la campagne peut tourner sans
surveillance.

**H2.5 n'est pas discriminée.** Le verdict « le mode JSON améliore le 1er
essai » est validé par une égalité — 100 % contre 100 % — et non par une
amélioration. Le mode contraint ne peut pas améliorer un taux déjà parfait.
L'hypothèse n'est ni confirmée ni infirmée : elle n'est pas testable tant que
le mode non contraint ne produit pas d'échec. Le comparatif ne redeviendra
informatif que sur un modèle plus faible, ou sur un schéma plus exigeant.

## Conséquences pour le dossier

| Décision | Effet |
|---|---|
| ADR-008 (guardrails, circuit breaker) | **Non rouvert.** Sa prémisse n'est pas démentie ; son seuil reste à certifier |
| ADR-004 (LangGraph déterministe) | Confortée : les sorties d'agent sont exploitables sans repli |
| ADR-003 / ADR-015 (modèle unique persistant) | H2.2 conforme — le modèle est resté résident sur toute la série |

**Ce que le spike coûte en usage réel.** Un plan complet demande environ
20 minutes de génération. Trois essais de circuit breaker en coûteraient une
heure. Ce n'est pas un tapis roulant tant que le premier essai passe — ce que
sept essais sur sept suggèrent sans le démontrer.

## Reste à mesurer

| Mesure | Commande |
|---|---|
| Certification du seuil de 95 % | `python spike_02_structured_output.py --n 59 --modes json` (~20 h) |
| Comparatif H2.5 sur un modèle plus faible | `--model google/gemma-4-e4b --n 20` |
| Spike 03 (Pyodide vendorisé) | distribution complète requise |
| Spike 04 (compilation PDF) | Quarto + TinyTeX à installer |
