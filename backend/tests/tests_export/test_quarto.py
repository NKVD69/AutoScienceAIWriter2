"""US-502 — configuration, analyse du journal Quarto, archivage, rapport.

Aucun test de ce fichier n'appelle Quarto, sauf celui marque `integration`.
Le journal est INJECTE : une compilation reelle produit rarement un renvoi
casse a la demande, ce qui rendrait le seul defaut qui compte impossible a
provoquer.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.migrations.runner import run_migrations
from app.db.session import connect, transaction
from app.export.quarto import (
    LatexPackageMissingError,
    QuartoResult,
    QuartoUnavailableError,
    analyse_log,
    build_arguments,
    extract_latex_error,
    find_quarto,
    find_unresolved_citations,
    find_unresolved_crossrefs,
    parse_version,
)
from app.models.section import SectionStatus
from app.services import export_service
from app.services.export_service import (
    BIB_FILENAME,
    CONFIG_FILENAME,
    CSL_FILENAME,
    QMD_FILENAME,
    REPORT_FILENAME,
    ExportRequest,
    nouveau_repertoire,
    prepare,
    render_config,
)

NOW = datetime.now(UTC).isoformat()

JOURNAL_RENVOI_CASSE = """
pandoc
  to: latex
[WARNING] Citeproc: citation dupont_2019_absent not found
WARNING: Unable to resolve crossref for 'fig-inexistante'
WARNING: Unable to resolve crossref for 'tbl-manquant'
Output created: document.pdf
"""

JOURNAL_LATEX = "\n".join(
    [f"ligne de contexte {i}" for i in range(40)]
    + ["! Undefined control sequence.", "l.128 \\macroinconnue"]
    + [f"ligne suivante {i}" for i in range(40)]
)


async def peupler(db: Path, avec_section: bool = True) -> None:
    """Projet minimal : un plan valide, une source citee, une section."""
    async with connect(db) as conn:
        await run_migrations(conn)
        async with transaction(conn):
            await conn.execute(
                "INSERT INTO project (id, name, subject, language, academic_level,"
                " created_at, updated_at) VALUES (1,'Memoire sur les polymeres',"
                "'Sujet','fr','doctorat',?,?)",
                (NOW, NOW),
            )
            await conn.execute(
                "INSERT INTO source_document (id, project_id, kind, title, authors, year,"
                " is_preprint, imported_at) VALUES (1,1,'article','Filtration renale',"
                "'Dupont, Alice',2019,0,?)",
                (NOW,),
            )
            await conn.execute(
                "INSERT INTO source_document (id, project_id, kind, title, authors, year,"
                " is_preprint, imported_at) VALUES (2,1,'preprint','Resultats recents',"
                "'Nguyen, Chi',2024,1,?)",
                (NOW,),
            )
            await conn.execute(
                "INSERT INTO plan (id, project_id, problematique, status, version, created_at)"
                " VALUES (1,1,'Problematique','VALIDATED',1,?)",
                (NOW,),
            )
            for nid, ordinal, titre in ((1, 0, "Premier chapitre"), (2, 1, "Second chapitre")):
                await conn.execute(
                    "INSERT INTO plan_node (id, plan_id, parent_id, ordinal, level, title,"
                    " objective, target_words) VALUES (?,1,NULL,?,1,?,'Objectif',1000)",
                    (nid, ordinal, titre),
                )

            if avec_section:
                await conn.execute(
                    "INSERT INTO draft_section (id, plan_node_id, content_qmd, status,"
                    " version, generated_at) VALUES (1,1,"
                    # Les DEUX cles figurent dans le texte. citeproc ne rend que
                    # ce qui est cite dans le document : une citation verifiee
                    # en base mais absente du texte n'apparait dans aucune
                    # bibliographie rendue, note de prepublication comprise.
                    "'La filtration decroit [@src1_2019_filtration], ce que confirment "
                    "des resultats recents [@src2_2024_resultats].',?,1,?)",
                    (str(SectionStatus.REVIEWING), NOW),
                )
                for cid, source_id, cle in (
                    (1, 1, "src1_2019_filtration"),
                    (2, 2, "src2_2024_resultats"),
                ):
                    await conn.execute(
                        "INSERT INTO citation (id, draft_section_id, source_id, bibtex_key,"
                        " verified) VALUES (?,1,?,?,1)",
                        (cid, source_id, cle),
                    )


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path / "donnees")
    return tmp_path


# --- Detection du binaire -------------------------------------------------


def test_quarto_missing_raises_actionable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """L'application n'embarque pas Quarto (ADR-012) : son absence est un
    etat normal, rapporte avec la procedure des deux plateformes."""
    monkeypatch.setattr(get_settings(), "quarto_path", None)
    monkeypatch.setattr(shutil, "which", lambda _: None)

    with pytest.raises(QuartoUnavailableError) as exc:
        find_quarto()

    message = exc.value.message
    assert exc.value.status_code == 503
    assert "winget" in message, "la procedure Windows doit figurer"
    assert "Linux" in message
    assert "tinytex" in message, "la chaine PDF demande une etape de plus"
    assert "SAW_QUARTO_PATH" in message


def test_configured_path_that_does_not_exist_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "quarto_path", tmp_path / "absent" / "quarto.exe")
    with pytest.raises(QuartoUnavailableError) as exc:
        find_quarto()
    assert "SAW_QUARTO_PATH" in exc.value.message


def test_version_parsing_and_minimum() -> None:
    assert parse_version("1.5.57") == (1, 5, 57)
    assert parse_version("quarto version 1.4.0\n") == (1, 4, 0)
    with pytest.raises(QuartoUnavailableError):
        parse_version("version indisponible")


def test_too_old_message_names_both_versions() -> None:
    erreur = QuartoUnavailableError.too_old("1.2.0", "1.4.0")
    assert "1.2.0" in erreur.message and "1.4.0" in erreur.message


def test_missing_latex_package_names_the_command() -> None:
    erreur = LatexPackageMissingError.for_package("koma-script.sty")
    assert "tlmgr install koma-script" in erreur.message
    assert "quarto install tinytex" in erreur.message


# --- Analyse du journal ---------------------------------------------------


def test_log_parser_detects_unresolved_crossref() -> None:
    """Quarto sort en 0 sur un document dont les renvois ne se resolvent
    pas : relayer son code de retour rapporterait un succes sur un document
    casse."""
    renvois = find_unresolved_crossrefs(JOURNAL_RENVOI_CASSE)
    assert renvois == ["fig-inexistante", "tbl-manquant"]


@pytest.mark.parametrize(
    "journal",
    [
        "WARNING: Unable to resolve crossref for 'fig-un'",
        'Unable to resolve crossref "fig-un"',
        "?? (fig-un)",
    ],
)
def test_log_parser_covers_the_observed_wordings(journal: str) -> None:
    assert find_unresolved_crossrefs(journal) == ["fig-un"]


def test_log_parser_detects_unresolved_citation() -> None:
    citations = find_unresolved_citations(JOURNAL_RENVOI_CASSE)
    assert citations == ["dupont_2019_absent"]


# Journal RELEVE sur Quarto 1.10.18, codes de couleur ANSI compris, en rendant
# un document volontairement casse. Quarto a rendu ce document en code 0 : sans
# analyse du journal, l'export aurait ete declare reussi.
JOURNAL_QUARTO_1_10 = (
    "\x1b[33mWARNING (C:/Program Files/Quarto/share/filters/main.lua:14840) "
    "Unable to resolve crossref @fig-inexistante\n"
    "\x1b[39m\x1b[33mWARNING (C:/Program Files/Quarto/share/filters/main.lua:14840) "
    "Unable to resolve crossref @tbl-absent\n"
    "\x1b[39m\x1b[33mWARNING (C:/Program Files/Quarto/share/filters/main.lua:14840) "
    "Unable to resolve crossref @sec-fantome\n"
    "\x1b[39m[WARNING] Citeproc: citation cle_inexistante_2020 not found\n"
    "Output created: document.html\n"
)


def test_log_parser_matches_real_quarto_1_10_warnings() -> None:
    """Les motifs avaient ete ecrits d'apres la documentation. Ce test les
    confronte a la formulation reellement emise, pour qu'une evolution de
    Quarto qui la changerait soit visible ici plutot que dans un PDF casse."""
    renvois, citations, erreur = analyse_log(JOURNAL_QUARTO_1_10)

    assert renvois == ["fig-inexistante", "tbl-absent", "sec-fantome"]
    assert citations == ["cle_inexistante_2020"]
    assert erreur is None


def test_log_parser_is_silent_on_a_clean_log() -> None:
    propre = "pandoc\n  to: latex\nOutput created: document.pdf\n"
    assert analyse_log(propre) == ([], [], None)


def test_latex_error_reported_with_context_window() -> None:
    """Un journal de compilation fait quatre mille lignes ; les vingt qui
    entourent l'echec sont les seules qui disent quoi corriger."""
    fenetre = extract_latex_error(JOURNAL_LATEX, contexte=20)

    assert fenetre is not None
    assert "! Undefined control sequence." in fenetre
    assert r"l.128 \macroinconnue" in fenetre
    lignes = fenetre.splitlines()
    assert len(lignes) <= 21, "la fenetre est bornee"
    assert len(lignes) < len(JOURNAL_LATEX.splitlines())
    # Le journal entier n'est pas transmis.
    assert "ligne de contexte 0" not in fenetre


