"""Pipeline d'export — US-501 et US-502, spécifications §9.

**Un répertoire d'export n'est jamais écrasé.** Il porte la preuve de ce qui
a été produit à une date : le `.qmd` assemblé, la bibliographie compilée, la
configuration, les sorties et un rapport. Un mémoire se relit sur des mois,
et « le PDF que j'ai envoyé au directeur en mars » doit rester reconstituable
en juin. Un répertoire horodaté à la seconde suffit, et une collision — deux
exports dans la même seconde — reçoit un suffixe plutôt qu'un écrasement.

**L'ordre des étapes n'est pas indifférent.** La bibliographie est compilée
AVANT l'assemblage : c'est elle qui attribue les clés publiées, et
l'assembleur en a besoin pour réécrire les citations du texte. Assembler
d'abord produirait un document citant des clés que le `.bib` ne contient pas.

**Une citation non vérifiée arrête tout, avant la moindre écriture.** Rien
n'est produit, pas même un répertoire : un artefact partiel laissé sur le
disque se prendrait pour un export réussi à la relecture.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import transaction
from app.export import quarto
from app.export.assembler import AssembledDocument, assemble
from app.export.bibliography import BibEntry, build_bibliography, includable_sections
from app.models.audit import AuditEventType
from app.models.plan import PlanOut
from app.services import audit_service, plan_service

logger = get_logger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "export" / "templates"
DEFAULT_TEMPLATE = "default"
BIB_FILENAME = "references.bib"
CSL_FILENAME = "references.csl"
QMD_FILENAME = "document.qmd"
CONFIG_FILENAME = "_quarto.yml"
REPORT_FILENAME = "rapport.json"
LOG_FILENAME = "quarto.log"

TOC_DEPTH = 3
NUMBER_DEPTH = 4


class ExportRequest(BaseModel):
    formats: list[str]
    include_ai_declaration: bool = True
    template: str = DEFAULT_TEMPLATE


class ExportReport(BaseModel):
    """Ce qui a été produit, et ce qui manquait. Écrit en JSON dans l'export."""

    project_id: int
    directory: str
    started_at: str
    formats: list[str]
    template: str
    include_ai_declaration: bool
    sections_included: list[dict]
    sections_missing: list[dict]
    bibliography_entries: int
    preprint_entries: int
    unresolved_crossrefs: list[str] = []
    unresolved_citations: list[str] = []
    latex_error: str | None = None
    outputs: list[str] = []
    duration_s: float = 0.0
    quarto_version: str = ""
    status: str = "OK"


def _horodatage() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def nouveau_repertoire(project_id: int) -> Path:
    """Répertoire daté, jamais réutilisé.

    Deux exports dans la même seconde reçoivent `-2`, `-3` : le suffixe
    coûte un caractère, l'écrasement coûterait un artefact de preuve.
    """
    racine = get_settings().exports_dir(project_id)
    racine.mkdir(parents=True, exist_ok=True)

    base = racine / _horodatage()
    candidat = base
    rang = 2
    while candidat.exists():
        candidat = racine / f"{base.name}-{rang}"
        rang += 1
    candidat.mkdir(parents=True)
    return candidat


def render_config(
    plan: PlanOut,
    projet: dict,
    formats: list[str],
    template: str = DEFAULT_TEMPLATE,
) -> str:
    """Rend `_quarto.yml` depuis le gabarit Jinja du modèle."""
    from jinja2 import Environment, FileSystemLoader, StrictUndefined

    environnement = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR / template)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,  # YAML, pas HTML : échapper corromprait le fichier.
    )
    gabarit = environnement.get_template("_quarto.yml.j2")
    return gabarit.render(
        title=projet["name"],
        author=projet.get("author") or "",
        date=datetime.now(UTC).date().isoformat(),
        # Sans `lang`, « Figure » et « Références » restent en anglais.
        lang=projet.get("language") or "fr",
        bibliography=BIB_FILENAME,
        csl=CSL_FILENAME,
        formats=formats,
        toc_depth=TOC_DEPTH,
        number_depth=NUMBER_DEPTH,
    )


