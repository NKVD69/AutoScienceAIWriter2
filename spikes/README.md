# Phase 1 — Spikes de dérisquage

Quatre expériences à mener **avant d'engager la moindre user story**. Chacune
teste une hypothèse dont dépend une décision d'architecture déjà figée. Un
échec n'est pas un bug à corriger : c'est un ADR à rouvrir.

## Pourquoi cette phase

Le dossier de conception compte soixante-deux fichiers pour zéro ligne de code
exécutée. Quatre hypothèses le portent sans avoir jamais été vérifiées sur une
machine réelle. Une semaine de spikes coûte moins qu'un mois d'implémentation
sur une fondation fausse.

| Spike | Hypothèse centrale | Décision en jeu | Si l'hypothèse tombe |
|---|---|---|---|
| 01 | sqlite-vec se charge, l'intégrité tient, le KNN est rapide | ADR-002 | Retour à un moteur vectoriel séparé — la sauvegarde par copie de fichier est perdue |
| 02 | Un 7B quantifié produit du JSON conforme de façon fiable | ADR-003, ADR-008 | Modèle plus grand, grammaire contrainte, ou découpage des sorties structurées |
| 03 | Pyodide isole réellement et couvre la pile scientifique | ADR-005 | Le niveau 1 ne tient pas : reconcevoir l'isolation du code produit par les agents |
| 04 | Quarto compile 150 pages avec renvois résolus | ADR-006 | Changer de chaîne de publication — décision structurante pour tout l'export |

**Le spike 02 est le décisif.** Toute l'orchestration suppose des sorties
structurées fiables. C'est celui à mener en premier si le temps manque.

## Exécution

```bash
pip install -r requirements-spikes.txt
npm install                      # pour le spike 03
python run_all.py                # les quatre, avec rapport de synthèse
python run_all.py --only 2       # un seul
```

Codes de sortie : `0` conforme · `1` non conforme · `2` non mesuré, dépendance
absente. Un `2` n'est pas un échec de conception : c'est une mesure à refaire
sur un poste représentatif.

## Prérequis par spike

| Spike | Prérequis |
|---|---|
| 01 | `pip install sqlite-vec` |
| 02 | Moteur démarré (`lms server start`, ou `ollama serve`) · modèle installé · `pip install httpx pydantic` |
| 03 | Node ≥ 20 · `npm i pyodide` |
| 04 | `quarto` ≥ 1.4 dans le PATH · `quarto install tinytex` |

Le spike 02 doit être exécuté sur la **machine cible**, avec sa mémoire réelle.

Depuis ADR-014, il vise LM Studio par défaut : `--backend lmstudio|ollama`.
Depuis ADR-015, son délai par requête est de 3 600 s et non de 300 s — sur un
modèle qui déverse sur CPU, une génération de plan dépasse la demi-heure, et
l'ancien délai faisait expirer le harnais sur son propre préchauffage avant
toute mesure. `--n` et `--modes` permettent de borner le coût d'une série.
Mené sur un poste mieux doté, il ne mesure rien d'utile.

## Critères de décision

Ce ne sont pas des seuils indicatifs. Ils déclenchent une action.

| Mesure | Seuil | Action si non tenu |
|---|---|---|
| Conformité Pydantic après 3 essais (spike 02) | ≥ 95 % | Sous 90 % : rouvrir ADR-003. Ne PAS ajuster les prompts à la marge |
| `load_duration` p95 après la 1re requête | < 50 ms | Vérifier `keep_alive=-1` avant de conclure |
| KNN p95 sur 50 000 chunks | < 200 ms | Rouvrir ADR-002 |
| Isolation réseau Pyodide | totale | Rouvrir ADR-005 : le niveau 1 ne peut plus être présenté comme garanti |
| Compilation PDF 150 pages | < 90 s | Acceptable jusqu'à 180 s ; au-delà, revoir la stratégie d'export |
| Renvoi cassé détectable dans le journal Quarto | oui | Sinon US-502 doit vérifier par analyse du `.qmd` assemblé |

## Ce que le spike 01 a déjà établi

Exécuté sur Python 3.12 / SQLite 3.45 / Linux, 50 000 chunks de dimension 768 :

- `REFERENCES` sur une table virtuelle `vec0` est **accepté à la création puis
  ignoré à l'insertion** — le défaut D-01 est confirmé empiriquement ;
- le schéma corrigé applique la contrainte, l'invariant `rowid` tient et la
  cascade par trigger fonctionne ;
- KNN p95 à 58 ms simple, 56 ms filtré — le budget de 200 ms est tenu avec une
  marge confortable ;
- **le fichier pèse 151 Mo pour 50 000 chunks**, soit environ 3 Ko par chunk.
  Une thèse de 300 sources produit un `.sqlite` de l'ordre de 150 Mo : la
  sauvegarde par copie reste praticable, mais ce chiffre doit figurer dans
  l'interface de sauvegarde.

Reste à confirmer sur Windows, où le support des extensions SQLite dépend de
la provenance de l'interpréteur Python.

## Ce que le spike 03 a déjà révélé

Deux constats préliminaires, obtenus dans un environnement sans accès CDN :

1. **Pyodide télécharge ses wheels scientifiques depuis un CDN par défaut.**
   C'est incompatible avec le mode local strict (ADR-010). La distribution
   complète doit être vendorisée et `indexURL` doit pointer sur le répertoire
   local. À vérifier explicitement, `PYODIDE_INDEX_URL` est prévu à cet effet.
2. **L'isolation réseau demande une vérification plus fine que prévu.** `urllib`
   échoue bien, mais `socket.connect` n'a pas levé d'exception. Un `connect()`
   qui ne lève pas ne prouve pas qu'une connexion existe — l'implémentation
   emscripten peut être un talon. Le spike exige désormais un aller-retour
   réel avant de conclure. Si une exfiltration s'avère possible, ADR-005 doit
   être rouvert : le niveau 1 ne pourrait plus être présenté comme garantissant
   l'isolation réseau, et c'est le fondement du traitement du code d'agent.

## Résultats déjà obtenus

Voir `RESULTATS.md`. Spike 01 conforme, spikes 03 et 04 partiels, spike 02 non
mesuré mais son instrument vérifié par `spike_02_selftest.py`.

Exécuter l'auto-test avant le spike 02 : il doit sortir 0.

## Livrable attendu

`rapport-spikes.md`, produit par `run_all.py`, plus une décision écrite par
hypothèse non tenue : ADR amendé ou ADR rouvert. Aucune story n'est engagée
avant que les quatre spikes portent un verdict.
