#!/usr/bin/env python3
"""Verifie la chaine d'export sur cette machine (US-501, US-502, ADR-006).

Construit un memoire de demonstration — 3 chapitres, 8 sections, 12 figures,
5 tableaux, 20 sources dont 3 prepublications, des renvois @fig-, @tbl- et
@sec- — puis compile en PDF.

Les controles de bibliographie et d'assemblage sont faits AVANT la
compilation, et rapportes meme quand Quarto est absent : ils ne dependent que
du code de l'application. Seule la compilation est reportee, parce qu'elle
seule exige un binaire externe (ADR-012 : rien n'est empaquete).

Sortie : 0 conforme - 1 non conforme - 2 Quarto absent.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

try:
    from app.core.config import get_settings
    from app.db.migrations.runner import run_migrations
    from app.db.session import connect, transaction
    from app.export import quarto
    from app.export.assembler import section_anchor
    from app.export.bibliography import PREPRINT_NOTE
    from app.models.plan import PlanNodeOut
    from app.models.section import SectionStatus
    from app.services import export_service
    from app.services.export_service import ExportRequest
except ImportError as exc:  # pragma: no cover - diagnostic d'installation
    print(f"Dependances absentes ({exc}) : pip install -e .[dev]")
    sys.exit(2)

NOW = datetime.now(UTC).isoformat()

N_SOURCES = 20
N_PREPRINTS = 3
N_FIGURES = 12
N_TABLES = 5
N_CHAPITRES = 3
N_SECTIONS = 8

# PNG 1x1 transparent, ecrit octet pour octet : produire douze vraies images
# n'exige aucune dependance de plus, et Quarto refuse un fichier vide.
PNG_1x1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)

results: list[tuple[str, bool, str]] = []
infos: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(f"  [{'OK ' if ok else 'ECHEC'}] {name}" + (f" - {detail}" if detail else ""))
    return ok


def info(message: str) -> None:
    """Canal d'information. N'entre PAS dans le code de sortie."""
    infos.append(message)
    print(f"  [INFO] {message}")


def noeud(nid: int, titre: str, niveau: int, ordinal: int) -> PlanNodeOut:
    return PlanNodeOut(
        id=nid,
        parent_id=None,
        title=titre,
        objective="Objectif verifiable.",
        target_words=1000,
        level=niveau,
        ordinal=ordinal,
    )


def structure() -> list[tuple[int, str, int, int, int | None]]:
    """(id, titre, niveau, ordinal, parent) — 3 chapitres, 8 sections."""
    noeuds: list[tuple[int, str, int, int, int | None]] = []
    identifiant = 1
    repartition = (3, 3, 2)  # 8 sections
    for chapitre in range(N_CHAPITRES):
        parent = identifiant
        noeuds.append((parent, f"Chapitre {chapitre + 1}", 1, chapitre, None))
        identifiant += 1
        for section in range(repartition[chapitre]):
            noeuds.append(
                (identifiant, f"Section {chapitre + 1}.{section + 1}", 2, section, parent)
            )
            identifiant += 1
    return noeuds


def contenu_section(rang: int, cles: list[str], ancre_precedente: str | None) -> str:
    """Texte d'une section, avec ses figures, tableaux et renvois."""
    lignes = [
        f"Cette section etablit le point {rang} a partir des sources approuvees "
        f"[@{cles[0]}]. Un second appui vient de [@{cles[1]}].",
        "",
    ]
    if ancre_precedente:
        lignes += [f"Comme etabli en @{ancre_precedente}, le mecanisme est documente.", ""]

    # Douze figures et cinq tableaux repartis sur les huit sections.
    for numero in range(rang, N_FIGURES + 1, N_SECTIONS):
        lignes += [
            f"![Figure de demonstration {numero}](figures/fig-{numero:02d}.png)"
            f"{{#fig-demo-{numero:02d}}}",
            "",
            f"La @fig-demo-{numero:02d} illustre le propos.",
            "",
        ]
    for numero in range(rang, N_TABLES + 1, N_SECTIONS):
        lignes += [
            "| Grandeur | Valeur |",
            "|---|---|",
            "| Mesure | 42 |",
            "",
            f": Tableau de demonstration {numero} {{#tbl-demo-{numero:02d}}}",
            "",
            f"Le @tbl-demo-{numero:02d} resume les mesures.",
            "",
        ]
    return "\n".join(lignes)


