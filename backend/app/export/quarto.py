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
import os
import re
import shutil
import signal
import sys
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
# Première erreur déclarée par Quarto, pour un rapport lisible sans le journal.
QUARTO_ERROR = re.compile(r"^ERROR: (.+)$", re.M)
# Base Deno KV que Quarto n'a pas pu ouvrir, relevé sur Quarto 1.10.18 :
# « ERROR: unable to open database file: <chemin> ».
UNOPENABLE_DATABASE = re.compile(r"unable to open database file: (.+?)\s*$", re.M)

LOG_FILENAME = "quarto.log"

IS_WINDOWS = sys.platform == "win32"
WINDOWS_MAX_PATH = 260
# Mesuré sur le poste cible : sous le répertoire rendu, Quarto ouvre
# `.quarto\quarto-session-temp<16 hex>\sass\sass.kv`, soit 57 caractères de
# plus. La marge couvre des fichiers de session plus profonds et le suffixe
# `-N` d'un répertoire d'export homonyme.
QUARTO_SESSION_OVERHEAD = 57
PATH_MARGIN = 20
# Attente bornée de la disparition d'un arbre de processus tué.
KILL_GRACE_SECONDS = 15


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


class CompilationTimeoutError(AppError):
    """La compilation a dépassé son budget ; elle a été tuée, descendants compris."""

    code = "EXPORT_TIMEOUT"
    status_code = 500

    @classmethod
    def after(cls, limite: int) -> CompilationTimeoutError:
        return cls(
            f"La compilation Quarto a dépassé {limite} s et a été interrompue, avec "
            "tous ses processus descendants. Une thèse de 300 pages demande "
            "plusieurs minutes : augmenter SAW_EXPORT_TIMEOUT_SECONDS, ou exporter "
            "un sous-ensemble du plan.",
            timeout_seconds=limite,
        )


class ExportPathTooLongError(AppError):
    """Répertoire d'export trop profond pour que Quarto y ouvre ses fichiers."""

    code = "EXPORT_PATH_TOO_LONG"
    status_code = 503

    @classmethod
    def for_directory(cls, repertoire: Path, requis: int) -> ExportPathTooLongError:
        return cls(
            f"Le répertoire d'export « {repertoire} » est trop profond : Quarto y crée "
            f"des fichiers de session dont le chemin atteindrait {requis} caractères, "
            f"au-delà de la limite Windows de {WINDOWS_MAX_PATH}. Activer les chemins "
            "longs de Windows ne suffit pas, Quarto ne les prend pas en charge. "
            "Raccourcir le répertoire de données par SAW_DATA_DIR, par exemple "
            "C:\\saw-donnees.",
            directory=str(repertoire),
            required_length=requis,
        )

    @classmethod
    def from_log(cls, chemin: str) -> ExportPathTooLongError:
        return cls(
            f"Quarto n'a pas pu ouvrir « {chemin} » ({len(chemin)} caractères) : "
            f"chemin trop long pour Windows (limite {WINDOWS_MAX_PATH}). Raccourcir "
            "le répertoire de données par SAW_DATA_DIR.",
            path=chemin,
        )


class QuartoResult(BaseModel):
    """Ce que la compilation a produit, et ce qu'elle a laissé passer."""

    returncode: int
    outputs: list[str] = []
    unresolved_crossrefs: list[str] = []
    unresolved_citations: list[str] = []
    latex_error: str | None = None
    error: str | None = None
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


# --- Chemins et processus -------------------------------------------------


def check_path_budget(repertoire: Path, windows: bool | None = None) -> None:
    """Refuse un répertoire où Quarto ne pourrait pas ouvrir ses fichiers.

    Au-delà de 260 caractères, la base Deno KV de Quarto ne s'ouvre pas, et
    `LongPathsEnabled` n'y change rien — mesuré sur le poste cible. L'export
    échouait alors en ECHEC sans explication.
    """
    sous_windows = IS_WINDOWS if windows is None else windows
    if not sous_windows:
        return
    requis = len(str(repertoire)) + QUARTO_SESSION_OVERHEAD
    if requis + PATH_MARGIN >= WINDOWS_MAX_PATH:
        raise ExportPathTooLongError.for_directory(repertoire, requis)


