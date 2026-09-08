"""US-301 — garde-fous de veracite et validateurs de sortie. Le coeur du produit.

Aucun de ces controles n'interroge un modele. C'est ce qui les rend fiables :
un modele qui invente une reference la trouve tout aussi plausible a la
relecture, et un controle fonde sur son jugement echouerait exactement dans
le cas qu'il doit attraper.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.agents.graph import section_guardrail_target
from app.agents.state import WorkflowState
from app.agents.veracity import (
    check_citation_keys,
    check_identifiers,
    check_numeric_claims,
    check_veracity,
    extract_inline_keys,
    find_significant_number,
)
from app.db.migrations.runner import run_migrations
from app.db.session import connect, transaction
from app.db.vector import ChunkHit
from app.models.section import Claim, SectionDraft
from app.rag.context_builder import SectionContext

NOW = datetime.now(UTC).isoformat()

DOI_CONNU = "10.1016/j.envint.2021.106274"
URL_CONNUE = "https://example.org/etude-renale"


def hit(chunk_id: int, source_id: int, titre: str, annee: int) -> ChunkHit:
    return ChunkHit(
        chunk_id=chunk_id,
        source_id=source_id,
        text="Extrait de reference.",
        page_start=4,
        page_end=5,
        distance=0.1,
        source_title=titre,
        source_year=annee,
        source_doi=None,
        is_preprint=False,
    )


def contexte(cles: list[str] | None = None) -> SectionContext:
    chunks = [
        hit(11, 1, "Filtration renale et polymeres", 2021),
        hit(12, 1, "Filtration renale et polymeres", 2021),
        hit(21, 2, "Exposition chronique urbaine", 2019),
    ]
    return SectionContext(
        node_id=1,
        node_title="Exposition chronique et fonction renale",
        node_objective="Etablir le lien entre exposition et filtration.",
        target_words=1000,
        query="requete",
        chunks=chunks,
        allowed_keys=cles if cles is not None else ["src1_2021_filtration", "src2_2019_exposition"],
        token_estimate=50,
    )


def draft(content: str, claims: list[Claim], mots: int = 1000) -> SectionDraft:
    return SectionDraft(content_qmd=content, claims=claims, word_count=mots)


@pytest.fixture
async def conn(tmp_path: Path):
    """Base migree portant deux sources, dont un DOI et une URL connus."""
    async with connect(tmp_path / "projet.sqlite") as connexion:
        await run_migrations(connexion)
        async with transaction(connexion):
            await connexion.execute(
                "INSERT INTO project (id, name, subject, language, academic_level,"
                " created_at, updated_at) VALUES (1,'P','Sujet','fr','doctorat',?,?)",
                (NOW, NOW),
            )
            await connexion.execute(
                "INSERT INTO source_document (id, project_id, kind, title, year, doi, url,"
                " is_preprint, imported_at) VALUES (1,1,'article','Filtration renale',"
                "2021,?,?,0,?)",
                (DOI_CONNU, URL_CONNUE, NOW),
            )
            await connexion.execute(
                "INSERT INTO source_document (id, project_id, kind, title, year,"
                " is_preprint, imported_at) VALUES (2,1,'article','Exposition',2019,0,?)",
                (NOW,),
            )
        yield connexion


# --- Validateurs Pydantic (etage 1) ---------------------------------------


def test_sourced_claim_requires_key_and_chunk() -> None:
    """Une affirmation qui se dit etablie sans l'etre est un rejet."""
    with pytest.raises(ValidationError) as sans_cle:
        Claim(text="La filtration decroit.", kind="sourced", chunk_ids=[11])
    assert "cle de citation" in str(sans_cle.value).replace("é", "e")

    with pytest.raises(ValidationError) as sans_chunk:
        Claim(text="La filtration decroit.", kind="sourced", citation_keys=["src1_2021_filtration"])
    assert "extrait" in str(sans_chunk.value)

    # Les deux presents : accepte.
    claim = Claim(
        text="La filtration decroit.",
        kind="sourced",
        citation_keys=["src1_2021_filtration"],
        chunk_ids=[11],
    )
    assert claim.chunk_ids == [11]