def test_no_latex_error_returns_none() -> None:
    assert extract_latex_error("compilation sans incident") is None


def test_command_line_does_not_pass_to_option() -> None:
    """Les deux ecritures de `--to` echouent, et les deux ont ete mesurees :
    repetee, la derniere occurrence ecrase les autres et un export de trois
    formats n'en produisait qu'un, sans erreur ; en liste separee par des
    virgules, Quarto 1.10 repond « Unknown format ». Ce sont les formats
    declares dans `_quarto.yml` qui gouvernent."""
    arguments = build_arguments(Path("quarto.exe"), Path("/export"), ["pdf", "docx", "html"])

    assert "--to" not in arguments
    assert arguments[1] == "render"
    assert arguments[-1] == str(Path("/export"))


def test_config_is_the_single_source_of_requested_formats() -> None:
    """Puisque la ligne de commande ne porte plus les formats, c'est la
    configuration qui doit les porter tous — sinon un format demande serait
    silencieusement perdu."""
    demandes = ["pdf", "docx", "html"]
    config = _config(formats=demandes)
    assert set(config["format"]) == set(demandes)


def test_partial_result_is_not_a_success() -> None:
    resultat = QuartoResult(returncode=0, unresolved_crossrefs=["fig-un"])
    assert not resultat.ok
    assert resultat.partial

    complet = QuartoResult(returncode=0)
    assert complet.ok and not complet.partial