async def _projet(conn: aiosqlite.Connection, project_id: int) -> dict:
    async with conn.execute(
        "SELECT name, subject, language, academic_level FROM project WHERE id = ?",
        (project_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        raise ValueError(f"Projet {project_id} absent de son propre fichier.")
    return {
        "name": str(row[0]),
        "subject": str(row[1]),
        "language": str(row[2]),
        "academic_level": str(row[3]),
    }


def _copier_figures(conn_dir: Path, destination: Path) -> list[str]:
    """Copie les artefacts de code dans le répertoire de compilation.

    Chemins relatifs : un `.qmd` qui référencerait un chemin absolu ne
    compilerait plus une fois le répertoire d'export déplacé ou archivé.
    """
    if not conn_dir.exists():
        return []
    cible = destination / "figures"
    cible.mkdir(exist_ok=True)
    copiees = []
    for fichier in sorted(conn_dir.glob("*")):
        if fichier.is_file():
            shutil.copy2(fichier, cible / fichier.name)
            copiees.append(f"figures/{fichier.name}")
    return copiees


async def prepare(
    conn: aiosqlite.Connection,
    project_id: int,
    requete: ExportRequest,
    figures_dir: Path | None = None,
) -> tuple[Path, ExportReport, AssembledDocument, list[BibEntry]]:
    """Compile la bibliographie, assemble le document, écrit les artefacts.

    Ne lance pas Quarto : la préparation est testable sans lui, et c'est ce
    qui permet d'exercer US-501 et l'assemblage sur un poste où Quarto n'est
    pas installé.
    """
    plan = await plan_service._require_plan(conn, project_id)
    projet = await _projet(conn, project_id)

    sections_par_noeud = await includable_sections(conn)
    section_ids = sorted(sections_par_noeud.values())

    # Avant toute écriture : une citation non vérifiée lève ici.
    bib, entrees = await build_bibliography(conn, section_ids)
    document = await assemble(conn, plan, sections_par_noeud, requete.include_ai_declaration)

    repertoire = nouveau_repertoire(project_id)
    (repertoire / BIB_FILENAME).write_text(bib, encoding="utf-8")
    (repertoire / QMD_FILENAME).write_text(document.qmd, encoding="utf-8")
    (repertoire / CONFIG_FILENAME).write_text(
        render_config(plan, projet, requete.formats, requete.template), encoding="utf-8"
    )
    shutil.copy2(TEMPLATES_DIR / requete.template / CSL_FILENAME, repertoire / CSL_FILENAME)
    if figures_dir:
        _copier_figures(figures_dir, repertoire)

    rapport = ExportReport(
        project_id=project_id,
        directory=str(repertoire),
        started_at=datetime.now(UTC).isoformat(),
        formats=requete.formats,
        template=requete.template,
        include_ai_declaration=requete.include_ai_declaration,
        sections_included=[
            {"node_id": n.node_id, "title": n.title, "words": n.words} for n in document.included()
        ],
        sections_missing=[{"node_id": n.node_id, "title": n.title} for n in document.missing()],
        bibliography_entries=len(entrees),
        preprint_entries=sum(1 for e in entrees if e.is_preprint),
    )
    return repertoire, rapport, document, entrees


def ecrire_rapport(repertoire: Path, rapport: ExportReport) -> Path:
    chemin = repertoire / REPORT_FILENAME
    chemin.write_text(
        json.dumps(rapport.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return chemin


async def run_export(
    conn: aiosqlite.Connection,
    project_id: int,
    requete: ExportRequest,
    figures_dir: Path | None = None,
) -> ExportReport:
    """Pipeline complet : bibliographie, assemblage, compilation, rapport."""
    async with transaction(conn):
        await audit_service.append(
            conn,
            project_id,
            AuditEventType.EXPORT_STARTED,
            {"formats": requete.formats, "template": requete.template},
            tx=True,
        )

    repertoire, rapport, _, _ = await prepare(conn, project_id, requete, figures_dir)

    resultat, journal = await quarto.render(repertoire, requete.formats)
    (repertoire / LOG_FILENAME).write_text(journal, encoding="utf-8")

    rapport = rapport.model_copy(
        update={
            "unresolved_crossrefs": resultat.unresolved_crossrefs,
            "unresolved_citations": resultat.unresolved_citations,
            "latex_error": resultat.latex_error,
            "outputs": resultat.outputs,
            "duration_s": resultat.duration_s,
            "quarto_version": resultat.version,
            "status": _statut(resultat, rapport),
        }
    )
    ecrire_rapport(repertoire, rapport)

    async with transaction(conn):
        await audit_service.append(
            conn,
            project_id,
            AuditEventType.EXPORT_COMPLETED,
            {
                "repertoire": repertoire.name,
                "statut": rapport.status,
                "entrees_bib": rapport.bibliography_entries,
                "sections_manquantes": len(rapport.sections_missing),
            },
            tx=True,
        )
    logger.info("Export %s : %s", repertoire.name, rapport.status)
    return rapport


def _statut(resultat: quarto.QuartoResult, rapport: ExportReport) -> str:
    """OK, PARTIEL ou ECHEC. Un renvoi cassé n'est pas un succès.

    « PARTIEL » couvre deux cas distincts et c'est voulu : un document dont
    des renvois ne se résolvent pas, et un document dont des sections ne sont
    pas rédigées. Dans les deux cas le fichier existe et se lit, dans les
    deux cas il n'est pas soutenable en l'état.
    """
    if resultat.returncode != 0 or resultat.latex_error:
        return "ECHEC"
    if resultat.partial or rapport.sections_missing:
        return "PARTIEL"
    return "OK"
