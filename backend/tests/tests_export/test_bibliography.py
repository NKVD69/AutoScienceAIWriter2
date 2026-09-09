"""US-501 — compilation du .bib : selection, cles, echappement, determinisme."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.db.migrations.runner import run_migrations
from app.db.session import connect, transaction
from app.export.bibliography import (
    PREPRINT_NOTE,
    BibEntry,
    UnverifiedCitationError,
    assign_keys,
    base_key,
    build_bibliography,
    cited_sources,
    escape_latex,
    existing_keys,
    first_author,
    includable_sections,
    protect_acronyms,
    render_bib,
    title_word,
)
from app.models.section import SectionStatus

NOW = datetime.now(UTC).isoformat()

# (id, kind, titre, auteurs, annee, venue, doi, preprint)
SOURCES = (
    (
        1,
        "article",
        "La genomique des populations marines",
        "Dupont, Alice; Martin, Bob",
        2019,
        "Revue de biologie",
        "10.1000/abc",
        0,
    ),
    # Meme auteur, meme annee, meme premier mot significatif : collision.
    (2, "book", "Genomique appliquee", "Dupont, Alice", 2019, "Presses", None, 0),
    (
        3,
        "preprint",
        "Resultats preliminaires sur l ADN mitochondrial",
        "Nguyen, Chi",
        2024,
        "bioRxiv",
        None,
        1,
    ),
    # Jamais citee : ne doit pas apparaitre dans le .bib.
    (4, "article", "Une source importee mais jamais citee", "Ignore, Ida", 2015, None, None, 0),
    # Citee seulement par une section ORPHANED.
    (5, "report", "Rapport rattache a une section orpheline", "Perdu, Paul", 2018, None, None, 0),
    # Titre a echapper, acronymes a proteger.
    (6, "article", "Analyse ADN & IRM : 100% des cas_limites", "Ecole, Emma", 2022, None, None, 0),
)

NODES = ((1, "Chapitre premier"), (2, "Chapitre second"))


async def peupler(db: Path) -> None:
    """Projet, sources, plan, sections et citations."""
    async with connect(db) as conn:
        await run_migrations(conn)
        async with transaction(conn):
            await conn.execute(
                "INSERT INTO project (id, name, subject, language, academic_level,"
                " created_at, updated_at) VALUES (1,'These','Sujet','fr','doctorat',?,?)",
                (NOW, NOW),
            )
            for sid, kind, titre, auteurs, annee, venue, doi, preprint in SOURCES:
                await conn.execute(
                    "INSERT INTO source_document (id, project_id, kind, title, authors,"
                    " year, venue, doi, is_preprint, imported_at, approved_at)"
                    " VALUES (?,1,?,?,?,?,?,?,?,?,?)",
                    (sid, kind, titre, auteurs, annee, venue, doi, preprint, NOW, NOW),
                )

            await conn.execute(
                "INSERT INTO plan (id, project_id, problematique, status, version, created_at)"
                " VALUES (1,1,'Problematique','VALIDATED',1,?)",
                (NOW,),
            )
            for nid, titre in NODES:
                await conn.execute(
                    "INSERT INTO plan_node (id, plan_id, parent_id, ordinal, level, title,"
                    " objective, target_words) VALUES (?,1,NULL,?,1,?,'Objectif',1000)",
                    (nid, nid - 1, titre),
                )

            # Section incluse : cite 1, 2, 3 et 6.
            await conn.execute(
                "INSERT INTO draft_section (id, plan_node_id, content_qmd, status, version,"
                " generated_at) VALUES (1,1,'Texte [@src1_2019_genomique].',?,1,?)",
                (str(SectionStatus.REVIEWING), NOW),
            )
            for cid, source_id, cle in (
                (1, 1, "src1_2019_genomique"),
                (2, 2, "src2_2019_genomique"),
                (3, 3, "src3_2024_resultats"),
                (4, 6, "src6_2022_analyse"),
            ):
                await conn.execute(
                    "INSERT INTO citation (id, draft_section_id, source_id, bibtex_key,"
                    " verified) VALUES (?,1,?,?,1)",
                    (cid, source_id, cle),
                )

            # Section ORPHANED : cite 5, qui ne doit pas entrer.
            await conn.execute(
                "INSERT INTO draft_section (id, plan_node_id, content_qmd, status, version,"
                " generated_at) VALUES (2,2,'Texte orphelin.',?,1,?)",
                (str(SectionStatus.ORPHANED), NOW),
            )
            await conn.execute(
                "INSERT INTO citation (id, draft_section_id, source_id, bibtex_key,"
                " verified) VALUES (5,2,5,'src5_2018_rapport',1)"
            )


async def sections_incluses(conn) -> list[int]:
    return sorted((await includable_sections(conn)).values())


# --- Selection des entrees ------------------------------------------------


async def test_bib_contains_only_verified_cited_sources(project_db: Path) -> None:
    """Ni plus, ni moins : une entree orpheline se lit comme une source
    consultee qui ne l'a pas ete."""
    await peupler(project_db)
    async with connect(project_db) as conn:
        bib, entrees = await build_bibliography(conn, await sections_incluses(conn))

    assert {e.source_id for e in entrees} == {1, 2, 3, 6}
    assert "jamais citee" not in bib, "une source importee non citee n'entre pas"
    assert "orpheline" not in bib