def test_hypothesis_claim_rejects_citation_key() -> None:
    """Une hypothese sourcee est une contradiction : elle emprunte l'autorite
    d'une source pour ce que l'auteur avance lui-meme."""
    for genre in ("hypothesis", "synthesis", "limitation"):
        with pytest.raises(ValidationError) as exc:
            Claim(
                text="Le mecanisme pourrait relever d'une inflammation chronique.",
                kind=genre,
                citation_keys=["src1_2021_filtration"],
            )
        assert "autorite" in str(exc.value).replace("é", "e")


def test_myst_syntax_rejected_in_content() -> None:
    """MyST ressemble assez a Quarto pour passer la relecture, et assez peu
    pour casser la compilation des semaines plus tard (ADR-006)."""
    valide = Claim(text="Point de synthese.", kind="synthesis")
    for fautif in (
        ":::{note}\nUn encadre MyST.\n:::",
        "Voir {ref}`chapitre-2` pour le detail.",
        "Comme le montre {numref}`fig-1`.",
        "Selon {cite}`dupont2021`.",
    ):
        with pytest.raises(ValidationError) as exc:
            draft(fautif, [valide])
        assert "MyST" in str(exc.value)

    # Le Quarto equivalent passe.
    assert draft("::: {.callout-note}\nUn encadre Quarto.\n:::", [valide])


def test_word_count_out_of_range_rejected() -> None:
    """Une section de 300 mots pour une cible de 1500 n'est pas une section
    courte : c'est un plan qui ne tient pas."""
    valide = Claim(text="Point de synthese.", kind="synthesis")

    with pytest.raises(ValueError) as trop_court:
        draft("Texte.", [valide], mots=300).check_word_count(1500)
    assert "300" in str(trop_court.value) and "1500" in str(trop_court.value)

    with pytest.raises(ValueError):
        draft("Texte.", [valide], mots=2500).check_word_count(1500)

    # Dans la fourchette 0,6 - 1,4 : accepte.
    draft("Texte.", [valide], mots=1000).check_word_count(1500)
    draft("Texte.", [valide], mots=2000).check_word_count(1500)


# --- V1 : cle hors liste blanche ------------------------------------------


def test_v1_unknown_citation_key_rejected() -> None:
    section = draft(
        "La filtration decroit apres exposition.",
        [
            Claim(
                text="La filtration decroit.",
                kind="sourced",
                citation_keys=["src9_2020_inventee"],
                chunk_ids=[11],
            )
        ],
    )
    violations = check_citation_keys(section, contexte())

    assert [v.rule for v in violations] == ["V1"]
    assert violations[0].token == "src9_2020_inventee"


def test_v1_inline_at_key_also_checked() -> None:
    """Une cle peut n'exister que dans le texte : la declarer n'est pas
    obligatoire pour qu'elle apparaisse dans le PDF final."""
    section = draft(
        "La filtration decroit [@src9_2020_inventee] et se stabilise ensuite.",
        [Claim(text="Point de synthese.", kind="synthesis")],
    )
    violations = check_citation_keys(section, contexte())

    assert [v.token for v in violations] == ["src9_2020_inventee"]


def test_v1_bare_at_key_also_checked() -> None:
    section = draft(
        "Comme le montre @src9_2020_inventee, la filtration decroit.",
        [Claim(text="Point de synthese.", kind="synthesis")],
    )
    assert [v.token for v in check_citation_keys(section, contexte())] == ["src9_2020_inventee"]


def test_v1_accepts_keys_from_the_closed_list() -> None:
    section = draft(
        "La filtration decroit [@src1_2021_filtration].",
        [
            Claim(
                text="La filtration decroit.",
                kind="sourced",
                citation_keys=["src1_2021_filtration"],
                chunk_ids=[11],
            )
        ],
    )
    assert check_citation_keys(section, contexte()) == []


