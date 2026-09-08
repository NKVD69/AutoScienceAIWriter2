# ADR-016 — Rédaction et ingestion sérialisées sur le CPU

- **Statut :** Accepté
- **Date :** 2026-09-08
- **Décideurs :** porteur de projet
- **Lié à :** ADR-013, ADR-015, ADR-003

## Contexte

Deux décisions antérieures, bonnes séparément, se révèlent en conflit une fois
réunies sur la même machine.

- **ADR-013** place les embeddings sur CPU, pour qu'ils ne disputent pas la
  VRAM au modèle de rédaction. Objectif atteint : mesuré à **+0 Mo de VRAM**
  sur 500 textes.
- **ADR-015** fait délibérément déverser le modèle de rédaction sur le CPU et
  la RAM, la qualité du modèle primant sur le temps de génération.

Le premier évite le GPU en allant sur le CPU ; le second quitte le GPU pour
aller sur le CPU. **Ils s'y rencontrent.**

Relevé du 8 septembre 2026, `check_embeddings_cpu.py`, 500 textes d'environ
512 tokens, modèle de rédaction résident et actif :

| Threads d'embedding | Durée | Débit |
|---|---|---|
| 8 | 429,6 s | 1,2 texte/s |
| 32 | 348,2 s | 1,4 texte/s |

Budget de §12.2 : 180 s. **Quadrupler les threads ne récupère que 19 %** : le
goulot n'est pas le parallélisme, c'est la contention. Le processus
d'inférence détenait alors 34,7 Go de RAM et plus d'un million de secondes
CPU cumulées.

## Décision

1. **Rédaction et ingestion ne s'exécutent jamais simultanément.** Un arbitre
   applicatif unique, `app/core/compute.py`, porte l'exclusion mutuelle.
2. **La granularité fait la politique.** Une génération réserve pour toute sa
   durée ; l'ingestion réserve **par lot d'embeddings**. Une demande de
   rédaction n'attend donc qu'un lot — quelques secondes — tandis que
   l'ingestion attend la fin d'une génération.
3. **La réservation est réentrante par tâche.** L'agent rédacteur interroge le
   RAG, qui vectorise la requête : sans réentrance, il attendrait un verrou
   qu'il détient lui-même.
4. **L'attente est mesurée et journalisée** par charge. Une ingestion qui
   n'avance plus doit être lisible comme telle, non confondue avec une panne.

## Options écartées

| Option | Motif du rejet |
|---|---|
| Ne rien faire, accepter la contention | L'ingestion double de durée et ralentit la rédaction en retour : les deux charges se dégradent mutuellement |
| Réserver pour toute la durée de l'ingestion | Une demande de rédaction attendrait l'ingestion de 200 PDF. L'ingestion est une tâche de fond, la rédaction est ce que l'utilisateur regarde |
| Priorité par file d'attente explicite | Complexité sans gain : la granularité par lot produit déjà la bonne priorité |
| Réduire les threads d'embedding | Ne résout rien — la mesure montre que le parallélisme n'est pas le facteur |
| Repasser les embeddings sur GPU | Contredit ADR-013, dont la mesure confirme par ailleurs le bien-fondé |

## Conséquences

**Positives.** Chaque charge s'exécute sur une machine qu'elle ne partage pas.
L'ingestion redevient prévisible, la rédaction cesse d'être ralentie par elle.
Le temps d'attente par charge est observable, donc affichable (US-DASH-001).

**Négatives.**

- **Le débit total baisse** quand les deux charges sont demandées ensemble :
  sérialiser, c'est renoncer au recouvrement. C'est le prix assumé, la mesure
  montrant que le recouvrement coûtait plus qu'il ne rapportait.
- **L'ingestion peut attendre longtemps.** Une génération dure une vingtaine
  de minutes (ADR-015) ; une ingestion lancée pendant ce temps ne démarre
  qu'après. L'interface doit le dire, faute de quoi l'utilisateur conclura à
  un blocage.
- **Un troisième consommateur de CPU devra rejoindre l'arbitre.** L'agent code
  (US-401) exécutera du calcul utilisateur : il relève du même arbitrage, et
  l'oublier réintroduirait exactement le défaut corrigé ici.

**Sur le code.** Toute charge de calcul lourde passe par
`get_arbiter().reserve(...)`. Un module qui consommerait le CPU sans réserver
serait un défaut, non une optimisation.

## Vérification

`test_generation_and_ingestion_never_overlap` ·
`test_reservation_is_reentrant_within_a_task` ·
`test_ingestion_reserves_per_batch_not_per_run` ·
`scripts/check_embeddings_cpu.py` — dont le verdict, sur une machine où une
génération tourne, doit devenir stable plutôt que dégradé.

---
> **Règle pour l'IA de codage :** un ADR au statut *Accepté* n'est pas rediscutable
> pendant l'implémentation. Une objection se consigne en section « Réserves » du
> rapport de story, jamais par une déviation silencieuse du code.
