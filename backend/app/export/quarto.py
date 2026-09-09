"""Appel à Quarto et analyse de son journal — US-502, ADR-006, ADR-012.

**Le journal est analysé, pas seulement transmis.** Quarto sort en code 0 sur
un document dont les renvois ne se résolvent pas : il émet un avertissement
et rend un PDF où figurent des `??`. Sur un mémoire de 300 pages, personne ne
les voit avant l'impression. Un export qui se contenterait de relayer le code
de retour rapporterait donc un succès sur un document cassé.

**Une erreur LaTeX est rendue avec sa fenêtre de contexte, jamais entière.**
Un journal de compilation fait quatre mille lignes ; les vingt qui entourent
l'échec sont les seules qui disent quoi corriger. Transmettre le reste, c'est
demander à l'utilisateur de faire le tri à notre place.

**Aucun conteneur (ADR-012).** Quarto est un binaire du poste, détecté dans
le PATH ou désigné par `settings.quarto_path`. Son absence est un état
normal, rapporté avec la procédure d'installation des deux plateformes — pas
une panne du service.
"""

from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path

from pydantic import BaseModel

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import get_logger

logger = get_logger(__name__)

VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")

# Renvoi non résolu. Quarto et Pandoc le signalent de plusieurs façons selon
# la version et le format ; les trois formes observées sont couvertes.
UNRESOLVED_CROSSREF = (
    re.compile(r"[Uu]nable to resolve crossref (?:for )?['\"]?@?([A-Za-z]+-[A-Za-z0-9_-]+)"),
    re.compile(r"WARNING: [Uu]nresolved crossref ['\"]?@?([A-Za-z]+-[A-Za-z0-9_-]+)"),
    re.compile(r"\?\?\s*\(([A-Za-z]+-[A-Za-z0-9_-]+)\)"),
)

# Citation non résolue par citeproc.
UNRESOLVED_CITATION = (
    re.compile(r"\[WARNING\] Citeproc: citation ([A-Za-z0-9_:.-]+) not found"),
    re.compile(r"citation ['\"]?([A-Za-z0-9_:.-]+)['\"]? not found in bibliography"),
)

# Marqueurs d'échec LaTeX. `!` en début de ligne est la convention TeX.
LATEX_ERROR = re.compile(r"^(?:! |LaTeX Error:|! LaTeX Error:|Emergency stop)", re.M)
MISSING_PACKAGE = re.compile(r"File [`']([A-Za-z0-9_.-]+\.sty)' not found")


class QuartoUnavailableError(AppError):
    """Quarto absent, ou version insuffisante."""

    code = "QUARTO_UNAVAILABLE"
    status_code = 503

    @classmethod
    def missing(cls) -> QuartoUnavailableError:
        return cls(
            "Quarto est introuvable. L'application ne l'embarque pas (ADR-012 : "
            "aucun conteneur au MVP). Windows : winget install --id Posit.Quarto, "
            "ou l'installeur de https://quarto.org/docs/get-started/. Linux : "
            "télécharger le .deb ou le .tar.gz depuis la même page. La chaîne PDF "
            "demande en plus « quarto install tinytex ». Renseigner "
            "SAW_QUARTO_PATH si le binaire n'est pas dans le PATH.",
            required="quarto",
        )

    @classmethod
    def too_old(cls, trouvee: str, minimale: str) -> QuartoUnavailableError:
        return cls(
            f"Quarto {trouvee} est installé, {minimale} au minimum est requis. "
            "En deçà, les renvois croisés d'un document long ne sont pas stables. "
            "Mettre à jour depuis https://quarto.org/docs/get-started/.",
            found=trouvee,
            required=minimale,
        )


class LatexPackageMissingError(AppError):
    """Un paquet LaTeX manque à la chaîne PDF."""

    code = "LATEX_PACKAGE_MISSING"
    status_code = 503

    @classmethod
    def for_package(cls, paquet: str) -> LatexPackageMissingError:
        nom = paquet.removesuffix(".sty")
        return cls(
            f"Le paquet LaTeX « {paquet} » manque à la distribution TinyTeX. "
            f"Installer : quarto install tinytex, puis tlmgr install {nom}.",
            package=paquet,
        )


class QuartoResult(BaseModel):
    """Ce que la compilation a produit, et ce qu'elle a laissé passer."""

    returncode: int
    outputs: list[str] = []
    unresolved_crossrefs: list[str] = []
    unresolved_citations: list[str] = []
    latex_error: str | None = None
    duration_s: float = 0.0
    version: str = ""

    @property
    def ok(self) -> bool:
        """Succès complet. Un renvoi non résolu n'en est pas un."""
        return (
            self.returncode == 0
            and not self.unresolved_crossrefs
            and not self.unresolved_citations
            and self.latex_error is None
        )

    @property
    def partial(self) -> bool:
        """Le document est sorti, mais avec des renvois cassés."""
        return self.returncode == 0 and not self.ok


# --- Détection du binaire -------------------------------------------------


def find_quarto() -> Path:
    """Binaire Quarto, depuis la configuration ou le PATH."""
    configure = get_settings().quarto_path
    if configure:
        chemin = Path(configure)
        if chemin.exists():
            return chemin
        raise QuartoUnavailableError(
            f"SAW_QUARTO_PATH désigne « {chemin} », qui n'existe pas. "
            "Corriger le réglage ou le retirer pour chercher dans le PATH.",
            configured=str(chemin),
        )

    trouve = shutil.which("quarto")
    if not trouve:
        raise QuartoUnavailableError.missing()
    return Path(trouve)


def parse_version(sortie: str) -> tuple[int, int, int]:
    trouve = VERSION.search(sortie)
    if not trouve:
        raise QuartoUnavailableError(
            f"Version de Quarto illisible dans « {sortie.strip()[:80]} ».",
        )
    return int(trouve.group(1)), int(trouve.group(2)), int(trouve.group(3))


