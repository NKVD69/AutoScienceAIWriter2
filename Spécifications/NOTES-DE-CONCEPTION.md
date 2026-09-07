# Phase 3 — Notes de conception des maquettes

Trois maquettes HTML autonomes, ouvrables directement dans un navigateur.
Elles répondent aux seules questions d'interface que le dossier laissait
ouvertes. Ce ne sont pas des composants de production : elles fixent des
contrats d'interaction que US-801, US-UI-002 et US-302 devront honorer.

| Fichier | Question traitée |
|---|---|
| `01-tracabilite.html` | Comment remonter d'une phrase à sa source, puis à la page du PDF |
| `02-relecture.html` | Comment présenter la relecture sans que le score ressemble à un feu vert |
| `03-premier-lancement.html` | Que voit quelqu'un qui ouvre l'outil sur un projet vide |

---

## Direction visuelle

Le sujet n'est pas une application SaaS : c'est un instrument de travail utilisé
six heures d'affilée sur un manuscrit. Le vocabulaire visuel vient donc de là où
il existe déjà — les **marques de correction d'épreuves** et l'**apparat critique**
des éditions savantes.

**Couleur.** `--papier` #FBFAF7, `--encre` #1B2430, `--filet` #D8D6CF, et trois
teintes de correcteur : encre verte #2F6B54, crayon bleu #3A5FA8, rouge d'épreuve
#A6402E. Ambre #8A6410 pour les remarques majeures. Le crème + terracotta et le
noir + vert acide ont été écartés : ce sont des réflexes, pas des choix.

**Typographie.** Spectral pour la prose du manuscrit — empattements dessinés pour
l'écran, bon rendu des accents français, confort en lecture longue. Archivo pour
les commandes. Deux familles franchement distinctes, mesure limitée à 38 rem
(environ 66 caractères) dans les panneaux de texte.

**Le parti pris principal.** Le statut d'une affirmation est porté par un
**soulignement de correcteur**, pas par un surlignage coloré :

| Statut | Trait |
|---|---|
| sourcée | plein, encre verte |
| synthèse | pointillé, graphite |
| hypothèse | ondulé, crayon bleu |
| limite | tireté, rouge d'épreuve |

La forme du trait porte l'information, la couleur ne fait que la confirmer. Cela
règle l'accessibilité — aucune information par la seule couleur — et respecte la
lecture : un manuscrit annoté reste un manuscrit lisible, pas un surligneur
fluorescent. Toute la hardiesse du projet est dépensée là ; le reste est
délibérément silencieux.

---

## Maquette 1 — La boucle de traçabilité

C'est la promesse qui justifie l'existence de l'outil, et elle n'avait aucune
conception d'interface.

**Le trajet complet en un clic.** Phrase soulignée → panneau *Apparat* → source,
extrait exact avec les segments correspondants surlignés, page, numéro de
fragment, distance vectorielle, et l'action « Ouvrir le PDF page 6 ». Quatre
niveaux de preuve, sans quitter la page.

**Décisions qui engagent le développement :**

- **Une hypothèse ne porte jamais de citation.** Le panneau l'explique au lieu de
  laisser un vide : « lui en attacher une serait la présenter pour ce qu'elle
  n'est pas ». Cohérent avec le validateur Pydantic de US-301.
- **Le filtre « ne montrer que ce qui n'est pas sourcé »** estompe le texte sourcé
  au lieu de le masquer. Un doctorant relit ainsi ses points faibles en contexte.
  C'est la fonction la plus utile de l'écran et elle ne figurait nulle part dans
  le dossier.
- **Le marqueur de prépublication** apparaît dans l'apparat avec la mention qu'il
  n'est pas retirable et suit jusqu'à la bibliographie exportée.
- **Les synthèses proposent « Convertir en affirmation sourcée »**, ce qui relance
  une recherche RAG ciblée. Fonction absente du backlog : à arbitrer.
- Navigation clavier par flèches haut/bas d'une affirmation à la suivante.

