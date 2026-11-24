from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from pydantic import ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root directory (RootLens/)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


class Settings(BaseSettings):
    """Application settings resolved from environment variables or safe defaults."""

    # Application metadata
    PROJECT_NAME: str = "RootLens API"
    VERSION: str = "0.1.0"
    API_V1_STR: str = "/api/v1"

    # Runtime environment
    ENVIRONMENT: str = "development"
    DEBUG: bool = True

    # Server binding defaults
    HOST: str = "127.0.0.1"
    PORT: int = 8000

    # CORS origins
    # Configurable via BACKEND_CORS_ORIGINS env var (e.g. comma-separated string or JSON list)
    BACKEND_CORS_ORIGINS: Union[List[str], str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

    # PostgreSQL Database
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "rootlens"
    POSTGRES_USER: str = "rootlens_user"
    POSTGRES_PASSWORD: str = "your_postgres_password_here"
    DATABASE_URL: Optional[str] = None

    # OpenSearch (Log Indexing & Search Engine)
    OPENSEARCH_HOST: str = "localhost"
    OPENSEARCH_PORT: int = 9200
    OPENSEARCH_USER: Optional[str] = None
    OPENSEARCH_PASSWORD: Optional[str] = None
    OPENSEARCH_USE_SSL: bool = False
    OPENSEARCH_VERIFY_CERTS: bool = False
    OPENSEARCH_INDEX_PREFIX: str = "rootlens-logs"
    OPENSEARCH_TIMEOUT: int = 5

    # Storage & Upload Configuration
    UPLOAD_DIR: Path = _PROJECT_ROOT / "data" / "uploads"
    MAX_UPLOAD_SIZE_BYTES: int = 50 * 1024 * 1024  # 50 MB default
    ALLOWED_LOG_EXTENSIONS: List[str] = [".csv", ".json", ".log", ".txt", ".syslog"]

    # Significant Event Detection (Phase 13)
    EVENT_ERROR_BURST_THRESHOLD: int = 3
    EVENT_WINDOW_MINUTES: int = 15

    # LLM & Explanation Service (Phase 18)
    LLM_PROVIDER: str = "offline"  # "offline" or "generic_http"
    LLM_MODEL_NAME: str = "meta-llama/Llama-3-8B-Instruct"
    LLM_API_BASE: str = "http://localhost:11434/v1"
    LLM_API_KEY: Optional[str] = None
    LLM_TEMPERATURE: float = 0.1
    LLM_REQUEST_TIMEOUT_SECONDS: int = 30
    LLM_MAX_EVIDENCE_RECORDS: int = 15
    LLM_MAX_SNIPPET_CHARS: int = 500
    LLM_MAX_PROVENANCE_REFERENCES: int = 30
    LLM_MAX_TIMELINE_EVENTS: int = 20

    # LogBERT Anomaly Detection (Phase 4)
    LOGBERT_MODEL_PATH: Path = _PROJECT_ROOT / "data" / "benchmarks" / "hdfs_full" / "model" / "logbert_best.pt"
    LOGBERT_VOCAB_PATH: Path = _PROJECT_ROOT / "data" / "benchmarks" / "hdfs_full" / "processed" / "vocab.json"
    LOGBERT_DEVICE: str = "cpu"
    LOGBERT_ENABLED: bool = True
    LOGBERT_DEFAULT_THRESHOLD: Optional[float] = None  # None uses checkpoint metadata threshold

    # Operator Authentication & Trusted Gateway Settings (Phase 8 Stage 4)
    AUTH_SECRET_KEY: str = "rootlens-insecure-dev-secret-key-change-in-production"
    ALLOW_DEV_TOKENS: bool = True
    AUTH_TOKEN_ISSUER: Optional[str] = "rootlens-api"
    AUTH_TOKEN_AUDIENCE: Optional[str] = "rootlens-app"
    TRUSTED_GATEWAY_ENABLED: bool = False
    GATEWAY_SHARED_SECRET: Optional[str] = None
    GATEWAY_SECRET_HEADER: str = "X-Gateway-Secret"

    @model_validator(mode="after")
    def validate_security_configuration(self) -> "Settings":
        is_production = self.ENVIRONMENT.lower() in {"production", "prod"}
        if is_production:
            if self.ALLOW_DEV_TOKENS:
                raise ValueError("ALLOW_DEV_TOKENS must be False in production environment.")
            if (
                not self.AUTH_SECRET_KEY
                or self.AUTH_SECRET_KEY == "rootlens-insecure-dev-secret-key-change-in-production"
                or len(self.AUTH_SECRET_KEY) < 32
            ):
                raise ValueError(
                    "AUTH_SECRET_KEY must be a secure random secret of at least 32 characters in production."
                )
            if self.TRUSTED_GATEWAY_ENABLED and (
                not self.GATEWAY_SHARED_SECRET or len(self.GATEWAY_SHARED_SECRET) < 16
            ):
                raise ValueError(
                    "GATEWAY_SHARED_SECRET must be at least 16 characters when TRUSTED_GATEWAY_ENABLED is True in production."
                )
        return self

    @field_validator(
        "LLM_MAX_EVIDENCE_RECORDS",
        "LLM_MAX_SNIPPET_CHARS",
        "LLM_MAX_PROVENANCE_REFERENCES",
        "LLM_MAX_TIMELINE_EVENTS",
        mode="before",
    )
    @classmethod
    def validate_budget_limits(cls, v: Any, info: ValidationInfo) -> int:
        field_defaults = {
            "LLM_MAX_EVIDENCE_RECORDS": 15,
            "LLM_MAX_SNIPPET_CHARS": 500,
            "LLM_MAX_PROVENANCE_REFERENCES": 30,
            "LLM_MAX_TIMELINE_EVENTS": 20,
        }
        field_max = {
            "LLM_MAX_EVIDENCE_RECORDS": 100,
            "LLM_MAX_SNIPPET_CHARS": 4000,
            "LLM_MAX_PROVENANCE_REFERENCES": 200,
            "LLM_MAX_TIMELINE_EVENTS": 100,
        }
        name = info.field_name or ""
        default_val = field_defaults.get(name, 15)
        max_val = field_max.get(name, 100)
        if v is None:
            return default_val
        try:
            val = int(v)
        except (TypeError, ValueError):
            return default_val
        if val <= 0:
            return default_val
        return min(val, max_val)

    @field_validator("BACKEND_CORS_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str):
            if not v.strip():
                return []
            if not v.startswith("["):
                return [origin.strip() for origin in v.split(",") if origin.strip()]
            import json

            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return [str(x) for x in parsed]
            except Exception:
                pass
            return [v]
        return list(v)

    @property
    def async_database_url(self) -> str:
        """Return the asynchronous database connection URL (using asyncpg)."""
        if self.DATABASE_URL:
            url = self.DATABASE_URL.strip()
            if url.startswith("postgresql://"):
                return url.replace("postgresql://", "postgresql+asyncpg://", 1)
            return url
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@"
            f"{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def opensearch_hosts(self) -> List[dict[str, Any]]:
        """Return host configuration list for OpenSearch client initialization."""
        return [
            {
                "host": self.OPENSEARCH_HOST,
                "port": self.OPENSEARCH_PORT,
            }
        ]

    @property
    def opensearch_auth(self) -> Optional[tuple[str, str]]:
        """Return HTTP authentication tuple if both username and password are provided and non-empty."""
        if self.OPENSEARCH_USER and self.OPENSEARCH_PASSWORD:
            user = self.OPENSEARCH_USER.strip()
            pwd = self.OPENSEARCH_PASSWORD.strip()
            if user and pwd:
                return (user, self.OPENSEARCH_PASSWORD)
        return None

    model_config = SettingsConfigDict(
        case_sensitive=True,
        env_file=(
            str(_PROJECT_ROOT / ".env"),
            ".env",
        ),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
