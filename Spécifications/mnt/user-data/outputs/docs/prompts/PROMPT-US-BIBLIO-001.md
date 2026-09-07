# Prompt d'implémentation — US-BIBLIO-001 (recherche bibliographique multi-API)

Section US-BIBLIO-001 de `docs/prompts/03-story-prompts.md`.
Dernier P0 du Backlog V0.3. Conforme aux Spécifications V0.3 §10 et à ADR-010.

---

```text
Tu es un agent de codage senior, spécialisé en intégration d'API
académiques, normalisation de métadonnées bibliographiques, déduplication
et clients HTTP asynchrones.

PROJET

Science AI Writer IDE — IDE scientifique local. Mode local strict par
défaut : aucune donnée du projet ne sort sans consentement de périmètre.

SOURCES DE VÉRITÉ, PAR ORDRE DÉCROISSANT

1. Ce prompt.
2. ADR-010 (local strict, consentement par périmètre), ADR-002 (sources et
   chunks), ADR-007 (bibliographie générée).
3. Backlog V0.3, US-BIBLIO-001.
4. Spécifications V0.3, §10.
5. contracts/openapi.yaml, section /biblio/search.

DÉPENDANCES REQUISES : US-101 (projets), US-102 (sources).

USER STORY

US-BIBLIO-001 — En tant que chercheur, je veux interroger les bases
académiques depuis l'outil, afin de constituer ma bibliographie sans
quitter l'IDE.

POURQUOI CETTE STORY EST P0

Sans elle, la base de connaissances ne peut être alimentée que par des PDF
déposés à la main, un par un. Sur un corpus de thèse — souvent 150 à 300
références — cela rend l'outil inutilisable en pratique. C'est la
fonctionnalité qui transforme un éditeur assisté en instrument de travail.

RÈGLE DE CONFIDENTIALITÉ — NON NÉGOCIABLE

Seules les REQUÊTES DE RECHERCHE sortent. Aucun texte du mémoire, aucun
titre de section, aucun extrait de source ingérée, aucune problématique ne
doit jamais être transmis à une API externe.

Une thèse en cours contient des résultats non publiés, parfois sous accord
de confidentialité industriel. Une fuite, même partielle, peut compromettre
une publication ou violer un contrat. Un test dédié vérifie qu'aucun
contenu de projet n'apparaît dans une requête sortante.

PÉRIMÈTRE STRICT

Tu implémentes :

- le client HTTP unique soumis au consentement et à la liste blanche ;
- cinq fournisseurs derrière une interface commune ;
- la normalisation des métadonnées ;
- la déduplication inter-fournisseurs ;
- la détection et le marquage des prépublications ;
- le cache local des réponses ;
- l'import d'un candidat en source_document ;
- les tests.

Tu n'implémentes PAS :

- le téléchargement automatique des PDF (les APIs ne fournissent pas
  légalement le texte intégral ; l'utilisateur dépose ses PDF) ;
- l'import DOI/BibTeX/RIS depuis un fichier (US-IMPORT-001) ;
- Zotero (US-ZOTERO-001) ;
- l'ingestion RAG (US-102, déjà faite) ;
- le frontend.

FICHIERS À CRÉER OU MODIFIER

- backend/app/biblio/__init__.py
- backend/app/biblio/http_client.py
- backend/app/biblio/base.py
- backend/app/biblio/providers/openalex.py
- backend/app/biblio/providers/crossref.py
- backend/app/biblio/providers/pubmed.py
- backend/app/biblio/providers/semanticscholar.py
- backend/app/biblio/providers/arxiv.py
- backend/app/biblio/normalize.py
- backend/app/biblio/dedupe.py
- backend/app/biblio/cache.py
- backend/app/services/biblio_service.py
- backend/app/api/v1/biblio.py
- backend/app/api/v1/__init__.py               (modification)
- backend/app/core/config.py                   (modification)
- backend/tests/tests_biblio/                  (fixtures JSON enregistrées)
- scripts/check_biblio_offline.py

Aucun autre fichier.

EXIGENCES D'IMPLÉMENTATION

1. Client HTTP unique (biblio/http_client.py)

    TOUT appel sortant du module passe par ce client, sans exception. Il
    applique, dans cet ordre :

      1. vérification du consentement biblio_search pour le projet ;
         en son absence, ConsentRequiredError avec le champ will_transmit
         décrivant ce qui sortirait ;
      2. vérification de la liste blanche de domaines, codée en dur :
         api.openalex.org, api.crossref.org, eutils.ncbi.nlm.nih.gov,
         api.semanticscholar.org, export.arxiv.org ;
      3. limitation de débit par fournisseur ;
      4. backoff exponentiel avec jitter sur 429 et 5xx, 3 tentatives ;
      5. en-tête User-Agent identifiant l'outil et en-tête mailto issu de
         settings.contact_email pour les pools polis d'OpenAlex et
         Crossref — sans lui, les quotas sont nettement plus stricts ;
      6. journalisation d'audit de chaque appel : fournisseur, requête,
         nombre de résultats. Jamais le contenu des réponses.

    Un domaine hors liste blanche lève WhitelistViolationError. Ce n'est
    pas une erreur réseau : c'est un défaut de code.

2. Interface fournisseur (biblio/base.py)

    class BiblioProvider(Protocol):
        name: str
        rate_limit_per_second: float
        async def search(self, query: str, *, limit: int,
                         year_min: int | None) -> list[RawRecord]
        async def by_doi(self, doi: str) -> RawRecord | None

    RawRecord conserve la réponse brute normalisée a minima. La
    normalisation complète appartient à normalize.py : un fournisseur ne
    décide jamais de la forme finale.

3. Fournisseurs

    - OpenAlex : /works?search=…&filter=… — le plus complet, à privilégier
      dans l'ordre d'agrégation ;
    - Crossref : /works?query.bibliographic=… — autorité sur les DOI ;
    - PubMed E-utilities : esearch puis efetch, deux appels, biomédical ;
    - Semantic Scholar : /graph/v1/paper/search — bonne couverture
      informatique, quotas serrés sans clé, prévoir la dégradation ;
    - arXiv : export.arxiv.org/api/query, réponse Atom à parser en XML.

    Chaque fournisseur échoue INDÉPENDAMMENT : si Semantic Scholar renvoie
    429, les quatre autres résultats sont retournés avec un champ
    partial_failures nommant le fournisseur indisponible. Une recherche ne
    doit jamais échouer entièrement parce qu'une API est en panne.

4. Normalisation (biblio/normalize.py)

    - DOI : minuscules, retrait des préfixes https://doi.org/ et doi:,
      espaces retirés ;
    - auteurs : « Nom, P. » séparés par des points-virgules ;
    - année : entier, extraite de la date de publication la plus fiable ;
    - titre : normalisation Unicode NFKC, espaces multiples réduits,
      ponctuation terminale retirée ;
    - kind déduit du type de publication ; par défaut article ;
    - langue déduite quand l'API la fournit, sinon nulle.

5. Détection des prépublications — EXIGENCE PROPAGÉE

    is_preprint vaut vrai si : le fournisseur est arXiv ; ou le DOI
    appartient à un préfixe connu de serveur de prépublication (bioRxiv,
    medRxiv, ChemRxiv, SSRN, Research Square) ; ou le type déclaré par
    l'API est preprint ou posted-content.

    Ce marqueur traverse toute la chaîne sans exception : résultat de
    recherche, source_document, bibliographie exportée (US-501, note
    « Prépublication, non révisé par les pairs »). Un doctorant doit savoir
    qu'il cite un travail non revu par les pairs.

6. Déduplication (biblio/dedupe.py)

    En deux passes :
      1. DOI normalisé identique ;
      2. titre normalisé identique ET année identique ou écart de 1 an —
         les bases divergent souvent d'une année entre publication en ligne
         et publication imprimée.

    La fusion retient, champ par champ, la valeur du fournisseur le plus
    fiable disponible, dans l'ordre Crossref > OpenAlex > PubMed >
    Semantic Scholar > arXiv pour les métadonnées bibliographiques, et le
    résumé le plus long quel que soit le fournisseur.

    merged_from liste les fournisseurs ayant contribué : c'est un signal de
    confiance utile à l'utilisateur.

7. Cache (biblio/cache.py)

    Cache local par (fournisseur, requête normalisée, limit, year_min),
    durée settings.biblio_cache_ttl_hours (défaut 168, soit une semaine).
    Stocké dans le fichier du projet, table biblio_cache créée par
    migration incrémentale.

    Le cache rend la suite de tests exécutable hors ligne et évite de
    consommer des quotas pendant le développement.

8. Import d'un candidat

    POST d'un SourceCandidate crée une ligne source_document avec
    approved_at nul : la validation humaine reste obligatoire (US-102).
    Aucun PDF n'est téléchargé. Si l'utilisateur possède le PDF, il le
    dépose ensuite et le rattachement se fait par DOI.

9. Tests — tous hors ligne

    Les réponses des cinq APIs sont ENREGISTRÉES sous forme de fixtures
    JSON et XML dans backend/tests/tests_biblio/fixtures/. Aucun test ne
    contacte le réseau ; httpx est intercepté.

    test_no_consent_blocks_search
    test_consent_error_describes_what_would_transmit
    test_whitelist_violation_raises
    test_mailto_header_present
    test_rate_limit_respected_per_provider
    test_backoff_on_429_then_success
    test_provider_failure_is_partial_not_total
    test_partial_failures_reported_to_caller

    test_doi_normalization_variants
    test_author_normalization
    test_title_unicode_normalization
    test_year_extraction_prefers_reliable_date

    test_arxiv_always_marked_preprint
    test_biorxiv_doi_prefix_marked_preprint
    test_posted_content_type_marked_preprint
    test_preprint_flag_persisted_to_source_document

    test_dedupe_by_doi
    test_dedupe_by_title_with_one_year_gap
    test_merge_prefers_crossref_metadata
    test_merge_keeps_longest_abstract
    test_merged_from_lists_contributors

    test_cache_hit_avoids_http_call
    test_cache_expires_after_ttl
    test_import_candidate_leaves_approved_at_null
    test_no_pdf_downloaded

    test_no_project_content_in_outgoing_request :
        crée un projet contenant une problématique et des sections au
        contenu reconnaissable, lance une recherche, et vérifie
        qu'aucun fragment de ce contenu n'apparaît dans l'URL, les
        en-têtes ou le corps de la requête interceptée.

10. Script (scripts/check_biblio_offline.py)

    Rejoue les fixtures à travers toute la chaîne — normalisation,
    déduplication, marquage des prépublications — et affiche le nombre de
    résultats bruts, fusionnés, et de prépublications détectées. Sortie 0
    si les compteurs correspondent aux valeurs attendues. Aucun appel
    réseau : ce script doit passer en intégration continue.

INTERDICTIONS

- Transmettre le moindre contenu de projet à une API.
- Appel sortant ne passant pas par le client unique.
- Domaine hors liste blanche.
- Échec total d'une recherche parce qu'un fournisseur est indisponible.
- Téléchargement automatique de PDF.
- Ingestion d'une source non approuvée.
- Test contactant le réseau.
- Modification d'un fichier hors périmètre.

CRITÈRES D'ACCEPTATION

    cd backend && pytest -q backend/tests/tests_biblio
    ruff check backend/ && ruff format --check backend/
    python scripts/check_biblio_offline.py
    python scripts/check_no_cloud_calls.py

FORMAT DE RÉPONSE

1. Plan d'implémentation, 10 lignes maximum.
2. Contenu intégral de chaque fichier.
3. Sortie attendue des vérifications.
4. Section "Réserves".
```

## Message de commit attendu

```text
feat(biblio): recherche bibliographique multi-API sous consentement

Implémente US-BIBLIO-001 (Backlog V0.3).

- Client HTTP unique : consentement, liste blanche, débit, backoff, audit.
- Cinq fournisseurs à échec indépendant ; une panne d'API dégrade le
  résultat sans faire échouer la recherche.
- Déduplication DOI puis titre + année ± 1 ; fusion par fiabilité de
  fournisseur.
- Prépublications détectées et marquées jusqu'à la bibliographie exportée.
- Cache local par requête ; suite de tests entièrement hors ligne.

Refs: US-BIBLIO-001, ADR-010
```
