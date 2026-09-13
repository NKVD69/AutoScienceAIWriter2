"""US-502 — assemblage du .qmd : ordre, ancres, sections manquantes, cles."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.db.migrations.runner import run_migrations
from app.db.session import connect, transaction
from app.export.assembler import (
    AI_DECLARATION_ANCHOR,
    MISSING_MARKER,
    assemble,
    build_key_map,
    flatten,
    rewrite_citation_keys,
    section_anchor,
    slugify,
)
from app.models.plan import PlanNodeOut, PlanOut, PlanStatus
from app.models.section import SectionStatus

NOW = datetime.now(UTC).isoformat()


def noeud(nid: int, titre: str, niveau: int, ordinal: int, enfants=None) -> PlanNodeOut:
    return PlanNodeOut(
        id=nid,
        parent_id=None,
        title=titre,
        objective="Objectif verifiable.",
        target_words=1000,
        level=niveau,
        ordinal=ordinal,
        children=enfants or [],
    )


def plan() -> PlanOut:
    """Deux chapitres, chacun deux sections. Ordre de lecture : 1,2,3,4,5,6."""
    return PlanOut(
        id=1,
        version=1,
        status=PlanStatus.VALIDATED,
        problematique="Problematique du memoire.",
        nodes=[
            noeud(
                1,
                "Etat de l art",
                1,
                0,
                [
                    noeud(2, "Methodes existantes", 2, 0),
                    noeud(3, "Limites identifiees", 2, 1),
                ],
            ),
            noeud(
                4,
                "Contribution",
                1,
                1,
                [
                    noeud(5, "Methodes existantes", 2, 0),  # titre repete
                    noeud(6, "Resultats", 2, 1),
                ],
            ),
        ],
    )


async def peupler(db: Path, sections: dict[int, str], cles: dict[str, str] | None = None) -> None:
    """`sections` : node_id -> contenu. `cles` : cle de redaction -> cle publiee."""
    async with connect(db) as conn:
        await run_migrations(conn)
        async with transaction(conn):
            await conn.execute(
                "INSERT INTO project (id, name, subject, language, academic_level,"
                " created_at, updated_at) VALUES (1,'These','Sujet','fr','doctorat',?,?)",
                (NOW, NOW),
            )
            await conn.execute(
                "INSERT INTO plan (id, project_id, problematique, status, version, created_at)"
                " VALUES (1,1,'P','VALIDATED',1,?)",
                (NOW,),
            )
            for noeud_courant in flatten(plan().nodes):
                await conn.execute(
                    "INSERT INTO plan_node (id, plan_id, parent_id, ordinal, level, title,"
                    " objective, target_words) VALUES (?,1,NULL,?,?,?,'O',1000)",
                    (
                        noeud_courant.id,
                        noeud_courant.ordinal,
                        noeud_courant.level,
                        noeud_courant.title,
                    ),
                )

            # Les sections d'abord : `citation.draft_section_id` est une cle
            # etrangere, et elle est reellement appliquee (US-002).
            for node_id, contenu in sections.items():
                await conn.execute(
                    "INSERT INTO draft_section (id, plan_node_id, content_qmd, status,"
                    " version, generated_at) VALUES (?,?,?,?,1,?)",
                    (node_id, node_id, contenu, str(SectionStatus.REVIEWING), NOW),
                )

            source_id = 0
            for cle_redaction, cle_publiee in (cles or {}).items():
                source_id += 1
                await conn.execute(
                    "INSERT INTO source_document (id, project_id, kind, title, year,"
                    " is_preprint, imported_at, bibtex_key) VALUES (?,1,'article',?,2020,0,?,?)",
                    (source_id, f"Source {source_id}", NOW, cle_publiee),
                )
                for node_id in sections:
                    await conn.execute(
                        "INSERT INTO citation (draft_section_id, source_id, bibtex_key,"
                        " verified) VALUES (?,?,?,1)",
                        (node_id, source_id, cle_redaction),
                    )


def sections_par_noeud(node_ids: list[int]) -> dict[int, int]:
    """Dans ces tests, `draft_section.id` vaut `plan_node_id`."""
    return {nid: nid for nid in node_ids}


# --- Ordre ----------------------------------------------------------------


def test_flatten_follows_reading_order() -> None:
    assert [n.id for n in flatten(plan().nodes)] == [1, 2, 3, 4, 5, 6]


def test_flatten_sorts_by_ordinal_not_by_insertion() -> None:
    """Un plan reordonne doit s'assembler dans son nouvel ordre."""
    desordre = [noeud(2, "Second", 1, 1), noeud(1, "Premier", 1, 0)]
    assert [n.id for n in flatten(desordre)] == [1, 2]