def ecrire_figures(figures: Path) -> None:
    """Ecrit les douze images de demonstration. Synchrone : de l'entree-sortie
    bloquante n'a rien a faire dans la boucle d'evenements."""
    figures.mkdir(parents=True, exist_ok=True)
    for numero in range(1, N_FIGURES + 1):
        (figures / f"fig-{numero:02d}.png").write_bytes(PNG_1x1)


async def batir(db: Path, figures: Path) -> None:
    """Ecrit le projet de demonstration dans son fichier."""
    await asyncio.to_thread(ecrire_figures, figures)

    noeuds = structure()
    feuilles = [n for n in noeuds if n[2] == 2]

    async with connect(db) as conn:
        await run_migrations(conn)
        async with transaction(conn):
            await conn.execute(
                "INSERT INTO project (id, name, subject, language, academic_level,"
                " created_at, updated_at) VALUES (1,'Memoire de demonstration',"
                "'Verification de la chaine d export','fr','doctorat',?,?)",
                (NOW, NOW),
            )
            for sid in range(1, N_SOURCES + 1):
                prepublication = 1 if sid <= N_PREPRINTS else 0
                await conn.execute(
                    "INSERT INTO source_document (id, project_id, kind, title, authors,"
                    " year, venue, is_preprint, imported_at, approved_at)"
                    " VALUES (?,1,?,?,?,?,?,?,?,?)",
                    (
                        sid,
                        "preprint" if prepublication else "article",
                        f"Etude numero {sid} sur les polymeres",
                        f"Auteur{sid:02d}, Prenom",
                        2000 + sid,
                        "Revue de demonstration",
                        prepublication,
                        NOW,
                        NOW,
                    ),
                )

            await conn.execute(
                "INSERT INTO plan (id, project_id, problematique, status, version, created_at)"
                " VALUES (1,1,'Problematique de demonstration','VALIDATED',1,?)",
                (NOW,),
            )
            for nid, titre, niveau, ordinal, parent in noeuds:
                await conn.execute(
                    "INSERT INTO plan_node (id, plan_id, parent_id, ordinal, level, title,"
                    " objective, target_words) VALUES (?,1,?,?,?,?,'Objectif',1000)",
                    (nid, parent, ordinal, niveau, titre),
                )

            # Une section par feuille, chacune citant deux sources ; les
            # vingt sources sont couvertes.
            precedente: str | None = None
            for rang, (nid, titre, niveau, ordinal, _) in enumerate(feuilles, start=1):
                sources = [
                    ((rang - 1) * 2) % N_SOURCES + 1,
                    ((rang - 1) * 2 + 1) % N_SOURCES + 1,
                ]
                # Cle de redaction : la forme produite par US-301.
                cles = [f"src{s}_{2000 + s}_etude" for s in sources]
                await conn.execute(
                    "INSERT INTO draft_section (id, plan_node_id, content_qmd, status,"
                    " version, generated_at) VALUES (?,?,?,?,1,?)",
                    (
                        nid,
                        nid,
                        contenu_section(rang, cles, precedente),
                        str(SectionStatus.REVIEWING),
                        NOW,
                    ),
                )
                for source_id, cle in zip(sources, cles, strict=True):
                    await conn.execute(
                        "INSERT INTO citation (draft_section_id, source_id, bibtex_key,"
                        " verified) VALUES (?,?,?,1)",
                        (nid, source_id, cle),
                    )
                precedente = section_anchor(noeud(nid, titre, niveau, ordinal))

            # Les sources non citees par le tour precedent sont rattachees a
            # la premiere section : le .bib doit en compter exactement vingt.
            citees = set()
            for rang in range(1, len(feuilles) + 1):
                citees.add(((rang - 1) * 2) % N_SOURCES + 1)
                citees.add(((rang - 1) * 2 + 1) % N_SOURCES + 1)
            for source_id in sorted(set(range(1, N_SOURCES + 1)) - citees):
                await conn.execute(
                    "INSERT INTO citation (draft_section_id, source_id, bibtex_key,"
                    " verified) VALUES (?,?,?,1)",
                    (feuilles[0][0], source_id, f"src{source_id}_{2000 + source_id}_etude"),
                )