async def test_bib_excludes_orphaned_sections(project_db: Path) -> None:
    """Le noeud d'une section ORPHANED n'existe plus : elle n'a pas de place
    dans le document, donc pas de citation dans la bibliographie."""
    await peupler(project_db)
    async with connect(project_db) as conn:
        incluses = await sections_incluses(conn)
        assert 2 not in incluses, "la section ORPHANED n'est pas incluse"
        _, entrees = await build_bibliography(conn, incluses)

    assert 5 not in {e.source_id for e in entrees}


async def test_export_fails_on_unverified_citation_with_location(project_db: Path) -> None:
    """Jamais ignoree, jamais incluse quand meme."""
    await peupler(project_db)
    async with connect(project_db) as conn:
        async with transaction(conn):
            await conn.execute("UPDATE citation SET verified = 0 WHERE id = 2")

        with pytest.raises(UnverifiedCitationError) as exc:
            await build_bibliography(conn, await sections_incluses(conn))

    charge = exc.value.to_payload()
    assert charge["code"] == "UNVERIFIED_CITATION"
    assert exc.value.status_code == 409
    bloquant = charge["blocking_items"][0]
    assert bloquant["section_id"] == 1
    assert bloquant["bibtex_key"] == "src2_2019_genomique"
    # Le passage permet de retrouver le texte fautif, pas seulement de savoir
    # qu'il existe.
    assert "Texte [@src1_2019_genomique]." in bloquant["excerpt"]
    assert charge["required_state"] == "ALL_CITATIONS_VERIFIED"


async def test_user_bib_never_used_for_compilation(project_db: Path) -> None:
    """Le .bib importe alimente source_document ; il ne compile jamais
    (ADR-007). Un fichier depose a cote du projet reste sans effet."""
    await peupler(project_db)
    intrus = project_db.parent / "bibliographie-utilisateur.bib"
    intrus.write_text(
        "@article{utilisateur_1999_intrus,\n  title = {Entree venue du fichier "
        "utilisateur},\n  year = {1999}\n}\n",
        encoding="utf-8",
    )

    async with connect(project_db) as conn:
        bib, entrees = await build_bibliography(conn, await sections_incluses(conn))

    assert "utilisateur_1999_intrus" not in bib
    assert "Entree venue du fichier" not in bib
    assert all(e.source_id in {1, 2, 3, 6} for e in entrees)


