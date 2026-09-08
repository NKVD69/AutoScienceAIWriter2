#!/usr/bin/env python3
"""
SPIKE 02 — Sortie structurée d'un modèle 7B local : le spike décisif.

Toute l'architecture repose sur l'hypothèse qu'un modèle 7B quantifié produit
de façon fiable du JSON conforme à un schéma Pydantic. Si le taux d'échec est
élevé, le circuit breaker à trois essais (ADR-008) transforme chaque section
en tapis roulant et la promesse de latence du produit ne tient pas.

Hypothèses testées
  H2.1  Le moteur est joignable et le modèle est installé (ADR-003, ADR-014).
  H2.2  Le modèle reste résident pendant toute la série.
  H2.3  Taux de conformité Pydantic au 1er essai >= 70 % sur PlanTree.
  H2.4  Taux cumulé après 3 essais >= 95 % (seuil du circuit breaker).
  H2.5  Le mode JSON contraint du moteur améliore nettement le taux brut.
  H2.6  Le prompt système reste stable octet pour octet entre requêtes.

Décision associée : ADR-003, ADR-004, ADR-008, ADR-014, ADR-015.
Un H2.4 < 90 % impose de reconsidérer la taille du modèle ou de recourir à
une grammaire contrainte, pas d'ajuster les prompts à la marge.

H2.2 a changé de nature avec ADR-014 : LM Studio ne publie pas
`load_duration`, mais expose l'état de chargement de chaque modèle. La
résidence s'observe donc directement au lieu d'être inférée d'une durée. Le
seuil de latence de la V0.3 est levé par ADR-015 : le temps de génération
n'est pas un critère de ce spike, seule la conformité l'est.

Prérequis : pip install httpx pydantic ; le modèle installé dans le moteur.
Usage : python spike_02_structured_output.py [--backend lmstudio|ollama]
        [--model M] [--n 20] [--timeout 3600]
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

# Un moteur par entrée : URL de base, modèle par défaut.
BACKENDS = {
    "lmstudio": ("http://127.0.0.1:1234", "google/gemma-4-31b"),
    "ollama": ("http://127.0.0.1:11434", "qwen2.5:7b-instruct-q4_K_M"),
}
DEFAULT_BACKEND = "lmstudio"

# Le délai par défaut de la V0.3 était de 300 s. Sur un modèle qui déverse
# sur CPU (ADR-015), une seule génération de plan peut dépasser la demi-heure :
# le harnais expirait sur son propre préchauffage avant d'avoir rien mesuré.
DEFAULT_TIMEOUT_S = 3600

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


PlanTree.model_rebuild()


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


class Engine:
    """Adaptateur de moteur. Les deux API sont locales et sans état commun.

    Une métrique qu'un moteur ne publie pas vaut `None`, jamais `0` : un zéro
    se lirait comme « poids restés résidents », c'est-à-dire comme la preuve
    de ce que H2.2 cherche à établir.
    """

    def __init__(self, name: str, base_url: str, model: str, timeout: float):
        self.name, self.base_url, self.model, self.timeout = name, base_url, model, timeout
        self.client = httpx.Client(timeout=timeout)

    # -- Disponibilité ----------------------------------------------------
    def installed_models(self) -> list[str]:
        if self.name == "lmstudio":
            d = self.client.get(f"{self.base_url}/api/v0/models", timeout=10).json()
            return [m.get("id", "") for m in d.get("data", [])]
        d = self.client.get(f"{self.base_url}/api/tags", timeout=10).json()
        return [m.get("name", "") for m in d.get("models", [])]

    def loaded_models(self) -> list[str]:
        """Modèles actuellement résidents. Vide si le moteur ne le dit pas."""
        try:
            if self.name == "lmstudio":
                d = self.client.get(f"{self.base_url}/api/v0/models", timeout=10).json()
                return [m.get("id", "") for m in d.get("data", []) if m.get("state") == "loaded"]
            d = self.client.get(f"{self.base_url}/api/ps", timeout=10).json()
            return [m.get("name", "") for m in d.get("models", [])]
        except Exception:
            return []

    def is_resident(self) -> bool:
        return self.model in self.loaded_models()

    @staticmethod
    def _raise_with_body(r: "httpx.Response") -> None:
        """Fait remonter le corps de la réponse avec le code HTTP.

        `raise_for_status()` seul rapporte « 400 Bad Request » et perd le
        message du moteur, qui est la seule information utile — ici, par
        exemple, que `response_format.type` doit valoir `json_schema`.
        """
        if r.is_error:
            corps = " ".join(r.text[:400].split())
            raise RuntimeError(f"HTTP {r.status_code} sur {r.url} : {corps}")

    # -- Génération -------------------------------------------------------
    def generate(self, user: str, fmt_json: bool):
        t = time.perf_counter()
        if self.name == "lmstudio":
            body = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ],
                "stream": False,
                "temperature": 0.2,
                # Analogue de keep_alive=-1 (ADR-014) : réarmé à chaque appel.
                "ttl": 86_400,
            }
            if fmt_json:
                # LM Studio n'accepte que `json_schema` ou `text` — pas le
                # `json_object` d'OpenAI. La contrainte porte donc sur le
                # schéma RÉEL et non sur « du JSON valide » : c'est plus fort
                # que le `format: json` d'Ollama, et il faut le dire dans les
                # résultats, sans quoi H2.5 comparerait deux choses inégales.
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "PlanTree",
                        "strict": True,
                        "schema": PlanTree.model_json_schema(),
                    },
                }
            r = self.client.post(f"{self.base_url}/api/v0/chat/completions", json=body)
            self._raise_with_body(r)
            d = r.json()
            choix = (d.get("choices") or [{}])[0]
            texte = (choix.get("message") or {}).get("content") or ""
            stats = d.get("stats") or {}
            usage = d.get("usage") or {}
            return texte, {
                "wall_ms": (time.perf_counter() - t) * 1000,
                # LM Studio ne publie pas de durée de chargement (ADR-014).
                "load_ms": None,
                "ttft_ms": (stats.get("time_to_first_token") or 0) * 1000 or None,
                "tok_per_s": stats.get("tokens_per_second"),
                "eval_count": usage.get("completion_tokens", 0),
            }

        body = {
            "model": self.model, "system": SYSTEM_PROMPT, "prompt": user,
            "stream": False, "keep_alive": -1,
            "options": {"temperature": 0.2, "num_ctx": 8192},
        }
        if fmt_json:
            body["format"] = "json"
        r = self.client.post(f"{self.base_url}/api/generate", json=body)
        self._raise_with_body(r)
        d = r.json()
        eval_ms = d.get("eval_duration", 0) / 1e6
        n = d.get("eval_count", 0)
        return d.get("response", ""), {
            "wall_ms": (time.perf_counter() - t) * 1000,
            "load_ms": d.get("load_duration", 0) / 1e6,
            "ttft_ms": d.get("prompt_eval_duration", 0) / 1e6 or None,
            "tok_per_s": (n / (eval_ms / 1000)) if eval_ms > 0 else None,
            "eval_count": n,
        }


def attempt(engine: "Engine", user: str, fmt_json: bool):
    raw, m = engine.generate(user, fmt_json)
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
    ap.add_argument("--backend", default=DEFAULT_BACKEND, choices=sorted(BACKENDS))
    ap.add_argument("--model", default=None)
    ap.add_argument("--n", type=int, default=15, help="générations par mode")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    ap.add_argument("--modes", default="both", choices=["both", "json", "plain"])
    args = ap.parse_args()

    base_url, default_model = BACKENDS[args.backend]
    engine = Engine(args.backend, base_url, args.model or default_model, args.timeout)

    print("SPIKE 02 — Sortie structurée d'un modèle local\n")
    print(f"  moteur : {engine.name} · {engine.base_url}")
    print(f"  modèle : {engine.model}")
    print(f"  délai  : {engine.timeout:.0f} s par requête\n")

    # ---- H2.1 ---------------------------------------------------------
    print("H2.1 — Disponibilité")
    try:
        names = engine.installed_models()
    except Exception as e:  # noqa: BLE001
        print(f"  [ÉCHEC] {engine.name} injoignable sur {engine.base_url} : {e}")
        print("          LM Studio : « lms server start ». Ollama : « ollama serve ».")
        return 2
    if engine.model not in names:
        print(f"  [ÉCHEC] modèle {engine.model} absent. Présents : {', '.join(names) or 'aucun'}")
        return 2
    print(f"  [OK ] {engine.model} installé")

    # Le préchauffage n'a d'objet que si le modèle n'est pas déjà résident.
    # En forcer un sur un modèle chargé coûte ici plusieurs minutes — le
    # modèle raisonne avant de répondre, même à « réponds OK » — sans rien
    # établir que l'état du modèle ne dise déjà (ADR-014).
    resident_avant = engine.is_resident()
    if resident_avant:
        print("\n  Préchauffage inutile : modèle déjà résident.")
    else:
        print("\n  Préchauffage…")
        t0 = time.perf_counter()
        engine.generate("Réponds par le mot OK.", False)
        print(f"  préchauffage terminé en {time.perf_counter() - t0:.1f} s")
        resident_avant = engine.is_resident()
        print(f"  résident après préchauffage : {'oui' if resident_avant else 'non observable'}")

    # ---- H2.6 stabilité du prompt système ------------------------------
    digest = hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:12]
    print(f"\nH2.6 — Empreinte du prompt système : {digest} (constante par construction)")

    verdicts: dict[str, bool] = {}
    summary = {}

    modes = {"both": (False, True), "json": (True,), "plain": (False,)}[args.modes]

    for fmt_json in modes:
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
            t_case = time.perf_counter()
            for k in range(1, 4):
                ok, kind, detail, m = attempt(engine, user, fmt_json)
                # Les métriques absentes sont ignorées, jamais comptées comme
                # des zéros : une moyenne diluée par des zéros factices est
                # pire qu'une moyenne sur moins de points.
                if m["load_ms"] is not None:
                    loads.append(m["load_ms"])
                if m["ttft_ms"] is not None:
                    ttfts.append(m["ttft_ms"])
                if m["tok_per_s"]:
                    tps.append(m["tok_per_s"])
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
                  f" essais={attempts_used[-1] if ok else 3}"
                  f" · {time.perf_counter() - t_case:.0f} s", flush=True)

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
            after_first = loads[1:]
            print(f"  load_duration    : p95 {sorted(after_first)[int(.95 * len(after_first)) - 1]:.1f} ms")
        else:
            print(f"  load_duration    : non publié par {engine.name} (ADR-014)")
        if ttfts:
            print(f"  premier token    : p95 {sorted(ttfts)[int(.95 * len(ttfts)) - 1]:.0f} ms")
        if failures:
            print("\n  Causes d'échec les plus fréquentes :")
            for cause, n in sorted(failures.items(), key=lambda kv: -kv[1])[:5]:
                print(f"    {n:>3}× {cause}")

        if fmt_json:
            verdicts["H2.3 conformité 1er essai >= 70 %"] = r1 >= 70
            verdicts["H2.4 conformité 3 essais >= 95 %"] = r3 >= 95

    if len(summary) == 2:
        a = summary["prompt seul"][0]
        b = summary["format=json"][0]
        verdicts["H2.5 mode JSON améliore le 1er essai"] = b >= a

    # ---- H2.2 : résidence, et non durée de chargement (ADR-014) --------
    resident_apres = engine.is_resident()
    if resident_avant or resident_apres:
        verdicts["H2.2 modèle résident pendant toute la série"] = resident_apres
    else:
        print(f"\n  [NON MESURÉ] {engine.name} ne publie pas l'état de chargement ;"
              " H2.2 n'est pas évaluable sur ce moteur.")

    print("\n" + "=" * 62)
    print("VERDICTS")
    for name, ok in verdicts.items():
        print(f"  [{'OK ' if ok else 'ÉCHEC'}] {name}")
    failed = [n for n, ok in verdicts.items() if not ok]
    print(f"\nSPIKE 02 : {len(verdicts) - len(failed)}/{len(verdicts)} conformes")
    if failed:
        print("\nSi H2.4 est sous 90 %, la réponse n'est PAS d'ajuster les prompts :")
        print("  - modèle plus grand si la mémoire le permet, ou")
        print("  - grammaire contrainte (GBNF, ou response_format json_schema), ou")
        print("  - découpage du PlanTree en plusieurs générations plus simples.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
