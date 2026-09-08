"""US-005 — la vectorisation ne consomme pas de VRAM. ADR-013, D-05.

C'est le test qui porte la décision : si ces vecteurs se calculaient sur GPU,
l'ingestion d'un lot de PDF évincerait le modèle de rédaction et ruinerait
l'objectif d'ADR-003.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

from app.core.config import get_settings
from app.llm.vram import read_vram_used_mb
from app.rag.embeddings import EmbeddingService, model_is_cached

VARIATION_MAX_MB = 200
N_TEXTES = 500


def _modele_disponible() -> bool:
    settings = get_settings()
    return model_is_cached(settings.embedding_model, Path(settings.embedding_cache_dir))


needs_model = pytest.mark.skipif(
    not _modele_disponible(), reason="modèle d'embedding absent du cache"
)
needs_gpu = pytest.mark.skipif(read_vram_used_mb() is None, reason="nvidia-smi absent")


@needs_model
@needs_gpu
async def test_embeddings_do_not_use_vram() -> None:
    """500 textes vectorisés, VRAM inchangée à 200 Mo près."""
    avant = read_vram_used_mb()
    assert avant is not None

    svc = EmbeddingService()
    textes = [f"Fragment de texte scientifique numéro {i}. " * 20 for i in range(N_TEXTES)]
    vecteurs = await svc.embed_documents(textes)

    apres = read_vram_used_mb()
    assert apres is not None
    assert len(vecteurs) == N_TEXTES
    assert all(len(v) == svc.dimension() for v in vecteurs)

    variation = apres - avant
    assert variation < VARIATION_MAX_MB, (
        f"la vectorisation a consommé {variation} Mo de VRAM : le calcul "
        "s'exécute sur GPU, ce qu'ADR-013 interdit"
    )


@needs_model
async def test_real_backend_produces_configured_dimension() -> None:
    """Le vrai moteur, pas un substitut : la dimension configurée doit être
    celle que le modèle produit réellement."""
    svc = EmbeddingService()
    vecteurs = await svc.embed_documents(["Un extrait de source scientifique."])
    assert len(vecteurs) == 1
    assert len(vecteurs[0]) == get_settings().embedding_dim
    assert any(abs(x) > 1e-9 for x in vecteurs[0]), "vecteur nul : le modèle n'a rien calculé"


@needs_model
async def test_real_backend_distinguishes_document_from_query() -> None:
    """Les préfixes changent le vecteur : c'est l'observable qui prouve
    qu'ils sont bien transmis au modèle."""
    svc = EmbeddingService()
    doc = (await svc.embed_documents(["la fonction rénale des mammifères marins"]))[0]
    req = await svc.embed_query("la fonction rénale des mammifères marins")
    assert doc != req, "le préfixe de tâche n'atteint pas le modèle"


@pytest.mark.integration
async def test_ollama_ps_lists_no_embedding_model() -> None:
    """Aucun modèle d'embedding résident dans le moteur d'inférence."""
    if os.environ.get("SAW_SKIP_OLLAMA"):
        pytest.skip("sonde Ollama désactivée")
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            reponse = await client.get(f"{settings.ollama_base_url}/api/ps")
            charges = [m.get("name", "") for m in reponse.json().get("models", [])]
    except httpx.HTTPError:
        pytest.skip("Ollama injoignable")

    fautifs = [n for n in charges if "embed" in n.lower()]
    assert not fautifs, f"modèle(s) d'embedding chargés dans Ollama : {fautifs}"
