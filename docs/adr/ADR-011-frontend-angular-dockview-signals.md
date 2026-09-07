# ADR-011 — Frontend Angular avec Dockview et Signals

- **Statut :** Accepté
- **Date :** 2026-09-05

## Contexte

Le frontend Angular est une contrainte d'entrée du projet. L'interface visée est
un IDE : panneaux redimensionnables et détachables (plan, éditeur, sources, code,
console, relecture), état partagé et fortement réactif, flux de tokens en
streaming pendant la génération.

## Décision

1. **Angular standalone components**, sans NgModules.
2. **Signals** comme mécanisme d'état par défaut ; RxJS conservé pour les flux
   véritables (streaming SSE, WebSocket).
3. **Dockview** pour la disposition en panneaux dockables, persistée par projet.
4. **Monaco Editor** pour le code, éditeur dédié pour le `.qmd` (ADR-006).
5. Aucun état métier dans les composants : un `store` par domaine, exposé en
   signaux en lecture seule.

## Options écartées

| Option | Motif |
|---|---|
| NgRx | Cérémonie disproportionnée pour un mono-utilisateur local |
| Golden Layout | Maintenance irrégulière, intégration Angular fragile |
| Grille CSS maison | Redimensionnement et détachement à réimplémenter |
| RxJS partout | Signals plus simples et plus performants pour l'état synchrone |

## Conséquences

La disposition des panneaux fait partie de l'état de projet et doit être versionnée
avec lui. Le streaming de tokens exige un pont SSE → signal soigneusement testé :
c'est le point d'intégration le plus fragile du frontend.

## Vérification

`test_layout_initialization` · `test_layout_persisted_per_project` ·
`test_signal_state_update` · `test_sse_stream_to_signal_bridge`