async def test_assembler_respects_plan_order(project_db: Path) -> None:
    contenus = {nid: f"Contenu du noeud {nid}." for nid in (1, 2, 3, 4, 5, 6)}
    await peupler(project_db, contenus)

    async with connect(project_db) as conn:
        document = await assemble(conn, plan(), sections_par_noeud(list(contenus)))

    positions = [document.qmd.index(f"Contenu du noeud {nid}.") for nid in (1, 2, 3, 4, 5, 6)]
    assert positions == sorted(positions)
    assert [n.node_id for n in document.nodes] == [1, 2, 3, 4, 5, 6]


async def test_heading_level_follows_node_depth(project_db: Path) -> None:
    await peupler(project_db, {1: "Chapitre.", 2: "Section."})

    async with connect(project_db) as conn:
        document = await assemble(conn, plan(), sections_par_noeud([1, 2]))

    assert "# Etat de l art {#sec-etat-de-l-art-1}" in document.qmd
    assert "## Methodes existantes {#sec-methodes-existantes-2}" in document.qmd


# --- Ancres ---------------------------------------------------------------


def test_slugify_is_ascii_and_bounded() -> None:
    assert slugify("Résultats préliminaires !") == "resultats-preliminaires"
    assert slugify("") == "section"
    assert len(slugify("mot " * 40)) <= 40
    assert not slugify("Fin ---").endswith("-")


def test_assembler_emits_stable_section_ids() -> None:
    """Deux chapitres peuvent legitimement porter une section « Methodes » :
    sans l'identifiant du noeud, les deux produiraient la meme ancre et
    Quarto n'en resoudrait qu'une."""
    ordre = flatten(plan().nodes)
    ancres = [section_anchor(n) for n in ordre]

    assert len(set(ancres)) == len(ancres)
    assert ancres[1] == "sec-methodes-existantes-2"
    assert ancres[4] == "sec-methodes-existantes-5"
    # Stable d'un appel a l'autre, et prefixee `sec-` comme l'attend Quarto.
    assert all(a.startswith("sec-") for a in ancres)
    assert ancres == [section_anchor(n) for n in flatten(plan().nodes)]


async def test_anchors_survive_a_reordering(project_db: Path) -> None:
    """Un renvoi ecrit dans un chapitre reste valide quand le plan bouge."""
    await peupler(project_db, {2: "Section."})
    avant = section_anchor(noeud(2, "Methodes existantes", 2, 0))
    apres = section_anchor(noeud(2, "Methodes existantes", 3, 7))
    assert avant == apres


# --- Sections manquantes --------------------------------------------------


async def test_assembler_marks_missing_sections_without_failing(project_db: Path) -> None:
    """Un apercu partiel de la moitie d'un memoire est utile ; un export qui
    refuse de produire quoi que ce soit ne l'est pas.

    Seules les FEUILLES sans texte manquent. Le chapitre 4 n'a pas de texte
    propre, mais il porte des sections : c'est un titre, pas un trou.
    """
    await peupler(project_db, {1: "Seul contenu redige."})

    async with connect(project_db) as conn:
        document = await assemble(conn, plan(), sections_par_noeud([1]))

    assert document.qmd.count(MISSING_MARKER) == 4
    assert [n.node_id for n in document.missing()] == [2, 3, 5, 6]
    assert [n.node_id for n in document.included()] == [1]
    # Le titre du noeud manquant figure quand meme : la structure se lit.
    assert "## Resultats {#sec-resultats-6}" in document.qmd


async def test_empty_content_counts_as_missing(project_db: Path) -> None:
    """Une section vide n'est pas une section : elle est signalee. Un chapitre
    vide ne l'est pas — ce sont ses sections qui le seront a sa place."""
    await peupler(project_db, {2: "   \n  "})

    async with connect(project_db) as conn:
        document = await assemble(conn, plan(), sections_par_noeud([2]))

    assert [n.node_id for n in document.missing()] == [2, 3, 5, 6]


async def test_container_chapter_is_a_heading_not_a_missing_section(project_db: Path) -> None:
    """Un memoire entierement redige ne porte AUCUN marqueur.

    Defaut constate sur un vrai rendu Quarto : les chapitres, noeuds
    conteneurs sans texte propre, recevaient le marqueur de section non
    redigee sous leur titre, et l'export restait PARTIEL a vie — pour une
    these dont toutes les sections etaient ecrites.
    """
    feuilles = {nid: f"Texte de la section {nid}." for nid in (2, 3, 5, 6)}
    await peupler(project_db, feuilles)

    async with connect(project_db) as conn:
        document = await assemble(conn, plan(), sections_par_noeud(list(feuilles)))

    assert MISSING_MARKER not in document.qmd
    assert document.missing() == []
    # Les chapitres restent des titres numerotes, ancres comprises.
    assert "# Etat de l art {#sec-etat-de-l-art-1}" in document.qmd
    assert "# Contribution {#sec-contribution-4}" in document.qmd


