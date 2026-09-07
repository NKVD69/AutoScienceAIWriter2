# ADR-009 — Journal d'audit à chaîne de hachage : détection d'altération

- **Statut :** Accepté
- **Date :** 2026-09-05
- **Lié à :** ADR-001, ADR-011
- **Corrige :** défaut D-07 (vocabulaire « immuable »)

## Contexte

L'intégrité académique est un argument central du produit : il faut pouvoir
établir quelles parties d'un mémoire ont été générées, à partir de quelles
sources, validées par qui et quand.

Les documents antérieurs qualifient ce journal d'« immuable ». C'est inexact et,
pour un outil vendu sur l'intégrité, l'inexactitude est coûteuse : le fichier
`.sqlite` appartient à l'utilisateur, qui peut réécrire la table et recalculer
l'intégralité de la chaîne. Une chaîne de hachage locale apporte une **détection
d'altération**, pas une **immuabilité**.

## Décision

1. Table `audit_log` **append-only par convention applicative** : une seule
   fonction d'insertion, aucun `UPDATE` ni `DELETE` dans le code.
2. Chaînage **SHA-256** : `hash = sha256(canonical(entry) || prev_hash)`.
3. Fonction de vérification retournant `VALIDE` / `ALTÉRÉ` avec l'index de la
   première incohérence.
4. **Vocabulaire imposé** dans le code, l'interface et la documentation :
   « journal à détection d'altération », « tamper-evident ». Les termes
   « immuable » et « infalsifiable » sont proscrits.
5. Évolution documentée, hors MVP : horodatage RFC 3161 d'un tiers pour ancrer
   périodiquement le dernier hash — seule voie vers une garantie opposable.

## Options écartées

| Option | Motif |
|---|---|
| Prétendre à l'immuabilité | Faux, et démontrable comme tel |
| Blockchain locale | Une chaîne de hachage mono-écrivain : même garantie, complexité inutile |
| Journal en fichier externe | Perd l'atomicité transactionnelle avec les données (ADR-001) |
| Ancrage tiers dès le MVP | Contredit le mode local strict par défaut |

## Événements journalisés obligatoirement

Transitions du graphe · appels LLM (modèle, agent, section, tokens) ·
validations humaines · exécutions de code avec niveau de sandbox ·
consentements accordés ou refusés · exports · déclenchements de guardrail ou de
circuit breaker.

## Vérification

`test_hash_chain_valid` · `test_tamper_detection_returns_index` ·
`test_no_update_or_delete_on_audit_log` (analyse statique) ·
`test_wording_no_immutable_claim` (contrôle lexical sur l'UI et la doc)
