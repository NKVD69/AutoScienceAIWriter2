#!/usr/bin/env python3
"""Verifie qu'ADR-013 tient sur cette machine : embeddings sur CPU, hors GPU.

500 textes d'environ 512 tokens sont vectorises. Deux criteres :

- **la VRAM ne bouge pas.** C'est l'objet meme d'ADR-013 : des embeddings
  calcules sur GPU evinceraient le modele de redaction pendant l'ingestion.
- **la duree reste sous le budget de S12.2** (180 s sur 8 coeurs). Le nombre
  de coeurs reellement disponibles est affiche : comparer une mesure a un
  budget etabli pour une autre machine n'aurait pas de sens sans lui.

Le script liste aussi les modeles residents dans Ollama : y voir un modele
d'embedding signifierait que la decision a ete contournee quelque part.

Sortie : 0 conforme - 1 non conforme - 2 mesure impossible (modele absent).
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

try:
    import httpx

    from app.core.config import get_settings
    from app.llm.vram import read_vram_used_mb
    from app.rag.embeddings import EmbeddingService, model_is_cached
except ImportError as exc:  # pragma: no cover - diagnostic d'installation
    print(f"Dependances absentes ({exc}) : pip install -e .[dev]")
    sys.exit(2)

N_TEXTES = 500
BUDGET_S = 180.0
VARIATION_MAX_MB = 200
# ~512 tokens : le motif fait une dizaine de tokens, repete cinquante fois.
MOTIF = "Analyse des mecanismes de transport membranaire en milieu marin. "


results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(f"  [{'OK ' if ok else 'ECHEC'}] {name}" + (f" - {detail}" if detail else ""))
    return ok


def modeles_ollama_residents() -> list[str]:
    """Modeles actuellement charges dans Ollama. Liste vide s'il est absent."""
    try:
        with httpx.Client(timeout=3.0) as client:
            url = f"{get_settings().ollama_base_url}/api/ps"
            return [m.get("name", "") for m in client.get(url).json().get("models", [])]
    except Exception:
        return []


async def run() -> int:
    settings = get_settings()
    cache = Path(settings.embedding_cache_dir)

    print(f"  modele  : {settings.embedding_model}")
    print(f"  cache   : {cache}")
    print(f"  coeurs  : {os.cpu_count()} logiques")

    if not model_is_cached(settings.embedding_model, cache):
        print("\n  [NON MESURE] modele absent du cache.")
        print("               Le telechargement releve du consentement model_download")
        print("               (ADR-010) : SAW_EMBEDDING_DOWNLOAD_CONSENT=true.")
        return 2

    service = EmbeddingService()
    print(f"  lots    : {service.batch_size} textes, {service.workers} threads\n")

    textes = [MOTIF * 50 for _ in range(N_TEXTES)]

    vram_avant = read_vram_used_mb()
    debut = time.perf_counter()
    vecteurs = await service.embed_documents(textes)
    duree = time.perf_counter() - debut
    vram_apres = read_vram_used_mb()

    check(
        f"{N_TEXTES} textes vectorises",
        len(vecteurs) == N_TEXTES,
        f"dimension {len(vecteurs[0]) if vecteurs else 0}",
    )
    check(
        "dimension conforme a la configuration",
        all(len(v) == settings.embedding_dim for v in vecteurs),
        f"attendu {settings.embedding_dim}",
    )

    debit = N_TEXTES / duree if duree else 0.0
    check(
        f"duree sous le budget de {BUDGET_S:.0f} s",
        duree < BUDGET_S,
        f"{duree:.1f} s - {debit:.1f} textes/s",
    )

    if vram_avant is None or vram_apres is None:
        print("  [NON MESURE] nvidia-smi absent : variation de VRAM non observable")
    else:
        variation = vram_apres - vram_avant
        check(
            f"VRAM stable a {VARIATION_MAX_MB} Mo pres",
            variation < VARIATION_MAX_MB,
            f"{vram_avant} -> {vram_apres} Mo, variation {variation:+d} Mo",
        )

    residents = modeles_ollama_residents()
    fautifs = [n for n in residents if "embed" in n.lower()]
    check(
        "aucun modele d'embedding resident dans Ollama",
        not fautifs,
        f"{len(residents)} modele(s) resident(s) : {', '.join(residents) or 'aucun'}",
    )

    ok = sum(1 for _, o, _ in results if o)
    print(f"\n{'=' * 60}\ncheck_embeddings_cpu : {ok}/{len(results)} conformes")
    return 0 if ok == len(results) else 1


def main() -> int:
    print("check_embeddings_cpu - ADR-013\n")
    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