async def run(racine: Path) -> int:
    db = racine / "projet.sqlite"
    figures = racine / "figures"
    get_settings().data_dir = racine / "donnees"

    await batir(db, figures)

    async with connect(db) as conn:
        repertoire, _, document, entrees = await export_service.prepare(
            conn, 1, ExportRequest(formats=["pdf"]), figures_dir=figures
        )

        # --- US-501 : bibliographie ---------------------------------------
        check(
            f"bibliographie de {N_SOURCES} entrees",
            len(entrees) == N_SOURCES,
            f"{len(entrees)} entree(s)",
        )
        bib = (repertoire / export_service.BIB_FILENAME).read_text(encoding="utf-8")
        check(
            f"{N_PREPRINTS} prepublications portant leur note",
            bib.count(PREPRINT_NOTE) == N_PREPRINTS,
            f"{bib.count(PREPRINT_NOTE)} note(s)",
        )
        check(
            "cles publiees normalisees",
            all("_" in e.key and e.key.islower() for e in entrees),
            f"exemple : {sorted(e.key for e in entrees)[0]}",
        )

        # --- US-502 : assemblage ------------------------------------------
        check(
            f"{N_SECTIONS} sections redigees assemblees",
            len(document.included()) == N_SECTIONS,
            f"{len(document.included())} incluse(s), {len(document.missing())} manquante(s)",
        )
        qmd = (repertoire / export_service.QMD_FILENAME).read_text(encoding="utf-8")
        check(
            f"{N_FIGURES} figures et {N_TABLES} tableaux referencees",
            qmd.count("#fig-demo-") == N_FIGURES and qmd.count("#tbl-demo-") == N_TABLES,
            f"{qmd.count('#fig-demo-')} figure(s), {qmd.count('#tbl-demo-')} tableau(x)",
        )
        check(
            "cles de redaction reecrites en cles publiees",
            "src1_2001_etude" not in qmd,
            "aucune cle de redaction ne subsiste",
        )
        check(
            "aucune syntaxe MyST (ADR-006)",
            not any(m in qmd for m in (":::{note}", "{ref}`", "{numref}`", "{cite}`")),
        )
        check(
            f"{N_FIGURES} figures copiees en chemin relatif",
            len(list((repertoire / "figures").glob("*.png"))) == N_FIGURES,
        )

        # --- Compilation ---------------------------------------------------
        try:
            binaire = quarto.find_quarto()
            version = await quarto.quarto_version(binaire)
        except quarto.QuartoUnavailableError as exc:
            info(f"compilation non mesuree : {exc.message}")
            return 2

        info(f"Quarto {version}")
        resultat, journal = await quarto.render(repertoire, ["pdf"])
        (repertoire / export_service.LOG_FILENAME).write_text(journal, encoding="utf-8")

        check(
            "compilation PDF aboutie",
            resultat.returncode == 0 and resultat.latex_error is None,
            f"code {resultat.returncode}, {resultat.duration_s} s",
        )
        check(
            "aucun renvoi non resolu",
            not resultat.unresolved_crossrefs,
            ", ".join(resultat.unresolved_crossrefs) or "aucun",
        )
        check(
            "aucune citation non resolue",
            not resultat.unresolved_citations,
            ", ".join(resultat.unresolved_citations) or "aucune",
        )
        check("PDF produit", any(nom.endswith(".pdf") for nom in resultat.outputs))

    return 0 if all(ok for _, ok, _ in results) else 1


def main() -> int:
    print("check_quarto_export - US-501, US-502, ADR-006\n")
    with tempfile.TemporaryDirectory(prefix="saw-export-") as temporaire:
        code = asyncio.run(run(Path(temporaire)))

    conformes = sum(1 for _, ok, _ in results if ok)
    print("\n" + "=" * 60)
    if code == 2:
        print(
            f"check_quarto_export : {conformes}/{len(results)} conformes, compilation non mesuree"
        )
        return 2
    print(f"check_quarto_export : {conformes}/{len(results)} conformes")
    return code


if __name__ == "__main__":
    sys.exit(main())