# --- Configuration --------------------------------------------------------


def _config(langue: str = "fr", formats: list[str] | None = None) -> dict:
    rendu = render_config(
        plan=None,
        projet={"name": "Memoire sur les polymeres", "language": langue},
        formats=formats or ["pdf"],
    )
    return yaml.safe_load(rendu)


def test_quarto_yml_sets_lang_from_project() -> None:
    """Sans `lang`, « Figure », « Tableau » et « References » restent en
    anglais au milieu d'un memoire francais."""
    assert _config("fr")["lang"] == "fr"
    assert _config("en")["lang"] == "en"


def test_quarto_yml_points_to_generated_bib() -> None:
    """Un chemin fixe ferait compiler avec la bibliographie d'un export
    anterieur."""
    config = _config()
    assert config["bibliography"] == BIB_FILENAME
    assert config["csl"] == CSL_FILENAME


def test_quarto_yml_declares_only_requested_formats() -> None:
    assert set(_config(formats=["pdf"])["format"]) == {"pdf"}
    assert set(_config(formats=["pdf", "docx", "html"])["format"]) == {"pdf", "docx", "html"}


def test_quarto_yml_enables_numbering_and_crossref() -> None:
    config = _config()
    assert config["number-sections"] is True
    assert config["crossref"]["fig-title"] == "Figure"
    assert config["crossref"]["tbl-title"] == "Tableau"


