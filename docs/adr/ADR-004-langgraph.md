# ADR-004 — Orchestration déterministe par LangGraph

- **Statut :** Accepté
- **Date :** 2026-09-05
- **Lié à :** ADR-003, ADR-008

## Contexte

Un système multi-agents peut être orchestré par conversation libre (les agents
s'échangent des messages jusqu'à convergence) ou par machine à états explicite.
La conversation libre accumule l'historique dans le contexte, consomme des tokens
de façon non bornée, sature le cache KV, et rend l'audit impossible : on ne peut
pas prouver *pourquoi* une section a été écrite ainsi.

Sur un modèle 7B local, la conversation libre échoue de surcroît fréquemment à
converger.

## Décision

**LangGraph, machine à états explicite.** Chaque nœud reçoit un état typé et
retourne un état typé. Les transitions sont déterministes et déclarées. Aucun
agent ne décide seul du prochain agent.

États principaux du graphe :

```
PROJECT_CREATED → SOURCES_INGESTING → SOURCES_READY
 → PLAN_DRAFTING → PLAN_REVIEW → PLAN_VALIDATED
 → SECTION_DRAFTING → SECTION_GUARDRAIL → SECTION_REVIEWING
 → SECTION_CORRECTING ⇄ SECTION_REVIEWING
 → SECTION_VALIDATED → ... → DOCUMENT_READY → EXPORTING → EXPORTED
                                            ↘ ERROR_STATE
```

Règles :

- l'état est persisté dans `task` à chaque transition, jamais seulement en mémoire ;
- chaque transition produit une entrée d'audit (ADR-009) ;
- `PLAN_VALIDATED` et `SECTION_VALIDATED` exigent une **action humaine** ;
- un nœud ne reçoit que le strict nécessaire, jamais l'historique complet.

## Options écartées

| Option | Motif |
|---|---|
| AutoGen en discussion libre | Contexte non borné, VRAM et tokens ingérables |
| CrewAI | Abstraction élevée, contrôle fin des transitions difficile |
| Orchestrateur maison | Réécriture de la persistance d'état et des reprises sur erreur |

## Conséquences

Reprise après crash possible : l'état est en base. Auditabilité complète.
En contrepartie, toute nouvelle capacité exige d'ajouter explicitement un nœud et
ses transitions — c'est le but.

## Vérification

`test_transition_persisted` · `test_human_gate_blocks_without_validation` ·
`test_resume_after_restart`
