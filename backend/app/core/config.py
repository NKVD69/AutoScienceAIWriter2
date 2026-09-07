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

    # --- LLM (ADR-003) ---------------------------------------------------
    ollama_base_url: str = "http://127.0.0.1:11434"
    llm_model: str = "qwen2.5:7b-instruct-q4_K_M"
    llm_keep_alive: int = -1
    code_model_enabled: bool = False
    code_model: str = "qwen2.5-coder:7b"
    code_model_min_vram_mb: int = 12_288
    vram_ceiling_mb: int = 9216

    # --- Embeddings (ADR-013) --------------------------------------------
    embedding_model: str = "nomic-ai/nomic-embed-text-v1.5"
    embedding_dim: int = 768
    embedding_batch_size: int = 32
    embedding_prefix_document: str = "search_document: "
    embedding_prefix_query: str = "search_query: "

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Instance unique. Le cache garantit que deux modules lisent la même
    dimension d'embedding : une divergence corromprait silencieusement l'index."""
    return Settings()
