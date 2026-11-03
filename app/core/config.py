from functools import lru_cache

from pydantic import Field, model_validator
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

    chunk_size: int = Field(default=512, gt=0, alias="CHUNK_SIZE")
    chunk_overlap: int = Field(default=64, ge=0, alias="CHUNK_OVERLAP")

    ingestion_max_retries: int = Field(default=3, ge=0, alias="INGESTION_MAX_RETRIES")
    ingestion_retry_backoff_seconds: int = Field(default=30, ge=0, alias="INGESTION_RETRY_BACKOFF_SECONDS")

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
