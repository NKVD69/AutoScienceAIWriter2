"""Garde-fous de véracité — US-301, spécifications §5.5. Le cœur du produit.

Une citation inventée dans un mémoire de doctorat détruit la crédibilité du
travail et celle de l'outil. Ces trois contrôles ne sont donc pas des
validations de confort.

**Ils sont syntaxiques, et c'est ce qui les rend fiables.** Aucun ne demande
à un modèle de vérifier sa propre production : un modèle qui invente une
référence la trouvera tout aussi plausible à la relecture, et un contrôle
fondé sur son jugement échouerait précisément dans le cas qu'il doit
attraper.

- **V1** — toute clé de citation, dans les affirmations comme dans le texte,
  doit figurer dans la liste close du contexte.
- **V2** — un chiffre présenté comme sourcé doit être rattaché à un extrait.
- **V3** — tout DOI ou URL du texte doit exister dans les sources du projet.
"""

from __future__ import annotations

import re

import aiosqlite
from pydantic import BaseModel

from app.core.logging import get_logger
from app.models.section import ClaimKind, SectionDraft
from app.rag.context_builder import SectionContext

logger = get_logger(__name__)

# Clés de citation Quarto, sous forme encadrée ou nue.
BRACKETED_KEYS = re.compile(r"\[@([A-Za-z0-9_:.-]+)\]")
BARE_KEY = re.compile(r"(?<![A-Za-z0-9])@([A-Za-z][A-Za-z0-9_:.-]{2,})")

# Renvois croisés Quarto : `@fig-`, `@tbl-`, `@eq-`, `@sec-` ne sont pas des
# citations. Les confondre ferait rejeter un document parfaitement correct.
CROSSREF_PREFIXES = ("fig-", "tbl-", "eq-", "sec-", "lst-", "thm-")

DOI = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")
# Une URL citée s'arrête avant les guillemets qui l'encadrent — droits,
# français, typographiques. Sans eux, une URL entre guillemets sortait de
# l'expression avec le guillemet fermant collé à sa fin, ne correspondait plus
# à l'URL importée, et une citation correcte était rejetée jusqu'à épuiser les
# essais du disjoncteur (constat de revue). Écrits par leur point de code :
# bruts, les guillemets typographiques se confondent avec les droits.
_GUILLEMETS = "\"'" + "".join(chr(c) for c in (0x00AB, 0x00BB, 0x201C, 0x201D, 0x2018, 0x2019))
URL = re.compile(r"https?://[^\s\)\]>," + re.escape(_GUILLEMETS) + "]+")

# --- V2 : ce qui n'est PAS une affirmation chiffrée ------------------------
# Retirés du texte avant de chercher un chiffre. Un numéro de section ou de
# figure n'affirme rien ; les signaler ferait rejeter du texte correct et
# rendrait le contrôle inutilisable, donc désactivé.
NON_SIGNIFICANT = (
    re.compile(r"@(?:fig|tbl|eq|sec|lst|thm)-[A-Za-z0-9_-]+"),
    # Les pluriels comptent : « sections 3 à 5 » est un renvoi, et sans eux
    # il serait lu comme un intervalle chiffré.
    re.compile(
        r"\b(?:sections?|§|chapitres?|figures?|tableaux?|tables?|annexes?)"
        r"\s*\d+(?:\.\d+)*(?:\s*(?:[-\u2013]|à)\s*\d+(?:\.\d+)*)?",
        re.I,
    ),
    re.compile(r"\b(?:fig|tab)\.\s*\d+", re.I),
    # Année isolée : 1900-2099, non suivie d'une unité ni d'un %.
    re.compile(r"(?<![\d.,])(?:19|20)\d{2}(?![\d.,%])"),
)

# Ce qui EST une affirmation chiffrée : décimal, pourcentage, intervalle,
# valeur p, effectif.
SIGNIFICANT_NUMBER = (
    re.compile(r"\d+[.,]\d+"),
    re.compile(r"\d+\s*%"),
    re.compile(r"\bp\s*[<>=]\s*[\d.,]+", re.I),
    re.compile(r"\bn\s*=\s*\d+", re.I),
    # Intervalle, sous ses trois graphies : trait d'union, tiret
    # demi-cadratin (U+2013, échappé — le caractère brut se confondrait avec
    # le trait d'union dans la source) et « de X à Y », la forme courante en
    # français. Renvois et millésimes ont déjà été retirés du texte, sans
    # quoi « de 2019 à 2023 » passerait pour une mesure.
    re.compile(r"\d+\s*(?:[-\u2013]|à)\s*\d+"),
    re.compile(r"(?<![\d.,])\d{3,}(?![\d.,])"),
)


class VeracityViolation(BaseModel):
    """Un rejet, avec de quoi le corriger."""

    rule: str
    token: str
    message: str


class VeracityResult(BaseModel):
    ok: bool
    violations: list[VeracityViolation] = []

    def to_correction(self) -> str:
        """Message destiné au modèle, pas à l'utilisateur."""
        if self.ok:
            return ""
        details = " ".join(v.message for v in self.violations[:5])
        return (
            f"{len(self.violations)} contrôle(s) de véracité en échec. {details} "
            "Corrige et renvoie UNIQUEMENT le JSON complet."
        )


