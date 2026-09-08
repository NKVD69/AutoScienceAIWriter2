"""US-102 — extraction paginee. La pagination conditionne la verifiabilite."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.rag.extractor import (
    PAGE_SEPARATOR,
    ExtractedDocument,
    Page,
    ScannedPdfError,
    UnsupportedDocumentError,
    extract,
)
from tests.fixtures import make_article_pdf, make_headered_pdf, make_scanned_pdf, make_text_file


def test_extract_preserves_page_numbers(tmp_path: Path) -> None:
    """Sans pagination, une citation n'est pas verifiable dans le PDF."""
    document = extract(make_article_pdf(tmp_path / "article.pdf"))
    assert len(document.pages) == 6
    assert [p.number for p in document.pages] == [1, 2, 3, 4, 5, 6]
    assert all(p.text.strip() for p in document.pages)


def test_page_at_maps_offsets_to_real_pages() -> None:
    """Les pages sont calculees depuis l'offset reel, jamais estimees."""
    document = ExtractedDocument(pages=[Page(1, "aaaa"), Page(2, "bbbbbb"), Page(3, "cc")])
    texte = document.full_text()
    assert texte == "aaaa" + PAGE_SEPARATOR + "bbbbbb" + PAGE_SEPARATOR + "cc"

    assert document.page_at(0) == 1
    assert document.page_at(3) == 1
    assert document.page_at(texte.index("bbbbbb")) == 2
    assert document.page_at(texte.index("cc")) == 3
    # Au-dela du texte : derniere page, pas une erreur.
    assert document.page_at(10_000) == 3


def test_page_at_on_empty_document() -> None:
    assert ExtractedDocument(pages=[]).page_at(0) == 1


def test_extract_removes_recurring_headers(tmp_path: Path) -> None:
    """Un titre courant repete deux cents fois polluerait deux cents chunks."""
    chemin = make_headered_pdf(tmp_path / "entetes.pdf", pages=6)
    document = extract(chemin)
    texte = document.full_text()

    assert "Revue de Science Appliquee" not in texte
    assert "ne pas diffuser" not in texte
    # Le contenu propre a chaque page survit.
    assert "Contenu propre a la page 1" in texte
    assert "Contenu propre a la page 6" in texte


def test_recurring_removal_skipped_on_short_documents(tmp_path: Path) -> None:
    """Sur deux pages, une ligne commune n'est pas un en-tete recurrent."""
    chemin = make_headered_pdf(tmp_path / "court.pdf", pages=2)
    assert "Revue de Science Appliquee" in extract(chemin).full_text()


def test_extract_scanned_pdf_raises_actionable_error(tmp_path: Path) -> None:
    """Indexer des pages vides donnerait une base peuplee en apparence."""
    chemin = make_scanned_pdf(tmp_path / "numerise.pdf")
    with pytest.raises(ScannedPdfError) as exc:
        extract(chemin)

    message = exc.value.message
    assert "numerise.pdf" in message
    assert "reconnaissance optique" in message
    assert exc.value.status_code == 422


def test_extract_text_file_is_one_page(tmp_path: Path) -> None:
    document = extract(make_text_file(tmp_path / "note.txt"))
    assert len(document.pages) == 1
    assert document.pages[0].number == 1
    assert "Introduction" in document.pages[0].text


def test_extract_markdown_is_supported(tmp_path: Path) -> None:
    document = extract(make_text_file(tmp_path / "note.md", "# Titre\n\nDu contenu."))
    assert document.pages[0].number == 1


def test_extract_rejects_unsupported_extension(tmp_path: Path) -> None:
    fichier = tmp_path / "tableur.xlsx"
    fichier.write_bytes(b"pas un document texte")
    with pytest.raises(UnsupportedDocumentError, match="xlsx"):
        extract(fichier)


def test_pdf_metadata_is_proposed(tmp_path: Path) -> None:
    """Les metadonnees sont proposees, jamais tenues pour verifiees."""
    document = extract(make_article_pdf(tmp_path / "article.pdf"))
    assert document.metadata.get("title") == "Titre de l'article de test"
    assert document.metadata.get("authors") == "Dupont, Martin"
