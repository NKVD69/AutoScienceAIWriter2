"""Configuration applicative — source unique des réglages.

Spécifications V0.3 §12, §13.1. Les valeurs sensibles au matériel (VRAM,
dimension d'embedding) sont figées ici et non recopiées ailleurs : un écart
entre deux emplacements produirait une base illisible plutôt qu'une erreur.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SAW_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Service ---------------------------------------------------------
    # ADR-010 : l'API est strictement locale. Le contrat openapi.yaml lie
    # explicitement le serveur à 127.0.0.1 ; exposer 0.0.0.0 est un défaut.
    host: str = "127.0.0.1"
    port: int = 8000
    api_prefix: str = "/api/v1"
    version: str = "0.3.1"
    log_level: str = "INFO"

    # --- Stockage --------------------------------------------------------
    # ADR-001 : un fichier .sqlite par projet, rien d'autre.
    data_dir: Path = Field(default=Path.home() / ".science-ai-writer")
    sqlite_busy_timeout_ms: int = 5000
    # Un projet = un fichier. Au-delà de cette limite, la connexion la moins
    # récemment utilisée est fermée : garder ouverts des dizaines de fichiers
    # .sqlite consommerait des descripteurs sans rien accélérer.
    max_open_projects: int = 5

    # --- LLM (ADR-003, ADR-014) ------------------------------------------
    # Moteur d'inférence local : "lmstudio" ou "ollama". Les deux vivent
    # derrière `LLMBackend` ; changer de moteur impose de changer aussi
    # `llm_model`, les identifiants de modèles n'étant pas interchangeables.
    llm_backend: str = "lmstudio"
    llm_model: str = "google/gemma-4-31b"
    llm_context_tokens: int = 8192
    llm_temperature_default: float = 0.2
    # Seuils observables. `load_duration` atteste la persistance des poids
    # quand le moteur la publie ; sous LM Studio, c'est l'état du modèle qui
    # fait foi (ADR-014). Le cache KV n'est exposé par aucune des deux API.
    llm_max_load_duration_ms: int = 50
    # ADR-015 : le budget de 2 000 ms de §12.2 est levé, le déversement
    # CPU/RAM étant assumé.
    #
    # Le temps au premier token ne détecte PAS un rechargement de poids, et
    # un seuil de 15 s a été essayé avant d'être écarté sur mesure : il est
    # dominé par le traitement du prompt, qui croît avec le contexte. Mesuré
    # à 3,7 s sur une invite de quelques dizaines de tokens et à 13,5 s sur
    # 700 tokens, soit ~52 tokens/s d'amorce : à 8 192 tokens de contexte,
    # l'ordre de grandeur attendu est de 160 s. Un seuil serré se
    # déclencherait donc en régime parfaitement sain, et un seuil large
    # recouvrirait le coût d'un rechargement. Les deux grandeurs ne se
    # séparent pas.
    #
    # Le détecteur de rechargement est ailleurs et il est fiable : l'état de
    # résidence du modèle (ADR-014). Ce seuil ne garde qu'un rôle de
    # plafond contre un état pathologique — thrashing, disque saturé.
    llm_max_ttft_ms: int = 300_000
    code_model_enabled: bool = False
    code_model: str = "qwen/qwen3-coder-next"
    code_model_min_vram_mb: int = 12_288
    # ADR-015 : le plafond de 9 216 Mo de §12.1 ne gouverne plus. Le modèle
    # retenu sature délibérément la VRAM et déverse le reste en RAM ; la
    # contrainte devient de ne pas provoquer d'éviction ni d'OOM, ce que
    # `check_llm_latency.py` observe par l'état du modèle. US-006 doit être
    # respécifiée sur ce critère, et non sur une marge de VRAM.
    vram_ceiling_mb: int = 10_240
    # Le déversement est un choix, pas un accident : US-006 ne doit pas le
    # rapporter comme un dépassement de budget.
    vram_offload_expected: bool = True

    # Ollama — conservé derrière l'interface (ADR-003 point 5).
    ollama_base_url: str = "http://127.0.0.1:11434"

    # LM Studio — le serveur local n'est pas démarré par défaut.
    lmstudio_base_url: str = "http://127.0.0.1:1234"
    # Analogue de `keep_alive=-1` : LM Studio n'accepte pas de durée infinie,
    # on demande une durée que le service ne dépassera pas en usage.
    lmstudio_ttl_seconds: int = 86_400

    @property
    def llm_base_url(self) -> str:
        """URL du moteur actif. Un seul réglage à changer pour basculer."""
        return self.lmstudio_base_url if self.llm_backend == "lmstudio" else self.ollama_base_url

    # --- Embeddings (ADR-013) --------------------------------------------
    # Calculés sur CPU, hors du serveur d'inférence : l'y router chargerait
    # le modèle sur GPU et évincerait le modèle de rédaction.
    embedding_model: str = "nomic-ai/nomic-embed-text-v1.5"
    embedding_dim: int = 768
    embedding_batch_size: int = 32
    embedding_cache_dir: Path = Path("./data/models")
    # `None` : borné automatiquement, en laissant une marge à l'interface.
    embedding_max_workers: int | None = None
    # ADR-010 : les poids d'embedding viennent du réseau. L'application ne
    # les télécharge pas d'elle-même ; ce réglage porte le consentement tant
    # qu'un enregistrement par projet n'existe pas (US-BIBLIO-001).
    embedding_download_consent: bool = False

    # --- Découpage RAG (§7.1) --------------------------------------------
    # Repli en fenêtre glissante quand un document n'expose pas de sections.
    chunk_tokens: int = 512
    chunk_overlap: int = 64

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    @property
    def registry_path(self) -> Path:
        """Registre global : quels projets existent, et où sont leurs fichiers."""
        return self.data_dir / "registry.sqlite"

    @property
    def trash_dir(self) -> Path:
        """Un mémoire représente des mois de travail : rien n'est effacé."""
        return self.data_dir / "trash"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Instance unique. Le cache garantit que deux modules lisent la même
    dimension d'embedding : une divergence corromprait silencieusement l'index."""
    return Settings()