def find_path_too_long(journal: str) -> str | None:
    """Chemin de la base que Quarto n'a pas pu ouvrir, ou None.

    La longueur n'est pas jugée ici : c'est l'appelant qui décide si le
    chemin trouvé dépasse la limite, une base peut aussi être inaccessible
    pour une autre raison.
    """
    trouve = UNOPENABLE_DATABASE.search(journal)
    return trouve.group(1) if trouve else None


async def _tuer_arbre(pid: int) -> None:
    """Termine un processus ET tous ses descendants.

    Sous Windows, `Process.kill()` ne termine que le processus direct : Pandoc,
    LuaLaTeX et Deno lui survivaient, orphelins (constat de revue sécurité).
    """
    if IS_WINDOWS:
        tueur = await asyncio.create_subprocess_exec(
            "taskkill",
            "/PID",
            str(pid),
            "/T",
            "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await tueur.wait()
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except ProcessLookupError:
        pass


async def _arreter(processus: asyncio.subprocess.Process) -> None:
    """Tue l'arbre de processus, puis attend sa disparition un temps borné."""
    await _tuer_arbre(processus.pid)
    try:
        await asyncio.wait_for(processus.wait(), timeout=KILL_GRACE_SECONDS)
    except TimeoutError:
        logger.error("Processus %s encore vivant après arrêt forcé", processus.pid)


async def run_bounded(arguments: list[str], cwd: Path, log_path: Path, timeout_s: int) -> int:
    """Lance un processus, journalise dans un fichier, borne sa durée.

    **La sortie va dans un fichier, pas dans un tube.** Un tube hérité par les
    descendants reste ouvert tant qu'ils vivent : `communicate()` attendait sa
    fermeture au-delà de tout délai, et `wait_for` ne rendait jamais la main
    (constat de revue sécurité, reproduit). Attendre la fin du seul processus
    lancé, par `wait()`, ne dépend pas d'eux.

    **L'entrée standard est fermée.** Un moteur LaTeX qui réclame une réponse
    interactive échoue au lieu d'attendre indéfiniment.

    **Au dépassement comme à l'annulation, c'est l'arbre entier qui est tué**,
    puis son extinction attendue un temps borné. L'annulation — un arrêt du
    service pendant une compilation — lève `CancelledError` et non
    `TimeoutError` : sans ce second cas, Quarto continuait seul, orphelin.
    """
    journal = await asyncio.to_thread(open, log_path, "wb")
    try:
        processus = await asyncio.create_subprocess_exec(
            *arguments,
            cwd=str(cwd),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=journal,
            stderr=asyncio.subprocess.STDOUT,
            # POSIX : un groupe de processus propre, pour tuer l'arbre d'un signal.
            start_new_session=not IS_WINDOWS,
        )
        try:
            return await asyncio.wait_for(processus.wait(), timeout=timeout_s)
        except TimeoutError:
            await _arreter(processus)
            raise CompilationTimeoutError.after(timeout_s) from None
        except asyncio.CancelledError:
            await _arreter(processus)
            raise
    finally:
        await asyncio.to_thread(journal.close)


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

    journal_path = repertoire / LOG_FILENAME

    debut = asyncio.get_running_loop().time()
    code = await run_bounded(
        build_arguments(chemin, repertoire, formats), repertoire, journal_path, limite
    )
    duree = round(asyncio.get_running_loop().time() - debut, 2)
    journal = await asyncio.to_thread(journal_path.read_text, encoding="utf-8", errors="replace")

    paquet = MISSING_PACKAGE.search(journal)
    if paquet:
        raise LatexPackageMissingError.for_package(paquet.group(1))

    chemin_inaccessible = find_path_too_long(journal)
    if chemin_inaccessible and len(chemin_inaccessible) >= WINDOWS_MAX_PATH - 1:
        raise ExportPathTooLongError.from_log(chemin_inaccessible)

    renvois, citations, erreur = analyse_log(journal)
    premiere_erreur = QUARTO_ERROR.search(journal) if code != 0 else None
    # Le balayage du repertoire est bloquant : hors de la boucle.
    sorties = await asyncio.to_thread(_lister_sorties, repertoire)
    resultat = QuartoResult(
        returncode=code,
        outputs=sorties,
        unresolved_crossrefs=renvois,
        unresolved_citations=citations,
        latex_error=erreur,
        error=premiere_erreur.group(1).strip() if premiere_erreur else None,
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
