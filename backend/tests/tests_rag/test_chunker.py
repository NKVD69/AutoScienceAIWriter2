"""US-102 — decoupage : sections, repli, pagination, bibliographie ecartee."""

from __future__ import annotations

from pathlib import Path

from app.rag.chunker import CHARS_PER_TOKEN, SectionKind, chunk_document, detect_sections
from app.rag.extractor import ExtractedDocument, Page, extract
from tests.fixtures import make_article_pdf


def document(*pages: str) -> ExtractedDocument:
    return ExtractedDocument(pages=[Page(i + 1, t) for i, t in enumerate(pages)])


# --- Detection de sections ------------------------------------------------


def test_detect_sections_on_canonical_titles() -> None:
    texte = "Abstract\nun bref apercu\n\n1. Introduction\ncontenu\n\nMethods\nplus de contenu"
    titres = [s.title for s in detect_sections(texte)]
    assert titres == ["Abstract", "1. Introduction", "Methods"]


def test_french_canonical_titles_are_recognised() -> None:
    """« Resume » seul sur sa ligne EST un titre de section, pas du corps."""
    for titre in ("Resume", "Résumé", "Methodes", "Resultats", "Conclusion"):
        sections = detect_sections(f"{titre}\ndu contenu qui suit le titre")
        assert [s.title for s in sections] == [titre], f"« {titre} » non reconnu"


def test_detect_sections_ignores_words_inside_sentences() -> None:
    """« discussion » au fil d'une phrase n'est pas un titre de section."""
    texte = "Une longue phrase ou la discussion des resultats est evoquee sans titre."
    assert detect_sections(texte) == []


def test_detect_sections_keeps_a_preamble() -> None:
    sections = detect_sections("Texte liminaire\n\n1. Introduction\ncontenu")
    assert sections[0].title is None
    assert sections[1].title == "1. Introduction"


def test_chunker_splits_on_section_boundaries() -> None:
    corps = "Phrase de contenu scientifique. " * 5
    doc = document(f"1. Introduction\n{corps}\n\n2. Methods\n{corps}\n\nResults\n{corps}")
    chunks = chunk_document(doc, chunk_tokens=4096, chunk_overlap=0)

    titres = [c.section_title for c in chunks]
    assert titres == ["1. Introduction", "2. Methods", "Results"]
    # Un chunk ne melange jamais deux sections.
    assert "Methods" not in chunks[0].text


def test_chunker_falls_back_to_sliding_window() -> None:
    """Sans titre detectable, fenetre glissante bornee au document."""
    doc = document("mot " * 2000)
    chunks = chunk_document(doc, chunk_tokens=100, chunk_overlap=20)
    assert len(chunks) > 1
    assert all(c.section_title is None for c in chunks)
    assert all(c.section_kind is SectionKind.BODY for c in chunks)


def test_chunker_windows_overlap() -> None:
    doc = document("".join(f"{i:04d} " for i in range(500)))
    chunks = chunk_document(doc, chunk_tokens=50, chunk_overlap=10)
    taille = 50 * CHARS_PER_TOKEN
    assert all(len(c.text) <= taille for c in chunks)
    assert len(chunks) >= 2


def test_chunker_never_crosses_document_boundary() -> None:
    """La fenetre s'arrete au texte de CE document, jamais au-dela."""
    doc = document("court")
    chunks = chunk_document(doc, chunk_tokens=500, chunk_overlap=100)
    assert len(chunks) == 1
    assert chunks[0].text == "court"
    assert chunks[0].page_end == 1


def test_chunker_returns_nothing_on_empty_document() -> None:
    assert chunk_document(document("", "   ")) == []


def test_chunker_ordinals_are_contiguous() -> None:
    doc = document("mot " * 800)
    chunks = chunk_document(doc, chunk_tokens=60, chunk_overlap=10)
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


# --- Pagination — exigence bloquante --------------------------------------


def test_chunk_has_page_start_and_page_end(tmp_path: Path) -> None:
    """Un chunk sans pagination issu d'un PDF est un defaut bloquant."""
    chunks = chunk_document(extract(make_article_pdf(tmp_path / "article.pdf")))
    assert chunks
    for chunk in chunks:
        assert chunk.page_start >= 1, f"chunk {chunk.ordinal} sans page de debut"
        assert chunk.page_end >= chunk.page_start


def test_pages_follow_the_document_order(tmp_path: Path) -> None:
    chunks = chunk_document(extract(make_article_pdf(tmp_path / "article.pdf")))
    debuts = [c.page_start for c in chunks]
    assert debuts == sorted(debuts), "les pages doivent progresser avec le texte"
    assert max(c.page_end for c in chunks) <= 6


def test_page_numbers_are_computed_not_estimated() -> None:
    """Trois pages de tailles tres inegales : une estimation par longueur
    moyenne se tromperait, un calcul par offset non."""
    doc = document("a" * 20, "b" * 4000, "c" * 20)
    chunks = chunk_document(doc, chunk_tokens=200, chunk_overlap=0)
    pages_vues = {c.page_start for c in chunks}
    assert 2 in pages_vues
    assert all(1 <= c.page_start <= 3 for c in chunks)


# --- Bibliographie --------------------------------------------------------


def test_references_section_marked_and_excluded(tmp_path: Path) -> None:
    """La bibliographie est indexee a part : la rendre au redacteur lui
    donnerait une liste de titres qu'il n'a pas lus."""
    chunks = chunk_document(extract(make_article_pdf(tmp_path / "article.pdf")))
    references = [c for c in chunks if c.section_kind is SectionKind.REFERENCES]
    corps = [c for c in chunks if c.section_kind is SectionKind.BODY]

    assert references, "la section References doit être détectée"
    assert corps, "le corps doit rester indexé"
    assert all("References" in (c.section_title or "") for c in references)
    assert not any("Dupont A., Etude des microplastiques" in c.text for c in corps)


def test_french_bibliography_title_is_recognised() -> None:
    for titre in ("Bibliographie", "Références", "6. References", "REFERENCES"):
        doc = document(f"1. Introduction\ncontenu\n\n{titre}\n[1] Un article, 2020.")
        chunks = chunk_document(doc, chunk_tokens=4096, chunk_overlap=0)
        genres = {c.section_kind for c in chunks}
        assert SectionKind.REFERENCES in genres, f"« {titre} » non reconnu"


def test_token_count_is_reported() -> None:
    chunks = chunk_document(document("mot " * 400), chunk_tokens=100, chunk_overlap=0)
    assert all(c.token_count >= 1 for c in chunks)
