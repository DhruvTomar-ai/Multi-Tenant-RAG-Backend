from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: str = Field(..., alias="DATABASE_URL")
    app_env: str = Field(default="development", alias="APP_ENV")
    debug: bool = False
    jwt_secret_key: str = Field(..., alias="JWT_SECRET_KEY")
    jwt_refresh_secret_key: str = Field(..., alias="JWT_REFRESH_SECRET_KEY")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    access_token_expire_minutes: int = Field(default=30, alias="ACCESS_TOKEN_EXPIRE_MINUTES")
    refresh_token_expire_days: int = Field(default=7, alias="REFRESH_TOKEN_EXPIRE_DAYS")

    celery_broker_url: str = Field(default="redis://localhost:6379/0", alias="CELERY_BROKER_URL")

    document_storage_root: str = Field(default="./data/documents", alias="DOCUMENT_STORAGE_ROOT")
    max_upload_size_mb: int = Field(default=25, gt=0, alias="MAX_UPLOAD_SIZE_MB")
    max_docx_uncompressed_mb: int = Field(default=200, gt=0, alias="MAX_DOCX_UNCOMPRESSED_MB")
    allowed_document_extensions: list[str] = Field(
        default_factory=lambda: [".pdf", ".docx", ".txt"],
        alias="ALLOWED_DOCUMENT_EXTENSIONS",
    )

    # Counted by the chunker's tokenizer, not BGE's. benchmarks/chunk_size_check.py measured the longest
    # chunk at 498 BGE tokens with this value, under the model's limit of 512. Text past that limit is
    # silently left out of the embedding, so re-run that check before raising this.
    chunk_size: int = Field(default=300, gt=0, alias="CHUNK_SIZE")
    chunk_overlap: int = Field(default=64, ge=0, alias="CHUNK_OVERLAP")

    ingestion_max_retries: int = Field(default=3, ge=0, alias="INGESTION_MAX_RETRIES")
    ingestion_retry_backoff_seconds: int = Field(default=30, ge=0, alias="INGESTION_RETRY_BACKOFF_SECONDS")

    embedding_model_name: str = Field(default="BAAI/bge-small-en-v1.5", alias="EMBEDDING_MODEL_NAME")
    # A pinned commit, so stored vectors can always be matched to the exact weights that made them.
    embedding_model_revision: str = Field(default="5c38ec7c405ec4b44b94cc5a9bb96e735b38267a", alias="EMBEDDING_MODEL_REVISION")
    embedding_cache_dir: str = Field(default="./models", alias="EMBEDDING_CACHE_DIR")
    embedding_device: str = Field(default="cpu", alias="EMBEDDING_DEVICE")
    # Changing this also requires a migration: the column is vector(384).
    embedding_dimension: int = Field(default=384, gt=0, alias="EMBEDDING_DIMENSION")
    embedding_batch_size: int = Field(default=32, gt=0, le=1024, alias="EMBEDDING_BATCH_SIZE")
    # BGE v1.5 recommends this prefix on queries, and none on documents.
    embedding_query_instruction: str = Field(
        default="Represent this sentence for searching relevant passages: ",
        alias="EMBEDDING_QUERY_INSTRUCTION",
    )
    embedding_max_retries: int = Field(default=3, ge=0, alias="EMBEDDING_MAX_RETRIES")
    embedding_retry_backoff_seconds: int = Field(default=30, ge=0, alias="EMBEDDING_RETRY_BACKOFF_SECONDS")

    hnsw_ef_search: int = Field(default=100, ge=1, le=1000, alias="HNSW_EF_SEARCH")
    hnsw_iterative_scan: Literal["off", "strict_order", "relaxed_order"] = Field(default="strict_order", alias="HNSW_ITERATIVE_SCAN")
    hnsw_max_scan_tuples: int = Field(default=20000, ge=1, alias="HNSW_MAX_SCAN_TUPLES")
    hnsw_scan_mem_multiplier: float = Field(default=1.0, ge=1, le=1000, alias="HNSW_SCAN_MEM_MULTIPLIER")

    search_default_top_k: int = Field(default=5, ge=1, alias="SEARCH_DEFAULT_TOP_K")
    search_max_top_k: int = Field(default=50, ge=1, le=200, alias="SEARCH_MAX_TOP_K")
    search_max_offset: int = Field(default=200, ge=0, le=1000, alias="SEARCH_MAX_OFFSET")

    # Optional at startup: only answer generation needs it, and it fails safely without one.
    groq_api_key: SecretStr | None = Field(default=None, alias="GROQ_API_KEY")
    groq_model: str = Field(default="openai/gpt-oss-120b", alias="GROQ_MODEL")
    groq_temperature: float = Field(default=0.1, ge=0, le=2, alias="GROQ_TEMPERATURE")
    groq_max_tokens: int = Field(default=2048, gt=0, alias="GROQ_MAX_TOKENS")
    groq_timeout_seconds: float = Field(default=30, gt=0, le=300, alias="GROQ_TIMEOUT_SECONDS")
    # The SDK retries only timeouts, connection errors, 408, 409, 429 and 5xx, with backoff.
    groq_max_retries: int = Field(default=2, ge=0, le=5, alias="GROQ_MAX_RETRIES")

    rag_top_k: int = Field(default=5, ge=1, le=50, alias="RAG_TOP_K")
    # A first filter on similarity_score (1 - cosine distance), so hopeless context never reaches the LLM.
    # benchmarks/relevance_threshold.py measured answerable questions at 0.627 or above and unrelated ones
    # at 0.542 or below on short passages. 0.50 leaves margin for longer chunks, and the LLM can still decline.
    rag_min_similarity: float = Field(default=0.50, ge=-1, le=1, alias="RAG_MIN_SIMILARITY")
    # Estimated tokens. See app/services/rag/context.py.
    rag_max_context_tokens: int = Field(default=3000, gt=0, alias="RAG_MAX_CONTEXT_TOKENS")
    rag_history_messages: int = Field(default=6, ge=0, le=50, alias="RAG_HISTORY_MESSAGES")
    rag_max_history_tokens: int = Field(default=1000, ge=0, alias="RAG_MAX_HISTORY_TOKENS")
    # Earlier questions added to the retrieval query, so a follow-up keeps its subject.
    rag_retrieval_history_questions: int = Field(default=1, ge=0, le=5, alias="RAG_RETRIEVAL_HISTORY_QUESTIONS")

    @model_validator(mode="after")
    def _overlap_must_be_smaller_than_chunk(self) -> "Settings":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("CHUNK_OVERLAP must be smaller than CHUNK_SIZE")
        return self

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
