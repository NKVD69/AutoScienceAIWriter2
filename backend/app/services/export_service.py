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

**La requête est validée à la frontière, et les écritures quittent la boucle.**
Formats et gabarit sont bornés à ce que le contrat autorise : un gabarit libre
faisait lire `TEMPLATES_DIR / "//hote/partage"`, c'est-à-dire un partage SMB
distant (constat de revue sécurité). Et toute l'écriture du répertoire passe
par `asyncio.to_thread` : faite dans la boucle, elle suspendait chaque autre
requête du service pendant sa durée (constat de revue).
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import aiosqlite
from pydantic import BaseModel, Field, field_validator

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.db.session import transaction
from app.export import quarto
from app.export.assembler import AssembledDocument, assemble
from app.export.bibliography import BibEntry, build_bibliography, includable_sections
from app.models.audit import AuditEventType
from app.models.plan import PlanOut
from app.models.project import LANGUAGE_TAG_PATTERN
from app.services import audit_service, plan_service

logger = get_logger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "export" / "templates"
DEFAULT_TEMPLATE = "default"
BIB_FILENAME = "references.bib"
CSL_FILENAME = "references.csl"
QMD_FILENAME = "document.qmd"
CONFIG_FILENAME = "_quarto.yml"
REPORT_FILENAME = "rapport.json"
LOG_FILENAME = quarto.LOG_FILENAME

TOC_DEPTH = 3
NUMBER_DEPTH = 4

# Un nom de gabarit est un segment de répertoire, et rien d'autre : ni
# séparateur, ni remontée, ni lecteur, ni chemin UNC.
TEMPLATE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
# Même définition que le contrat et que le modèle d'entrée. Revérifiée ici
# parce qu'un fichier projet reçu d'un tiers n'est jamais passé par l'API.
LANGUAGE_TAG = re.compile(LANGUAGE_TAG_PATTERN)

Format = Literal["pdf", "docx", "html"]


class InvalidProjectLanguageError(AppError):
    """La langue du projet n'est pas une étiquette de langue valide."""

    code = "INVALID_PROJECT_LANGUAGE"
    status_code = 422

    @classmethod
    def for_value(cls, valeur: str) -> InvalidProjectLanguageError:
        return cls(
            f"La langue du projet {valeur[:40]!r} n'est pas une étiquette de langue "
            "valide (fr, en, en-GB…). Quarto s'en sert pour traduire « Figure », "
            "« Tableau » et « Références », et pour construire le chemin de son "
            "fichier de traduction : une valeur arbitraire fait échouer l'export. "
            "Corriger la langue du projet.",
            language=valeur[:80],
        )


