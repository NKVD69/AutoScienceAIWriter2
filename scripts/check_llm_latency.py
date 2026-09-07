#!/usr/bin/env python3
"""Verifie la persistance du modele en VRAM (ADR-003) sur cette machine.

Trois generations courtes consecutives. Le critere porte sur `load_duration`
retourne par Ollama, pas sur le cache KV : l'API ne l'expose pas, et deux
prompts differents ne partagent de toute facon aucun cache. Un
`load_duration` non nul sur la deuxieme requete signifie que les poids ont
ete relus depuis le disque — c'est exactement ce que ADR-003 interdit.

Le temps au premier token est, lui, mesure cote client : c'est une latence
percue, pas une metrique du moteur.

Sortie : 0 seuils tenus - 1 seuils depasses - 2 Ollama injoignable.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

try:
    from app.core.config import get_settings
    from app.core.errors import ModelNotFoundError, OllamaUnavailableError
    from app.llm.backends.ollama import OllamaBackend
    from app.llm.manager import LLMManager
    from app.llm.prompts.registry import AgentName
    from app.llm.vram import read_vram_total_mb, read_vram_used_mb
except ImportError as exc:  # pragma: no cover - diagnostic d'installation
    print(f"Dependances absentes ({exc}) : pip install -e .[dev]")
    sys.exit(2)

N_REQUESTS = 3
PROMPT = "Donne le titre d'un chapitre d'introduction. Une ligne, sans commentaire."


async def run() -> int:
    settings = get_settings()
    backend = OllamaBackend()
    manager = LLMManager(backend)

    print(f"  modele  : {settings.llm_model}")
    print(f"  hote    : {settings.llm_base_url}")
    total = read_vram_total_mb()
    print(f"  VRAM    : {total} Mo totale, {read_vram_used_mb()} Mo utilisee\n")

    try:
        await manager.startup()
    except (OllamaUnavailableError, ModelNotFoundError) as exc:
        print(f"  [NON MESURE] {exc.message}")
        await backend.aclose()
        return 2

    print(f"  {'requete':<9}{'load_ms':>10}{'ttft_ms':>10}{'tokens/s':>10}{'total_ms':>11}")
    print(f"  {'-' * 50}")

    echecs: list[str] = []
    for i in range(1, N_REQUESTS + 1):
        debut = time.perf_counter()
        premier_token_ms = 0.0
        async for _ in manager.stream_for_agent(AgentName.PLAN, PROMPT, max_tokens=24):
            premier_token_ms = (time.perf_counter() - debut) * 1000
            break

        result = await manager.generate_for_agent(AgentName.PLAN, PROMPT, max_tokens=24)
        print(
            f"  {i:<9}{result.load_duration_ms:>10.1f}{premier_token_ms:>10.1f}"
            f"{result.tokens_per_second:>10.1f}{result.total_duration_ms:>11.1f}"
        )

        # La premiere requete a le droit de charger les poids : c'est son role.
        if i > 1:
            if result.load_duration_ms >= settings.llm_max_load_duration_ms:
                echecs.append(
                    f"requete {i} : load_duration {result.load_duration_ms:.1f} ms "
                    f">= {settings.llm_max_load_duration_ms} ms — poids rechargés"
                )
            if premier_token_ms >= settings.llm_max_ttft_ms:
                echecs.append(
                    f"requete {i} : ttft {premier_token_ms:.0f} ms >= {settings.llm_max_ttft_ms} ms"
                )

    await backend.aclose()

    print(f"\n{'=' * 60}")
    if echecs:
        for e in echecs:
            print(f"  [ECHEC] {e}")
        print(f"check_llm_latency : {len(echecs)} seuil(s) depasse(s)")
        return 1
    print("check_llm_latency : seuils tenus sur les requetes posterieures a la premiere")
    return 0


def main() -> int:
    print("check_llm_latency - ADR-003\n")
    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