# --- Durcissement : constats de la revue securite -------------------------


@pytest.mark.parametrize(
    "nom",
    [
        'Le "vivant" et ses marges',
        "Titre\nfilters:\n  - evil.cmd",
        "Titre : avec deux-points # et diese",
        "Accolades {#sec-x} et crochets [@cle]",
    ],
)
def test_quarto_yml_survives_any_project_name(nom: str) -> None:
    """Le nom d'un projet est du texte libre. Interpole brut dans le YAML, un
    simple guillemet cassait l'export, et un saut de ligne injectait une cle
    de configuration — dont `filters`, qui fait EXECUTER un programme par
    Quarto. Constat de revue, confirme sur le binaire reel."""
    config = yaml.safe_load(
        render_config(plan=None, projet={"name": nom, "language": "fr"}, formats=["pdf"])
    )

    assert config["title"] == nom
    assert config["project"]["title"] == nom
    assert "filters" not in config
    assert "pre-render" not in config["project"]


def test_quarto_yml_refuses_an_invalid_project_language() -> None:
    """`lang` etait interpole SANS guillemets : la langue d'un projet recu d'un
    tiers, dans un fichier .sqlite, suffisait a ajouter une cle `filters`.

    Echappee, elle restait nuisible autrement : Quarto construit avec elle le
    chemin de son fichier de traduction, et une valeur piegee faisait echouer
    l'export sur une erreur systeme opaque — mesure sur Quarto 1.10.18. Elle
    est donc refusee au rendu, avec un message qui dit quoi corriger.
    """
    from app.services.export_service import InvalidProjectLanguageError

    hostile = 'fr\nfilters:\n  - "evil_filter.cmd"\nx: y'
    with pytest.raises(InvalidProjectLanguageError) as exc:
        render_config(plan=None, projet={"name": "These", "language": hostile}, formats=["html"])
    assert "langue" in exc.value.message.lower()


@pytest.mark.parametrize("langue", ["fr", "en", "en-GB", "pt-BR", "zh-Hans", "de-CH"])
def test_quarto_yml_accepts_standard_language_tags(langue: str) -> None:
    config = yaml.safe_load(
        render_config(plan=None, projet={"name": "These", "language": langue}, formats=["html"])
    )
    assert config["lang"] == langue


@pytest.mark.parametrize("formats", [[], ["epub"], ["pdf", "exe"]])
def test_export_request_rejects_formats_outside_the_contract(formats: list[str]) -> None:
    """Le contrat enumere pdf, docx et html. Une liste vide ou un format
    inconnu produisait un `_quarto.yml` sans aucun format, au lieu d'un 422."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ExportRequest(formats=formats)


@pytest.mark.parametrize(
    "gabarit",
    ["../..", "..\\..", "C:/Windows/System32", "//hote/partage", "\\\\hote\\partage", "inexistant"],
)
def test_export_request_rejects_template_outside_the_templates_dir(gabarit: str) -> None:
    """`TEMPLATES_DIR / gabarit` remplace tout le chemin quand `gabarit` est
    absolu : un chemin UNC faisait lire le gabarit sur un partage SMB distant
    — sortie reseau non consentie (ADR-010) et fuite de condensat NTLM."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ExportRequest(formats=["pdf"], template=gabarit)


def test_export_request_accepts_the_contract_values() -> None:
    requete = ExportRequest(formats=["pdf", "docx", "html"])
    assert requete.template == "default"


def _processus_vivant(pid: int) -> bool:
    """Le processus existe-t-il encore ? Sans psutil, et sans jamais le tuer.

    Sous Windows, `os.kill(pid, 0)` n'est PAS une sonde : il appelle
    TerminateProcess. On interroge donc le code de sortie par l'API Win32.
    """
    import os
    import sys

    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True

    import ctypes

    process_query_limited_information = 0x1000
    still_active = 259
    noyau = ctypes.windll.kernel32
    poignee = noyau.OpenProcess(process_query_limited_information, False, pid)
    if not poignee:
        return False
    try:
        code = ctypes.c_ulong()
        noyau.GetExitCodeProcess(poignee, ctypes.byref(code))
        return code.value == still_active
    finally:
        noyau.CloseHandle(poignee)


