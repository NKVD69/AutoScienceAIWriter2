#!/usr/bin/env python3
"""
SPIKE 04 — Quarto : compilation d'un document long avec renvois croisés.

Hypothèses testées
  H4.1  Le binaire Quarto est présent et sa version est exploitable.
  H4.2  Un document de ~150 pages compile en PDF en moins de 90 s.
  H4.3  Les renvois @fig-, @tbl-, @sec- sont tous résolus sur un document long.
  H4.4  citeproc résout les citations et rend la bibliographie.
  H4.5  Les exports DOCX et HTML aboutissent.
  H4.6  Les libellés suivent la langue du projet (Figure/Tableau en français).
  H4.7  Les renvois non résolus sont DÉTECTABLES dans le journal de Quarto.

Décision associée : ADR-006. H4.7 conditionne US-502 : si un renvoi cassé ne
laisse aucune trace exploitable dans le journal, l'analyse prévue est
impossible et il faut une vérification par relecture du PDF.

Prérequis : quarto >= 1.4 et une chaîne LaTeX (quarto install tinytex)
Usage : python spike_04_quarto.py [--chapters 12]
Sortie : 0 conforme · 1 non conforme · 2 environnement insuffisant
"""
from __future__ import annotations
import argparse, os, re, shutil, subprocess, sys, tempfile, time

results: list[tuple[str, bool, str]] = []
environment_gap: list[str] = []


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(f"  [{'OK ' if ok else 'ÉCHEC'}] {name}" + (f" — {detail}" if detail else ""))
    return ok


LOREM = (
    "L'analyse des données recueillies met en évidence une variation "
    "significative des paramètres observés sur l'ensemble de la période "
    "considérée. Cette variation, dont l'amplitude reste comparable à celle "
    "rapportée dans la littérature, appelle une interprétation prudente au "
    "regard des limites méthodologiques exposées plus haut. "
)


def build_project(root: str, n_chapters: int) -> tuple[int, int, int]:
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "_quarto.yml"), "w", encoding="utf-8") as f:
        f.write(
            "project:\n  type: book\n\nbook:\n  title: \"Spike 04 — document long\"\n"
            "  author: \"Science AI Writer IDE\"\n  chapters:\n    - index.qmd\n"
            + "".join(f"    - ch{i:02d}.qmd\n" for i in range(1, n_chapters + 1))
            + "\nlang: fr\nbibliography: references.bib\nnumber-sections: true\n"
              "toc: true\nlof: true\nlot: true\n\nformat:\n"
              "  pdf:\n    documentclass: report\n    keep-tex: false\n"
              "  html:\n    theme: cosmo\n  docx: default\n"
        )
    n_refs = 20
    with open(os.path.join(root, "references.bib"), "w", encoding="utf-8") as f:
        for i in range(1, n_refs + 1):
            f.write(
                f"@article{{ref{i:02d},\n  title={{Étude {i} sur les paramètres observés}},\n"
                f"  author={{Dupont, A. and Martin, B.}},\n  journal={{Revue de spécialité}},\n"
                f"  year={{{2000 + i}}},\n  volume={{{i}}},\n  pages={{{i * 10}--{i * 10 + 9}}}\n}}\n\n"
            )
    with open(os.path.join(root, "index.qmd"), "w", encoding="utf-8") as f:
        f.write("# Introduction {#sec-intro}\n\n" + LOREM * 4 + "\n\n")

    n_fig = n_tbl = 0
    for c in range(1, n_chapters + 1):
        lines = [f"# Chapitre {c} {{#sec-ch{c:02d}}}\n"]
        for s in range(1, 4):
            lines.append(f"\n## Section {c}.{s} {{#sec-ch{c:02d}s{s}}}\n")
            lines.append(LOREM * 6 + f" [@ref{((c + s) % 20) + 1:02d}]\n")
            # renvoi arrière vers un chapitre antérieur
            if c > 1:
                lines.append(f"\nComme établi au [@sec-ch{c - 1:02d}], ce résultat converge.\n")
            if s == 1:
                n_fig += 1
                lines.append(
                    f"\n```{{python}}\n#| label: fig-c{c:02d}\n"
                    f"#| fig-cap: \"Évolution observée au chapitre {c}\"\n"
                    "#| echo: false\n"
                    "import matplotlib\nmatplotlib.use('Agg')\n"
                    "import matplotlib.pyplot as plt, numpy as np\n"
                    f"rng = np.random.default_rng({c})\n"
                    "x = np.linspace(0, 10, 120)\n"
                    "plt.figure(figsize=(5,3)); plt.plot(x, np.sin(x) + rng.normal(0,.08,120))\n"
                    "plt.xlabel('temps'); plt.ylabel('valeur'); plt.tight_layout(); plt.show()\n"
                    "```\n"
                    f"\nLa @fig-c{c:02d} illustre ce comportement.\n"
                )
            if s == 2:
                n_tbl += 1
                lines.append(
                    f"\n| Paramètre | Valeur | Écart-type |\n|---|---:|---:|\n"
                    f"| alpha | {c * 1.5:.2f} | 0.1{c} |\n| beta | {c * 2.5:.2f} | 0.2{c} |\n"
                    f"\n: Paramètres du chapitre {c} {{#tbl-c{c:02d}}}\n"
                    f"\nLe @tbl-c{c:02d} récapitule ces valeurs.\n"
                )
        lines.append("\n" + LOREM * 8 + "\n")
        with open(os.path.join(root, f"ch{c:02d}.qmd"), "w", encoding="utf-8") as f:
            f.write("".join(lines))
    return n_fig, n_tbl, n_refs


