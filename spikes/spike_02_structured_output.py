#!/usr/bin/env python3
"""
SPIKE 02 — Sortie structurée d'un modèle 7B local : le spike décisif.

Toute l'architecture repose sur l'hypothèse qu'un modèle 7B quantifié produit
de façon fiable du JSON conforme à un schéma Pydantic. Si le taux d'échec est
élevé, le circuit breaker à trois essais (ADR-008) transforme chaque section
en tapis roulant et la promesse de latence du produit ne tient pas.

Hypothèses testées
  H2.1  Ollama est joignable et le modèle est résident (ADR-003).
  H2.2  load_duration < 50 ms après la première requête ; TTFT < 2 s.
  H2.3  Taux de conformité Pydantic au 1er essai >= 70 % sur PlanTree.
  H2.4  Taux cumulé après 3 essais >= 95 % (seuil du circuit breaker).
  H2.5  Le format JSON natif d'Ollama améliore nettement le taux brut.
  H2.6  Le prompt système reste stable octet pour octet entre requêtes.

Décision associée : ADR-003, ADR-004, ADR-008.
Un H2.4 < 90 % impose de reconsidérer la taille du modèle ou de recourir à
une grammaire contrainte, pas d'ajuster les prompts à la marge.

Prérequis : pip install httpx pydantic ; ollama pull qwen2.5:7b-instruct-q4_K_M
Usage : python spike_02_structured_output.py [--model M] [--n 20]
Sortie : 0 conforme · 1 non conforme · 2 environnement insuffisant
"""
from __future__ import annotations
import argparse, hashlib, json, statistics, sys, time

try:
    import httpx
    from pydantic import BaseModel, Field, ValidationError, model_validator
except ImportError:
    print("Dépendances manquantes : pip install httpx pydantic")
    sys.exit(2)

BASE_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen2.5:7b-instruct-q4_K_M"

SYSTEM_PROMPT = (
    "Tu es un architecte de plans de recherche de niveau doctoral.\n"
    "Tu réponds UNIQUEMENT par un objet JSON valide, sans préambule, sans "
    "commentaire, sans délimiteur Markdown.\n"
    "Tu n'inventes aucune référence bibliographique.\n"
    "Chaque section porte un objectif vérifiable et une longueur cible.\n"
)

USER_TEMPLATE = """Sujet : {subject}
Discipline : {discipline}
Longueur cible totale : {target} mots

Produis un plan de recherche au format JSON strict suivant :

{{
  "problematique": "chaîne de 40 à 1500 caractères",
  "research_questions": ["1 à 6 questions"],
  "methodology_note": "chaîne",
  "nodes": [
    {{
      "title": "titre du chapitre",
      "objective": "ce que le chapitre établit",
      "target_words": 5000,
      "children": [
        {{"title": "...", "objective": "...", "target_words": 1500, "children": []}}
      ]
    }}
  ]
}}

Contraintes : entre 4 et 8 chapitres racine ; chaque chapitre a au moins
2 sous-sections ; titres uniques entre frères ; la somme des target_words des
feuilles est comprise entre {lo} et {hi}."""

CASES = [
    ("Impact des microplastiques sur la fonction rénale des mammifères marins", "Écotoxicologie", 60000),
    ("Optimisation topologique d'échangeurs thermiques pour la fabrication additive", "Génie mécanique", 80000),
    ("Représentations du travail domestique dans la presse féminine 1950-1975", "Histoire sociale", 70000),
]