def test_v1_crossrefs_are_not_citations() -> None:
    """@fig-, @tbl-, @sec- sont des renvois Quarto. Les confondre avec des
    cles ferait rejeter un document parfaitement correct."""
    section = draft(
        "Le profil est donne en @fig-filtration et resume en @tbl-cohortes, cf. @sec-methodes.",
        [Claim(text="Point de synthese.", kind="synthesis")],
    )
    assert check_citation_keys(section, contexte()) == []
    assert extract_inline_keys("Voir @fig-un et @tbl-deux.") == set()


def test_v1_unknown_chunk_id_rejected() -> None:
    """Un extrait non fourni est une invention de la meme nature qu'une cle."""
    section = draft(
        "La filtration decroit.",
        [
            Claim(
                text="La filtration decroit.",
                kind="sourced",
                citation_keys=["src1_2021_filtration"],
                chunk_ids=[99],
            )
        ],
    )
    violations = check_citation_keys(section, contexte())

    assert [v.token for v in violations] == ["99"]


# --- V2 : affirmation chiffree non rattachee ------------------------------


def test_v2_numeric_claim_without_chunk_rejected() -> None:
    """Un chiffre presente comme sourcE sans extrait d'origine est un rejet.

    L'affirmation est construite SANS validation Pydantic : l'etage 1 refuse
    deja une affirmation sourcee sans chunk_id, si bien que ce cas ne peut
    pas se presenter par le chemin normal. Le controle est ici exerce seul,
    pour qu'un assouplissement de l'etage 1 ne le desarme pas en silence.
    """
    orpheline = Claim.model_construct(
        text="La filtration chute de 34,5 % apres six mois.",
        kind="sourced",
        citation_keys=["src1_2021_filtration"],
        chunk_ids=[],
    )
    section = SectionDraft.model_construct(
        content_qmd="La filtration chute de 34,5 %.", claims=[orpheline], word_count=1000
    )
    violations = check_numeric_claims(section)

    assert [v.rule for v in violations] == ["V2"]
    assert "34,5" in violations[0].token


@pytest.mark.parametrize(
    "texte",
    [
        "La reduction atteint 34,5 apres six mois.",
        "Une baisse de 27 % est observee.",
        "L'effet est significatif (p < 0,01).",
        "La cohorte compte n = 482 participants.",
        "Les valeurs s'etendent de 12 à 18 unites.",
        "L'intervalle 12-18 couvre la majorite des cas.",
        "Le suivi porte sur 1450 individus.",
    ],
)
def test_v2_detects_significant_numbers(texte: str) -> None:
    assert find_significant_number(texte) is not None


def test_v2_section_number_not_flagged_as_numeric_claim() -> None:
    """Un numero de section n'affirme rien. Le signaler ferait rejeter du
    texte correct, et rendrait le controle inutilisable donc desactive."""
    for texte in (
        "Comme etabli en section 3.",
        "Voir chapitre 2 pour la methode.",
        "Le detail figure en section 4.2.",
        "Le profil est donne en figure 3.",
        "Voir fig. 2 et tableau 5.",
        "Le renvoi @fig-filtration porte le detail.",
        # Un renvoi pluriel : sans les pluriels dans le filtre, « 3 à 5 »
        # serait lu comme un intervalle chiffre.
        "Voir sections 3 à 5.",
    ):
        assert find_significant_number(texte) is None, texte


def test_v2_isolated_year_not_flagged() -> None:
    """Une annee isolee n'est pas une donnee chiffree."""
    for texte in (
        "L'etude de 2021 le montre.",
        "Entre 1998 et aujourd'hui, la question reste ouverte.",
        "Les travaux de 2019 vont dans ce sens.",
        "De 2019 à 2023, la tendance reste stable.",
    ):
        assert find_significant_number(texte) is None, texte

    # Mais une annee accolee a une unite reste un chiffre.
    assert find_significant_number("Une hausse de 2019,5 unites.") is not None


def test_v2_ignores_non_sourced_claims() -> None:
    """Le controle porte sur ce qui se presente comme etabli."""
    section = SectionDraft.model_construct(
        content_qmd="Texte.",
        claims=[Claim(text="Une baisse de l'ordre de 30 % est plausible.", kind="hypothesis")],
        word_count=1000,
    )
    assert check_numeric_claims(section) == []