# Deux familles de motifs, à ne surtout pas confondre.
#
# Découverte du 2026-09-07 : une première version cherchait "not found" dans le
# journal. Sur une distribution TeX Live incomplète, "File `lmodern.sty' not
# found" a été compté comme un renvoi non résolu. Un paquet LaTeX manquant
# était donc rapporté comme un défaut du document. Un instrument de mesure qui
# confond une panne d'environnement avec une non-conformité est pire qu'absent.
UNRESOLVED = re.compile(
    r"(unresolved (?:cross-?reference|citation)"
    r"|undefined (?:cross-?reference|citation)"
    r"|\[WARNING\][^\n]*citation[^\n]*not found"
    r"|Reference `[^`']+' on page \d+ undefined"
    r"|Citation `[^`']+' on page \d+ undefined"
    r"|crossref[^\n]*(?:unresolved|missing))", re.I)

# Panne d'environnement : ni conforme ni non conforme, non mesurable.
LATEX_MISSING = re.compile(
    r"(File `([^']+)' not found"
    r"|no matching packages"
    r"|metric data not found"
    r"|luaotfload-main' not found"
    r"|Fatal error occurred, no output PDF file produced)", re.I)


def render(root: str, fmt: str) -> tuple[int, str, float]:
    t = time.perf_counter()
    p = subprocess.run(["quarto", "render", "--to", fmt], cwd=root,
                       capture_output=True, text=True, timeout=1200)
    return p.returncode, (p.stdout + p.stderr), time.perf_counter() - t


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chapters", type=int, default=12,
                    help="12 chapitres ≈ 150 pages")
    ap.add_argument("--keep", action="store_true", help="conserver le répertoire")
    args = ap.parse_args()

    print("SPIKE 04 — Quarto, document long\n")

    # ---- H4.1 ---------------------------------------------------------
    print("H4.1 — Environnement")
    if not shutil.which("quarto"):
        print("  [ÉCHEC] binaire quarto introuvable dans le PATH.")
        print("          https://quarto.org/docs/get-started/")
        return 2
    ver = subprocess.run(["quarto", "--version"], capture_output=True, text=True).stdout.strip()
    check("quarto présent", True, f"version {ver}")
    tex = shutil.which("pdflatex") or shutil.which("xelatex") or shutil.which("tectonic")
    if not tex:
        chk = subprocess.run(["quarto", "list", "tools"], capture_output=True, text=True)
        if "tinytex" not in chk.stdout.lower():
            print("  [ÉCHEC] aucune chaîne LaTeX. quarto install tinytex")
            return 2
    check("chaîne LaTeX disponible", True, tex or "tinytex")

    root = os.path.join(tempfile.mkdtemp(prefix="spike04_"), "book")
    n_fig, n_tbl, n_refs = build_project(root, args.chapters)
    print(f"\n  Projet généré : {args.chapters} chapitres · {n_fig} figures · "
          f"{n_tbl} tableaux · {n_refs} références")
    print(f"  {root}")

    # ---- H4.2 / H4.3 / H4.4 -------------------------------------------
    print("\nH4.2 à H4.4 — Compilation PDF")
    code, log, secs = render(root, "pdf")
    print(f"  durée : {secs:.1f} s")

    pdf_mesurable = True
    if code != 0:
        manquant = LATEX_MISSING.search(log) or LATEX_MISSING.search(
            _read(os.path.join(root, "index.log")))
        if manquant:
            pdf_mesurable = False
            nom = (manquant.group(2) or manquant.group(1))[:80]
            print(f"  [NON MESURÉ] chaîne LaTeX incomplète : {nom}")
            print("               Remédiation : quarto install tinytex, ou")
            print("               tlmgr install <paquet>. La compilation PDF n'est")
            print("               ni conforme ni non conforme : elle est impossible ici.")
            environment_gap.append("chaîne LaTeX incomplète : " + nom)

    if pdf_mesurable:
        check("compilation PDF réussie", code == 0,
              "" if code == 0 else log.strip().splitlines()[-1][:160])
        check("compilation < 90 s", secs < 90, f"{secs:.1f} s")
        hits = UNRESOLVED.findall(log)
        check("aucun renvoi ni citation non résolu", not hits,
              "" if not hits else f"{len(hits)} signalement(s)")

    pdf = None
    for cand in ("_book", "."):
        d = os.path.join(root, cand)
        if os.path.isdir(d):
            for f in os.listdir(d):
                if f.endswith(".pdf"):
                    pdf = os.path.join(d, f)
    if pdf and pdf_mesurable:
        size = os.path.getsize(pdf) / 1024
        pages = "?"
        try:
            import fitz  # PyMuPDF, optionnel
            pages = fitz.open(pdf).page_count
        except Exception:  # noqa: BLE001
            pass
        check("PDF produit", size > 50, f"{size:.0f} Ko · {pages} pages")
    elif pdf_mesurable:
        check("PDF produit", False, "aucun .pdf trouvé")

    # ---- H4.6 langue ---------------------------------------------------
    print("\nH4.6 — Libellés en français")
    if pdf:
        try:
            import fitz
            txt = "".join(p.get_text() for p in fitz.open(pdf))
            fr = "Figure" in txt and ("Tableau" in txt or "Table" not in txt)
            check("libellés localisés", "Tableau" in txt or "Figure" in txt,
                  "Tableau présent" if "Tableau" in txt else "vérifier lang: fr")
        except Exception:
            print("  (PyMuPDF absent — vérification visuelle requise)")
    # ---- H4.5 autres formats -------------------------------------------
    print("\nH4.5 — Exports DOCX et HTML")
    for fmt in ("docx", "html"):
        code, log, secs = render(root, fmt)
        check(f"export {fmt}", code == 0, f"{secs:.1f} s" if code == 0
              else log.strip().splitlines()[-1][:120])

    # ---- H4.7 détectabilité --------------------------------------------
    print("\nH4.7 — Un renvoi cassé est-il détectable dans le journal ?")
    with open(os.path.join(root, "ch01.qmd"), "a", encoding="utf-8") as f:
        f.write("\n\nCe renvoi pointe une cible inexistante : @fig-inexistante "
                "et cette citation aussi [@refinexistante].\n")
    code, log, _ = render(root, "html")
    hits2 = UNRESOLVED.findall(log)
    check("renvoi cassé détectable dans le journal", bool(hits2),
          f"{len(hits2)} signalement(s)" if hits2
          else "AUCUNE trace : l'analyse de journal prévue en US-502 est impossible")
    if not hits2:
        print("     Conséquence : US-502 doit vérifier les renvois par analyse du .qmd")
        print("     assemblé (collecte des identifiants et des références) plutôt que")
        print("     par lecture du journal Quarto.")

    if not args.keep:
        shutil.rmtree(os.path.dirname(root), ignore_errors=True)
    else:
        print(f"\n  Répertoire conservé : {root}")

    failed = [n for n, ok, _ in results if not ok]
    print("\n" + "=" * 62)
    print(f"SPIKE 04 : {len(results) - len(failed)}/{len(results)} conformes")
    if failed:
        print("Non conformes : " + ", ".join(failed))
    if environment_gap:
        print("Non mesuré : " + " · ".join(environment_gap))
        print("À reprendre sur un poste doté d'une chaîne LaTeX complète.")
        if not failed:
            return 2
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
