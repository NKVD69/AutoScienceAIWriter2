"""Service d'embeddings — CPU, hors du serveur d'inférence. US-005, ADR-013.

**Pourquoi hors du serveur d'inférence.** Deux exigences des spécifications
antérieures se contredisaient : « embeddings sur CPU pour préserver la VRAM »
et « modèle de rédaction résident ». Un serveur d'inférence sollicité pour des
embeddings charge le modèle correspondant sur GPU : sur une carte déjà occupée
par le modèle principal, l'ingestion d'un lot de PDF évince le modèle de
rédaction — ruinant l'objectif d'ADR-003 — ou dépasse la VRAM en plein travail.
Les embeddings sortent donc entièrement d'Ollama et de LM Studio.

**Pourquoi les préfixes sont appliqués ici.** `nomic-embed-text` est entraîné
avec des préfixes de tâche. Les omettre dégrade nettement le rappel, sans rien
casser d'observable : les vecteurs restent de bonne dimension et la recherche
retourne des résultats, simplement moins pertinents. C'est un défaut bloquant
qui ne se voit pas — raison pour laquelle l'appelant n'a pas le droit de s'en
charger.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from app.core.config import get_settings
from app.core.errors import ModelDownloadConsentRequiredError
from app.core.logging import get_logger

logger = get_logger(__name__)

DOCUMENT_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "

# Au-delà, le gain sur une charge d'embedding est nul et la machine n'a plus
# de marge pour l'interface et le serveur. La borne est basse à dessein :
# l'ingestion est une tâche de fond, pas la priorité de l'utilisateur.
MAX_WORKERS_CAP = 8


class EmbeddingBackend(Protocol):
    """Moteur de calcul substituable."""

    def encode(self, texts: Sequence[str]) -> list[list[float]]: ...

    @property
    def identifier(self) -> str: ...


def default_workers(max_workers: int | None = None) -> int:
    """Threads de calcul, bornés pour laisser une marge au reste du service.

    La bibliothèque standard n'expose que les cœurs *logiques* : `os.cpu_count`
    compte le SMT. La spécification vise les cœurs physiques moins un ; à
    défaut de pouvoir les connaître sans dépendance supplémentaire, on borne
    par `MAX_WORKERS_CAP`, ce qui donne le même effet pratique — ne pas
    saturer la machine — sans prétendre à une précision qu'on n'a pas.
    """
    if max_workers is not None:
        return max(1, max_workers)
    logiques = os.cpu_count() or 2
    return max(1, min(logiques - 1, MAX_WORKERS_CAP))


class FastEmbedBackend:
    """`fastembed` sur ONNX Runtime, fournisseur CPU exclusivement."""

    def __init__(self, model_name: str, cache_dir: Path, threads: int) -> None:
        from fastembed import TextEmbedding

        self._model_name = model_name
        self._model = TextEmbedding(
            model_name=model_name,
            cache_dir=str(cache_dir),
            threads=threads,
            # Jamais de fournisseur GPU : c'est l'objet même d'ADR-013.
            providers=["CPUExecutionProvider"],
        )

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        return [list(map(float, v)) for v in self._model.embed(list(texts))]

    @property
    def identifier(self) -> str:
        return f"fastembed:{self._model_name}"


class SentenceTransformersBackend:
    """Repli toléré par ADR-013. `device="cpu"` sans condition."""

    def __init__(self, model_name: str, cache_dir: Path) -> None:
        from sentence_transformers import SentenceTransformer

        self._model_name = model_name
        self._model = SentenceTransformer(
            model_name, cache_folder=str(cache_dir), device="cpu", trust_remote_code=True
        )

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = self._model.encode(list(texts), convert_to_numpy=True)
        return [list(map(float, v)) for v in vectors]

    @property
    def identifier(self) -> str:
        return f"sentence-transformers:{self._model_name}"


def model_is_cached(model_name: str, cache_dir: Path) -> bool:
    """Le modèle est-il déjà sur le disque ?

    Le format de cache appartient à la bibliothèque ; on se contente de
    chercher un répertoire portant le nom du modèle, ce qui suffit à
    distinguer « présent » de « à télécharger ».
    """
    if not cache_dir.exists():
        return False
    empreinte = model_name.replace("/", "--").lower()
    return any(empreinte in p.name.lower() for p in cache_dir.iterdir() if p.is_dir())


def _load_backend(model_name: str, cache_dir: Path, threads: int) -> EmbeddingBackend:
    """Choisit le moteur. Isolé pour rester substituable (US-005, point 1)."""
    try:
        return FastEmbedBackend(model_name, cache_dir, threads)
    except ImportError:
        logger.warning("fastembed indisponible, repli sur sentence-transformers (CPU)")
        return SentenceTransformersBackend(model_name, cache_dir)


class EmbeddingService:
    """Vectorisation de documents et de requêtes, sur CPU."""

    def __init__(
        self,
        model_name: str | None = None,
        dim: int | None = None,
        batch_size: int | None = None,
        max_workers: int | None = None,
        cache_dir: Path | None = None,
        backend: EmbeddingBackend | None = None,
    ) -> None:
        settings = get_settings()
        self._model_name = model_name or settings.embedding_model
        self._dim = dim if dim is not None else settings.embedding_dim
        self._batch_size = batch_size or settings.embedding_batch_size
        self._workers = default_workers(
            max_workers if max_workers is not None else settings.embedding_max_workers
        )
        self._cache_dir = Path(cache_dir or settings.embedding_cache_dir)
        self._backend = backend
        self._load_lock = asyncio.Lock()

    # --- Métadonnées -----------------------------------------------------

    def dimension(self) -> int:
        return self._dim

    def model_id(self) -> str:
        """Identifiant à consigner dans `model_config`."""
        if self._backend is not None:
            return self._backend.identifier
        return self._model_name

    @property
    def batch_size(self) -> int:
        return self._batch_size

    @property
    def workers(self) -> int:
        return self._workers

    # --- Préfixes --------------------------------------------------------

    @staticmethod
    def _apply_prefix(text: str, task: str) -> str:
        """Préfixe de tâche nomic. Exigence bloquante d'ADR-013."""
        if task == "document":
            return f"{DOCUMENT_PREFIX}{text}"
        if task == "query":
            return f"{QUERY_PREFIX}{text}"
        raise ValueError(f"Tâche d'embedding inconnue : {task!r}")

    # --- Chargement ------------------------------------------------------

    async def _ensure_backend(self) -> EmbeddingBackend:
        if self._backend is not None:
            return self._backend
        async with self._load_lock:
            if self._backend is not None:
                return self._backend
            settings = get_settings()
            if not model_is_cached(self._model_name, self._cache_dir):
                # ADR-010 : les poids viennent du réseau. L'application ne
                # décide pas de les télécharger, elle le propose.
                if not settings.embedding_download_consent:
                    raise ModelDownloadConsentRequiredError.for_embedding(
                        self._model_name, self._cache_dir
                    )
                logger.info("Téléchargement de %s (consentement accordé)", self._model_name)
            # Le chargement lit des centaines de Mo : hors de l'event loop.
            self._backend = await asyncio.to_thread(
                _load_backend, self._model_name, self._cache_dir, self._workers
            )
            return self._backend

    # --- Vectorisation ---------------------------------------------------

    async def embed_documents(
        self,
        texts: Sequence[str],
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[list[float]]:
        """Vectorise des documents à indexer, préfixe `search_document:`.

        L'annulation de la tâche appelante interrompt le traitement **entre
        deux lots** : chaque lot est attendu, et une annulation se manifeste
        à ce point d'attente plutôt qu'à la fin des mille chunks.
        """
        backend = await self._ensure_backend()
        prefixes = [self._apply_prefix(t, "document") for t in texts]

        vecteurs: list[list[float]] = []
        total = len(prefixes)
        for debut in range(0, total, self._batch_size):
            lot = prefixes[debut : debut + self._batch_size]
            calcules = await asyncio.to_thread(backend.encode, lot)
            self._check_dimensions(calcules)
            vecteurs.extend(calcules)
            if on_progress is not None:
                on_progress(len(vecteurs), total)
        return vecteurs

    async def embed_query(self, text: str) -> list[float]:
        """Vectorise une requête, préfixe `search_query:`."""
        backend = await self._ensure_backend()
        prefixe = self._apply_prefix(text, "query")
        calcules = await asyncio.to_thread(backend.encode, [prefixe])
        self._check_dimensions(calcules)
        return calcules[0]

    def _check_dimensions(self, vecteurs: list[list[float]]) -> None:
        """Contrôle la sortie RÉELLE, pas la configuration déclarée.

        Un modèle changé sous le même nom, ou une variante tronquée,
        produirait des vecteurs incompatibles avec l'index existant sans
        qu'aucun réglage ne l'annonce.
        """
        for vecteur in vecteurs:
            if len(vecteur) != self._dim:
                raise ValueError(
                    f"Le moteur d'embedding a produit un vecteur de dimension "
                    f"{len(vecteur)}, attendu {self._dim}. Changer de modèle "
                    "d'embedding impose une réindexation complète du projet."
                )


_service: EmbeddingService | None = None


def get_embedding_service() -> EmbeddingService:
    """Service applicatif unique : le modèle n'est chargé qu'une fois."""
    global _service
    if _service is None:
        _service = EmbeddingService()
    return _service


def reset_embedding_service() -> None:
    """Oublie le service. Destiné aux tests."""
    global _service
    _service = None