def test_v2_accepts_a_numeric_claim_carrying_its_chunk() -> None:
    section = draft(
        "La filtration chute de 34,5 %.",
        [
            Claim(
                text="La filtration chute de 34,5 % apres six mois.",
                kind="sourced",
                citation_keys=["src1_2021_filtration"],
                chunk_ids=[11],
            )
        ],
    )
    assert check_numeric_claims(section) == []


# --- V3 : DOI ou URL hors base --------------------------------------------


async def test_v3_unknown_doi_rejected(conn) -> None:
    section = draft(
        "Le mecanisme est decrit dans 10.9999/inexistant.2024.001.",
        [Claim(text="Point de synthese.", kind="synthesis")],
    )
    violations = await check_identifiers(conn, section)

    assert [v.rule for v in violations] == ["V3"]
    assert violations[0].token == "10.9999/inexistant.2024.001"


async def test_v3_known_doi_accepted(conn) -> None:
    section = draft(
        f"Le mecanisme est decrit dans {DOI_CONNU}.",
        [Claim(text="Point de synthese.", kind="synthesis")],
    )
    assert await check_identifiers(conn, section) == []


async def test_v3_unknown_url_rejected(conn) -> None:
    section = draft(
        "Les donnees sont sur https://exemple-invente.test/jeu-de-donnees.",
        [Claim(text="Point de synthese.", kind="synthesis")],
    )
    violations = await check_identifiers(conn, section)

    assert [v.rule for v in violations] == ["V3"]
    assert "exemple-invente" in violations[0].token


async def test_v3_known_url_accepted(conn) -> None:
    section = draft(
        f"Les donnees sont sur {URL_CONNUE}.",
        [Claim(text="Point de synthese.", kind="synthesis")],
    )
    assert await check_identifiers(conn, section) == []


# --- Orchestration --------------------------------------------------------


async def test_veracity_message_names_offending_token(conn) -> None:
    """Le message est destine au MODELE : sans le jeton fautif, l'essai
    suivant est aleatoire plutot qu'utile."""
    section = draft(
        "La filtration decroit [@src9_2020_inventee], cf. 10.9999/inexistant.2024.001.",
        [Claim(text="Point de synthese.", kind="synthesis")],
    )
    resultat = await check_veracity(conn, section, contexte())

    assert not resultat.ok
    assert {v.rule for v in resultat.violations} == {"V1", "V3"}

    correction = resultat.to_correction()
    assert "src9_2020_inventee" in correction
    assert "10.9999/inexistant.2024.001" in correction
    # La consigne dit quoi faire, pas seulement ce qui ne va pas.
    assert "Retire" in correction
    # Les cles admises sont rappelees, pour que la correction soit possible.
    assert "src1_2021_filtration" in correction


async def test_veracity_passes_a_clean_section(conn) -> None:
    section = draft(
        f"La filtration decroit [@src1_2021_filtration]. Voir {DOI_CONNU}.",
        [
            Claim(
                text="La filtration decroit de 12,4 % en six mois.",
                kind="sourced",
                citation_keys=["src1_2021_filtration"],
                chunk_ids=[11],
            ),
            Claim(text="Le mecanisme reste discute.", kind="limitation"),
        ],
    )
    resultat = await check_veracity(conn, section, contexte())

    assert resultat.ok
    assert resultat.to_correction() == ""


def test_veracity_failure_reruns_writer_not_reviewer() -> None:
    """Une cle inventee est un defaut de production, pas de fond. Passer au
    relecteur un texte porteur d'une reference inventee lui ferait juger un
    contenu que le produit refuse d'ecrire."""
    assert (
        section_guardrail_target(ok=False, breaker_tripped=False) is WorkflowState.SECTION_DRAFTING
    )
    assert section_guardrail_target(ok=True, breaker_tripped=False) is (
        WorkflowState.SECTION_REVIEWING
    )
    assert section_guardrail_target(ok=False, breaker_tripped=True) is WorkflowState.ERROR_STATE

    # Aucun rejet ne mene au relecteur.
    for tripped in (False, True):
        assert section_guardrail_target(False, tripped) is not WorkflowState.SECTION_REVIEWING