async def test_timeout_kills_the_whole_process_tree(tmp_path: Path) -> None:
    """Sous Windows, `kill()` ne tue que le processus direct : Pandoc, LuaLaTeX
    et Deno, descendants de Quarto, survivaient au depassement de delai. Et
    `communicate()` pouvait attendre sans fin la fermeture de tubes qu'ils
    tenaient encore — le delai ne bornait alors plus rien.

    Simule sans Quarto : un enfant lance un petit-enfant qui dort deux minutes
    en heritant de la sortie standard, exactement la forme du cas reel.
    """
    import asyncio
    import sys
    import time

    from app.export.quarto import CompilationTimeoutError, run_bounded

    marque = tmp_path / "petit_enfant.pid"
    enfant = (
        "import pathlib, subprocess, sys, time\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        f"pathlib.Path(r'{marque}').write_text(str(p.pid))\n"
        "time.sleep(120)\n"
    )

    debut = time.monotonic()
    with pytest.raises(CompilationTimeoutError):
        await run_bounded(
            [sys.executable, "-c", enfant], tmp_path, tmp_path / "journal.log", timeout_s=3
        )
    assert time.monotonic() - debut < 30, "le delai doit borner l'attente"

    petit_enfant = int(marque.read_text(encoding="utf-8"))
    for _ in range(50):
        if not _processus_vivant(petit_enfant):
            break
        await asyncio.sleep(0.1)
    assert not _processus_vivant(petit_enfant), "le petit-enfant a survecu au depassement"


def test_path_budget_refuses_directories_quarto_cannot_open() -> None:
    """Quarto ouvre une base SQLite (Deno KV) sous le repertoire d'export, dans
    `.quarto/quarto-session-temp<16 hex>/sass/sass.kv`. Au-dela de 260
    caracteres elle ne s'ouvre pas — et `LongPathsEnabled` n'y change rien,
    mesure sur ce poste. L'export echouait alors en ECHEC muet. Le budget est
    donc verifie AVANT de compiler, avec la variable a raccourcir."""
    from app.export.quarto import ExportPathTooLongError, check_path_budget

    check_path_budget(Path("C:/Users/u/.science-ai-writer/exports/1/20260912T220957Z"), True)

    long = Path("C:/" + "d" * 200 + "/exports/1/20260912T220957Z")
    with pytest.raises(ExportPathTooLongError) as exc:
        check_path_budget(long, True)
    assert "SAW_DATA_DIR" in exc.value.message
    # Hors Windows, la limite ne s'applique pas.
    check_path_budget(long, False)


def test_log_parser_recognizes_quarto_path_too_long() -> None:
    """Erreur reellement emise par Quarto 1.10.18, relevee sur ce poste."""
    from app.export.quarto import find_path_too_long

    journal = (
        "ERROR: unable to open database file: C:\\tres\\long\\chemin\\.quarto\\"
        "quarto-session-tempb23e1b808a0fe21d\\sass\\sass.kv\n\nStack trace:\n"
        "    at async Object.openKv (ext:deno_kv/01_db.ts:9:15)\n"
    )
    assert find_path_too_long(journal) == (
        "C:\\tres\\long\\chemin\\.quarto\\quarto-session-tempb23e1b808a0fe21d\\sass\\sass.kv"
    )
    assert find_path_too_long("Output created: document.pdf") is None


async def test_prepare_refuses_a_data_dir_too_long_for_quarto(
    project_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le refus a lieu avant d'ecrire quoi que ce soit sur le disque."""
    from app.export import quarto as module_quarto
    from app.export.quarto import ExportPathTooLongError

    await peupler(project_db)
    monkeypatch.setattr(module_quarto, "IS_WINDOWS", True)
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path / ("x" * 200))

    async with connect(project_db) as conn:
        with pytest.raises(ExportPathTooLongError):
            await prepare(conn, 1, ExportRequest(formats=["pdf"]))

    assert not (tmp_path / ("x" * 200)).exists()


