"""Compilation du fichier `.bib` — US-501, ADR-007, spécifications §9.3.

**Le `.bib` est regénéré à chaque export depuis la base, et de rien d'autre.**
Le fichier `.bib` que l'utilisateur importe alimente `source_document` au
moment de l'import ; il ne sert JAMAIS à compiler. Compiler depuis lui
rendrait des entrées que le document ne cite pas, et laisserait passer des
citations que la base ne connaît pas — les deux défauts qu'ADR-007 existe
pour empêcher.

**Une citation non vérifiée fait échouer l'export.** Ni ignorée, ni incluse
« quand même » : une référence dont on ne sait plus si elle correspond au
texte est précisément ce qui détruit la crédibilité d'un mémoire, et un
export qui la laisse passer transforme un doute en affirmation imprimée.

**Une clé attribuée ne change plus.** Elle est écrite dans
`source_document.bibtex_key` au premier export et relue ensuite. La
résolution des collisions dépend de l'ensemble des sources : recalculer à
chaque export ferait glisser les suffixes dès qu'une source homonyme est
importée, et casserait les renvois d'un document déjà relu.

**La sortie est déterministe.** Deux exports du même état produisent deux
fichiers identiques octet pour octet — sans quoi comparer deux versions d'un
mémoire ferait apparaître un bruit qui n'est pas du travail.
"""

from __future__ import annotations

import re
import unicodedata

import aiosqlite
from pydantic import BaseModel

from app.core.errors import AppError
from app.core.logging import get_logger
from app.db.session import transaction
from app.models.section import SectionStatus

logger = get_logger(__name__)

# `kind` de la base → type BibTeX. Les cinq types de §9.3 ; tout ce qui n'a
# pas de forme bibliographique propre tombe en `misc`, qui n'affirme rien.
BIBTEX_TYPES = {
    "article": "article",
    "book": "book",
    "thesis": "phdthesis",
    "report": "techreport",
    "standard": "techreport",
    "preprint": "misc",
    "other": "misc",
}
DEFAULT_TYPE = "misc"

# Marquage de prépublication. Traverse tout le pipeline sans exception : un
# résultat non relu par les pairs cité comme un article publié est une
# erreur de méthode, pas une nuance de présentation.
PREPRINT_NOTE = "Prépublication, non révisé par les pairs"

# Caractères que LaTeX interprète.
#
# La substitution se fait en UN SEUL passage, par expression régulière. Une
# suite de `str.replace` serait fausse quel que soit l'ordre : `\` devient
# `\textbackslash{}`, qui contient des accolades ; les remplacer ensuite les
# échapperait, et les remplacer avant laisserait les antislashs nus. Un
# passage unique ne relit jamais ce qu'il vient d'écrire.
LATEX_ESCAPES = {
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}
LATEX_SPECIAL = re.compile("|".join(re.escape(c) for c in LATEX_ESCAPES))

# Les imports mélangent apostrophe ASCII et apostrophe typographique.
APOSTROPHE_TYPO = chr(0x2019)
# Élision française : « l'évolution » doit donner `evolution`, pas
# `levolution` — l'article collé au mot rendrait la clé illisible et
# différente de celle qu'un lecteur écrirait.
ELISION = re.compile(r"^(?:qu|[ldjnmtsc])'", re.I)

# Acronyme : au moins deux capitales consécutives. BibTeX abaisse la casse
# des titres selon le style ; sans protection, {ADN} devient « adn ».
ACRONYM = re.compile(r"(?<![\\{A-Za-z])([A-Z]{2,})(?![A-Za-z}])")

# Mots vides écartés du mot-clé de titre : `de_2019_les` n'identifie rien.
STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "into",
        "des",
        "les",
        "une",
        "aux",
        "par",
        "sur",
        "dans",
        "pour",
        "chez",
        "que",
        "qui",
        "est",
        "son",
        "sa",
        "ses",
        "leur",
        "leurs",
        "ce",
        "cet",
        "cette",
        "au",
        "la",
        "le",
        "un",
        "en",
        "of",
        "in",
        "on",
        "to",
        "a",
        "an",
        "et",
        "du",
        "d",
        "l",
    }
)

UNKNOWN_AUTHOR = "anon"
UNKNOWN_YEAR = "nd"
FALLBACK_WORD = "sanstitre"


