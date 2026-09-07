#!/usr/bin/env python3
"""Exécute les quatre spikes et produit un rapport de synthèse.

Codes de sortie par spike : 0 conforme · 1 non conforme · 2 environnement
insuffisant. Un code 2 n'est pas un échec du projet : c'est une mesure
impossible sur cette machine, à refaire ailleurs.

Usage : python run_all.py [--only 1,2] [--report rapport.md]
"""
from __future__ import annotations
import argparse, datetime, shutil, subprocess, sys, os

SPIKES = {
    1: ("sqlite-vec : intégrité et performance", [sys.executable, "spike_01_sqlite_vec.py"], "ADR-002"),
    2: ("Sortie structurée d'un 7B local", [sys.executable, "spike_02_structured_output.py"], "ADR-003, ADR-008"),
    3: ("Pyodide : isolation et paquets", ["node", "spike_03_pyodide.mjs"], "ADR-005"),
    4: ("Quarto : document long", [sys.executable, "spike_04_quarto.py"], "ADR-006"),
}
LABELS = {0: "CONFORME", 1: "NON CONFORME", 2: "NON MESURÉ"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="ex. 1,3")
    ap.add_argument("--report", default="rapport-spikes.md")
    args = ap.parse_args()
    wanted = [int(x) for x in args.only.split(",") if x.strip()] or list(SPIKES)

    here = os.path.dirname(os.path.abspath(__file__))
    out, rows = [], []
    for n in wanted:
        title, cmd, adr = SPIKES[n]
        print(f"\n{'#' * 66}\n# SPIKE {n:02d} — {title}\n{'#' * 66}\n")
        if cmd[0] == "node" and not shutil.which("node"):
            code, log = 2, "node absent du PATH"
            print(log)
        else:
            p = subprocess.run(cmd, cwd=here, capture_output=True, text=True)
            code, log = p.returncode, p.stdout + p.stderr
            print(log)
        rows.append((n, title, adr, code))
        out.append(f"## Spike {n:02d} — {title}\n\nDécisions concernées : {adr}\n\n"
                   f"Verdict : **{LABELS.get(code, 'ERREUR')}**\n\n```\n{log.strip()}\n```\n")

    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    head = [f"# Rapport de spikes — {stamp}\n",
            "| Spike | Objet | Décisions | Verdict |", "|---|---|---|---|"]
    head += [f"| {n:02d} | {t} | {a} | {LABELS.get(c, 'ERREUR')} |" for n, t, a, c in rows]
    head.append("\n> Un verdict NON MESURÉ signale une dépendance absente de cette "
                "machine, pas un échec de conception. Il doit être repris sur un "
                "poste représentatif avant d'engager les stories concernées.\n")

    path = os.path.join(here, args.report)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(head) + "\n" + "\n".join(out))
    print(f"\nRapport écrit : {path}")

    blocking = [n for n, _, _, c in rows if c == 1]
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
