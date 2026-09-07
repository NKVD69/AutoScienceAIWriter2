# Prompt d'implémentation — US-AUTH-001 (rôles et authentification locale)

Conforme au Backlog V0.3 et aux ADR-001, ADR-009, ADR-012.

---

```text
Tu es un agent de codage senior, spécialisé en authentification
applicative, contrôle d'accès et sécurité des applications locales.

SOURCES DE VÉRITÉ : ce prompt > ADR-001 (SQLite unique), ADR-009 (audit),
ADR-012 (pas de Docker) > Backlog V0.3 US-AUTH-001 > contrats.

DÉPENDANCES : US-101, US-701, US-801.

USER STORY

US-AUTH-001 — En tant que doctorant, je veux donner à mon directeur de
recherche un accès en lecture et en commentaire à mon projet, afin qu'il
suive mon travail sans pouvoir le modifier.

CE QUE CETTE STORY EST, ET SURTOUT CE QU'ELLE N'EST PAS

Ce n'est PAS un système de sécurité au sens réseau. L'application est liée
à 127.0.0.1 et le fichier .sqlite appartient à l'utilisateur : quiconque a
accès au poste a accès aux données, quel que soit le contrôle applicatif.

C'est un mécanisme de RÔLES, destiné à structurer une collaboration de
bonne foi et à HORODATER QUI A VALIDÉ QUOI dans le journal d'audit — ce
dernier point est la vraie valeur, puisqu'il alimente la déclaration
d'usage de l'IA (US-EXPORT-003).

Tu écris cela explicitement dans la documentation du module. Ne présente
jamais ce mécanisme comme une protection contre un accès malveillant.

PÉRIMÈTRE

Comptes locaux, rôles, session, contrôle d'accès aux opérations,
attribution du validateur dans l'audit, écran de connexion.

PAS d'IdP externe, PAS d'OAuth, PAS de chiffrement de la base — le
chiffrement au repos relèverait de l'OS, pas de l'application.

FICHIERS

- backend/app/auth/models.py
- backend/app/auth/password.py
- backend/app/auth/session.py
- backend/app/auth/permissions.py
- backend/app/api/v1/auth.py
- backend/app/db/registry_schema.sql            (modification : table user)
- backend/app/api/deps.py
- frontend/src/app/features/auth/
- backend/tests/tests_auth/
Aucun autre fichier.

EXIGENCES

1. Comptes dans le REGISTRE GLOBAL, pas dans les fichiers de projet : un
   compte doit survivre à la suppression d'un projet et valoir pour tous.
   Table user(id, username UNIQUE, display_name, password_hash, created_at,
   disabled).

2. Mots de passe : Argon2id via argon2-cffi, paramètres par défaut de la
   bibliothèque. Pas de bcrypt, pas de sha256 salé maison. Aucun mot de
   passe ni hash n'apparaît jamais dans un journal ou une réponse d'API.

3. Trois rôles, par projet, table project_member(project_id, user_id, role) :

   AUTHOR     — tout, y compris valider et exporter ;
   SUPERVISOR — lecture, relecture, commentaires (US-UI-005), export ;
                ne peut PAS valider une section ni modifier le contenu.
                La validation engage l'auteur, elle ne se délègue pas ;
   READER     — lecture seule.

   Le créateur d'un projet en est AUTHOR. Un projet a exactement un AUTHOR :
   le transfert est possible, le partage de ce rôle ne l'est pas.

4. Session par jeton opaque en base, pas de JWT : sur une application
   locale, un JWT n'apporte que l'impossibilité de révoquer. Expiration
   configurable, défaut 30 jours. Renouvellement glissant.

5. Contrôle d'accès en DÉPENDANCE FastAPI, pas en vérification dispersée :
   require_role(Role.AUTHOR) sur chaque opération d'écriture. Un test
   énumère les routes et échoue si l'une d'elles n'est protégée par aucune
   dépendance de rôle — l'oubli est le mode d'échec normal de ce genre de
   mécanisme.

6. Audit : HUMAN_VALIDATION porte désormais user_id et display_name. Les
   entrées antérieures à cette story restent valides avec un validateur
   nul ; la vérification de chaîne n'est pas affectée.

7. Mode mono-utilisateur par défaut : si aucun compte n'existe, l'appli
   fonctionne sans authentification et toute opération s'exécute comme
   AUTHOR. La création du premier compte active le mécanisme. Ne jamais
   imposer une inscription à un utilisateur solitaire.

8. Tests

   test_password_hashed_with_argon2id
   test_password_never_in_logs_or_responses
   test_no_auth_required_when_no_user_exists
   test_first_user_creation_enables_auth
   test_author_can_validate
   test_supervisor_cannot_validate
   test_supervisor_cannot_edit_content
   test_reader_cannot_write
   test_single_author_per_project
   test_author_transfer
   test_every_write_route_has_role_dependency
   test_session_token_revocable
   test_session_expiry
   test_validation_audit_records_user
   test_legacy_audit_entries_still_verify

INTERDICTIONS

- JWT.
- IdP externe.
- Présenter le mécanisme comme une protection contre un accès malveillant.
- Permettre à un SUPERVISOR de valider une section.
- Imposer l'authentification en usage solitaire.
- Écrire un mot de passe ou son hash dans un journal.

ACCEPTATION : pytest -q ; ruff ; python scripts/check_audit_chain.py

FORMAT : plan 10 lignes, fichiers complets, sorties, "Réserves".
```