async def quarto_version(binaire: Path | None = None) -> str:
    """Version déclarée par le binaire. Lève si elle est insuffisante."""
    chemin = binaire or find_quarto()
    processus = await asyncio.create_subprocess_exec(
        str(chemin),
        "--version",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    brut, _ = await processus.communicate()
    sortie = brut.decode("utf-8", errors="replace").strip()

    trouvee = parse_version(sortie)
    minimale = parse_version(get_settings().quarto_min_version)
    if trouvee < minimale:
        raise QuartoUnavailableError.too_old(
            ".".join(str(p) for p in trouvee), get_settings().quarto_min_version
        )
    return ".".join(str(p) for p in trouvee)


# --- Analyse du journal ---------------------------------------------------


def _uniques(valeurs: list[str]) -> list[str]:
    """Dédoublonne en préservant l'ordre d'apparition."""
    vus: set[str] = set()
    return [v for v in valeurs if not (v in vus or vus.add(v))]


def find_unresolved_crossrefs(journal: str) -> list[str]:
    trouves: list[str] = []
    for motif in UNRESOLVED_CROSSREF:
        trouves.extend(motif.findall(journal))
    return _uniques(trouves)


def find_unresolved_citations(journal: str) -> list[str]:
    trouves: list[str] = []
    for motif in UNRESOLVED_CITATION:
        trouves.extend(motif.findall(journal))
    return _uniques(trouves)


def extract_latex_error(journal: str, contexte: int | None = None) -> str | None:
    """Fenêtre de journal autour du premier échec LaTeX, ou None."""
    trouve = LATEX_ERROR.search(journal)
    if not trouve:
        return None

    fenetre = contexte if contexte is not None else get_settings().export_log_context_lines
    lignes = journal.splitlines()
    # Numéro de la ligne où commence l'erreur.
    index = journal[: trouve.start()].count("\n")
    debut = max(0, index - fenetre // 2)
    fin = min(len(lignes), index + fenetre // 2 + 1)
    return "\n".join(lignes[debut:fin])


def analyse_log(journal: str) -> tuple[list[str], list[str], str | None]:
    """(renvois non résolus, citations non résolues, erreur LaTeX)."""
    return (
        find_unresolved_crossrefs(journal),
        find_unresolved_citations(journal),
        extract_latex_error(journal),
    )


# --- Compilation ----------------------------------------------------------


def build_arguments(binaire: Path, repertoire: Path, formats: list[str]) -> list[str]:
    """Ligne de commande de la compilation.

    **`--to` n'est pas passé, et c'est délibéré.** Sur un projet, Quarto rend
    tous les formats déclarés dans `_quarto.yml` — que le gabarit dérive
    précisément de `formats`. Les deux manières de nommer les formats sur la
    ligne de commande échouent, chacune à sa façon, et toutes deux ont été
    mesurées ici : répéter `--to` ne cumule pas, la dernière occurrence écrase
    les précédentes et un export de trois formats n'en produisait qu'un, sans
    erreur ni avertissement ; et `--to pdf,docx,html` est refusé net par
    Quarto 1.10, « Unknown format ».

    `formats` reste dans la signature : il n'ordonne pas la commande, mais il
    documente ce que l'appelant demande, et un test compare la configuration
    rendue à cette liste.
    """
    return [str(binaire), "render", str(repertoire)]


async def render(
    repertoire: Path,
    formats: list[str],
    binaire: Path | None = None,
    timeout_s: int | None = None,
) -> tuple[QuartoResult, str]:
    """Compile le projet et analyse le journal. Rend (résultat, journal)."""
    settings = get_settings()
    chemin = binaire or find_quarto()
    version = await quarto_version(chemin)
    limite = timeout_s if timeout_s is not None else settings.export_timeout_seconds

    arguments = build_arguments(chemin, repertoire, formats)

    debut = asyncio.get_running_loop().time()
    processus = await asyncio.create_subprocess_exec(
        *arguments,
        cwd=str(repertoire),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        brut, _ = await asyncio.wait_for(processus.communicate(), timeout=limite)
    except TimeoutError:
        processus.kill()
        await processus.wait()
        raise AppError(
            f"La compilation Quarto a dépassé {limite} s et a été interrompue. "
            "Une thèse de 300 pages demande plusieurs minutes : augmenter "
            "SAW_EXPORT_TIMEOUT_SECONDS, ou exporter un sous-ensemble du plan.",
        ) from None

    journal = brut.decode("utf-8", errors="replace")
    duree = round(asyncio.get_running_loop().time() - debut, 2)

    paquet = MISSING_PACKAGE.search(journal)
    if paquet:
        raise LatexPackageMissingError.for_package(paquet.group(1))

    renvois, citations, erreur = analyse_log(journal)
    # Le balayage du repertoire est bloquant : hors de la boucle.
    sorties = await asyncio.to_thread(_lister_sorties, repertoire)
    resultat = QuartoResult(
        returncode=processus.returncode or 0,
        outputs=sorties,
        unresolved_crossrefs=renvois,
        unresolved_citations=citations,
        latex_error=erreur,
        duration_s=duree,
        version=version,
    )
    if resultat.partial:
        logger.warning(
            "Export partiel : %s renvoi(s) et %s citation(s) non résolus",
            len(renvois),
            len(citations),
        )
    return resultat, journal


_EXTENSIONS = frozenset({".pdf", ".docx", ".html"})


def _lister_sorties(repertoire: Path) -> list[str]:
    """Fichiers produits par la compilation, tries."""
    return sorted(p.name for p in repertoire.glob("*") if p.suffix in _EXTENSIONS)
