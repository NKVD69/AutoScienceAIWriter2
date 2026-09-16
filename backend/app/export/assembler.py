"""Assemblage du document Quarto — US-502, ADR-006, spécifications §9.1.

**Le format canonique est Quarto, et rien d'autre.** Pas de MyST, pas de
repli MyST, pas de Sphinx. MyST écrit ses renvois `{ref}` et `{numref}`,
Quarto écrit `@fig-`, `@tbl-`, `@sec-` ; mélanger les deux produit des
renvois non résolus et des figures non numérotées sur un document long,
c'est-à-dire exactement le défaut que l'abandon du Markdown standard visait.

**Les identifiants de section sont stables.** `{#sec-<slug>}` est dérivé du
titre du nœud et de son identifiant : deux sections homonymes dans deux
chapitres différents ne peuvent pas se confondre, et un renvoi écrit dans un
chapitre reste valide quand le plan est réordonné.

**Un nœud sans texte n'interrompt pas l'export.** Il insère un marqueur
visible et il est signalé dans le rapport. Un aperçu partiel de la moitié
d'un mémoire est utile ; un export qui refuse de produire quoi que ce soit
tant que tout n'est pas rédigé ne l'est pas.

**Les clés de citation sont réécrites à l'assemblage — et c'est délibéré.**
Pendant la rédaction, une clé identifie un extrait fourni au modèle : elle
vaut `src<id>_<annee>_<mot>`, forme sans collision possible parce qu'elle
porte l'identifiant de la source, ce dont le rédacteur a besoin puisqu'il ne
voit qu'une poignée d'extraits et ignore tout des autres sources du projet.
La clé PUBLIÉE, elle, obéit à §9.3 : `premierauteur_annee_motclef`, dont les
collisions ne se résolvent qu'en connaissant l'ensemble des sources. Les deux
formes ne peuvent pas coïncider. La correspondance est exacte — `citation`
relie la clé du texte à sa source, `source_document.bibtex_key` donne la clé
publiée — et la substitution a lieu ici, sur la copie assemblée. Le
`content_qmd` stocké n'est pas touché : c'est le texte de l'auteur.
"""

from __future__ import annotations

import re
import unicodedata

import aiosqlite
from pydantic import BaseModel

from app.core.logging import get_logger
from app.models.plan import PlanNodeOut, PlanOut

logger = get_logger(__name__)

MISSING_MARKER = "*[section non rédigée]*"
MAX_HEADING_LEVEL = 6
SLUG_MAX = 40

# Point d'insertion de l'annexe de déclaration d'usage de l'IA. Le contenu
# relève de US-EXPORT-003 ; seul l'emplacement est posé ici.
AI_DECLARATION_ANCHOR = "<!-- science-ai-writer:ai-declaration -->"


class AssembledNode(BaseModel):
    """Ce qu'un nœud du plan a produit dans le document."""

    node_id: int
    title: str
    level: int
    section_id: int | None
    anchor: str
    words: int = 0
    # Nœud qui porte des sections. Sans texte propre, c'est un titre, pas un
    # trou : le compter manquant faisait sortir une thèse entièrement rédigée
    # avec un marqueur sous chaque chapitre, et un statut PARTIEL à vie —
    # défaut constaté sur un vrai rendu Quarto.
    is_container: bool = False


class AssembledDocument(BaseModel):
    qmd: str
    nodes: list[AssembledNode]

    def missing(self) -> list[AssembledNode]:
        """Feuilles sans texte. Un conteneur sans texte propre n'en fait pas partie."""
        return [n for n in self.nodes if n.section_id is None and not n.is_container]

    def included(self) -> list[AssembledNode]:
        return [n for n in self.nodes if n.section_id is not None]


def one_line(texte: str) -> str:
    """Réduit un titre à une seule ligne, espaces multiples compris.

    Défense pour le chemin qui ne passe par aucun validateur : un titre lu
    depuis un fichier projet reçu d'un tiers. `str.split()` sépare sur tout
    blanc Unicode — sauts de ligne, tabulations, séparateurs U+2028/U+2029 —
    et retire donc ce par quoi un titre ouvrirait un bloc autonome, jusqu'à un
    shortcode Quarto exécuté à la compilation (constat de revue de sécurité).
    """
    return " ".join(texte.split())


def slugify(texte: str) -> str:
    """Slug ASCII, stable et borné."""
    decompose = unicodedata.normalize("NFKD", texte)
    sans_accent = "".join(c for c in decompose if not unicodedata.combining(c))
    slug = re.sub(r"[^a-z0-9]+", "-", sans_accent.lower()).strip("-")
    return slug[:SLUG_MAX].rstrip("-") or "section"


def section_anchor(node: PlanNodeOut) -> str:
    """Identifiant Quarto d'un nœud : `sec-<slug>-<id>`.

    L'identifiant du nœud est suffixé parce que deux chapitres peuvent
    légitimement porter une section « Méthode » : sans lui, les deux
    produiraient la même ancre et Quarto n'en résoudrait qu'une.
    """
    return f"sec-{slugify(node.title)}-{node.id}"


def flatten(nodes: list[PlanNodeOut]) -> list[PlanNodeOut]:
    """Plan aplati dans l'ordre de lecture : un nœud, puis ses enfants."""
    ordonne: list[PlanNodeOut] = []
    for noeud in sorted(nodes, key=lambda n: n.ordinal):
        ordonne.append(noeud)
        ordonne.extend(flatten(noeud.children))
    return ordonne