class UnverifiedCitationError(AppError):
    """Une citation non vérifiée bloque l'export (ADR-007)."""

    code = "UNVERIFIED_CITATION"
    status_code = 409

    def __init__(self, blocking_items: list[dict]) -> None:
        premiere = blocking_items[0]
        super().__init__(
            f"{len(blocking_items)} citation(s) non vérifiée(s) bloquent l'export. "
            f"Première : la clé « {premiere['bibtex_key']} » de la section "
            f"{premiere['section_id']} — « {premiere['excerpt']} ». Une citation "
            "cesse d'être vérifiée quand le texte qui la portait a été modifié à "
            "la main : relancer la rédaction de la section, ou retirer la "
            "référence du texte.",
        )
        self.blocking_items = blocking_items

    def to_payload(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "current_state": "UNVERIFIED_CITATIONS",
            "required_state": "ALL_CITATIONS_VERIFIED",
            "blocking_items": self.blocking_items,
        }


class BibEntry(BaseModel):
    """Une entrée de bibliographie, telle qu'elle sera rendue."""

    source_id: int
    key: str
    entry_type: str
    title: str
    authors: str | None = None
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    url: str | None = None
    is_preprint: bool = False


# --- Normalisation des clés ----------------------------------------------


def _ascii(texte: str) -> str:
    """Translittération ASCII. « Müller » et « Mueller » restent distincts,
    mais « Müller » et « Muller » produisent la même clé — c'est voulu : une
    clé n'identifie pas, elle référence."""
    decompose = unicodedata.normalize("NFKD", texte.replace(APOSTROPHE_TYPO, "'"))
    return "".join(c for c in decompose if not unicodedata.combining(c))


def _mot(texte: str) -> str:
    """Mot réduit à ses lettres et chiffres, élision retirée."""
    return re.sub(r"[^a-z0-9]", "", ELISION.sub("", _ascii(texte)).lower())


def first_author(authors: str | None) -> str:
    """Nom du premier auteur, en minuscules ASCII, ou `anon`.

    Quatre graphies coexistent dans les imports, et la virgule ne les
    départage pas : elle sépare le nom du prénom dans « Dupont, Alice » et
    deux auteurs dans « Alice Dupont, Bob Martin ». Le texte avant la
    première virgule couvre les deux cas — c'est soit le nom de famille seul,
    soit le premier auteur entier.

    Reste à choisir le mot : le dernier, sauf s'il fait une seule lettre,
    auquel cas c'est une initiale et le nom est en tête. « Dupont A. » donne
    donc `dupont`, et « Wei Li » donne `li`.
    """
    if not authors or not authors.strip():
        return UNKNOWN_AUTHOR

    premier = re.split(r";| and ", authors)[0]
    tete = premier.split(",")[0]
    mots = [m for m in tete.split() if _mot(m)]
    if not mots:
        return UNKNOWN_AUTHOR

    dernier = _mot(mots[-1])
    nom = dernier if len(dernier) > 1 else _mot(mots[0])
    return nom or UNKNOWN_AUTHOR


def title_word(title: str) -> str:
    """Premier mot significatif du titre."""
    for brut in title.split():
        mot = _mot(brut)
        if len(mot) > 2 and mot not in STOPWORDS:
            return mot
    return FALLBACK_WORD


def base_key(authors: str | None, year: int | None, title: str) -> str:
    """Forme `premierauteur_annee_motclefdutitre`, ASCII et minuscules."""
    return f"{first_author(authors)}_{year if year else UNKNOWN_YEAR}_{title_word(title)}"


def _suffixe(rang: int) -> str:
    """a, b, … z, aa, ab — sans limite, et stable."""
    lettres = ""
    rang += 1
    while rang > 0:
        rang, reste = divmod(rang - 1, 26)
        lettres = chr(ord("a") + reste) + lettres
    return lettres


