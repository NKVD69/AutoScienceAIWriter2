# Prompt d'implémentation — US-601 (anti-plagiat externe sous consentement)

Conforme au Backlog V0.3 (US-601 révisée 0.3.1), aux Spécifications V0.3.1 §11.3 à §11.5 et à ADR-010 amendé.

---

```text
Tu es un agent de codage senior, spécialisé en intégration de services
tiers, minimisation de données et conception de dialogues de consentement.

SOURCES DE VÉRITÉ : ce prompt > ADR-010 (local strict, consentement par
périmètre), ADR-009 (audit) > Backlog V0.3 US-601 > Spécifications V0.3
§11.3 > contracts/openapi.yaml.

DÉPENDANCES : US-301 (sections), US-701 (audit), US-801 (dialogue de
consentement).

USER STORY

US-601 — En tant que doctorant, je veux vérifier la similarité d'une
section avec la littérature publiée, afin de détecter une paraphrase trop
proche avant le dépôt.

LA TENSION À TRAITER HONNÊTEMENT

Cette story est le seul endroit du produit par lequel du texte non publié de
la thèse quitte le poste. Elle est donc conçue autour du CONSENTEMENT
ÉCLAIRÉ.

Point de conception révisé en V0.3.1 — à respecter tel quel : ce n'est PAS à
l'outil de décider combien de texte sort. L'utilisateur choisit l'étendue à
chaque requête, parmi passages, section et document. Une version antérieure
de cette spécification limitait la transmission aux seuls passages signalés
localement ; cette restriction est levée.

Ce que tu garantis n'est donc pas une limite de volume, mais la QUALITÉ DE
L'INFORMATION sur laquelle la décision est prise : le texte exact affiché
avant l'envoi, le volume en mots, aucun choix mémorisé implicitement, et une
journalisation de l'étendue sans le contenu.

PÉRIMÈTRE

Tu implémentes : le service de vérification, l'interface d'adaptateur, un
adaptateur local hors ligne, un adaptateur distant, le sélecteur d'étendue,
le dialogue de consentement enrichi, la présentation des résultats, les
tests.

Tu n'implémentes PAS : la souscription à un service commercial ; la
remédiation automatique des passages signalés ; l'anonymisation ou la
paraphrase automatique du texte avant envoi.

FICHIERS

- backend/app/plagiarism/base.py
- backend/app/plagiarism/local_corpus.py
- backend/app/plagiarism/remote_provider.py
- backend/app/plagiarism/fingerprint.py
- backend/app/plagiarism/scope.py
- backend/app/services/plagiarism_service.py
- backend/app/api/v1/plagiarism.py
- backend/app/api/v1/__init__.py               (modification)
- frontend/src/app/features/review/plagiarism-report.component.ts
- backend/tests/tests_plagiarism/
Aucun autre fichier.

EXIGENCES

1. Vérification LOCALE d'abord, toujours (plagiarism/local_corpus.py)

   Avant tout appel externe, comparaison contre :
   - les chunks ingérés du projet : c'est le cas le plus fréquent et le
     plus grave, la paraphrase trop proche d'une source qu'on a soi-même
     importée ;
   - les autres sections du même document : autoplagiat interne.

   Méthode : empreintes par winnowing sur n-grammes de 5 mots
   (fingerprint.py), seuil de similarité configurable, défaut 0,35.
   Aucune sortie réseau. Cette vérification est GRATUITE, HORS LIGNE et
   couvre la majorité des risques réels d'un doctorant. Elle est activée
   par défaut ; la vérification distante ne l'est pas.

2. Vérification DISTANTE (plagiarism/remote_provider.py)

   - interface PlagiarismProvider : check(text) -> list[Match] ;
   - aucun fournisseur n'est codé en dur : l'URL, la clé et le format sont
     configurés par l'utilisateur. L'outil ne recommande aucun service ;
   - la clé d'API est stockée dans le trousseau du système via `keyring`,
     jamais dans le fichier de projet, qui est destiné à être copié et
     transmis à un directeur de recherche.

3. ÉTENDUE CHOISIE PAR L'UTILISATEUR (plagiarism/scope.py)

   Trois valeurs, sélectionnées À CHAQUE REQUÊTE :

     passages  — les passages signalés par la vérification locale, avec au
                 plus 500 mots de contexte chacun. Proposé par défaut.
     section   — le contenu complet d'une section.
     document  — l'ensemble des sections validées.

   Règles impératives :

   - la vérification distante NE DÉPEND PAS du résultat local. Une
     vérification à l'étendue `section` ou `document` s'exécute même si le
     contrôle local n'a rien signalé, sans message la présentant comme
     superflue ;
   - l'étendue n'est JAMAIS mémorisée comme préférence implicite. Un
     utilisateur ayant choisi `document` une fois se voit reproposer
     `passages` la fois suivante. Le consentement autorise la sortie ; il
     ne présume pas de son ampleur ;
   - avant l'envoi, l'interface affiche LE TEXTE EXACT qui sera transmis,
     mot pour mot, et son volume en mots. Pas un résumé, pas un décompte
     seul : le texte, dans une zone défilable. C'est sur cette base que la
     confirmation est demandée ;
   - le consentement plagiarism_check reste requis, par projet, révocable,
     avec les mêmes options que les autres périmètres.

4. Résultats

   Chaque Match : passage local, source rapprochée, score, origine
   (locale ou distante). Présentation par gravité décroissante.

   Aucun verdict global, aucun pourcentage de plagiat pour le document.
   Ces chiffres sont produits par les outils institutionnels selon leurs
   propres règles ; en afficher un ici donnerait une fausse assurance.
   L'outil signale des passages, il ne délivre pas de quitus.

5. Audit

   Chaque vérification distante journalise : date, étendue retenue, nombre
   de mots transmis, fournisseur configuré. Jamais le texte.

   L'étendue figure dans la déclaration d'usage de l'IA (US-EXPORT-003) :
   un doctorant doit pouvoir déclarer qu'un contrôle externe a porté sur
   l'intégralité de son document.

6. Tests

   test_local_check_runs_without_network
   test_local_check_detects_paraphrase_of_ingested_chunk
   test_local_check_detects_internal_self_overlap
   test_remote_requires_consent
   test_consent_dialog_shows_exact_text
   test_dialog_shows_word_count
   test_scope_selected_per_request
   test_scope_defaults_to_passages
   test_scope_not_remembered_between_requests
   test_scope_section_sends_full_section
   test_scope_document_sends_all_validated_sections
   test_remote_runs_even_when_local_finds_nothing
   test_no_message_discourages_remote_after_clean_local
   test_passages_scope_context_capped_at_500_words
   test_no_send_without_confirmation_on_displayed_text
   test_no_global_plagiarism_percentage
   test_audit_records_scope_and_word_count_not_text
   test_scope_available_to_ai_declaration
   test_api_key_in_keyring_not_project_file
   test_no_hardcoded_provider_url

INTERDICTIONS

- Restreindre l'étendue transmise au-delà du choix de l'utilisateur.
- Mémoriser l'étendue d'une requête à la suivante.
- Envoyer sans afficher le texte exact et son volume.
- Subordonner la vérification distante à un signalement local.
- Décourager l'utilisateur d'un contrôle distant après un local propre.
- Coder en dur un fournisseur.
- Stocker la clé d'API dans le fichier de projet.
- Afficher un pourcentage global de plagiat.
- Journaliser le texte transmis.

ACCEPTATION : pytest -q backend/tests/tests_plagiarism ; ruff ;
python scripts/check_no_cloud_calls.py

FORMAT : plan 10 lignes, fichiers complets, sorties de vérification,
section "Réserves".
```