async def test_chapter_with_its_own_text_keeps_it(project_db: Path) -> None:
    """Un chapeau de chapitre est un usage academique legitime : il est rendu
    sous le titre, avant les sections."""
    await peupler(project_db, {1: "Chapeau du chapitre.", 2: "Premiere section."})

    async with connect(project_db) as conn:
        document = await assemble(conn, plan(), sections_par_noeud([1, 2]))

    assert document.qmd.index("Chapeau du chapitre.") < document.qmd.index("Premiere section.")
    assert 1 in [n.node_id for n in document.included()]


# --- Cles de citation -----------------------------------------------------


def test_key_map_drops_ambiguous_pairs() -> None:
    """Reecrire au hasard produirait une citation fausse ; laisser la cle
    intacte la fait signaler comme non resolue par Quarto."""
    assert build_key_map([("src1_2019_a", "dupont_2019_a")]) == {"src1_2019_a": "dupont_2019_a"}
    assert build_key_map([("src1_2019_a", "dupont_2019_a"), ("src1_2019_a", "martin_2019_b")]) == {}


def test_rewrite_respects_key_boundaries() -> None:
    """Sans borne droite, `@dupont_2019_a` reecrirait le debut de
    `@dupont_2019_abc`."""
    correspondance = {"src1_2019_a": "dupont_2019_a"}
    assert rewrite_citation_keys("Texte [@src1_2019_a].", correspondance) == (
        "Texte [@dupont_2019_a]."
    )
    # La cle plus longue n'est pas entamee.
    assert rewrite_citation_keys("Texte [@src1_2019_abc].", correspondance) == (
        "Texte [@src1_2019_abc]."
    )
    # Un renvoi croise n'est pas une cle de citation.
    assert rewrite_citation_keys("Voir @fig-resultats.", correspondance) == ("Voir @fig-resultats.")


def test_rewrite_without_map_is_identity() -> None:
    assert rewrite_citation_keys("Texte [@src1_2019_a].", {}) == "Texte [@src1_2019_a]."


async def test_assembler_rewrites_drafting_keys_to_published_keys(project_db: Path) -> None:
    """La cle de redaction porte l'identifiant de source, la cle publiee suit
    §9.3 : les deux formes ne peuvent pas coincider, la correspondance est
    faite ici pour que les citations du PDF se resolvent."""
    await peupler(
        project_db,
        {1: "La filtration decroit [@src1_2019_genomique]."},
        cles={"src1_2019_genomique": "dupont_2019_genomique"},
    )

    async with connect(project_db) as conn:
        document = await assemble(conn, plan(), sections_par_noeud([1]))

        # Le texte STOCKE n'est pas touche : c'est celui de l'auteur.
        async with conn.execute("SELECT content_qmd FROM draft_section WHERE id = 1") as cur:
            stocke = str((await cur.fetchone())[0])

    assert "[@dupont_2019_genomique]" in document.qmd
    assert "src1_2019_genomique" not in document.qmd
    assert "[@src1_2019_genomique]" in stocke


# --- Bibliographie et point d'extension -----------------------------------


async def test_document_ends_with_a_references_section(project_db: Path) -> None:
    """Sans le bloc `#refs`, Pandoc place la bibliographie en fin de fichier
    sans titre — invisible dans une table des matieres."""
    await peupler(project_db, {1: "Contenu."})

    async with connect(project_db) as conn:
        document = await assemble(conn, plan(), sections_par_noeud([1]))

    assert "# Références {.unnumbered}" in document.qmd
    assert "::: {#refs}" in document.qmd


@pytest.mark.parametrize("demande", [True, False])
async def test_ai_declaration_anchor_is_only_a_placeholder(project_db: Path, demande: bool) -> None:
    """Le contenu releve d'US-EXPORT-003 : seul l'emplacement est pose."""
    await peupler(project_db, {1: "Contenu."})

    async with connect(project_db) as conn:
        document = await assemble(conn, plan(), sections_par_noeud([1]), demande)

    assert (AI_DECLARATION_ANCHOR in document.qmd) is demande


async def test_no_myst_syntax_in_assembled_document(project_db: Path) -> None:
    """ADR-006 : aucun repli MyST nulle part dans la chaine."""
    await peupler(project_db, {1: "Contenu [@src1_2019_a]."})

    async with connect(project_db) as conn:
        document = await assemble(conn, plan(), sections_par_noeud([1]))

    for interdit in (":::{note}", "{ref}`", "{numref}`", "{cite}`"):
        assert interdit not in document.qmd