def assign_keys(
    sources: list[BibEntry], deja_prises: dict[int, str] | None = None
) -> dict[int, str]:
    """Attribue une clé à chaque source, collisions suffixées.

    `deja_prises` porte les clés déjà écrites en base : elles sont reprises
    telles quelles et RÉSERVÉES, de sorte qu'une nouvelle source ne puisse
    pas s'attribuer la clé d'une ancienne. L'ordre de suffixage suit
    l'identifiant de source, qui ne bouge jamais.

    **Le premier occupant garde la clé nue ; ce sont les suivants qui sont
    suffixés.** L'usage bibliographique voudrait `dupont2019a` et
    `dupont2019b` dès qu'il y a deux Dupont 2019 — mais cela changerait la
    clé du premier le jour où le second est importé, c'est-à-dire casserait
    les renvois d'un chapitre déjà relu. Entre la convention et la stabilité,
    c'est la stabilité qui gouverne ici.
    """
    figees = deja_prises or {}
    attribuees: dict[int, str] = {}
    occupees = set(figees.values())

    for source in sorted(sources, key=lambda s: s.source_id):
        if source.source_id in figees:
            attribuees[source.source_id] = figees[source.source_id]
            continue

        racine = base_key(source.authors, source.year, source.title)
        clef = racine
        rang = 0
        while clef in occupees:
            clef = f"{racine}{_suffixe(rang)}"
            rang += 1
        attribuees[source.source_id] = clef
        occupees.add(clef)

    return attribuees


# --- Rendu ----------------------------------------------------------------


def escape_latex(texte: str) -> str:
    """Échappe les caractères spéciaux LaTeX, en un seul passage."""
    return LATEX_SPECIAL.sub(lambda m: LATEX_ESCAPES[m.group()], texte)


def protect_acronyms(texte: str) -> str:
    """Protège la casse des acronymes : {ADN}, {IRM}."""
    return ACRONYM.sub(lambda m: "{" + m.group(1) + "}", texte)


def _champ(texte: str) -> str:
    """Valeur de champ : échappée, puis acronymes protégés.

    L'ordre compte. Protéger avant d'échapper ferait échapper les accolades
    de protection, qui apparaîtraient telles quelles dans le rendu.
    """
    return protect_acronyms(escape_latex(texte))


def render_entry(entry: BibEntry) -> str:
    """Rend une entrée BibTeX. Champs triés, sortie stable."""
    champs: list[tuple[str, str]] = [("title", _champ(entry.title))]
    if entry.authors:
        champs.append(("author", _champ(entry.authors)))
    if entry.year:
        champs.append(("year", str(entry.year)))
    if entry.venue:
        # `journal` pour un article, `note` ne conviendrait pas ; les autres
        # types acceptent `howpublished`, que Pandoc rend également.
        cle = "journal" if entry.entry_type == "article" else "howpublished"
        champs.append((cle, _champ(entry.venue)))
    if entry.doi:
        champs.append(("doi", escape_latex(entry.doi)))
    if entry.url:
        champs.append(("url", escape_latex(entry.url)))
    if entry.is_preprint:
        champs.append(("note", _champ(PREPRINT_NOTE)))

    champs.sort(key=lambda c: c[0])
    lignes = ",\n".join(f"  {nom} = {{{valeur}}}" for nom, valeur in champs)
    return f"@{entry.entry_type}{{{entry.key},\n{lignes}\n}}"


def render_bib(entries: list[BibEntry]) -> str:
    """Fichier `.bib` complet, trié par clé. Octet pour octet reproductible."""
    ordonnees = sorted(entries, key=lambda e: e.key)
    corps = "\n\n".join(render_entry(e) for e in ordonnees)
    entete = (
        "% Bibliographie générée par Science AI Writer IDE.\n"
        "% Regénérée à chaque export depuis les seules citations vérifiées.\n"
        "% Ne pas éditer : toute modification sera perdue au prochain export.\n\n"
    )
    return entete + corps + "\n"


# --- Lecture de la base ---------------------------------------------------


async def unverified_citations(conn: aiosqlite.Connection, section_ids: list[int]) -> list[dict]:
    """Citations non vérifiées des sections incluses, avec leur passage."""
    if not section_ids:
        return []
    marques = ",".join("?" * len(section_ids))
    async with conn.execute(
        # `marques` ne contient que des points d'interrogation.
        f"""
        SELECT c.id, c.draft_section_id, c.bibtex_key, c.source_id,
               substr(d.content_qmd, 1, 200)
        FROM citation c JOIN draft_section d ON d.id = c.draft_section_id
        WHERE c.verified = 0 AND c.draft_section_id IN ({marques})
        ORDER BY c.id
        """,
        section_ids,
    ) as cur:
        return [
            {
                "citation_id": int(r[0]),
                "section_id": int(r[1]),
                "bibtex_key": str(r[2]),
                "source_id": int(r[3]),
                "excerpt": str(r[4] or "").strip(),
            }
            for r in await cur.fetchall()
        ]


