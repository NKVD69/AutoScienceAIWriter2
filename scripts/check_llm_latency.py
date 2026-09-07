#!/usr/bin/env python3
"""Verifie la persistance du modele en memoire (ADR-003) sur cette machine.

Trois generations courtes consecutives, sur le moteur configure.

**Le critere de persistance depend du moteur, parce que les moteurs ne
publient pas la meme chose.**

- Ollama publie `load_duration`. Une valeur non nulle sur la deuxieme requete
  signifie que les poids ont ete relus depuis le disque : c'est ce qu'ADR-003
  interdit. Seuil : `llm_max_load_duration_ms`.
- LM Studio ne publie pas cette duree, mais expose l'etat de chargement de
  chaque modele. La residence s'observe alors *directement*, avant et apres
  la serie — ce qui vaut mieux que de l'inferer d'une duree.

Ne jamais traiter une metrique absente comme une metrique a zero : cela
transformerait « le moteur ne mesure pas » en « rien n'a ete recharge ».

Une metrique qu'un moteur ne publie pas n'est pas une mesure impossible : si
un autre observable etablit le meme fait, le critere est tenu. L'absence de
`load_duration` sous LM Studio est donc informative, pas disqualifiante.

Sortie : 0 criteres tenus - 1 critere non tenu - 2 mesure impossible sur cette
machine (moteur injoignable, modele absent, ou aucun token emis).
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
    from app.core.errors import BackendUnavailableError, ModelNotFoundError
    from app.llm.manager import LLMManager, resolve_backend_factory
    from app.llm.prompts.registry import AgentName
    from app.llm.vram import read_vram_total_mb, read_vram_used_mb
except ImportError as exc:  # pragma: no cover - diagnostic d'installation
    print(f"Dependances absentes ({exc}) : pip install -e .[dev]")
    sys.exit(2)

N_REQUESTS = 3
# Budget volontairement court : on mesure une latence d'amorce, pas un texte.
N_STREAM_TOKENS = 24
PROMPT = "Donne le titre d'un chapitre d'introduction. Une ligne, sans commentaire."


def _fmt(value: float | None) -> str:
    return "aucun" if value is None else f"{value:.1f}"


async def _close(backend: object) -> None:
    aclose = getattr(backend, "aclose", None)
    if aclose is not None:
        await aclose()


async def run() -> int:
    settings = get_settings()
    backend = resolve_backend_factory(settings.llm_backend)()  # type: ignore[call-arg]
    manager = LLMManager(backend)

    print(f"  moteur  : {settings.llm_backend}")
    print(f"  modele  : {settings.llm_model}")
    print(f"  hote    : {settings.llm_base_url}")
    print(f"  VRAM    : {read_vram_total_mb()} Mo totale, {read_vram_used_mb()} Mo utilisee\n")

    try:
        await manager.startup()
    except (BackendUnavailableError, ModelNotFoundError) as exc:
        print(f"  [NON MESURE] {exc.message}")
        await _close(backend)
        return 2

    resident_avant = (await manager.health()).is_resident(settings.llm_model)

    print(f"  {'requete':<9}{'load_ms':>10}{'ttft_ms':>10}{'tokens/s':>10}{'total_ms':>11}")
    print(f"  {'-' * 50}")

    echecs: list[str] = []
    non_mesurables: list[str] = []
    informations: list[str] = []

    for i in range(1, N_REQUESTS + 1):
        debut = time.perf_counter()
        # `None` distingue « aucun token recu » de « token recu
        # instantanement ». Un modele a raisonnement peut depenser tout son
        # budget en reflexion : lire cette absence comme une latence nulle
        # transformerait un echec en succes.
        ttft_client_ms: float | None = None
        async for _ in manager.stream_for_agent(AgentName.PLAN, PROMPT, max_tokens=N_STREAM_TOKENS):
            ttft_client_ms = (time.perf_counter() - debut) * 1000
            break

        result = await manager.generate_for_agent(
            AgentName.PLAN, PROMPT, max_tokens=N_STREAM_TOKENS
        )
        # Le moteur prime sur le chronometre client quand il publie la mesure.
        ttft_ms = result.time_to_first_token_ms or ttft_client_ms

        print(
            f"  {i:<9}{_fmt(result.load_duration_ms):>10}{_fmt(ttft_ms):>10}"
            f"{result.tokens_per_second:>10.1f}{_fmt(result.total_duration_ms):>11}"
        )

        # La premiere requete a le droit de charger les poids : c'est son role.
        if i == 1:
            continue

        if result.load_duration_ms is None:
            # Metrique REMPLACEE, non pas manquante. ADR-014 point 4 designe
            # l'etat du modele comme critere de persistance sous LM Studio :
            # ce n'est pas une mesure impossible sur cette machine, c'est un
            # autre observable, verifie plus bas. Le degrader en code 2
            # rendrait le controle definitivement non concluant sur le moteur
            # par defaut, donc inutile.
            informations.append(
                f"le moteur « {settings.llm_backend} » ne publie pas load_duration ; "
                "la persistance est etablie par l'etat du modele (ADR-014)"
            )
        elif result.load_duration_ms >= settings.llm_max_load_duration_ms:
            echecs.append(
                f"requete {i} : load_duration {result.load_duration_ms:.1f} ms "
                f">= {settings.llm_max_load_duration_ms} ms — poids recharges"
            )

        if ttft_ms is None:
            non_mesurables.append(
                f"aucun token emis en {N_STREAM_TOKENS} tokens de budget : "
                "temps au premier token non mesurable"
            )
        elif ttft_ms >= settings.llm_max_ttft_ms:
            echecs.append(f"requete {i} : ttft {ttft_ms:.0f} ms >= {settings.llm_max_ttft_ms} ms")

    resident_apres = (await manager.health()).is_resident(settings.llm_model)
    await _close(backend)

    print(f"\n{'=' * 60}")

    # Observation directe de la residence : seul critere commun aux deux
    # moteurs, et seul critere disponible sous LM Studio.
    if resident_avant and resident_apres:
        print(f"  [OK ] modele resident avant et apres la serie ({settings.llm_model})")
    elif resident_apres:
        non_mesurables.append("residence non observable avant la serie")
    else:
        echecs.append(
            f"le modele {settings.llm_model} n'est plus resident apres {N_REQUESTS} requetes"
        )

    for info in dict.fromkeys(informations):
        print(f"  [INFO] {info}")

    if echecs:
        for e in echecs:
            print(f"  [ECHEC] {e}")
        print(f"check_llm_latency : {len(echecs)} critere(s) non tenu(s)")
        return 1
    if non_mesurables:
        for n in dict.fromkeys(non_mesurables):
            print(f"  [NON MESURE] {n}")
        print("check_llm_latency : residence verifiee, certaines metriques non mesurables")
        return 2
    print("check_llm_latency : seuils tenus sur les requetes posterieures a la premiere")
    return 0


def main() -> int:
    print("check_llm_latency - ADR-003\n")
    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