class ExportRequest(BaseModel):
    """Corps de POST /export, borné à ce que le contrat autorise."""

    formats: list[Format] = Field(min_length=1)
    include_ai_declaration: bool = True
    template: str = DEFAULT_TEMPLATE

    @field_validator("template")
    @classmethod
    def _gabarit_connu(cls, valeur: str) -> str:
        """Le motif est vérifié AVANT tout accès disque : un chemin UNC ne doit
        même pas être sondé, sa simple résolution sortant sur le réseau."""
        if not TEMPLATE_NAME.fullmatch(valeur) or not (TEMPLATES_DIR / valeur).is_dir():
            disponibles = ", ".join(sorted(p.name for p in TEMPLATES_DIR.iterdir() if p.is_dir()))
            raise ValueError(f"Gabarit {valeur!r} inconnu. Gabarits disponibles : {disponibles}.")
        return valeur


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
    error: str | None = None
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

    Le budget de longueur est vérifié AVANT toute création : un répertoire
    dans lequel Quarto ne pourrait pas ouvrir ses fichiers de session ne doit
    pas être laissé derrière soi.
    """
    racine = get_settings().exports_dir(project_id)
    base = racine / _horodatage()
    quarto.check_path_budget(base)

    racine.mkdir(parents=True, exist_ok=True)
    candidat = base
    rang = 2
    while candidat.exists():
        candidat = racine / f"{base.name}-{rang}"
        rang += 1
    candidat.mkdir(parents=True)
    return candidat


def valider_langue(langue: str) -> str:
    """Refuse une langue qui n'est pas une étiquette BCP 47.

    L'échappement du gabarit rend la langue inerte dans le YAML, mais Quarto
    construit avec elle le chemin de son fichier de traduction : une valeur
    arbitraire faisait échouer l'export sur une erreur système opaque —
    mesuré sur Quarto 1.10.18.
    """
    # `fullmatch`, pas `match` : en Python, `$` accepte un saut de ligne final,
    # et « fr » suivi d'un saut de ligne passait ici alors que le contrat,
    # appliqué par pydantic-core, le refuse.
    if not LANGUAGE_TAG.fullmatch(langue):
        raise InvalidProjectLanguageError.for_value(langue)
    return langue


def render_config(
    plan: PlanOut | None,
    projet: dict,
    formats: list[str],
    template: str = DEFAULT_TEMPLATE,
) -> str:
    """Rend `_quarto.yml` depuis le gabarit Jinja du modèle.

    Toute valeur passe par le filtre `tojson` du gabarit ; la langue est en
    outre validée ici.
    """
    from jinja2 import Environment, FileSystemLoader, StrictUndefined

    langue = valider_langue(projet.get("language") or "fr")
    environnement = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR / template)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,  # YAML, pas HTML : `tojson` fait l'échappement utile.
    )
    gabarit = environnement.get_template("_quarto.yml.j2")
    return gabarit.render(
        title=projet["name"],
        date=datetime.now(UTC).date().isoformat(),
        # Sans `lang`, « Figure » et « Références » restent en anglais.
        lang=langue,
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


def _copier_figures(source_dir: Path, destination: Path) -> list[str]:
    """Copie les artefacts de code dans le répertoire de compilation.

    Chemins relatifs : un `.qmd` qui référencerait un chemin absolu ne
    compilerait plus une fois le répertoire d'export déplacé ou archivé.
    """
    if not source_dir.exists():
        return []
    cible = destination / "figures"
    cible.mkdir(exist_ok=True)
    copiees = []
    for fichier in sorted(source_dir.glob("*")):
        if fichier.is_file():
            shutil.copy2(fichier, cible / fichier.name)
            copiees.append(f"figures/{fichier.name}")
    return copiees


def _ecrire_artefacts(
    repertoire: Path,
    bib: str,
    qmd: str,
    config: str,
    template: str,
    figures_dir: Path | None,
) -> None:
    """Écritures du répertoire d'export. Synchrone : appelé hors de la boucle."""
    (repertoire / BIB_FILENAME).write_text(bib, encoding="utf-8")
    (repertoire / QMD_FILENAME).write_text(qmd, encoding="utf-8")
    (repertoire / CONFIG_FILENAME).write_text(config, encoding="utf-8")
    shutil.copy2(TEMPLATES_DIR / template / CSL_FILENAME, repertoire / CSL_FILENAME)
    if figures_dir:
        _copier_figures(figures_dir, repertoire)


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
    # Avant toute écriture : une langue invalide lève ici.
    config = await asyncio.to_thread(render_config, plan, projet, requete.formats, requete.template)

    sections_par_noeud = await includable_sections(conn)
    section_ids = sorted(sections_par_noeud.values())

    # Avant toute écriture : une citation non vérifiée lève ici.
    bib, entrees = await build_bibliography(conn, section_ids)
    document = await assemble(conn, plan, sections_par_noeud, requete.include_ai_declaration)

    repertoire = await asyncio.to_thread(nouveau_repertoire, project_id)
    await asyncio.to_thread(
        _ecrire_artefacts, repertoire, bib, document.qmd, config, requete.template, figures_dir
    )

    rapport = ExportReport(
        project_id=project_id,
        directory=str(repertoire),
        started_at=datetime.now(UTC).isoformat(),
        formats=list(requete.formats),
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
            {"formats": list(requete.formats), "template": requete.template},
            tx=True,
        )

    repertoire, rapport, _, _ = await prepare(conn, project_id, requete, figures_dir)

    try:
        # `render` écrit lui-même le journal de compilation dans le répertoire.
        resultat, _ = await quarto.render(repertoire, list(requete.formats))
    except AppError as exc:
        # Le répertoire existe déjà : sans rapport, un export avorté se lirait
        # comme un export dont on a perdu la trace.
        echec = rapport.model_copy(update={"status": "ECHEC", "error": exc.message})
        await asyncio.to_thread(ecrire_rapport, repertoire, echec)
        raise

    rapport = rapport.model_copy(
        update={
            "unresolved_crossrefs": resultat.unresolved_crossrefs,
            "unresolved_citations": resultat.unresolved_citations,
            "latex_error": resultat.latex_error,
            "error": resultat.error,
            "outputs": resultat.outputs,
            "duration_s": resultat.duration_s,
            "quarto_version": resultat.version,
            "status": _statut(resultat, rapport),
        }
    )
    await asyncio.to_thread(ecrire_rapport, repertoire, rapport)

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