**Contrat pour US-801 et US-UI-002.** L'éditeur doit exposer les `Claim` comme des
éléments focalisables portant `data-kind` et `data-id`, et le panneau d'apparat
consomme le `ChunkHit` complet — `page_start`, `distance`, `chunk_id` compris.
Ces champs existent déjà dans le contrat OpenAPI.

---

## Maquette 2 — L'écran de relecture

Le problème posé : comment afficher un score sans qu'il ressemble à une
autorisation.

**Aucune note globale.** Six barres pondérées, avec le coefficient visible en clair
(`Sourçage ×0,30`). Le sourçage est affiché en premier et en rouge à 41 : c'est
l'information qui doit sauter aux yeux. Une note unique de 68 sur 100 aurait été
lue comme un feu orange ; six mesures obligent à regarder laquelle est basse.

**Décisions qui engagent le développement :**

- **La porte de validation énonce l'engagement**, pas l'action : « Valider signifie
  que vous assumez le contenu de cette section ». Le bouton n'est jamais désactivé
  — la relecture conseille, elle n'autorise pas —, mais valider avec des remarques
  bloquantes ouvre une confirmation qui les nomme et rappelle qu'une citation non
  vérifiée bloquera l'export.
- **Les remarques et le texte sont liés dans les deux sens.** Cliquer une remarque
  cible le passage, cliquer un passage souligné révèle la remarque.
- **Le pied de panneau indique l'origine** : modèle, date, numéro de passage, et le
  nombre de remarques du passage précédent. Un relecteur qui trouve moins de
  remarques au second tour n'a pas forcément amélioré le texte.
- Les remarques mineures déjà traitées restent visibles, barrées et estompées.

**Contrat pour US-302.** La sévérité, la catégorie, l'extrait exact et la
proposition sont tous nécessaires à l'affichage. L'extrait doit être une
sous-chaîne exacte, sans quoi le lien vers le texte est impossible à établir — ce
que le validateur de US-302 impose déjà.

---

## Maquette 3 — Le premier lancement

Un écran vide est une invitation, pas une erreur.

**Trois étapes déverrouillées l'une après l'autre.** La numérotation est ici
justifiée : c'est réellement une séquence, et chaque étape est physiquement
inaccessible avant la précédente. L'étape 2 refuse d'avancer sous trois sources.

**Décisions qui engagent le développement :**

- **La phrase d'ouverture énonce la contrainte comme une promesse** : « Cet outil ne
  rédige rien qu'il ne puisse rattacher à un document que vous avez approuvé. »
  C'est la même règle que le garde-fou technique, dite à l'endroit où elle
  rassure au lieu de frustrer.
- **L'état de la machine est visible dès le bandeau** : modèle chargé et sa taille,
  version de Quarto, recherche en ligne désactivée. Alimenté par
  `/system/capabilities`. Une fonction absente se voit avant d'échouer.
- **Le bloc « Ce qui reste sur votre machine »** dit ce qui ne sort pas, nomme les
  trois exceptions et précise que chacune sera autorisée séparément. Placé au
  premier lancement, ce texte fait plus pour la confiance que n'importe quelle
  page de réglages.
- Le champ de problématique est facultatif et annonce qu'une problématique
  saisie sera reprise mot pour mot — exigence de US-PLAN-001, énoncée au moment
  où elle est utile.

---

## Ce qui reste à concevoir

Trois écrans suffisent pour débloquer le développement. Deux sujets peuvent
attendre, mais devront être traités avant la fin du frontend :

1. **La lisibilité en rédaction longue** — mode de rédaction plein cadre, sans
   panneaux, pour les sessions de six heures. Réglages de mesure, d'interligne et
   de contraste. Ce n'est pas un thème sombre : c'est une question de fatigue.
2. **L'écran d'erreur du graphe** — `ERROR_STATE` avec ses trois actions
   (réessayer, ignorer cette section, abandonner). La maquette 2 en pose le ton ;
   l'écran reste à dessiner.

## Ce que ces maquettes ne sont pas

Elles n'établissent pas de système de design, pas de bibliothèque de composants,
pas de grille formelle. Les jetons de couleur et de typographie qu'elles portent
suffisent à démarrer US-801 ; les formaliser maintenant reviendrait à figer des
choix avant le premier contact avec du code réel.