def extract_inline_keys(content: str) -> set[str]:
    """Clés de citation présentes dans le texte, renvois croisés exclus."""
    cles = set(BRACKETED_KEYS.findall(content)) | set(BARE_KEY.findall(content))
    return {c for c in cles if not c.startswith(CROSSREF_PREFIXES)}


def strip_non_significant(texte: str) -> str:
    for motif in NON_SIGNIFICANT:
        texte = motif.sub(" ", texte)
    return texte


def find_significant_number(texte: str) -> str | None:
    """Premier motif numérique significatif, ou None."""
    nettoye = strip_non_significant(texte)
    for motif in SIGNIFICANT_NUMBER:
        trouve = motif.search(nettoye)
        if trouve:
            return trouve.group().strip()
    return None


# --- V1 -------------------------------------------------------------------


def check_citation_keys(draft: SectionDraft, context: SectionContext) -> list[VeracityViolation]:
    """Toute clé, déclarée ou en ligne, doit figurer dans la liste close."""
    autorisees = set(context.allowed_keys)
    violations: list[VeracityViolation] = []

    declarees = {cle for claim in draft.claims for cle in claim.citation_keys}
    for cle in sorted(declarees | extract_inline_keys(draft.content_qmd)):
        if cle not in autorisees:
            violations.append(
                VeracityViolation(
                    rule="V1",
                    token=cle,
                    message=(
                        f"La clé de citation « {cle} » ne correspond à aucune source "
                        f"fournie. Clés autorisées : {', '.join(context.allowed_keys)}. "
                        "Retire cette référence ou remplace-la par une clé de la liste."
                    ),
                )
            )

    # Un chunk_id absent du contexte est une invention de la même nature.
    connus = context.chunk_ids()
    for claim in draft.claims:
        for chunk_id in claim.chunk_ids:
            if chunk_id not in connus:
                violations.append(
                    VeracityViolation(
                        rule="V1",
                        token=str(chunk_id),
                        message=(
                            f"L'extrait {chunk_id} n'a pas été fourni. Extraits "
                            f"disponibles : {sorted(connus)}."
                        ),
                    )
                )
    return violations


# --- V2 -------------------------------------------------------------------


def check_numeric_claims(draft: SectionDraft) -> list[VeracityViolation]:
    """Un chiffre présenté comme sourcé doit venir d'un extrait."""
    violations: list[VeracityViolation] = []
    for claim in draft.claims:
        if claim.kind != ClaimKind.SOURCED:
            continue
        chiffre = find_significant_number(claim.text)
        if chiffre and not claim.chunk_ids:
            violations.append(
                VeracityViolation(
                    rule="V2",
                    token=chiffre,
                    message=(
                        f"La valeur « {chiffre} » est présentée comme sourcée sans "
                        f"extrait d'origine, dans : « {claim.text[:120]} ». Rattache "
                        "l'affirmation au chunk_id qui porte ce chiffre, ou retire-le."
                    ),
                )
            )
    return violations


# --- V3 -------------------------------------------------------------------


async def check_identifiers(
    conn: aiosqlite.Connection, draft: SectionDraft
) -> list[VeracityViolation]:
    """Tout DOI ou URL du texte doit exister dans les sources du projet."""
    violations: list[VeracityViolation] = []

    async with conn.execute(
        "SELECT doi, url FROM source_document WHERE doi IS NOT NULL OR url IS NOT NULL"
    ) as cur:
        rows = await cur.fetchall()
    dois = {str(r[0]).lower() for r in rows if r[0]}
    urls = {str(r[1]) for r in rows if r[1]}

    for doi in sorted(set(DOI.findall(draft.content_qmd))):
        if doi.lower().rstrip(".,;") not in dois:
            violations.append(
                VeracityViolation(
                    rule="V3",
                    token=doi,
                    message=(
                        f"Le DOI « {doi} » ne figure dans aucune source du projet. "
                        "Un identifiant qui n'a pas été importé ne peut pas être "
                        "cité : retire-le."
                    ),
                )
            )

    for url in sorted(set(URL.findall(draft.content_qmd))):
        if url.rstrip(".,;") not in urls:
            violations.append(
                VeracityViolation(
                    rule="V3",
                    token=url,
                    message=(f"L'URL « {url} » ne figure dans aucune source du projet. Retire-la."),
                )
            )
    return violations


# --- Orchestration --------------------------------------------------------


async def check_veracity(
    conn: aiosqlite.Connection, draft: SectionDraft, context: SectionContext
) -> VeracityResult:
    """Applique V1, V2 et V3. Aucun jugement délégué à un modèle."""
    violations = (
        check_citation_keys(draft, context)
        + check_numeric_claims(draft)
        + await check_identifiers(conn, draft)
    )
    if violations:
        logger.warning(
            "Véracité : %s violation(s) — %s",
            len(violations),
            ", ".join(f"{v.rule}:{v.token}" for v in violations[:5]),
        )
    return VeracityResult(ok=not violations, violations=violations)
