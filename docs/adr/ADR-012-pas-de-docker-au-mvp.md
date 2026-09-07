# ADR-012 — Pas de Docker au MVP

- **Statut :** Accepté
- **Date :** 2026-09-05
- **Lié à :** ADR-005

## Contexte

Le public visé — doctorants, chercheurs, ingénieurs — dispose souvent d'un poste
géré par une DSI, sans droits d'administration. Docker Desktop sous Windows exige
WSL2 ou Hyper-V, des privilèges élevés, et complique l'accès au GPU. Faire de
Docker un prérequis d'installation exclurait une part significative des
utilisateurs cibles.

## Décision

**Aucune dépendance à Docker pour installer et exécuter le MVP.**

- Backend : environnement virtuel Python, installé par script.
- Frontend : Node et Angular CLI.
- LLM : Ollama installé nativement.
- Base : SQLite embarquée, aucun service.
- Isolation du code : WebAssembly et primitives natives de l'OS (ADR-005).

Conséquence pour l'agent de codage : ni `Dockerfile`, ni `docker-compose.yml`, ni
instruction d'installation supposant Docker.

## Options écartées

| Option | Motif |
|---|---|
| Docker obligatoire | Exclut les postes sans droits d'administration |
| Docker optionnel dès le MVP | Deux chemins d'installation à tester en parallèle |
| WSL2 obligatoire | Windows uniquement ; même problème de droits |

## Conséquences

L'isolation du code repose sur Wasm et les primitives de l'OS, moins fortes que
des conteneurs — d'où la conception à deux niveaux d'ADR-005. Les scripts
d'installation doivent exister en `.sh` **et** en `.ps1`, et rester testés
symétriquement.

Docker reste envisageable **après** le MVP, comme mode d'exécution optionnel pour
les laboratoires disposant de l'infrastructure, notamment pour US-CALC-001
(OpenFOAM, Serpent).

## Vérification

`scripts/setup_local.sh` et `scripts/setup_local.ps1` fonctionnent sur une machine
vierge · absence de `Dockerfile` et de `docker-compose.yml` contrôlée en CI