async def test_cited_sources_reads_only_the_database(project_db: Path) -> None:
    await peupler(project_db)
    async with connect(project_db) as conn:
        sources = await cited_sources(conn, await sections_incluses(conn))

    assert [s.source_id for s in sources] == [1, 2, 3, 6]
    assert [s.entry_type for s in sources] == ["article", "book", "misc", "article"]


# --- Normalisation des cles -----------------------------------------------


def test_key_normalization_format() -> None:
    """Forme premierauteur_annee_motclefdutitre, ASCII, minuscules."""
    assert base_key("Dupont, Alice; Martin, Bob", 2019, "La genomique des populations") == (
        "dupont_2019_genomique"
    )
    # Graphie « Prenom Nom », sans virgule.
    assert base_key("Alice Dupont and Bob Martin", 2019, "Genomique") == "dupont_2019_genomique"
    # Accents et ponctuation retires.
    assert base_key("Müller, Éric", 2021, "L'évolution des espèces") == "muller_2021_evolution"
    # Auteur inconnu, annee inconnue.
    assert base_key(None, None, "Un titre quelconque") == "anon_nd_titre"
    assert base_key("   ", 2020, "Methodes") == "anon_2020_methodes"


def test_title_word_skips_stopwords() -> None:
    """`de_2019_les` n'identifierait rien."""
    assert title_word("Les methodes de dosage") == "methodes"
    assert title_word("The and for") == "sanstitre"


@pytest.mark.parametrize(
    ("saisie", "attendu"),
    [
        # « Nom, Prenom » separe par point-virgule.
        ("Dupont, Alice; Martin, Bob", "dupont"),
        # « Prenom Nom » separe par « and ».
        ("Alice Dupont and Bob Martin", "dupont"),
        # « Prenom Nom » separe par virgule : la virgule ne dit pas laquelle
        # des deux graphies on lit, c'est le mot retenu qui tranche.
        ("Alice Dupont, Bob Martin", "dupont"),
        # Initiale en fin : le nom est en tete.
        ("Dupont A., Martin B.", "dupont"),
        # Nom de deux lettres : ce n'est pas une initiale.
        ("Wei Li", "li"),
        ("Li, Wei", "li"),
        ("Müller, Éric", "muller"),
        (None, "anon"),
        ("   ", "anon"),
    ],
)
def test_first_author_handles_every_notation(saisie: str | None, attendu: str) -> None:
    assert first_author(saisie) == attendu


def test_key_collision_gets_stable_suffix() -> None:
    """Le premier occupant garde la cle nue : la suffixer le jour ou un
    homonyme arrive casserait les renvois d'un chapitre deja relu."""
    sources = [
        BibEntry(
            source_id=7,
            key="",
            entry_type="article",
            title="Genomique",
            authors="Dupont, Alice",
            year=2019,
        ),
        BibEntry(
            source_id=3,
            key="",
            entry_type="book",
            title="Genomique appliquee",
            authors="Dupont, Alice",
            year=2019,
        ),
        BibEntry(
            source_id=9,
            key="",
            entry_type="article",
            title="Genomique comparee",
            authors="Dupont, Alice",
            year=2019,
        ),
    ]
    clefs = assign_keys(sources)

    # L'ordre de suffixage suit l'identifiant de source, qui ne bouge jamais.
    assert clefs[3] == "dupont_2019_genomique"
    assert clefs[7] == "dupont_2019_genomiquea"
    assert clefs[9] == "dupont_2019_genomiqueb"
    assert len(set(clefs.values())) == 3

    # Deterministe : l'ordre de la liste d'entree n'influe pas.
    assert assign_keys(list(reversed(sources))) == clefs


def test_existing_key_is_reserved_against_a_newcomer() -> None:
    """Une source nouvelle ne peut pas s'emparer de la cle d'une ancienne."""
    ancienne = {3: "dupont_2019_genomique"}
    nouvelle = [
        BibEntry(
            source_id=1,
            key="",
            entry_type="article",
            title="Genomique",
            authors="Dupont, Alice",
            year=2019,
        )
    ]
    assert assign_keys(nouvelle, ancienne)[1] == "dupont_2019_genomiquea"


