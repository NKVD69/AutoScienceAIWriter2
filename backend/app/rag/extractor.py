"""Extraction de texte paginé — US-102, spécifications §7.1.

**La pagination n'est pas une métadonnée d'agrément.** Une citation qu'on ne
peut pas retrouver dans le PDF d'origine n'est pas vérifiable, et une citation
non vérifiable est sans valeur dans un mémoire. Le numéro de page voyage donc
depuis l'extraction jusqu'au chunk, sans jamais être estimé : il est déduit de
l'offset réel des caractères.

**Aucune tentative d'OCR.** Un PDF sans couche texte produit des pages vides.
Les indexer silencieusement donnerait une base de connaissances qui paraît
peuplée et ne retourne rien — un défaut invisible jusqu'à la rédaction. On
lève, et le message dit quoi faire.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from app.core.errors import AppError
from app.core.logging import get_logger

logger = get_logger(__name__)

TEXT_SUFFIXES = {".txt", ".md"}
PDF_SUFFIXES = {".pdf"}
SUPPORTED_SUFFIXES = TEXT_SUFFIXES | PDF_SUFFIXES

# Une ligne présente sur plus de cette part des pages est un en-tête ou un
# pied de page : la conserver polluerait chaque chunk du document.
RECURRING_LINE_RATIO = 0.6
# En deçà, on considère qu'une page n'a pas de couche texte exploitable.
MIN_CHARS_PER_PAGE = 20
# Séparateur entre pages dans le texte assemblé. Sa longueur entre dans le
# calcul des offsets : la changer sans changer `page_at` fausserait les pages.
PAGE_SEPARATOR = "\n\n"


class UnsupportedDocumentError(AppError):
    code = "UNSUPPORTED_DOCUMENT"
    status_code = 422


class ScannedPdfError(AppError):
    """PDF sans couche texte. Une OCR est nécessaire, hors périmètre."""

    code = "SCANNED_PDF"
    status_code = 422

    @classmethod
    def for_file(cls, path: Path, pages: int) -> ScannedPdfError:
        return cls(
            f"« {path.name} » ne contient pas de couche texte exploitable "
            f"({pages} page(s) analysée(s)). C'est un document numérisé : une "
            "reconnaissance optique de caractères est nécessaire avant "
            "l'import. L'outil n'en effectue pas — l'indexer en l'état "
            "produirait une base de connaissances vide en apparence peuplée.",
            filename=path.name,
        )


@dataclass(frozen=True)
class Page:
    number: int
    text: str


@dataclass
class ExtractedDocument:
    """Texte paginé et métadonnées proposées, jamais vérifiées."""

    pages: list[Page]
    metadata: dict[str, str] = field(default_factory=dict)

    def full_text(self) -> str:
        return PAGE_SEPARATOR.join(p.text for p in self.pages)

    def page_at(self, offset: int) -> int:
        """Numéro de page contenant l'offset donné du texte assemblé.

        Calculé, jamais estimé : c'est la fonction dont dépend la
        vérifiabilité de toutes les citations du mémoire.
        """
        if not self.pages:
            return 1
        curseur = 0
        for page in self.pages:
            fin = curseur + len(page.text)
            if offset <= fin:
                return page.number
            curseur = fin + len(PAGE_SEPARATOR)
        return self.pages[-1].number


def _strip_recurring_lines(pages: list[Page]) -> list[Page]:
    """Retire les lignes présentes sur la majorité des pages.

    Un titre courant répété deux cents fois se retrouverait dans deux cents
    chunks, où il ferait du bruit à la recherche sans jamais rien apporter.
    """
    if len(pages) < 3:
        return pages

    compte: Counter[str] = Counter()
    for page in pages:
        for ligne in {line.strip() for line in page.text.splitlines() if line.strip()}:
            compte[ligne] += 1

    seuil = len(pages) * RECURRING_LINE_RATIO
    recurrentes = {ligne for ligne, n in compte.items() if n > seuil}
    if not recurrentes:
        return pages

    logger.debug("%s ligne(s) récurrente(s) retirée(s)", len(recurrentes))
    nettoyees: list[Page] = []
    for page in pages:
        gardees = [ln for ln in page.text.splitlines() if ln.strip() not in recurrentes]
        nettoyees.append(Page(number=page.number, text="\n".join(gardees).strip()))
    return nettoyees


def _extract_pdf(path: Path) -> ExtractedDocument:
    import pymupdf

    with pymupdf.open(path) as document:
        pages = [
            Page(number=index + 1, text=page.get_text("text").strip())
            for index, page in enumerate(document)
        ]
        brutes = document.metadata or {}

    utiles = sum(1 for p in pages if len(p.text) >= MIN_CHARS_PER_PAGE)
    if not pages or utiles == 0:
        raise ScannedPdfError.for_file(path, len(pages))

    metadata = {
        cle: str(valeur).strip()
        for cle, valeur in (
            ("title", brutes.get("title")),
            ("authors", brutes.get("author")),
            ("venue", brutes.get("subject")),
        )
        if valeur and str(valeur).strip()
    }
    return ExtractedDocument(pages=_strip_recurring_lines(pages), metadata=metadata)


def _extract_text_file(path: Path) -> ExtractedDocument:
    """`.txt` et `.md` : une page unique, numérotée 1."""
    contenu = path.read_text(encoding="utf-8", errors="replace").strip()
    return ExtractedDocument(pages=[Page(number=1, text=contenu)])


def extract(path: Path) -> ExtractedDocument:
    """Texte paginé d'un document. Lève sur un format non pris en charge."""
    suffixe = path.suffix.lower()
    if suffixe in PDF_SUFFIXES:
        return _extract_pdf(path)
    if suffixe in TEXT_SUFFIXES:
        return _extract_text_file(path)
    raise UnsupportedDocumentError(
        f"Extension « {suffixe or 'aucune'} » non prise en charge. "
        f"Formats acceptés : {', '.join(sorted(SUPPORTED_SUFFIXES))}.",
        filename=path.name,
    )