def test_default_csl_is_valid_xml() -> None:
    """Un CSL casse fait echouer Pandoc a la derniere etape."""
    from xml.etree import ElementTree

    chemin = export_service.TEMPLATES_DIR / "default" / CSL_FILENAME
    racine = ElementTree.fromstring(chemin.read_text(encoding="utf-8"))
    assert racine.tag.endswith("style")
    assert racine.get("version") == "1.0"


# --- Archivage et rapport -------------------------------------------------


def test_export_artifacts_archived_and_never_overwritten(data_dir: Path) -> None:
    """Le repertoire est la preuve de ce qui a ete produit a une date."""
    premier = nouveau_repertoire(1)
    (premier / "temoin.txt").write_text("premier export", encoding="utf-8")

    second = nouveau_repertoire(1)
    troisieme = nouveau_repertoire(1)

    assert premier != second != troisieme
    assert len({premier, second, troisieme}) == 3
    assert (premier / "temoin.txt").read_text(encoding="utf-8") == "premier export"
    # Meme seconde : suffixe, jamais ecrasement.
    assert second.name.startswith(premier.name)


async def test_prepare_writes_every_artifact(project_db: Path, data_dir: Path) -> None:
    await peupler(project_db)
    async with connect(project_db) as conn:
        repertoire, _, _, entrees = await prepare(conn, 1, ExportRequest(formats=["pdf"]))

    for nom in (BIB_FILENAME, QMD_FILENAME, CONFIG_FILENAME, CSL_FILENAME):
        assert (repertoire / nom).exists(), nom
    assert "Premier chapitre" in (repertoire / QMD_FILENAME).read_text(encoding="utf-8")
    assert "dupont_2019_filtration" in (repertoire / BIB_FILENAME).read_text(encoding="utf-8")
    assert len(entrees) == 2


async def test_export_report_fields(project_db: Path, data_dir: Path) -> None:
    await peupler(project_db)
    async with connect(project_db) as conn:
        repertoire, rapport, _, _ = await prepare(conn, 1, ExportRequest(formats=["pdf", "html"]))

    assert rapport.project_id == 1
    assert rapport.formats == ["pdf", "html"]
    assert rapport.bibliography_entries == 2
    assert rapport.preprint_entries == 1
    assert [s["node_id"] for s in rapport.sections_included] == [1]
    # Le second chapitre n'est pas redige : signale, pas bloquant.
    assert [s["node_id"] for s in rapport.sections_missing] == [2]
    assert rapport.include_ai_declaration is True

    chemin = export_service.ecrire_rapport(repertoire, rapport)
    relu = json.loads(chemin.read_text(encoding="utf-8"))
    assert relu["directory"] == str(repertoire)
    assert set(relu) >= {
        "sections_included",
        "sections_missing",
        "bibliography_entries",
        "unresolved_crossrefs",
        "duration_s",
        "quarto_version",
        "status",
    }


def test_status_partial_when_a_section_is_missing() -> None:
    """Un document dont des sections manquent se lit, mais n'est pas
    soutenable en l'etat."""
    rapport = export_service.ExportReport(
        project_id=1,
        directory=".",
        started_at=NOW,
        formats=["pdf"],
        template="default",
        include_ai_declaration=True,
        sections_included=[],
        sections_missing=[{"node_id": 2, "title": "Second"}],
        bibliography_entries=0,
        preprint_entries=0,
    )
    assert export_service._statut(QuartoResult(returncode=0), rapport) == "PARTIEL"

    complet = rapport.model_copy(update={"sections_missing": []})
    assert export_service._statut(QuartoResult(returncode=0), complet) == "OK"
    assert export_service._statut(QuartoResult(returncode=1), complet) == "ECHEC"
    assert (
        export_service._statut(QuartoResult(returncode=0, unresolved_crossrefs=["fig-un"]), complet)
        == "PARTIEL"
    )


