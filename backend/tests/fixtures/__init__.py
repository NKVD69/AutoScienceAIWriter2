"""PDF de test GÉNÉRÉS, jamais téléchargés — US-102, point 8.

La suite doit passer hors ligne : un jeu d'essai téléchargé ferait dépendre
les tests d'un réseau et d'un tiers, et rendrait leur résultat non
reproductible dans six mois.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

MARGE = 50
INTERLIGNE = 14
TAILLE = 10


def _ecrire_pages(document: pymupdf.Document, pages: list[list[str]]) -> None:
    for lignes in pages:
        page = document.new_page()
        y = MARGE
        for ligne in lignes:
            page.insert_text((MARGE, y), ligne, fontsize=TAILLE)
            y += INTERLIGNE


def make_article_pdf(path: Path, paragraphes_par_section: int = 6) -> Path:
    """Article à sections canoniques, réparti sur plusieurs pages."""
    corps = "Le transport membranaire en milieu marin conditionne la filtration renale. "

    def bloc(titre: str, n: int) -> list[str]:
        return [titre, *[f"{corps}Paragraphe {i + 1} de la section." for i in range(n)]]

    pages = [
        ["Titre de l'article de test", *bloc("Abstract", 2)],
        bloc("1. Introduction", paragraphes_par_section),
        bloc("2. Methods", paragraphes_par_section),
        bloc("3. Results", paragraphes_par_section),
        bloc("4. Discussion", paragraphes_par_section),
        [
            "References",
            "[1] Dupont A., Etude des microplastiques, Journal, 2021.",
            "[2] Martin B., Fonction renale comparee, Revue, 2019.",
            "[3] Nguyen C., Transport membranaire, Annales, 2023.",
        ],
    ]

    document = pymupdf.open()
    _ecrire_pages(document, pages)
    document.set_metadata({"title": "Titre de l'article de test", "author": "Dupont, Martin"})
    document.save(path)
    document.close()
    return path


def make_headered_pdf(path: Path, pages: int = 6) -> Path:
    """Document dont chaque page porte le même titre courant et le même pied."""
    entete = "Revue de Science Appliquee — Volume 12"
    pied = "Page confidentielle — ne pas diffuser"
    contenu = [
        [
            entete,
            f"Contenu propre a la page {n + 1}, distinct des autres pages du document.",
            f"Seconde ligne specifique numero {n + 1} pour donner de la matiere.",
            pied,
        ]
        for n in range(pages)
    ]
    document = pymupdf.open()
    _ecrire_pages(document, contenu)
    document.save(path)
    document.close()
    return path


def make_scanned_pdf(path: Path, pages: int = 3) -> Path:
    """PDF sans couche texte : des pages vides, comme un document numérisé."""
    document = pymupdf.open()
    for _ in range(pages):
        document.new_page()
    document.save(path)
    document.close()
    return path


def make_text_file(path: Path, contenu: str | None = None) -> Path:
    path.write_text(
        contenu
        or (
            "Introduction\n\n"
            + "Une phrase de contenu scientifique repetee pour donner du volume. " * 40
        ),
        encoding="utf-8",
    )
    return path