async def test_key_persisted_and_reused_across_exports(project_db: Path) -> None:
    """Une cle qui change d'un export a l'autre casse les renvois d'un
    document deja relu."""
    await peupler(project_db)
    async with connect(project_db) as conn:
        _, premier = await build_bibliography(conn, await sections_incluses(conn))
        persistees = await existing_keys(conn)

        # Une source homonyme est importee entre les deux exports.
        async with transaction(conn):
            await conn.execute(
                "INSERT INTO source_document (id, project_id, kind, title, authors, year,"
                " is_preprint, imported_at) VALUES (99,1,'article','Genomique nouvelle',"
                "'Dupont, Alice',2019,0,?)",
                (NOW,),
            )
        _, second = await build_bibliography(conn, await sections_incluses(conn))

    assert persistees[1] == "dupont_2019_genomique"
    assert {e.source_id: e.key for e in premier} == {e.source_id: e.key for e in second}


# --- Rendu ----------------------------------------------------------------


def test_latex_escaping_in_titles() -> None:
    assert escape_latex("100% & plus_encore") == r"100\% \& plus\_encore"
    assert escape_latex("cout de 30$") == r"cout de 30\$"
    # L'antislash est traite en premier, sinon les echappements introduits
    # seraient echappes a leur tour.
    assert escape_latex(r"a\b") == r"a\textbackslash{}b"


def test_acronym_case_protected() -> None:
    """BibTeX abaisse la casse des titres : sans accolades, ADN devient adn."""
    assert protect_acronyms("Analyse ADN et IRM") == "Analyse {ADN} et {IRM}"
    # Un mot capitalise ordinaire n'est pas un acronyme.
    assert protect_acronyms("Analyse De Cas") == "Analyse De Cas"


async def test_rendered_entry_escapes_and_protects(project_db: Path) -> None:
    await peupler(project_db)
    async with connect(project_db) as conn:
        bib, _ = await build_bibliography(conn, await sections_incluses(conn))

    assert r"{ADN}" in bib and r"{IRM}" in bib
    assert r"\&" in bib and r"\%" in bib and r"\_" in bib


async def test_preprint_note_present(project_db: Path) -> None:
    """Un resultat non relu par les pairs cite comme un article publie est
    une erreur de methode. Le marquage traverse tout le pipeline."""
    await peupler(project_db)
    async with connect(project_db) as conn:
        bib, entrees = await build_bibliography(conn, await sections_incluses(conn))

    prepublication = next(e for e in entrees if e.source_id == 3)
    assert prepublication.is_preprint
    assert PREPRINT_NOTE in bib
    assert bib.count("note = {") == 1, "seule la prepublication porte la note"


async def test_bib_output_deterministic(project_db: Path) -> None:
    """Deux exports du meme etat produisent deux fichiers identiques octet
    pour octet : comparer deux versions d'un memoire ne doit pas faire
    apparaitre un bruit qui n'est pas du travail."""
    await peupler(project_db)
    async with connect(project_db) as conn:
        incluses = await sections_incluses(conn)
        premier, _ = await build_bibliography(conn, incluses)
        second, _ = await build_bibliography(conn, incluses)

    assert premier.encode("utf-8") == second.encode("utf-8")


def test_entries_sorted_by_key() -> None:
    entrees = [
        BibEntry(source_id=2, key="zeta_2020_titre", entry_type="article", title="Zeta"),
        BibEntry(source_id=1, key="alpha_2019_titre", entry_type="article", title="Alpha"),
    ]
    rendu = render_bib(entrees)
    assert rendu.index("alpha_2019_titre") < rendu.index("zeta_2020_titre")


def test_empty_bibliography_is_still_valid() -> None:
    """Un document sans citation compile : le .bib est vide, pas absent."""
    rendu = render_bib([])
    assert rendu.startswith("%")
    assert "@" not in rendu