async def test_unverified_citation_produces_no_artifact(project_db: Path, data_dir: Path) -> None:
    """Rien n'est ecrit, pas meme un repertoire : un artefact partiel se
    prendrait pour un export reussi a la relecture."""
    from app.export.bibliography import UnverifiedCitationError

    await peupler(project_db)
    async with connect(project_db) as conn:
        async with transaction(conn):
            await conn.execute("UPDATE citation SET verified = 0 WHERE id = 1")

        with pytest.raises(UnverifiedCitationError):
            await prepare(conn, 1, ExportRequest(formats=["pdf"]))

    racine = get_settings().exports_dir(1)
    assert not racine.exists() or not list(racine.iterdir())


# --- Compilation reelle ---------------------------------------------------


@pytest.mark.integration
async def test_export_pdf_docx_html(project_db: Path, data_dir: Path) -> None:
    """Compilation reelle. Ignoree si Quarto n'est pas installe."""
    try:
        find_quarto()
    except QuartoUnavailableError:
        pytest.skip("Quarto absent : mesure impossible, pas un echec")

    await peupler(project_db)
    async with connect(project_db) as conn:
        rapport = await export_service.run_export(
            conn, 1, ExportRequest(formats=["pdf", "docx", "html"])
        )

    assert rapport.status in ("OK", "PARTIEL")
    assert rapport.unresolved_citations == []
    assert rapport.unresolved_crossrefs == []
    produits = {Path(nom).suffix for nom in rapport.outputs}
    assert produits >= {".pdf", ".docx", ".html"}
    assert (Path(rapport.directory) / REPORT_FILENAME).exists()

    # Scenario Gherkin « Export triple » : la bibliographie est rendue par
    # citeproc DANS CHAQUE format — la presence des fichiers ne le prouve pas.
    from app.export.bibliography import PREPRINT_NOTE

    for extension in (".pdf", ".docx", ".html"):
        texte = texte_rendu(Path(rapport.directory) / f"document{extension}")
        assert "Dupont" in texte, f"entree bibliographique absente du {extension}"
        assert PREPRINT_NOTE in texte, f"note de prepublication absente du {extension}"


def texte_rendu(chemin: Path) -> str:
    """Texte lisible d'une sortie, espaces aplatis.

    Aplatir n'est pas une commodite : dans le PDF, la note de prepublication
    est coupee par un saut de ligne, et une recherche brute la declarait
    absente alors qu'elle y figure. Les cesures de fin de ligne sont
    recollees pour la meme raison.
    """
    import re
    import zipfile

    if chemin.suffix == ".pdf":
        import pymupdf

        with pymupdf.open(chemin) as document:
            brut = "\n".join(page.get_text() for page in document)
    elif chemin.suffix == ".docx":
        with zipfile.ZipFile(chemin) as archive:
            brut = re.sub(r"<[^>]+>", "", archive.read("word/document.xml").decode("utf-8"))
    else:
        brut = re.sub(r"<[^>]+>", " ", chemin.read_text(encoding="utf-8"))

    recolle = re.sub(r"-\s*\n\s*", "", brut)
    return " ".join(recolle.split())


@pytest.mark.integration
async def test_export_timeout_is_reported(project_db: Path, data_dir: Path) -> None:
    """Un depassement propose d'augmenter le budget, il ne constate pas."""
    try:
        find_quarto()
    except QuartoUnavailableError:
        pytest.skip("Quarto absent : mesure impossible, pas un echec")

    await peupler(project_db)
    async with connect(project_db) as conn:
        repertoire, _, _, _ = await prepare(conn, 1, ExportRequest(formats=["pdf"]))
        from app.export import quarto as module_quarto

        # Delai nul, et non d'une seconde : le test ne doit pas dependre de la
        # vitesse de Quarto. Avec une seconde, un Quarto qui echoue plus vite
        # que cela — un chemin trop long suffit — fait croire que le delai
        # n'est jamais applique.
        with pytest.raises(AppError) as exc:
            await module_quarto.render(repertoire, ["pdf"], timeout_s=0)

    assert "SAW_EXPORT_TIMEOUT_SECONDS" in exc.value.message