async def cited_sources(conn: aiosqlite.Connection, section_ids: list[int]) -> list[BibEntry]:
    """Sources référencées par une citation VÉRIFIÉE des sections incluses."""
    if not section_ids:
        return []
    marques = ",".join("?" * len(section_ids))
    async with conn.execute(
        # `marques` ne contient que des points d'interrogation.
        f"""
        SELECT DISTINCT s.id, s.kind, s.title, s.authors, s.year, s.venue,
               s.doi, s.url, s.is_preprint
        FROM source_document s JOIN citation c ON c.source_id = s.id
        WHERE c.verified = 1 AND c.draft_section_id IN ({marques})
        ORDER BY s.id
        """,
        section_ids,
    ) as cur:
        rows = await cur.fetchall()

    return [
        BibEntry(
            source_id=int(r[0]),
            key="",  # attribuée par `assign_keys`
            entry_type=BIBTEX_TYPES.get(str(r[1]), DEFAULT_TYPE),
            title=str(r[2]),
            authors=r[3],
            year=r[4],
            venue=r[5],
            doi=r[6],
            url=r[7],
            is_preprint=bool(r[8]),
        )
        for r in rows
    ]


async def existing_keys(conn: aiosqlite.Connection) -> dict[int, str]:
    """Clés déjà attribuées. Elles ne seront pas recalculées."""
    async with conn.execute(
        "SELECT id, bibtex_key FROM source_document WHERE bibtex_key IS NOT NULL"
    ) as cur:
        return {int(r[0]): str(r[1]) for r in await cur.fetchall()}


async def includable_sections(conn: aiosqlite.Connection) -> dict[int, int]:
    """Sections incluses dans l'export : `plan_node_id` → `draft_section.id`.

    La dernière version de chaque nœud, les sections ORPHANED exclues — leur
    nœud n'existe plus, elles n'ont pas de place dans le document.
    """
    async with conn.execute(
        """
        SELECT d.plan_node_id, d.id FROM draft_section d
        WHERE d.plan_node_id IS NOT NULL AND d.status <> ?
          AND d.version = (
            SELECT MAX(v.version) FROM draft_section v
            WHERE v.plan_node_id = d.plan_node_id AND v.status <> ?
          )
        ORDER BY d.plan_node_id
        """,
        (str(SectionStatus.ORPHANED), str(SectionStatus.ORPHANED)),
    ) as cur:
        return {int(r[0]): int(r[1]) for r in await cur.fetchall()}


async def build_bibliography(
    conn: aiosqlite.Connection, section_ids: list[int]
) -> tuple[str, list[BibEntry]]:
    """Compile le `.bib` des sections incluses. Échoue sur citation non vérifiée."""
    bloquantes = await unverified_citations(conn, section_ids)
    if bloquantes:
        logger.warning("Export bloqué : %s citation(s) non vérifiée(s)", len(bloquantes))
        raise UnverifiedCitationError(bloquantes)

    sources = await cited_sources(conn, section_ids)
    # Lecture, attribution et écriture dans une seule transaction : séparées,
    # deux exports simultanés calculaient la même clé pour une source nouvelle,
    # et le second butait sur l'index UNIQUE en erreur brute.
    async with transaction(conn):
        deja = await existing_keys(conn)
        clefs = assign_keys(sources, deja)
        nouvelles = {sid: cle for sid, cle in clefs.items() if sid not in deja}
        for source_id, clef in sorted(nouvelles.items()):
            await conn.execute(
                "UPDATE source_document SET bibtex_key = ? WHERE id = ?",
                (clef, source_id),
            )
    if nouvelles:
        logger.info("%s clé(s) BibTeX attribuée(s)", len(nouvelles))

    entrees = [e.model_copy(update={"key": clefs[e.source_id]}) for e in sources]
    return render_bib(entrees), entrees