def build_key_map(citations: list[tuple[str, str]]) -> dict[str, str]:
    """Clé du texte → clé publiée. Les paires ambiguës sont écartées.

    Une même clé de rédaction ne peut pointer que vers une source ; si la
    base en présentait deux, réécrire au hasard produirait une citation
    fausse — on préfère laisser la clé intacte et laisser l'analyse du
    journal Quarto la signaler comme non résolue.
    """
    vues: dict[str, set[str]] = {}
    for ancienne, nouvelle in citations:
        vues.setdefault(ancienne, set()).add(nouvelle)

    correspondance = {}
    for ancienne, nouvelles in vues.items():
        if len(nouvelles) == 1:
            correspondance[ancienne] = next(iter(nouvelles))
        else:
            logger.warning(
                "Clé « %s » rattachée à %s clés publiées : laissée telle quelle",
                ancienne,
                len(nouvelles),
            )
    return correspondance


def rewrite_citation_keys(contenu: str, correspondance: dict[str, str]) -> str:
    """Remplace les clés de rédaction par les clés publiées.

    La substitution porte sur la clé précédée d'une arobase, et la borne
    droite est explicite : sans elle, `@dupont_2019_a` réécrirait aussi le
    début de `@dupont_2019_abc`.
    """
    if not correspondance:
        return contenu

    motif = re.compile(
        r"@("
        + "|".join(re.escape(cle) for cle in sorted(correspondance, key=len, reverse=True))
        + r")(?![A-Za-z0-9_:.-])"
    )
    return motif.sub(lambda m: "@" + correspondance[m.group(1)], contenu)


async def citation_key_map(conn: aiosqlite.Connection, section_ids: list[int]) -> dict[str, str]:
    """Correspondance des clés, lue depuis les citations des sections incluses."""
    if not section_ids:
        return {}
    marques = ",".join("?" * len(section_ids))
    async with conn.execute(
        # `marques` ne contient que des points d'interrogation.
        f"""
        SELECT DISTINCT c.bibtex_key, s.bibtex_key
        FROM citation c JOIN source_document s ON s.id = c.source_id
        WHERE c.draft_section_id IN ({marques}) AND s.bibtex_key IS NOT NULL
        """,
        section_ids,
    ) as cur:
        paires = [(str(r[0]), str(r[1])) for r in await cur.fetchall()]
    return build_key_map(paires)


async def section_contents(conn: aiosqlite.Connection, section_ids: list[int]) -> dict[int, str]:
    if not section_ids:
        return {}
    marques = ",".join("?" * len(section_ids))
    async with conn.execute(
        # `marques` ne contient que des points d'interrogation.
        f"SELECT id, content_qmd FROM draft_section WHERE id IN ({marques})",
        section_ids,
    ) as cur:
        return {int(r[0]): str(r[1] or "") for r in await cur.fetchall()}


async def assemble(
    conn: aiosqlite.Connection,
    plan: PlanOut,
    sections_par_noeud: dict[int, int],
    include_ai_declaration: bool = True,
) -> AssembledDocument:
    """Assemble le document. Un nœud sans texte est signalé, pas bloquant."""
    ordre = flatten(plan.nodes)
    section_ids = [sections_par_noeud[n.id] for n in ordre if n.id in sections_par_noeud]
    contenus = await section_contents(conn, section_ids)
    correspondance = await citation_key_map(conn, section_ids)

    morceaux: list[str] = []
    assembles: list[AssembledNode] = []

    for noeud in ordre:
        # Quarto ne connaît pas de titre au-delà du niveau 6 ; le plan est
        # borné à 5 niveaux, la butée ne sert que de garde-fou.
        niveau = min(noeud.level, MAX_HEADING_LEVEL)
        ancre = section_anchor(noeud)
        section_id = sections_par_noeud.get(noeud.id)
        corps = rewrite_citation_keys(contenus.get(section_id, ""), correspondance).strip()
        conteneur = bool(noeud.children)
        # Aplati systématiquement : un titre venu de la base n'a pas forcément
        # traversé le validateur du modèle (fichier projet reçu d'un tiers).
        titre = one_line(noeud.title)

        morceaux.append(f"{'#' * niveau} {titre} {{#{ancre}}}")
        if corps:
            # Texte propre — y compris le chapeau d'un chapitre.
            morceaux.append(corps)
        elif not conteneur:
            morceaux.append(MISSING_MARKER)
        assembles.append(
            AssembledNode(
                node_id=noeud.id,
                title=titre,
                level=niveau,
                section_id=section_id if corps else None,
                anchor=ancre,
                words=len(corps.split()),
                is_container=conteneur,
            )
        )

    morceaux.append("# Références {.unnumbered}")
    # Pandoc place la bibliographie ici, sous ce titre.
    morceaux.append("::: {#refs}\n:::")

    if include_ai_declaration:
        morceaux.append(AI_DECLARATION_ANCHOR)

    document = AssembledDocument(qmd="\n\n".join(morceaux) + "\n", nodes=assembles)
    if document.missing():
        logger.info("Assemblage : %s section(s) sans texte rédigé", len(document.missing()))
    return document