class PlanNode(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    objective: str = Field(min_length=10)
    target_words: int = Field(ge=150, le=20000)
    children: list["PlanNode"] = []

    @model_validator(mode="after")
    def _children_rule(self):
        if self.children and len(self.children) < 2:
            raise ValueError("un nœud non terminal doit avoir au moins 2 enfants")
        titles = [c.title.strip().lower() for c in self.children]
        if len(titles) != len(set(titles)):
            raise ValueError("titres frères dupliqués")
        return self


PlanNode.model_rebuild()


class PlanTree(BaseModel):
    problematique: str = Field(min_length=40, max_length=1500)
    research_questions: list[str] = Field(min_length=1, max_length=6)
    methodology_note: str
    nodes: list[PlanNode] = Field(min_length=4, max_length=12)

    @model_validator(mode="after")
    def _depth_and_budget(self):
        def depth(n: PlanNode, d: int = 1) -> int:
            return d if not n.children else max(depth(c, d + 1) for c in n.children)
        if max(depth(n) for n in self.nodes) < 2:
            raise ValueError("profondeur insuffisante")
        titles = [n.title.strip().lower() for n in self.nodes]
        if len(titles) != len(set(titles)):
            raise ValueError("titres de chapitres dupliqués")
        return self


def extract_json(raw: str) -> str:
    """Extrait le premier objet équilibré. Tolérance de FORMAT, pas de CONTENU."""
    start = raw.find("{")
    if start < 0:
        raise ValueError("aucun objet JSON")
    depth, in_str, esc = 0, False, False
    for i, ch in enumerate(raw[start:], start):
        if in_str:
            if esc: esc = False
            elif ch == "\\": esc = True
            elif ch == '"': in_str = False
            continue
        if ch == '"': in_str = True
        elif ch == "{": depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return raw[start:i + 1]
    raise ValueError("objet JSON non refermé")


def generate(client, model, user, fmt_json: bool):
    body = {
        "model": model, "system": SYSTEM_PROMPT, "prompt": user,
        "stream": False, "keep_alive": -1,
        "options": {"temperature": 0.2, "num_ctx": 8192},
    }
    if fmt_json:
        body["format"] = "json"
    t = time.perf_counter()
    r = client.post(f"{BASE_URL}/api/generate", json=body, timeout=300)
    r.raise_for_status()
    d = r.json()
    return d.get("response", ""), {
        "wall_ms": (time.perf_counter() - t) * 1000,
        "load_ms": d.get("load_duration", 0) / 1e6,
        "prompt_eval_ms": d.get("prompt_eval_duration", 0) / 1e6,
        "eval_ms": d.get("eval_duration", 0) / 1e6,
        "eval_count": d.get("eval_count", 0),
    }


def attempt(client, model, user, fmt_json: bool):
    raw, m = generate(client, model, user, fmt_json)
    try:
        obj = json.loads(extract_json(raw))
    except Exception as e:  # noqa: BLE001
        return False, "json", str(e)[:120], m
    try:
        PlanTree.model_validate(obj)
    except ValidationError as e:
        err = e.errors()[0]
        return False, "schema", f"{'.'.join(str(x) for x in err['loc'])} : {err['msg']}"[:120], m
    return True, None, None, m


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--n", type=int, default=15, help="générations par mode")
    args = ap.parse_args()

    print("SPIKE 02 — Sortie structurée d'un modèle 7B local\n")
    client = httpx.Client()

    # ---- H2.1 ---------------------------------------------------------
    print("H2.1 — Disponibilité")
    try:
        tags = client.get(f"{BASE_URL}/api/tags", timeout=5).json()
    except Exception as e:  # noqa: BLE001
        print(f"  [ÉCHEC] Ollama injoignable sur {BASE_URL} : {e}")
        print("          Démarrer Ollama puis : ollama pull " + args.model)
        return 2
    names = [m["name"] for m in tags.get("models", [])]
    if not any(args.model.split(":")[0] in n for n in names):
        print(f"  [ÉCHEC] modèle {args.model} absent. Modèles présents : {', '.join(names) or 'aucun'}")
        return 2
    print(f"  [OK ] {args.model} disponible")

    print("\n  Préchauffage…")
    generate(client, args.model, "Réponds par le mot OK.", False)

    # ---- H2.6 stabilité du prompt système ------------------------------
    digest = hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:12]
    print(f"\nH2.6 — Empreinte du prompt système : {digest} (constante par construction)")

    verdicts: dict[str, bool] = {}
    summary = {}

    for fmt_json in (False, True):
        label = "format=json" if fmt_json else "prompt seul"
        print(f"\n{'=' * 62}\nMode : {label} · {args.n} générations\n")
        first_ok = 0
        cumulative_ok = 0
        attempts_used: list[int] = []
        failures: dict[str, int] = {}
        loads, ttfts, tps = [], [], []

        for i in range(args.n):
            subject, disc, target = CASES[i % len(CASES)]
            user = USER_TEMPLATE.format(subject=subject, discipline=disc, target=target,
                                        lo=int(target * 0.7), hi=int(target * 1.3))
            ok = False
            for k in range(1, 4):
                ok, kind, detail, m = attempt(client, args.model, user, fmt_json)
                loads.append(m["load_ms"])
                ttfts.append(m["prompt_eval_ms"])
                if m["eval_ms"] > 0:
                    tps.append(m["eval_count"] / (m["eval_ms"] / 1000))
                if k == 1 and ok:
                    first_ok += 1
                if ok:
                    attempts_used.append(k)
                    cumulative_ok += 1
                    break
                failures[f"{kind}: {detail}"] = failures.get(f"{kind}: {detail}", 0) + 1
                user += (f"\n\nTa réponse précédente était invalide ({kind}) : {detail}\n"
                         "Corrige et renvoie UNIQUEMENT le JSON.")
            print(f"  {i + 1:>3}/{args.n}  {'conforme' if ok else 'ÉCHEC après 3 essais':<22}"
                  f" essais={attempts_used[-1] if ok else 3}")

        r1 = 100 * first_ok / args.n
        r3 = 100 * cumulative_ok / args.n
        summary[label] = (r1, r3, statistics.mean(attempts_used) if attempts_used else 0)
        print(f"\n  1er essai        : {r1:5.1f} %")
        print(f"  après 3 essais   : {r3:5.1f} %")
        if attempts_used:
            print(f"  essais moyens    : {statistics.mean(attempts_used):.2f}")
        if tps:
            print(f"  débit            : {statistics.median(tps):.1f} tok/s")
        if len(loads) > 1:
            after_first = [x for x in loads[1:]]
            print(f"  load_duration    : p95 {sorted(after_first)[int(.95 * len(after_first)) - 1]:.1f} ms")
        if ttfts:
            print(f"  prompt_eval      : p95 {sorted(ttfts)[int(.95 * len(ttfts)) - 1]:.0f} ms")
        if failures:
            print("\n  Causes d'échec les plus fréquentes :")
            for cause, n in sorted(failures.items(), key=lambda kv: -kv[1])[:5]:
                print(f"    {n:>3}× {cause}")

        if fmt_json:
            verdicts["H2.3 conformité 1er essai >= 70 %"] = r1 >= 70
            verdicts["H2.4 conformité 3 essais >= 95 %"] = r3 >= 95
            after_first = loads[1:] or [0]
            verdicts["H2.2 load_duration p95 < 50 ms"] = sorted(after_first)[int(.95 * len(after_first)) - 1] < 50

    if len(summary) == 2:
        a = summary["prompt seul"][0]
        b = summary["format=json"][0]
        verdicts["H2.5 format=json améliore le 1er essai"] = b >= a

    print("\n" + "=" * 62)
    print("VERDICTS")
    for name, ok in verdicts.items():
        print(f"  [{'OK ' if ok else 'ÉCHEC'}] {name}")
    failed = [n for n, ok in verdicts.items() if not ok]
    print(f"\nSPIKE 02 : {len(verdicts) - len(failed)}/{len(verdicts)} conformes")
    if failed:
        print("\nSi H2.4 est sous 90 %, la réponse n'est PAS d'ajuster les prompts :")
        print("  - modèle plus grand si la VRAM le permet, ou")
        print("  - grammaire contrainte (GBNF via llama.cpp) plutôt qu'Ollama, ou")
        print("  - découpage du PlanTree en plusieurs générations plus simples.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
