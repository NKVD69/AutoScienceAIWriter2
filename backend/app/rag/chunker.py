"""Découpage en chunks — US-102, spécifications §7.1.

Deux stratégies, dans cet ordre : aux frontières de sections quand le document
en expose, sinon par fenêtre glissante. Un découpage aveugle coupe au milieu
d'un raisonnement et produit des extraits qu'un rédacteur ne peut pas citer.

**La section References est marquée et écartée de la rédaction.** Elle sert à
l'extraction bibliographique. La rendre au rédacteur lui donnerait une liste
de titres d'articles qu'il n'a pas lus — la matière première exacte d'une
citation inventée.

**`page_start` et `page_end` sont calculés à partir de l'offset des caractères**
dans le texte paginé, jamais estimés à partir d'une longueur moyenne.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from app.core.config import get_settings
from app.rag.extractor import ExtractedDocument

# Approximation usuelle pour du texte académique latin. Sert au découpage,
# jamais à la facturation : une borne approchée suffit ici, et le vrai compte
# de tokens dépendrait du tokeniseur du modèle.
CHARS_PER_TOKEN = 4


class SectionKind(StrEnum):
    BODY = "body"
    REFERENCES = "references"


# Intitulés canoniques d'article, français et anglais. La casse est ignorée.
_CANONICAL = (
    r"abstract|r[ée]sum[ée]|introduction|state of the art|[ée]tat de l'art|"
    r"related works?|travaux (?:connexes|ant[ée]rieurs)|"
    r"m[ée]thodes?|methods?|methodology|m[ée]thodologie|mat[ée]riel et m[ée]thodes|"
    r"materials? and methods?|r[ée]sultats?|results?|discussion|"
    r"conclusions?|perspectives|remerciements|acknowledgements?|"
    r"r[ée]f[ée]rences?|references?|bibliographie|bibliography|annexes?|appendix"
)

# Un titre est soit numéroté (« 3.2 Méthodes »), soit un intitulé canonique
# seul sur sa ligne. Exiger la ligne entière évite de couper sur le mot
# « discussion » rencontré dans une phrase.
_HEADING = re.compile(
    rf"^(?:(?:\d+(?:\.\d+)*\.?\s+\S.*)|(?:(?:{_CANONICAL})\s*:?))$",
    re.IGNORECASE | re.MULTILINE,
)

_REFERENCES_TITLE = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*\.?\s+)?"
    r"(?:r[ée]f[ée]rences?|references?|bibliographie|bibliography)\s*:?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Chunk:
    """Fragment indexable, porteur de sa provenance."""

    ordinal: int
    text: str
    page_start: int
    page_end: int
    section_kind: SectionKind = SectionKind.BODY
    section_title: str | None = None

    @property
    def token_count(self) -> int:
        return max(1, len(self.text) // CHARS_PER_TOKEN)


@dataclass(frozen=True)
class _Section:
    title: str | None
    start: int
    end: int

    @property
    def kind(self) -> SectionKind:
        if self.title and _REFERENCES_TITLE.match(self.title):
            return SectionKind.REFERENCES
        return SectionKind.BODY


def detect_sections(text: str) -> list[_Section]:
    """Frontières de sections détectées par les titres. Vide si aucun."""
    titres = [(m.start(), m.group().strip()) for m in _HEADING.finditer(text)]
    if not titres:
        return []

    sections: list[_Section] = []
    if titres[0][0] > 0:
        sections.append(_Section(title=None, start=0, end=titres[0][0]))
    for index, (debut, titre) in enumerate(titres):
        fin = titres[index + 1][0] if index + 1 < len(titres) else len(text)
        sections.append(_Section(title=titre, start=debut, end=fin))
    return sections


def _sliding_windows(start: int, end: int, taille: int, recouvrement: int) -> list[tuple[int, int]]:
    """Fenêtres d'offsets, bornées au segment. Ne franchit jamais `end`."""
    if end <= start:
        return []
    pas = max(1, taille - recouvrement)
    fenetres: list[tuple[int, int]] = []
    curseur = start
    while curseur < end:
        fin = min(curseur + taille, end)
        fenetres.append((curseur, fin))
        if fin >= end:
            break
        curseur += pas
    return fenetres


def chunk_document(
    document: ExtractedDocument,
    chunk_tokens: int | None = None,
    chunk_overlap: int | None = None,
) -> list[Chunk]:
    """Découpe un document extrait. Chaque chunk porte ses pages réelles."""
    settings = get_settings()
    taille = (chunk_tokens or settings.chunk_tokens) * CHARS_PER_TOKEN
    recouvrement = (chunk_overlap or settings.chunk_overlap) * CHARS_PER_TOKEN

    texte = document.full_text()
    if not texte.strip():
        return []

    # Un chunk ne franchit jamais une frontière de document : les segments
    # sont bornés au texte de CE document, et la fenêtre glissante ne
    # dépasse jamais la fin d'une section.
    sections = detect_sections(texte) or [_Section(title=None, start=0, end=len(texte))]

    chunks: list[Chunk] = []
    for section in sections:
        for debut, fin in _sliding_windows(section.start, section.end, taille, recouvrement):
            fragment = texte[debut:fin].strip()
            if not fragment:
                continue
            chunks.append(
                Chunk(
                    ordinal=len(chunks),
                    text=fragment,
                    # Offsets réels → pages réelles.
                    page_start=document.page_at(debut),
                    page_end=document.page_at(max(debut, fin - 1)),
                    section_kind=section.kind,
                    section_title=section.title,
                )
            )
    return chunks
