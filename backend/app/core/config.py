"""Configuration applicative — source unique des réglages.

Spécifications V0.3 §12, §13.1. Les valeurs sensibles au matériel (VRAM,
dimension d'embedding) sont figées ici et non recopiées ailleurs : un écart
entre deux emplacements produirait une base illisible plutôt qu'une erreur.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

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
    # Moteur d'inférence local : "lmstudio" ou "ollama", indifféremment. Les
    # deux vivent derrière `LLMBackend`, et basculer ne demande que
    # SAW_LLM_BACKEND. Chaque moteur a donc son propre identifiant de modèle :
    # les identifiants ne passent pas d'un moteur à l'autre — `google/gemma-4-31b`
    # n'existe pas chez Ollama. Un réglage unique obligeait à changer aussi le
    # modèle en changeant de moteur, faute de quoi le moteur recevait
    # l'identifiant de l'autre.
    llm_backend: Literal["lmstudio", "ollama"] = "lmstudio"
    # ADR-015 : le même modèle 31B, sous l'identifiant propre à chaque moteur.
    lmstudio_model: str = "google/gemma-4-31b"
    ollama_model: str = "gemma4:31b"
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
    # Second résident réservé à l'agent code, lui aussi par moteur. Aucun
    # identifiant Ollama n'est supposé par défaut : l'option est refusée, avec
    # un message, tant que SAW_OLLAMA_CODE_MODEL n'est pas renseigné.
    lmstudio_code_model: str | None = "qwen/qwen3-coder-next"
    ollama_code_model: str | None = None
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

    # --- Rédaction (US-301) ----------------------------------------------
    # Budget d'extraits injectés dans le message du rédacteur. Le contexte
    # fait 8 192 tokens : au-delà de la moitié pour la matière, une section
    # de 1 500 mots — soit ~2 000 tokens — n'aurait plus la place de sortir,
    # et le modèle tronquerait son JSON en fin de génération.
    writer_context_tokens: int = 4000
    # Plus haute que la température par défaut : un plan est une structure,
    # un texte académique a besoin d'un peu de liberté de formulation. La
    # véracité n'en dépend pas — elle est tenue par les garde-fous
    # syntaxiques de §5.5, pas par la prudence du modèle.
    writer_temperature: float = 0.3

    # --- Relecture (US-302) ----------------------------------------------
    # Basse : une évaluation doit être aussi reproductible que possible. Deux
    # relectures du même texte ne devraient pas donner deux verdicts.
    reviewer_temperature: float = 0.1
    # Poids des six catégories dans le score global. Le sourçage pèse le plus :
    # c'est ce qui distingue un mémoire d'un devoir. La somme est vérifiée à 1.0
    # au démarrage (échouer tôt vaut mieux qu'un score faux à chaque relecture).
    review_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "sourcing": 0.30,
            "coherence": 0.25,
            "argumentation": 0.20,
            "completeness": 0.15,
            "structure": 0.05,
            "style": 0.05,
        }
    )
    # Boucle de correction : au-delà, pause et proposition de la meilleure
    # version (US-202). Les modèles 7B dégradent souvent leur sortie en
    # corrigeant, d'où « meilleure » et non « dernière ».
    max_review_loops: int = 3

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

    @property
    def llm_model(self) -> str:
        """Modèle du moteur actif, sous l'identifiant que ce moteur connaît."""
        return self.lmstudio_model if self.llm_backend == "lmstudio" else self.ollama_model

    @property
    def code_model(self) -> str | None:
        """Second modèle du moteur actif, ou None s'il n'est pas configuré."""
        if self.llm_backend == "lmstudio":
            return self.lmstudio_code_model
        return self.ollama_code_model

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

    # --- Export (US-501, US-502, ADR-006) ---------------------------------
    # `None` : cherché dans le PATH. Quarto n'est pas empaqueté avec
    # l'application (ADR-012, pas de conteneur) ; son absence est un état
    # normal, rapporté avec une procédure d'installation.
    quarto_path: Path | None = None
    # 1.4 introduit le rendu de `_quarto.yml` en projet `book` avec crossref
    # stable ; en deçà, les renvois d'un document de 300 pages se perdent.
    quarto_min_version: str = "1.4.0"
    # Une thèse de 300 pages compile en plusieurs minutes, LaTeX faisant
    # deux à trois passes. Un timeout serré tuerait une compilation saine.
    export_timeout_seconds: int = 600
    # Fenêtre de journal rendue autour d'une erreur LaTeX. Transmettre les
    # 4 000 lignes brutes n'apprend rien à personne.
    export_log_context_lines: int = 20

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

    def exports_dir(self, project_id: int) -> Path:
        """Racine des exports d'un projet. Un sous-répertoire daté par export.

        Jamais écrasée : chaque répertoire est la preuve de ce qui a été
        produit à une date, et un mémoire se relit sur des mois.
        """
        return self.data_dir / "exports" / str(project_id)

    def assert_review_weights(self) -> None:
        """Vérifie les six catégories et une somme de poids à 1.0 (US-302).

        Appelée au démarrage : un poids mal réglé produirait un score faux à
        chaque relecture, sans jamais lever. Échouer tôt le rend visible.
        """
        attendues = {"sourcing", "coherence", "argumentation", "completeness", "structure", "style"}
        presentes = set(self.review_weights)
        if presentes != attendues:
            raise ValueError(
                f"review_weights doit porter exactement les six catégories {sorted(attendues)} ; "
                f"reçu {sorted(presentes)}."
            )
        total = sum(self.review_weights.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"La somme des review_weights doit valoir 1.0 ; reçu {total}.")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Instance unique. Le cache garantit que deux modules lisent la même
    dimension d'embedding : une divergence corromprait silencieusement l'index."""
    return Settings()
